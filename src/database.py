"""
database.py
------------
Handles Postgres database creation, connections, and seeding from the
starter CSV files.
"""
import csv
import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    employee_id             TEXT,
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
    shift_date   TEXT NOT NULL,
    shift_type   TEXT,
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    location     TEXT,
    department   TEXT,
    PRIMARY KEY (shift_id, owner_email),
    FOREIGN KEY (employee_id, owner_email) REFERENCES employees(employee_id, owner_email)
);

CREATE TABLE IF NOT EXISTS availability (
    id           SERIAL PRIMARY KEY,
    owner_email  TEXT DEFAULT 'demo',
    employee_id  TEXT NOT NULL,
    date         TEXT NOT NULL,
    available    TEXT NOT NULL,
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
    id             SERIAL PRIMARY KEY,
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

GOOGLE_AUTH_COLUMNS = [
    ("auth_provider",            "TEXT DEFAULT 'local'"),
    ("google_id",                "TEXT"),
    ("google_access_token",      "TEXT"),
    ("google_refresh_token",     "TEXT"),
    ("google_token_expiry",      "TEXT"),
    ("linked_sheet_id",          "TEXT"),
    ("linked_sheet_name",        "TEXT"),
    ("linked_sheet_last_synced", "TEXT"),
    ("session_token",            "TEXT"),
]

class PgConnectionWrapper:
    """Wraps a psycopg2 connection to mimic sqlite3 conn.execute() behavior."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(query, params)
        return cur

    def executemany(self, query, params=None):
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.executemany(query, params)
        return cur

    def executescript(self, script):
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(script)
        return cur

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

def get_connection():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL environment variable is not set")
    conn = psycopg2.connect(url)
    return PgConnectionWrapper(conn)

@contextmanager
def db_session():
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
    with db_session() as conn:
        conn.executescript(SCHEMA)
        cur = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
        existing = {row["column_name"] for row in cur.fetchall()}
        for col_name, col_def in GOOGLE_AUTH_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

def init_db(reset: bool = False):
    if reset:
        with db_session() as conn:
            conn.executescript("""
            DROP TABLE IF EXISTS subjective_fatigue CASCADE;
            DROP TABLE IF EXISTS availability CASCADE;
            DROP TABLE IF EXISTS shifts CASCADE;
            DROP TABLE IF EXISTS employees CASCADE;
            DROP TABLE IF EXISTS users CASCADE;
            DROP TABLE IF EXISTS fatigue_rules CASCADE;
            """)
    with db_session() as conn:
        conn.executescript(SCHEMA)
    migrate_google_columns()
    print("Database initialized")

def _read_csv(filename):
    path = os.path.join(DATA_DIR, filename)
    with open(path, newline="") as f:
        # Convert empty strings to None (null) for floats
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            parsed_row = {}
            for k, v in row.items():
                parsed_row[k] = v if v != "" else None
            rows.append(parsed_row)
        return rows

def seed_from_csv():
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
            """INSERT INTO employees
               (employee_id, owner_email, name, role, department, employment_type,
                max_weekly_hours, contracted_hours, experience_years, min_rest_hours_required)
               VALUES (%(employee_id)s, 'demo', %(name)s, %(role)s, %(department)s, %(employment_type)s,
                       %(max_weekly_hours)s, %(contracted_hours)s, %(experience_years)s, %(min_rest_hours_required)s)
               ON CONFLICT (employee_id, owner_email) DO UPDATE SET
               name=EXCLUDED.name, role=EXCLUDED.role, department=EXCLUDED.department, employment_type=EXCLUDED.employment_type,
               max_weekly_hours=EXCLUDED.max_weekly_hours, contracted_hours=EXCLUDED.contracted_hours, 
               experience_years=EXCLUDED.experience_years, min_rest_hours_required=EXCLUDED.min_rest_hours_required""",
            employees,
        )
        conn.executemany(
            """INSERT INTO shifts
               (shift_id, owner_email, employee_id, shift_date, shift_type, start_time, end_time, location, department)
               VALUES (%(shift_id)s, 'demo', %(employee_id)s, %(shift_date)s, %(shift_type)s, %(start_time)s, %(end_time)s, %(location)s, %(department)s)
               ON CONFLICT (shift_id, owner_email) DO UPDATE SET
               employee_id=EXCLUDED.employee_id, shift_date=EXCLUDED.shift_date, shift_type=EXCLUDED.shift_type,
               start_time=EXCLUDED.start_time, end_time=EXCLUDED.end_time, location=EXCLUDED.location, department=EXCLUDED.department""",
            shifts,
        )
        conn.executemany(
            """INSERT INTO availability (employee_id, date, available, reason)
               VALUES (%(employee_id)s, %(date)s, %(available)s, %(reason)s)""",
            availability,
        )
        conn.executemany(
            """INSERT INTO fatigue_rules
               (rule_id, rule_name, description, threshold_value, unit, severity)
               VALUES (%(rule_id)s, %(rule_name)s, %(description)s, %(threshold_value)s, %(unit)s, %(severity)s)
               ON CONFLICT (rule_id) DO UPDATE SET
               rule_name=EXCLUDED.rule_name, description=EXCLUDED.description, threshold_value=EXCLUDED.threshold_value,
               unit=EXCLUDED.unit, severity=EXCLUDED.severity""",
            fatigue_rules,
        )
        if subjective:
            conn.executemany(
                """INSERT INTO subjective_fatigue
                   (employee_id, report_date, fatigue_rating, notes)
                   VALUES (%(employee_id)s, %(report_date)s, %(fatigue_rating)s, %(notes)s)""",
                subjective,
            )

    print(f"Seeded: {len(employees)} employees, {len(shifts)} shifts, "
          f"{len(availability)} availability rows, {len(fatigue_rules)} fatigue rules, {len(subjective)} subjective reports.")

if __name__ == "__main__":
    init_db(reset=True)
    seed_from_csv()
