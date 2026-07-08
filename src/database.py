"""
database.py
------------
Handles SQLite database creation, connections, and seeding from the
starter CSV files (data/employees.csv, shifts.csv, availability.csv,
fatigue_rules.csv).

Uses Python's built-in sqlite3 module only - no external ORM dependency,
so the project runs with zero extra install friction.
"""
import csv
import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "VERCEL" in os.environ:
    DB_PATH = "/tmp/shift_planning.db"
else:
    DB_PATH = os.path.join(BASE_DIR, "shift_planning.db")
DATA_DIR = os.path.join(BASE_DIR, "data")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email                  TEXT PRIMARY KEY,
    password_hash          TEXT NOT NULL,
    first_name             TEXT NOT NULL,
    last_name              TEXT NOT NULL,
    security_question      TEXT NOT NULL,
    security_answer_hash   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    employee_id            TEXT,
    owner_email             TEXT DEFAULT 'demo',
    name                    TEXT NOT NULL,
    role                    TEXT,
    department              TEXT,
    employment_type         TEXT,
    max_weekly_hours        REAL DEFAULT 48,
    contracted_hours        REAL DEFAULT 40,
    experience_years        REAL,
    min_rest_hours_required REAL DEFAULT 11,
    PRIMARY KEY (employee_id, owner_email)
);

CREATE TABLE IF NOT EXISTS shifts (
    shift_id     TEXT,
    owner_email  TEXT DEFAULT 'demo',
    employee_id  TEXT NOT NULL,
    shift_date   TEXT NOT NULL,   -- YYYY-MM-DD
    shift_type   TEXT,            -- Morning / Day / Evening / Night
    start_time   TEXT NOT NULL,   -- HH:MM
    end_time     TEXT NOT NULL,   -- HH:MM (may be earlier than start_time if it crosses midnight)
    location     TEXT,
    department   TEXT,
    PRIMARY KEY (shift_id, owner_email),
    FOREIGN KEY (employee_id, owner_email) REFERENCES employees(employee_id, owner_email)
);

CREATE TABLE IF NOT EXISTS availability (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_email  TEXT DEFAULT 'demo',
    employee_id  TEXT NOT NULL,
    date         TEXT NOT NULL,
    available    TEXT NOT NULL,  -- 'Y' or 'N'
    reason       TEXT,
    FOREIGN KEY (employee_id, owner_email) REFERENCES employees(employee_id, owner_email)
);

CREATE TABLE IF NOT EXISTS fatigue_rules (
    rule_id         TEXT PRIMARY KEY,
    rule_name       TEXT NOT NULL,
    description     TEXT,
    threshold_value REAL,
    unit            TEXT,
    severity        TEXT
);

CREATE TABLE IF NOT EXISTS subjective_fatigue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_email    TEXT DEFAULT 'demo',
    employee_id    TEXT NOT NULL,
    report_date    TEXT NOT NULL,
    fatigue_rating INTEGER NOT NULL,
    notes          TEXT,
    FOREIGN KEY (employee_id, owner_email) REFERENCES employees(employee_id, owner_email)
);

CREATE INDEX IF NOT EXISTS idx_shifts_employee_date ON shifts(employee_id, shift_date);
CREATE INDEX IF NOT EXISTS idx_availability_employee_date ON availability(employee_id, date);
CREATE INDEX IF NOT EXISTS idx_subj_fatigue_employee_date ON subjective_fatigue(employee_id, report_date);
"""

# Columns added later for "Sign in with Google" + Google Sheets auto-sync.
# Kept separate (rather than baked into SCHEMA above) and applied via
# ALTER TABLE in migrate_google_columns() so an existing shift_planning.db
# created by an older version of this schema upgrades automatically on the
# next startup, instead of needing a full reset.
GOOGLE_AUTH_COLUMNS = [
    ("auth_provider",            "TEXT DEFAULT 'local'"),
    ("google_id",                "TEXT"),
    ("google_access_token",      "TEXT"),
    ("google_refresh_token",     "TEXT"),
    ("google_token_expiry",      "TEXT"),   # ISO 8601 UTC timestamp
    ("linked_sheet_id",          "TEXT"),
    ("linked_sheet_name",        "TEXT"),
    ("linked_sheet_last_synced", "TEXT"),   # ISO 8601 UTC timestamp
]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    """Context manager for a connection that commits on success and
    rolls back on error: `with db_session() as conn: ...`"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_google_columns():
    """Idempotently add the Google-auth / sheet-sync columns to `users` if
    they don't already exist. Safe to call on every startup.

    Also re-runs the base CREATE TABLE IF NOT EXISTS statements first, since
    some older shift_planning.db files out there predate the `users` table
    entirely (it was added after employees/shifts/etc.) - without this, a
    fresh ALTER TABLE on a DB missing `users` altogether would just error."""
    with db_session() as conn:
        conn.executescript(SCHEMA)  # no-op for tables that already exist
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for col_name, col_def in GOOGLE_AUTH_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")


def init_db(reset: bool = False):
    """Create tables. If reset=True, drops and recreates everything."""
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    with db_session() as conn:
        conn.executescript(SCHEMA)
    migrate_google_columns()
    print(f"Database initialized at {DB_PATH}")


def _read_csv(filename):
    path = os.path.join(DATA_DIR, filename)
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def seed_from_csv():
    """Load the starter CSVs into the database. Safe to re-run (uses
    INSERT OR REPLACE so it's idempotent)."""
    employees = _read_csv("employees.csv")
    shifts = _read_csv("shifts.csv")
    availability = _read_csv("availability.csv")
    fatigue_rules = _read_csv("fatigue_rules.csv")
    try:
        subjective = _read_csv("subjective_fatigue.csv")
    except Exception:
        subjective = []

    with db_session() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO employees
               (employee_id, owner_email, name, role, department, employment_type,
                max_weekly_hours, contracted_hours, experience_years, min_rest_hours_required)
               VALUES (:employee_id, 'demo', :name, :role, :department, :employment_type,
                       :max_weekly_hours, :contracted_hours, :experience_years, :min_rest_hours_required)""",
            employees,
        )
        conn.executemany(
            """INSERT OR REPLACE INTO shifts
               (shift_id, owner_email, employee_id, shift_date, shift_type, start_time, end_time, location, department)
               VALUES (:shift_id, 'demo', :employee_id, :shift_date, :shift_type, :start_time, :end_time, :location, :department)""",
            shifts,
        )
        conn.executemany(
            """INSERT INTO availability (employee_id, date, available, reason)
               VALUES (:employee_id, :date, :available, :reason)""",
            availability,
        )
        conn.executemany(
            """INSERT OR REPLACE INTO fatigue_rules
               (rule_id, rule_name, description, threshold_value, unit, severity)
               VALUES (:rule_id, :rule_name, :description, :threshold_value, :unit, :severity)""",
            fatigue_rules,
        )
        if subjective:
            conn.executemany(
                """INSERT INTO subjective_fatigue
                   (employee_id, report_date, fatigue_rating, notes)
                   VALUES (:employee_id, :report_date, :fatigue_rating, :notes)""",
                subjective,
            )

    print(f"Seeded: {len(employees)} employees, {len(shifts)} shifts, "
          f"{len(availability)} availability rows, {len(fatigue_rules)} fatigue rules, {len(subjective)} subjective reports.")


if __name__ == "__main__":
    init_db(reset=True)
    seed_from_csv()
