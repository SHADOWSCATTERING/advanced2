"""
app.py
-------
Flask REST API for the Workforce Shift Planning and Fatigue Risk
Management System.

Run with:
    python app/app.py
Then visit:
    http://localhost:5000/api/health

See README.md for the full endpoint list and example requests.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

# Allow running this file directly (python app/app.py) by adding the
# project root to sys.path so `import src...` works either way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()  # must happen BEFORE importing src.ai_service, which reads ANTHROPIC_API_KEY at import time

from flask import Flask, request, jsonify, make_response, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io
import secrets
import requests
import urllib.parse

from src.database import get_connection, init_db, seed_from_csv, db_session, migrate_google_columns
from src.fatigue_engine import FatigueEngine
from src.ai_service import explain_fatigue_risk, explain_conflict, is_ai_configured, chat_with_ai

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "super-secret-default-key")
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.permanent_session_lifetime = timedelta(days=7)

try:
    with db_session() as conn:
        emp = conn.execute("SELECT 1 FROM employees WHERE owner_email = 'demo' LIMIT 1").fetchone()
        if not emp:
            print("Seeding demo data...", flush=True)
            seed_from_csv()
except Exception as e:
    print(f"Warning: Could not check/seed demo data: {e}", flush=True)

# ---------------------------------------------------------------------
# Google OAuth config (Sign in with Google + Sheets read access)
# ---------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/api/auth/google/callback").strip()
GOOGLE_POST_LOGIN_REDIRECT = os.environ.get("GOOGLE_POST_LOGIN_REDIRECT", "/").strip()
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_SCOPES = "openid email profile https://www.googleapis.com/auth/spreadsheets.readonly"

# Ensure the database tables and Google columns exist
try:
    migrate_google_columns()
except Exception as e:
    print(f"Failed to migrate database columns on startup: {e}")

# Enable simple CORS globally for local front-end testing
@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        res = Flask.make_response(app, "")
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        res.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return res

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def error_response(message: str, status: int = 400):
    return jsonify({"error": message}), status


def get_owner():
    # Rely entirely on the secure session cookie for authenticated users
    if "user_email" in session:
        return session["user_email"]
    # Fallback to demo mode only if explicitly requested and no session exists
    return request.headers.get("X-User-Email", "demo")


def require_fields(payload: dict, fields: list):
    missing = [f for f in fields if not payload.get(f)]
    if missing:
        return f"Missing required field(s): {', '.join(missing)}"
    return None


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------
import re

@app.before_request
def verify_session():
    if request.method == "OPTIONS":
        return
    
    # If the user has a session cookie, validate it against the database
    if "user_email" in session:
        email = session["user_email"]
        token = session.get("session_token")
        if not token:
            session.clear()
            return error_response("Invalid session", 401)
            
        with db_session() as conn:
            user = conn.execute("SELECT session_token FROM users WHERE email = %s", (email,)).fetchone()
            if not user or user["session_token"] != token:
                session.clear()
                return error_response("Session expired or invalidated", 401)

@app.route("/api/auth/register", methods=["POST"])
def register():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["email", "first_name", "last_name", "password", "security_question", "security_answer"])
    if err:
        return error_response(err)
    
    email = payload["email"].strip().lower()
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return error_response("Invalid email format")
        
    password = payload["password"]
    if len(password) < 8 or not any(char.isdigit() for char in password):
        return error_response("Password must be at least 8 characters and contain at least one number")

    password_hash = generate_password_hash(password)
    sec_answer_hash = generate_password_hash(payload["security_answer"].strip().lower())

    with db_session() as conn:
        existing = conn.execute("SELECT email FROM users WHERE email = %s", (email,)).fetchone()
        if existing:
            return error_response("Email already registered", 409)
            
        conn.execute(
            """INSERT INTO users (email, password_hash, first_name, last_name, security_question, security_answer_hash)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (email, password_hash, payload["first_name"], payload["last_name"], payload["security_question"], sec_answer_hash)
        )
    return jsonify({"message": "Registration successful", "email": email}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["email", "password"])
    if err:
        return error_response(err)
        
    email = payload["email"].strip().lower()
    with db_session() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], payload["password"]):
            return error_response("Invalid email or password", 401)
            
        session_token = secrets.token_urlsafe(32)
        conn.execute("UPDATE users SET session_token = %s WHERE email = %s", (session_token, email))
            
    session.permanent = True
    session["user_email"] = email
    session["session_token"] = session_token
    return jsonify({"message": "Login successful", "email": email})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    if "user_email" in session:
        with db_session() as conn:
            conn.execute("UPDATE users SET session_token = NULL WHERE email = %s", (session["user_email"],))
    session.clear()
    return jsonify({"message": "Logout successful"})

@app.route("/api/auth/session", methods=["GET"])
def check_session():
    # If they reach here and have user_email, before_request has already validated them
    if "user_email" in session:
        return jsonify({"valid": True, "email": session["user_email"]})
    return jsonify({"valid": False}), 401

