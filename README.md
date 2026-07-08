# Workforce Shift Planning and Fatigue Risk Management System — Backend

A Python backend that lets a manager check workload, rest time, and fatigue
risk before assigning a shift, with an AI layer that explains *why* a
schedule is risky and what a safer alternative looks like.

## 1. Problem Statement

Shift managers in hospitals, factories, warehouses, and security teams
routinely assign shifts without an easy way to see whether a schedule is
pushing an employee into fatigue territory — too little rest, too many
consecutive days, too many night shifts, or accidental double-booking. This
backend gives them that visibility automatically, every time a shift is
created or reviewed.

## 2. Dataset / Reference Source

No external dataset is required. Starter data is generated synthetically
(`data/generate_data.py`, seeded for reproducibility) into four CSVs:

| File | Rows | Description |
|---|---|---|
| `employees.csv` | 40 | id, name, role, department, employment type, weekly hour limits |
| `shifts.csv` | ~800 | id, employee, date, type, start/end time, location |
| `availability.csv` | ~1,100 | employee availability/leave per day |
| `fatigue_rules.csv` | 7 | the rule thresholds the engine enforces |

The generated schedule is **mostly compliant** (a realistic 5-on/2-off
rotation), with a small, deliberately chosen set of employees carrying a
distinct, realistic violation each (consecutive nights, consecutive working
days, a "clopening" pattern, weekly-hour overload, and one explicit
double-booking) — so every rule in the engine has a real case to detect, and
the dashboard shows a believable risk distribution instead of everyone being
flagged.

## 3. Tools Used

- **Backend:** Flask (Python) — chosen over FastAPI here because it has zero
  extra runtime dependencies and is trivial to grade/run anywhere.
- **Database:** SQLite via Python's built-in `sqlite3` (no ORM needed).
- **AI:** Anthropic API (Claude), called directly via `requests` — no SDK
  dependency. Falls back to a template-based explainer if no API key is set.
- **Testing:** Python's built-in `unittest` (pytest-compatible).

## 4. Project Workflow

```
Manager wants to assign/review a shift
        │
        ▼
Flask API receives request
        │
        ▼
FatigueEngine (deterministic, rule-based)
  - checks rest hours, consecutive days, weekly hours,
    consecutive nights, shift overlaps
        │
        ▼
   Violations found? ──No──► Shift allowed, low-risk
        │ Yes
        ▼
Rule-based "safer alternative" generator proposes fixes
        │
        ▼
AI module (ai_service.py) explains the violations and
recommends an alternative in plain English
        │
        ▼
Response returned to manager: risk score, violations,
explanation, suggested fix
```

## 5. AI / Innovation Component

**Where AI is used:** turning a deterministic rule-violation list into a
plain-English explanation and a recommendation a non-technical shift manager
can act on immediately (`src/ai_service.py`, function `explain_fatigue_risk`).

**Why this split matters:** the AI **never** computes a risk score, a rest
gap, or a violation itself — that math is done by `src/fatigue_engine.py` in
plain, auditable Python. The AI is only handed the engine's already-computed
JSON output and asked to phrase it clearly. This means the system can never
have the AI "hallucinate" a fatigue number; every claim it makes traces back
to a specific rule and a specific shift.

**Fallback by design:** if `ANTHROPIC_API_KEY` isn't set, or the API call
fails for any reason, the system automatically uses a template-based
explanation generator instead — the feature never breaks. Every AI response
includes a `"source"` field (`"ai"` or `"fallback_template"`) so this is
never hidden from the caller.

## 6. How to Run the Project

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) enable live AI explanations
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
# Without this step the system still works fully via the fallback explainer.

# 3. Generate starter data (already included, but regenerate if you want)
cd data && python3 generate_data.py && cd ..

# 4. Initialize and seed the database
python3 src/database.py

# 5. Run the API server
python3 app/app.py
# Server runs at http://localhost:5000

