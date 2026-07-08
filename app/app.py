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

from flask import Flask, request, jsonify, make_response, session
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io

from src.database import get_connection, init_db, seed_from_csv, DB_PATH, db_session
from src.fatigue_engine import FatigueEngine
from src.ai_service import explain_fatigue_risk, explain_conflict, is_ai_configured, chat_with_ai


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())
app.permanent_session_lifetime = timedelta(days=7)

# Initialize database on startup if running on Vercel
if "VERCEL" in os.environ:
    if not os.path.exists(DB_PATH):
        print("Vercel environment detected. Initializing database in /tmp...")
        init_db(reset=True)
        seed_from_csv()

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
    if "user_email" in session:
        return session["user_email"]
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
        existing = conn.execute("SELECT email FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return error_response("Email already registered", 409)
            
        conn.execute(
            """INSERT INTO users (email, password_hash, first_name, last_name, security_question, security_answer_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
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
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], payload["password"]):
            return error_response("Invalid email or password", 401)
            
    session.permanent = True
    session["user_email"] = email
    return jsonify({"message": "Login successful", "email": email})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logout successful"})

@app.route("/api/auth/session", methods=["GET"])
def check_session():
    if "user_email" in session:
        return jsonify({"valid": True, "email": session["user_email"]})
    return jsonify({"valid": False}), 401


@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["email"])
    if err:
        return error_response(err)
        
    email = payload["email"].strip().lower()
    with db_session() as conn:
        user = conn.execute("SELECT security_question FROM users WHERE email = ?", (email,)).fetchone()
        # Always return generic response to prevent enumeration
        question = user["security_question"] if user else "What was your first pet's name?"
        return jsonify({"security_question": question})


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    payload = request.get_json(force=True, silent=True) or {}
    err = require_fields(payload, ["email", "security_answer", "new_password"])
    if err:
        return error_response(err)
        
    email = payload["email"].strip().lower()
    password = payload["new_password"]
    if len(password) < 8 or not any(char.isdigit() for char in password):
        return error_response("Password must be at least 8 characters and contain at least one number")

    with db_session() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not check_password_hash(user["security_answer_hash"], payload["security_answer"].strip().lower()):
            return error_response("Invalid security answer or email", 401)
            
        password_hash = generate_password_hash(password)
        conn.execute("UPDATE users SET password_hash = ? WHERE email = ?", (password_hash, email))
        
    return jsonify({"message": "Password reset successful"})


# ---------------------------------------------------------------------
# Health / meta
# ---------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    db_exists = os.path.exists(DB_PATH)
    return jsonify({
        "status": "ok",
        "database_initialized": db_exists,
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
    rows = conn.execute("SELECT * FROM employees WHERE owner_email = ? ORDER BY employee_id", (owner,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/employees/<employee_id>", methods=["GET"])
def get_employee(employee_id):
    owner = get_owner()
    conn = get_connection()
    row = conn.execute("SELECT * FROM employees WHERE employee_id = ? AND owner_email = ?", (employee_id, owner)).fetchone()
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        emp = conn.execute("SELECT * FROM employees WHERE employee_id = ? AND owner_email = ?", (emp_id, owner)).fetchone()
        if not emp:
            return error_response(f"Employee {emp_id} not found", 404)

        conn.execute(
            """INSERT INTO subjective_fatigue (employee_id, owner_email, report_date, fatigue_rating, notes)
               VALUES (?, ?, ?, ?, ?)""",
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
    query = "SELECT * FROM shifts WHERE owner_email = ?"
    params = [owner]
    if employee_id:
        query += " AND employee_id = ?"
        params.append(employee_id)
    if start_date:
        query += " AND shift_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND shift_date <= ?"
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
    row = conn.execute("SELECT * FROM shifts WHERE shift_id = ? AND owner_email = ?", (shift_id, owner)).fetchone()
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    cur = conn.execute("DELETE FROM shifts WHERE shift_id = ?", (shift_id,))
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
        "SELECT * FROM availability WHERE employee_id = ? ORDER BY date", (employee_id,)
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
                alternatives = eng.suggest_safer_alternatives(
                    employee_id, last_shift["shift_date"],
                    last_shift["start_time"], last_shift["end_time"], last_shift.get("shift_type"),
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
            res = eng.conn.execute("SELECT MAX(shift_date) as max_date FROM shifts WHERE owner_email = ?", (owner,)).fetchone()
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
    analysis = engine.analyze_employee(employee_id)
    safer_alternatives = [] # To save time/compute we skip alternatives in chat payload for now

    reply = chat_with_ai(analysis, safer_alternatives, history, message)
    return jsonify({"reply": reply})

@app.route("/api/ai/models", methods=["GET"])
def list_ai_models():
    """Temporary diagnostic endpoint to check which Gemini models the API key has access to."""
    import requests, os
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "No API key found in environment"})
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        resp = requests.get(url)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)})

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
        employees = eng.conn.execute("SELECT employee_id FROM employees WHERE owner_email = ?", (owner,)).fetchall()
        
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


# ---------------------------------------------------------------------
# Admin / DB
# ---------------------------------------------------------------------
@app.route("/api/admin/seed", methods=["POST"])
def admin_seed():
    """Convenience endpoint to (re)initialize and seed the database from
    the starter CSVs in data/. Intended for local dev/demo use only."""
    reset = bool((request.get_json(silent=True) or {}).get("reset", True))
    init_db(reset=reset)
    seed_from_csv()
    return jsonify({"message": "Database initialized and seeded."})


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
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        reader = csv.DictReader(stream)
        shifts = list(reader)
        
        return _process_shifts_data(shifts, owner)
        
    except Exception as exc:
        return error_response(f"Failed to parse CSV: {exc}")


@app.errorhandler(404)
def not_found(e):
    return error_response("Endpoint not found", 404)

def _process_shifts_data(shifts, owner):
    employees_created = 0
    shifts_inserted = 0
    conn = get_connection()
    try:
        for s in shifts:
            emp_id = s.get("employee_id")
            if not emp_id: continue
            # if employee doesn't exist for this owner, create a default one
            emp = conn.execute("SELECT * FROM employees WHERE employee_id = ? AND owner_email = ?", (emp_id, owner)).fetchone()
            if not emp:
                conn.execute(
                    """INSERT INTO employees (employee_id, owner_email, name, role, department)
                       VALUES (?, ?, ?, ?, ?)""",
                    (emp_id, owner, f"User {emp_id}", "Staff", s.get("department", "General"))
                )
                employees_created += 1
            
            # insert shift
            import uuid
            shift_id = s.get("shift_id") or f"S_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT OR REPLACE INTO shifts (shift_id, owner_email, employee_id, shift_date, shift_type, start_time, end_time, location, department)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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


@app.route("/api/shifts/import-sheet", methods=["POST"])
def import_sheet():
    owner = get_owner()
    if not owner or owner == "demo":
        return error_response("Please log in to import from Google Sheets.", 401)
        
    payload = request.get_json(force=True, silent=True) or {}
    sheet_input = payload.get("sheet_id")
    if not sheet_input:
        return error_response("sheet_id is required")
        
    # Extract ID if URL is provided
    import re
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", sheet_input)
    sheet_id = match.group(1) if match else sheet_input.strip()
    
    api_key = os.environ.get("GOOGLE_SHEETS_API_KEY")
    if not api_key:
        return error_response("Google Sheets integration is not configured", 500)
        
    try:
        # First, fetch the spreadsheet metadata to get the first sheet's name
        meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}?key={api_key}"
        meta_res = requests.get(meta_url)
        if not meta_res.ok:
            return error_response("Failed to fetch spreadsheet. Ensure it is public ('Anyone with the link can view').", 400)
            
        meta_data = meta_res.json()
        if not meta_data.get("sheets"):
            return error_response("Spreadsheet is empty or invalid.", 400)
            
        first_sheet_name = meta_data["sheets"][0]["properties"]["title"]
        
        # Now fetch the values
        values_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{first_sheet_name}?key={api_key}"
        values_res = requests.get(values_url)
        if not values_res.ok:
            return error_response("Failed to fetch sheet data.", 400)
            
        values_data = values_res.json()
        rows = values_data.get("values", [])
        if not rows or len(rows) < 2:
            return error_response("Sheet is empty or has no data rows.")
            
        headers = rows[0]
        shifts = []
        for row in rows[1:]:
            # Map row to dictionary based on headers
            shift_dict = {}
            for i, val in enumerate(row):
                if i < len(headers):
                    shift_dict[headers[i]] = val
            shifts.append(shift_dict)
            
        return _process_shifts_data(shifts, owner)
    except Exception as exc:
        return error_response(f"Error communicating with Google Sheets API: {exc}", 500)


@app.errorhandler(404)
def not_found(e):
    return error_response("Endpoint not found", 404)


@app.errorhandler(500)
def server_error(e):
    return error_response("Internal server error", 500)


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print("No database found - initializing and seeding from starter CSVs...")
        init_db(reset=True)
        seed_from_csv()
    app.run(debug=True, host="0.0.0.0", port=5000)