import time
from collections import defaultdict

# Simple rate limiter for password recovery (in-memory)
recovery_attempts = defaultdict(list)
MAX_ATTEMPTS = 5
COOLDOWN_SECONDS = 900 # 15 minutes

def is_rate_limited(email):
    now = time.time()
    # Clean up old attempts
    recovery_attempts[email] = [t for t in recovery_attempts[email] if now - t < COOLDOWN_SECONDS]
    if len(recovery_attempts[email]) >= MAX_ATTEMPTS:
        return True
    recovery_attempts[email].append(now)
    return False

@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["email"])
    if err:
        return error_response(err)
        
    email = payload["email"].strip().lower()
    
    if is_rate_limited(email):
        return error_response("Too many attempts. Please try again later.", 429)
        
    with db_session() as conn:
        user = conn.execute("SELECT security_question FROM users WHERE email = %s", (email,)).fetchone()
        # Always return generic response to prevent enumeration
        question = user["security_question"] if user else "What was the name of your first pet?"
        return jsonify({"security_question": question})


@app.route("/api/auth/verify-security-answer", methods=["POST"])
def verify_security_answer():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["email", "security_answer"])
    if err:
        return error_response(err)
        
    email = payload["email"].strip().lower()
    if is_rate_limited(email):
        return error_response("Too many attempts. Please try again later.", 429)

    with db_session() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        if not user or not check_password_hash(user["security_answer_hash"], payload["security_answer"].strip().lower()):
            return error_response("Incorrect answer", 401)
            
        from itsdangerous import URLSafeTimedSerializer
        serializer = URLSafeTimedSerializer(app.secret_key)
        # Include current password_hash in token to make it single-use (invalidates once password changes)
        token_data = {"email": email, "hash_prefix": user["password_hash"][:10]}
        reset_token = serializer.dumps(token_data, salt="password-reset")
        
    return jsonify({"reset_token": reset_token})

@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["email", "reset_token", "new_password"])
    if err:
        return error_response(err)
        
    email = payload["email"].strip().lower()
    password = payload["new_password"]
    
    if len(password) < 8 or not any(char.isdigit() for char in password):
        return error_response("Password must be at least 8 characters and contain at least one number")
        
    from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
    serializer = URLSafeTimedSerializer(app.secret_key)
    
    with db_session() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        if not user:
            return error_response("Invalid request", 400)
            
        try:
            # Max age of 15 minutes (900 seconds)
            token_data = serializer.loads(payload["reset_token"], salt="password-reset", max_age=900)
            if token_data["email"] != email or token_data["hash_prefix"] != user["password_hash"][:10]:
                return error_response("Invalid or used token", 401)
        except (SignatureExpired, BadSignature):
            return error_response("Invalid or expired token", 401)
            
        password_hash = generate_password_hash(password)
        # Rotate session_token to invalidate existing sessions
        new_session_token = secrets.token_urlsafe(32)
        conn.execute("UPDATE users SET password_hash = %s, session_token = %s WHERE email = %s", (password_hash, new_session_token, email))
        
    return jsonify({"message": "Password reset successful"})


# ---------------------------------------------------------------------
# Google OAuth ("Sign in with Google")
# ---------------------------------------------------------------------
@app.route("/api/auth/google/login", methods=["GET"])
def google_login():
    if not GOOGLE_CLIENT_ID:
        return error_response(
            "Google sign-in is not configured on the server (missing GOOGLE_CLIENT_ID).", 500
        )

    # CSRF protection: a random state we can verify on the way back.
    state = secrets.token_urlsafe(24)
    session["google_oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": state,
        "access_type": "offline",   # ask for a refresh_token
        "prompt": "consent",        # force Google to actually hand one back every time
        "include_granted_scopes": "true",
    }
    return redirect(f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}")