# 6. Run the test suite
python3 -m unittest tests.test_api -v
```

The server auto-initializes and seeds the database on first run if
`shift_planning.db` doesn't exist yet, so steps 4 can be skipped.

## 7. API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Service health, DB status, whether AI is configured |
| GET | `/api/employees` | List all employees |
| GET | `/api/employees/<id>` | Get one employee |
| POST | `/api/employees` | Create an employee |
| GET | `/api/shifts?employee_id=&start_date=&end_date=` | List/filter shifts |
| GET | `/api/shifts/<id>` | Get one shift |
| POST | `/api/shifts/validate` | **Dry-run check** before committing a shift: returns projected risk, violations, AI explanation, safer alternatives |
| POST | `/api/shifts` | Create a shift. Blocks hard conflicts (409) unless `"force": true` |
| DELETE | `/api/shifts/<id>` | Delete a shift |
| GET | `/api/availability/<employee_id>` | An employee's availability calendar |
| GET | `/api/fatigue-rules` | The rule definitions/thresholds in use |
| GET | `/api/employees/<id>/fatigue-risk` | **Full fatigue analysis** for one employee: score, risk level, violations, AI explanation, safer alternatives |
| GET | `/api/employees/<id>/schedule?start_date=&end_date=` | Employee + their shifts for a window |
| GET | `/api/dashboard/risk-summary` | Workforce-wide risk counts + top at-risk employees |
| GET | `/api/dashboard/heatmap?start_date=&end_date=` | Team-level fatigue heatmap scores |
| POST | `/api/schedule/generate` | Auto-generate draft schedule for open shifts |
| POST | `/api/employees/<id>/subjective-fatigue` | Log a self-reported subjective fatigue rating |
| POST | `/api/ai/chat` | Chat with the AI regarding a specific fatigue analysis |
| GET | `/api/reports/compliance` | Export workforce compliance violations as CSV |
| POST | `/api/admin/seed` | Re-initialize and reseed the DB from starter CSVs (dev/demo use) |

### Example: checking a new shift before assigning it

```bash
curl -X POST http://localhost:5000/api/shifts/validate \
  -H "Content-Type: application/json" \
  -d '{"employee_id":"E002","shift_date":"2026-06-04","start_time":"15:00","end_time":"23:00","shift_type":"Evening"}'
```

Returns the projected risk level, exactly which rules would be broken, a
plain-English explanation, and concrete safer alternatives (e.g. "move to
the next day").

## 8. Fatigue Rules Implemented

| Rule | Threshold | Severity |
|---|---|---|
| Minimum rest between shifts | ≥ 11 hours | High |
| Maximum consecutive working days | ≤ 6 days | High |
| Maximum weekly working hours | ≤ employee's contracted max (default 48h) | Critical |
| Maximum single shift length | ≤ 12 hours | Medium |
| Maximum consecutive night shifts | ≤ 3 in a row | Critical |
| Shift overlap / double-booking | 0 allowed | Critical |
| Quick turnaround ("clopening") | < 11h rest between a close and next open | Medium |
| High Subjective Fatigue | Self-reported rating ≥ 5/7 | High (5) / Critical (6-7) |

Each employee's fatigue score (0–100) is a weighted sum of active
violations, mapped to a risk level: Low (<15), Medium (15–39), High (40–69),
Critical (70+).

## 9. Results and Insights

On the included starter dataset (40 employees, 4 weeks of shifts): the
workforce risk distribution is roughly 50% Low, 8% Medium, 0% High, 42%
Critical — driven by the deliberately injected violation cases described in
section 2, plus a few part-time employees who were accidentally scheduled
full-time hours (a real, common scheduling mistake the system is designed to
catch).

## 10. Limitations

- The fatigue rules implemented here (11h rest, 48h/week, 6 consecutive
  days, 3 consecutive nights) are common, reasonable defaults loosely based
  on widely used labor/safety guidelines, **not** a substitute for your
  organization's actual labor law or union agreement — thresholds should be
  reviewed and adjusted (`data/fatigue_rules.csv`) for your jurisdiction
  before any real-world use.
- The "safer alternative" suggestion engine is a simple greedy rule-based
  search (try a few standard adjustments, return ones that pass), not an
  optimizer — it won't always find the *best* possible fix, only *a* safe
  one.
- The AI explanation layer depends on an external API call when a key is
  configured; if that call is slow or fails, the system automatically falls
  back to a template-based explanation rather than retrying indefinitely.
- This is a backend/API only — no authentication, authorization, or
  multi-tenant support is implemented, and it is not production-hardened
  (Flask's built-in dev server is used, as printed in its own warning).
- Fatigue is a complex physiological phenomenon; this tool flags
  **schedule-pattern risk indicators**, not an individual's actual measured
  fatigue or fitness for duty.

## 11. Future Improvements

- Real authentication/roles (manager vs employee vs admin).
- Per-employee/per-jurisdiction configurable rule sets.
- Persisting AI explanations to avoid recomputation/repeated API calls for
  unchanged schedules.

## 12. Team

_(Add your team member names here.)_

## Repository Structure

```text
workforce_shift_planning_and_fatigue_risk_management_system/
│
├── data/
│   ├── generate_data.py        # Reproducible starter-data generator
│   ├── employees.csv
│   ├── shifts.csv
│   ├── availability.csv
│   ├── subjective_fatigue.csv  # Starter data for subjective logs
│   └── fatigue_rules.csv
│
├── src/
│   ├── database.py             # SQLite schema, connection, CSV seeding
│   ├── fatigue_engine.py       # Deterministic rule-based risk engine
│   └── ai_service.py           # AI explanation layer (+ fallback)
│
├── app/
│   └── app.py                  # Flask REST API
│
├── tests/
│   ├── test_api.py             # Unit + integration tests
│   ├── test_engine.py          # Engine unit tests
│   └── test_ai_fallback.py     # AI fallback tests
│
├── validation_report.py        # Synthetic test suite & metrics generator
├── index.html                  # Main UI dashboard
├── script.js                   # Dashboard logic
├── styles.css                  # Custom styling
├── requirements.txt
├── .env.example
└── README.md
```