@app.route("/api/auth/google/callback", methods=["GET"])
def google_callback():
    error = request.args.get("error")
    if error:
        return redirect(f"{GOOGLE_POST_LOGIN_REDIRECT}?google_login=error&reason={urllib.parse.quote(error)}")

    state = request.args.get("state")
    if not state or state != session.pop("google_oauth_state", None):
        return redirect(f"{GOOGLE_POST_LOGIN_REDIRECT}?google_login=error&reason=invalid_state")

    code = request.args.get("code")
    if not code:
        return redirect(f"{GOOGLE_POST_LOGIN_REDIRECT}?google_login=error&reason=no_code")

    try:
        token_resp = requests.post(GOOGLE_TOKEN_ENDPOINT, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        token_resp.raise_for_status()
        tokens = token_resp.json()

        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")  # only present on first consent
        expires_in = tokens.get("expires_in", 3600)
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        userinfo_resp = requests.get(
            GOOGLE_USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
        )
        userinfo_resp.raise_for_status()
        info = userinfo_resp.json()

        email = info["email"].strip().lower()
        google_id = info["sub"]
        first_name = info.get("given_name", info.get("name", "Google")).strip()
        last_name = info.get("family_name", "User").strip()

        with db_session() as conn:
            existing = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
            if existing:
                # Keep existing refresh_token if Google didn't send a new one
                # (it only sends one on the very first consent for this app).
                if refresh_token:
                    conn.execute(
                        """UPDATE users SET auth_provider='google', google_id=%s,
                           google_access_token=%s, google_refresh_token=%s, google_token_expiry=%s
                           WHERE email=%s""",
                        (google_id, access_token, refresh_token, expiry, email),
                    )
                else:
                    conn.execute(
                        """UPDATE users SET auth_provider='google', google_id=%s,
                           google_access_token=%s, google_token_expiry=%s
                           WHERE email=%s""",
                        (google_id, access_token, expiry, email),
                    )
            else:
                # New account created via Google. password_hash/security fields
                # are unusable placeholders since this user never sets a local
                # password - they always sign in through Google.
                placeholder_hash = generate_password_hash(secrets.token_urlsafe(32))
                conn.execute(
                    """INSERT INTO users
                       (email, password_hash, first_name, last_name, security_question,
                        security_answer_hash, auth_provider, google_id,
                        google_access_token, google_refresh_token, google_token_expiry)
                       VALUES (%s, %s, %s, %s, %s, %s, 'google', %s, %s, %s, %s)""",
                    (email, placeholder_hash, first_name, last_name,
                     "N/A (Google account)", placeholder_hash,
                     google_id, access_token, refresh_token, expiry),
                )

            session_token = secrets.token_urlsafe(32)
            conn.execute("UPDATE users SET session_token = %s WHERE email = %s", (session_token, email))

        session.permanent = True
        session["user_email"] = email
        session["session_token"] = session_token
        return redirect(f"{GOOGLE_POST_LOGIN_REDIRECT}?google_login=success")

    except Exception as exc:
        return redirect(f"{GOOGLE_POST_LOGIN_REDIRECT}?google_login=error&reason={urllib.parse.quote(str(exc))}")


def get_valid_google_token(email: str):
    """Returns a fresh Google access token for this user, refreshing it via
    their stored refresh_token if it's expired. Returns None if the user
    never signed in with Google / has no usable token."""
    with db_session() as conn:
        user = conn.execute(
            "SELECT google_access_token, google_refresh_token, google_token_expiry FROM users WHERE email = %s",
            (email,),
        ).fetchone()

    if not user or not user["google_access_token"]:
        return None

    expiry = user["google_token_expiry"]
    is_expired = True
    if expiry:
        try:
            is_expired = datetime.now(timezone.utc) >= datetime.fromisoformat(expiry)
        except ValueError:
            is_expired = True

    if not is_expired:
        return user["google_access_token"]

    if not user["google_refresh_token"]:
        return None  # expired and nothing to refresh with

    resp = requests.post(GOOGLE_TOKEN_ENDPOINT, data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": user["google_refresh_token"],
        "grant_type": "refresh_token",
    })
    if not resp.ok:
        return None

    tokens = resp.json()
    new_access_token = tokens["access_token"]
    new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))).isoformat()

    with db_session() as conn:
        conn.execute(
            "UPDATE users SET google_access_token = %s, google_token_expiry = %s WHERE email = %s",
            (new_access_token, new_expiry, email),
        )
    return new_access_token


# ---------------------------------------------------------------------
# Health / meta
# ---------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "database_initialized": True,
        "ai_configured": is_ai_configured(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------
@app.route("/api/employees", methods=["GET"])
def list_employees():
    owner = get_owner()
    conn = get_connection()
    rows = conn.execute("SELECT * FROM employees WHERE owner_email = %s ORDER BY employee_id", (owner,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/employees/<employee_id>", methods=["GET"])
def get_employee(employee_id):
    owner = get_owner()
    conn = get_connection()
    row = conn.execute("SELECT * FROM employees WHERE employee_id = %s AND owner_email = %s", (employee_id, owner)).fetchone()
    conn.close()
    if not row:
        return error_response(f"Employee {employee_id} not found", 404)
    return jsonify(dict(row))


@app.route("/api/employees", methods=["POST"])
def create_employee():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["employee_id", "name"])
    if err:
        return error_response(err)

    owner = get_owner()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO employees
               (employee_id, owner_email, name, role, department, employment_type,
                max_weekly_hours, contracted_hours, experience_years, min_rest_hours_required)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                payload["employee_id"], owner, payload["name"], payload.get("role"),
                payload.get("department"), payload.get("employment_type", "Full-Time"),
                payload.get("max_weekly_hours", 48), payload.get("contracted_hours", 40),
                payload.get("experience_years"), payload.get("min_rest_hours_required", 11),
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        return error_response(f"Could not create employee: {exc}")
    conn.close()
    return jsonify({"message": "Employee created", "employee_id": payload["employee_id"]}), 201


@app.route("/api/employees/<emp_id>/subjective-fatigue", methods=["POST"])
def add_subjective_fatigue(emp_id):
    """Log a subjective fatigue rating (1-7) for an employee."""
    payload = request.get_json(force=True, silent=True) or {}
    rating = payload.get("fatigue_rating")
    report_date = payload.get("report_date", datetime.today().strftime("%Y-%m-%d"))
    notes = payload.get("notes", "")

    if not rating or not isinstance(rating, int) or rating < 1 or rating > 7:
        return error_response("fatigue_rating must be an integer between 1 and 7")

    owner = get_owner()
    with db_session() as conn:
        # verify employee
        emp = conn.execute("SELECT * FROM employees WHERE employee_id = %s AND owner_email = %s", (emp_id, owner)).fetchone()
        if not emp:
            return error_response(f"Employee {emp_id} not found", 404)

        conn.execute(
            """INSERT INTO subjective_fatigue (employee_id, owner_email, report_date, fatigue_rating, notes)
               VALUES (%s, %s, %s, %s, %s)""",
            (emp_id, owner, report_date, rating, notes)
        )
    return jsonify({"status": "ok", "message": "Fatigue rating logged"})

# ---------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------
@app.route("/api/shifts", methods=["GET"])
def list_shifts():
    employee_id = request.args.get("employee_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    owner = get_owner()
    query = "SELECT * FROM shifts WHERE owner_email = %s"
    params = [owner]
    if employee_id:
        query += " AND employee_id = %s"
        params.append(employee_id)
    if start_date:
        query += " AND shift_date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND shift_date <= %s"
        params.append(end_date)
    query += " ORDER BY shift_date, start_time"

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/shifts/<shift_id>", methods=["GET"])
def get_shift(shift_id):
    owner = get_owner()
    conn = get_connection()
    row = conn.execute("SELECT * FROM shifts WHERE shift_id = %s AND owner_email = %s", (shift_id, owner)).fetchone()
    conn.close()
    if not row:
        return error_response(f"Shift {shift_id} not found", 404)
    return jsonify(dict(row))


@app.route("/api/shifts/validate", methods=["POST"])
def validate_shift():
    """Dry-run check BEFORE committing a shift assignment. Returns the
    projected fatigue impact, any new violations, an AI explanation, and
    rule-based safer alternatives. Does not write to the database."""
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["employee_id", "shift_date", "start_time", "end_time"])
    if err:
        return error_response(err)

    owner = get_owner()
    eng = FatigueEngine(owner_email=owner)
    try:
        check = eng.validate_new_shift(
            payload["employee_id"], payload["shift_date"],
            payload["start_time"], payload["end_time"], payload.get("shift_type"),
        )
        if "error" in check:
            return error_response(check["error"], 404)

        alternatives = []
        if not check["safe_to_assign"]:
            alternatives = eng.suggest_safer_alternatives(
                payload["employee_id"], payload["shift_date"],
                payload["start_time"], payload["end_time"], payload.get("shift_type"),
            )

        explanation_input = {
            "employee_name": check["employee_name"],
            "risk_level": check["projected_risk_level"],
            "violations": check["would_introduce_violations"],
        }
        ai_explanation = explain_fatigue_risk(explanation_input, alternatives)
    finally:
        eng.close()

    return jsonify({**check, "safer_alternatives": alternatives, "ai_explanation": ai_explanation})


@app.route("/api/shifts", methods=["POST"])
def create_shift():
    """Create a shift. By default this BLOCKS hard conflicts (overlaps)
    unless force=true is passed; soft fatigue risk is allowed through but
    flagged in the response so a manager can make an informed call."""
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["shift_id", "employee_id", "shift_date", "start_time", "end_time"])
    if err:
        return error_response(err)

    force = bool(payload.get("force", False))
    owner = get_owner()

    eng = FatigueEngine(owner_email=owner)
    try:
        check = eng.validate_new_shift(
            payload["employee_id"], payload["shift_date"],
            payload["start_time"], payload["end_time"], payload.get("shift_type"),
        )
        if "error" in check:
            return error_response(check["error"], 404)

        has_overlap = any(v["rule_id"] == "R006" for v in check["would_introduce_violations"])
        if has_overlap and not force:
            ai_explanation = explain_conflict({
                "rule_id": "R006", "rule_name": "Shift Overlap / Double-Booking",
                "severity": "Critical",
                "detail": "The new shift overlaps with an existing shift for this employee.",
                "employee_name": check["employee_name"],
            })
            return jsonify({
                "error": "This shift conflicts with an existing shift for this employee.",
                "blocked": True,
                "validation": check,
                "ai_explanation": ai_explanation,
                "hint": "Resend with \"force\": true to assign anyway (not recommended).",
            }), 409
    finally:
        eng.close()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO shifts (shift_id, owner_email, employee_id, shift_date, shift_type,
               start_time, end_time, location, department)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                payload["shift_id"], owner, payload["employee_id"], payload["shift_date"],
                payload.get("shift_type", "Day"), payload["start_time"], payload["end_time"],
                payload.get("location"), payload.get("department"),
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        return error_response(f"Could not create shift: {exc}")
    conn.close()

    return jsonify({
        "message": "Shift created",
        "shift_id": payload["shift_id"],
        "fatigue_warnings": check["would_introduce_violations"],
        "projected_risk_level": check["projected_risk_level"],
    }), 201


@app.route("/api/shifts/<shift_id>", methods=["DELETE"])
def delete_shift(shift_id):
    conn = get_connection()
    cur = conn.execute("DELETE FROM shifts WHERE shift_id = %s", (shift_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if not deleted:
        return error_response(f"Shift {shift_id} not found", 404)
    return jsonify({"message": f"Shift {shift_id} deleted"})


# ---------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------
@app.route("/api/availability/<employee_id>", methods=["GET"])
def get_availability(employee_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM availability WHERE employee_id = %s ORDER BY date", (employee_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------
# Fatigue rules (reference data)
# ---------------------------------------------------------------------
@app.route("/api/fatigue-rules", methods=["GET"])
def list_fatigue_rules():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM fatigue_rules").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------
# Fatigue risk analysis (the core AI + business logic feature)
# ---------------------------------------------------------------------
@app.route("/api/employees/<employee_id>/fatigue-risk", methods=["GET"])
def employee_fatigue_risk(employee_id):
    """Full fatigue-risk analysis for one employee, with an AI-generated
    plain-English explanation and (if risky) rule-based safer-alternative
    suggestions for their most recent flagged shift."""
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    owner = get_owner()
    eng = FatigueEngine(owner_email=owner)
    try:
        analysis = eng.analyze_employee(employee_id, start_date, end_date)
        if "error" in analysis:
            return error_response(analysis["error"], 404)

        alternatives = []
        if analysis["violations"]:
            shifts = eng.get_shifts_for_employee(employee_id, start_date, end_date)
            if shifts:
                last_shift = shifts[-1]
                # suggest_safer_alternatives will fetch employee itself internally once
                alternatives = eng.suggest_safer_alternatives(
                    employee_id, last_shift["shift_date"],
                    last_shift["start_time"], last_shift["end_time"], last_shift.get("shift_type")
                )
        
        ai_explanation = explain_fatigue_risk(analysis, alternatives)
    finally:
        eng.close()

    return jsonify({**analysis, "safer_alternatives": alternatives, "ai_explanation": ai_explanation})

@app.route("/api/employees/<employee_id>/schedule", methods=["GET"])
def employee_schedule(employee_id):
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    owner = get_owner()
    eng = FatigueEngine(owner_email=owner)
    try:
        employee = eng.get_employee(employee_id)
        if not employee:
            return error_response(f"Employee {employee_id} not found", 404)
        shifts = eng.get_shifts_for_employee(employee_id, start_date, end_date)
    finally:
        eng.close()
    return jsonify({"employee": employee, "shifts": shifts})


@app.route("/api/dashboard/risk-summary", methods=["GET"])
def dashboard_risk_summary():
    """Workforce-wide fatigue risk dashboard: counts by risk level and the
    top at-risk employees. Powers the manager-facing dashboard view."""
    owner = get_owner()
    eng = FatigueEngine(owner_email=owner)
    try:
        summary = eng.dashboard_risk_summary()
    finally:
        eng.close()
    return jsonify(summary)

@app.route("/api/dashboard/heatmap", methods=["GET"])
def dashboard_heatmap():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    
    owner = get_owner()
    eng = FatigueEngine(owner_email=owner)
    try:
        if not start_date or not end_date:
            res = eng.conn.execute("SELECT MAX(shift_date) as max_date FROM shifts WHERE owner_email = %s", (owner,)).fetchone()
            if res and res["max_date"]:
                max_date = datetime.strptime(res["max_date"], "%Y-%m-%d").date()
                d1 = max_date - timedelta(days=6)
                start_date = d1.isoformat()
                end_date = max_date.isoformat()
            else:
                return jsonify({"dates": [], "heatmap": []})

        heatmap = eng.heatmap_data(start_date, end_date)
    finally:
        eng.close()
    return jsonify(heatmap)


# ---------------------------------------------------------------------
# Auto-Generation
# ---------------------------------------------------------------------
@app.route("/api/schedule/generate", methods=["POST"])
def generate_schedule():
    """Generates a draft schedule for a list of open shifts."""
    payload = request.get_json(force=True, silent=True) or {}
    open_shifts = payload.get("open_shifts", [])
    
    if not open_shifts:
        return error_response("Missing 'open_shifts' in payload")

    owner = get_owner()
    eng = FatigueEngine(owner_email=owner)
    try:
        result = eng.generate_draft_schedule(open_shifts)
    finally:
        eng.close()
        
    return jsonify(result)


# ---------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------
@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    payload = request.get_json(force=True, silent=True) or {}
    employee_id = payload.get("employee_id")
    history = payload.get("history", [])
    message = payload.get("message", "")

    if not employee_id or not message:
        return error_response("employee_id and message are required")

    owner = get_owner()
    engine = FatigueEngine(owner_email=owner)
    try:
        analysis = engine.analyze_employee(employee_id)
        safer_alternatives = [] # To save time/compute we skip alternatives in chat payload for now

        reply = chat_with_ai(analysis, safer_alternatives, history, message)
        return jsonify({"reply": reply})
    finally:
        engine.close()

# ---------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------
@app.route("/api/download-example-csv", methods=["GET"])
def download_example_csv():
    """Returns a static example CSV for shift upload format."""
    si = io.StringIO()
    cw = csv.writer(si)
    # Match exact columns expected by /api/upload-csv
    cw.writerow(["employee_id", "shift_date", "shift_type", "start_time", "end_time", "location", "department"])
    cw.writerow(["E101", "2026-06-01", "Day", "08:00", "16:00", "Ward A", "Emergency"])
    cw.writerow(["E101", "2026-06-02", "Day", "08:00", "16:00", "Ward A", "Emergency"])
    cw.writerow(["E102", "2026-06-01", "Night", "20:00", "04:00", "Ward B", "ICU"])
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=example_shifts.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/api/reports/daily-email", methods=["GET", "POST"])
def daily_email_report():
    cron_secret = os.environ.get("CRON_SECRET")
    auth_header = request.headers.get("Authorization")
    if cron_secret and auth_header != f"Bearer {cron_secret}":
        return error_response("Unauthorized", 401)
        
    resend_api_key = os.environ.get("RESEND_API_KEY")
    recipients = os.environ.get("REPORT_RECIPIENT_EMAILS")
    
    if not resend_api_key or not recipients:
        print("Missing RESEND_API_KEY or REPORT_RECIPIENT_EMAILS")
        return error_response("Email configuration missing", 500)
        
    owner = get_owner()
    eng = FatigueEngine(owner_email=owner)
    try:
        summary = eng.dashboard_risk_summary()
        employees = eng.conn.execute("SELECT employee_id FROM employees WHERE owner_email = %s", (owner,)).fetchall()
        
        html_content = "<h2>Daily Fatigue Risk Report</h2>"
        html_content += f"<p><strong>High Risk:</strong> {summary.get('high_risk', 0)} | <strong>Medium Risk:</strong> {summary.get('medium_risk', 0)} | <strong>Low Risk:</strong> {summary.get('low_risk', 0)}</p>"
        html_content += "<table border='1' cellpadding='5' cellspacing='0' style='border-collapse: collapse;'><tr><th>Employee</th><th>Risk Score</th><th>Violations</th></tr>"
        
        for row in employees:
            analysis = eng.analyze_employee(row["employee_id"])
            emp_name = analysis.get("employee_name", row["employee_id"])
            score = analysis.get("fatigue_score", 0)
            violations_count = len(analysis.get("violations", []))
            
            row_style = ""
            if score >= 75:
                row_style = "background-color: #ffeaea;"
            elif score >= 50:
                row_style = "background-color: #fff4ea;"
                
            html_content += f"<tr style='{row_style}'><td>{emp_name}</td><td>{score}</td><td>{violations_count}</td></tr>"
            
        html_content += "</table>"
    finally:
        eng.close()
        
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"},
            json={
                "from": "FatigueX <onboarding@resend.dev>",
                "to": [email.strip() for email in recipients.split(",") if email.strip()],
                "subject": "Daily Fatigue Risk Report",
                "html": html_content
            }
        )
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send email: {e}")
        pass

    return jsonify({"message": "Daily email processed", "status": "ok"})



import re
import uuid


def _fetch_sheet_rows(sheet_id: str, owner: str):
    """Fetch all rows (as list-of-dicts, keyed by header) from the first
    tab of a Google Sheet. Prefers the signed-in user's own OAuth token
    (works for private sheets they own or can view); falls back to a
    server-wide API key (only works for sheets shared as "Anyone with the
    link can view"). Returns (header, shifts) or raises ValueError with a
    user-facing message."""
    access_token = get_valid_google_token(owner)

    if access_token:
        headers = {"Authorization": f"Bearer {access_token}"}
        params = ""
    else:
        api_key = os.environ.get("GOOGLE_SHEETS_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "No Google account is linked and no server API key is configured. "
                "Sign in with Google, or set GOOGLE_SHEETS_API_KEY on the server."
            )
        headers = {}
        params = f"?key={api_key}"

    meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}{params}"
    meta_resp = requests.get(meta_url, headers=headers)
    if meta_resp.status_code == 403:
        raise ValueError(
            "Permission denied. If you're not signed in with Google, the sheet must be "
            "shared as 'Anyone with the link can view'. If you are signed in, make sure "
            "this sheet is in your Google account."
        )
    elif meta_resp.status_code == 404:
        raise ValueError("Spreadsheet not found. Please check the URL or ID.")
    meta_resp.raise_for_status()

    meta_data = meta_resp.json()
    first_sheet_title = meta_data.get("sheets", [{}])[0].get("properties", {}).get("title")
    if not first_sheet_title:
        raise ValueError("Could not read any sheet tabs from that document.")

    encoded_title = urllib.parse.quote(first_sheet_title)
    values_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{encoded_title}{params}"
    values_resp = requests.get(values_url, headers=headers)
    values_resp.raise_for_status()

    rows = values_resp.json().get("values", [])
    if not rows or len(rows) < 2:
        raise ValueError("The spreadsheet is empty or has no data rows.")

    header = [str(h).strip().lower() for h in rows[0]]
    shifts = []
    for row in rows[1:]:
        padded_row = row + [""] * (len(header) - len(row))
        shifts.append(dict(zip(header, padded_row)))

    return first_sheet_title, shifts


def _extract_sheet_id(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
        
    # Standard format: /d/ID
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", raw)
    if match:
        # Handle the edge case of 'e' which is for published sheets
        if match.group(1) == "e":
            raise ValueError("Published sheet URLs (starting with /d/e/) are not supported. Please use the standard 'Share' link.")
        return match.group(1)
        
    # Old format or query param: key=ID or id=ID
    match = re.search(r"[?&](?:key|id)=([a-zA-Z0-9-_]+)", raw)
    if match:
        return match.group(1)
        
    # If it's still a full URL and didn't match, it's invalid
    if raw.startswith("http://") or raw.startswith("https://"):
        raise ValueError("Invalid Google Sheets URL format. Please paste a standard Google Sheets link (e.g., https://docs.google.com/spreadsheets/d/...).")
        
    # Otherwise, assume the user just pasted the raw ID directly
    return raw


@app.route("/api/shifts/import-sheet", methods=["POST"])
def import_sheet():
    """One-off import: paste a sheet URL/ID and pull its rows in immediately.
    Does NOT remember the sheet for auto-sync - use /api/sheets/link for that."""
    owner = get_owner()
    if not owner or owner == "demo":
        return error_response("Please log in to import from Google Sheets.", 401)

    payload = request.get_json(force=True, silent=True) or {}
    sheet_id = _extract_sheet_id(payload.get("sheet_id", ""))
    if not sheet_id:
        return error_response("No Google Sheets URL or ID provided.")

    try:
        _sheet_title, shifts = _fetch_sheet_rows(sheet_id, owner)
        return _process_shifts_data(shifts, owner)
    except ValueError as ve:
        return error_response(str(ve))
    except requests.exceptions.HTTPError as he:
        return error_response(f"Google Sheets API Error ({he.response.status_code}): {he.response.text}")
    except Exception as exc:
        return error_response(f"Failed to import Google Sheet: {exc}")


@app.route("/api/sheets/link", methods=["POST"])
def link_sheet():
    """Remember a spreadsheet against the logged-in user's account and do
    an immediate first sync. The frontend then polls /api/sheets/sync
    periodically to pick up any edits made in the sheet afterwards."""
    owner = get_owner()
    if not owner or owner == "demo":
        return error_response("Please log in to link a Google Sheet.", 401)

    payload = request.get_json(force=True, silent=True) or {}
    sheet_id = _extract_sheet_id(payload.get("sheet_id", ""))
    if not sheet_id:
        return error_response("No Google Sheets URL or ID provided.")

    try:
        sheet_title, shifts = _fetch_sheet_rows(sheet_id, owner)
    except ValueError as ve:
        return error_response(str(ve))
    except requests.exceptions.HTTPError as he:
        return error_response(f"Google Sheets API Error ({he.response.status_code}): {he.response.text}")
    except Exception as exc:
        return error_response(f"Failed to link Google Sheet: {exc}")

    result = _process_shifts_data(shifts, owner)

    now_iso = datetime.now(timezone.utc).isoformat()
    with db_session() as conn:
        conn.execute(
            "UPDATE users SET linked_sheet_id = %s, linked_sheet_name = %s, linked_sheet_last_synced = %s WHERE email = %s",
            (sheet_id, sheet_title, now_iso, owner),
        )

    if isinstance(result, tuple):
        body, status = result
        data = body.get_json()
    else:
        data = result.get_json()
    data["linked_sheet_id"] = sheet_id
    data["linked_sheet_name"] = sheet_title
    data["last_synced"] = now_iso
    return jsonify(data)


@app.route("/api/sheets/sync", methods=["POST"])
def sync_linked_sheet():
    """Re-fetch the user's already-linked sheet and pull in any changes.
    Meant to be called on page load and on a timer from the frontend."""
    owner = get_owner()
    if not owner or owner == "demo":
        return error_response("Please log in.", 401)

    with db_session() as conn:
        user = conn.execute("SELECT linked_sheet_id, linked_sheet_name FROM users WHERE email = %s", (owner,)).fetchone()

    if not user or not user["linked_sheet_id"]:
        return error_response("No Google Sheet is linked to this account yet.", 400)

    sheet_id = user["linked_sheet_id"]
    try:
        sheet_title, shifts = _fetch_sheet_rows(sheet_id, owner)
    except ValueError as ve:
        return error_response(str(ve))
    except requests.exceptions.HTTPError as he:
        return error_response(f"Google Sheets API Error ({he.response.status_code}): {he.response.text}")
    except Exception as exc:
        return error_response(f"Failed to sync Google Sheet: {exc}")

    result = _process_shifts_data(shifts, owner)
    now_iso = datetime.now(timezone.utc).isoformat()
    with db_session() as conn:
        conn.execute("UPDATE users SET linked_sheet_last_synced = %s WHERE email = %s", (now_iso, owner))

    if isinstance(result, tuple):
        body, status = result
        data = body.get_json()
    else:
        data = result.get_json()
    data["linked_sheet_id"] = sheet_id
    data["linked_sheet_name"] = sheet_title
    data["last_synced"] = now_iso
    return jsonify(data)


@app.route("/api/sheets/unlink", methods=["POST"])
def unlink_sheet():
    owner = get_owner()
    if not owner or owner == "demo":
        return error_response("Please log in.", 401)
    with db_session() as conn:
        conn.execute(
            "UPDATE users SET linked_sheet_id = NULL, linked_sheet_name = NULL, linked_sheet_last_synced = NULL WHERE email = %s",
            (owner,),
        )
    return jsonify({"message": "Sheet unlinked."})


@app.route("/api/sheets/status", methods=["GET"])
def sheet_status():
    owner = get_owner()
    if not owner or owner == "demo":
        return jsonify({"linked": False})
    with db_session() as conn:
        user = conn.execute(
            "SELECT linked_sheet_id, linked_sheet_name, linked_sheet_last_synced, auth_provider FROM users WHERE email = %s",
            (owner,),
        ).fetchone()
    if not user or not user["linked_sheet_id"]:
        return jsonify({"linked": False, "auth_provider": user["auth_provider"] if user else None})
    return jsonify({
        "linked": True,
        "sheet_id": user["linked_sheet_id"],
        "sheet_name": user["linked_sheet_name"],
        "last_synced": user["linked_sheet_last_synced"],
        "auth_provider": user["auth_provider"],
    })


@app.route("/api/upload-csv", methods=["POST"])
def upload_csv():
    owner = get_owner()
    if not owner or owner == "demo":
        return error_response("Please log in to upload a CSV.", 401)

    if "file" not in request.files:
        return error_response("No file uploaded.")

    file = request.files["file"]
    if file.filename == "":
        return error_response("No file selected.")

    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        reader = csv.DictReader(stream)
        shifts = list(reader)

        return _process_shifts_data(shifts, owner)

    except Exception as exc:
        return error_response(f"Failed to parse CSV: {exc}")


def _process_shifts_data(shifts, owner):
    employees_created = 0
    shifts_inserted = 0
    conn = get_connection()
    try:
        conn.execute("DELETE FROM subjective_fatigue WHERE owner_email = %s", (owner,))
        conn.execute("DELETE FROM availability WHERE owner_email = %s", (owner,))
        conn.execute("DELETE FROM shifts WHERE owner_email = %s", (owner,))
        conn.execute("DELETE FROM employees WHERE owner_email = %s", (owner,))
        
        for s in shifts:
            emp_id = s.get("employee_id")
            if not emp_id: continue
            # if employee doesn't exist for this owner, create a default one
            emp = conn.execute("SELECT * FROM employees WHERE employee_id = %s AND owner_email = %s", (emp_id, owner)).fetchone()
            if not emp:
                conn.execute(
                    """INSERT INTO employees (employee_id, owner_email, name, role, department)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (emp_id, owner, f"User {emp_id}", "Staff", s.get("department", "General"))
                )
                employees_created += 1

            # insert shift
            shift_id = s.get("shift_id") or f"S_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT INTO shifts (shift_id, owner_email, employee_id, shift_date, shift_type, start_time, end_time, location, department)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (shift_id, owner_email) DO UPDATE SET
                       employee_id = EXCLUDED.employee_id,
                       shift_date = EXCLUDED.shift_date,
                       shift_type = EXCLUDED.shift_type,
                       start_time = EXCLUDED.start_time,
                       end_time = EXCLUDED.end_time,
                       location = EXCLUDED.location,
                       department = EXCLUDED.department""",
                (shift_id, owner, emp_id, s.get("shift_date"), s.get("shift_type", "Day"), s.get("start_time"), s.get("end_time"), s.get("location"), s.get("department"))
            )
            shifts_inserted += 1
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return error_response(f"Database error during import: {exc}")
    finally:
        conn.close()

    return jsonify({"message": f"Successfully processed {shifts_inserted} shifts. Created {employees_created} new employees."})



@app.errorhandler(404)
def not_found(e):
    return error_response("Endpoint not found", 404)


@app.errorhandler(500)
def server_error(e):
    return error_response("Internal server error", 500)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)