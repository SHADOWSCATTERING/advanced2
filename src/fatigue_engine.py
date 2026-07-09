"""
fatigue_engine.py
-------------------
Deterministic, rule-based business logic for the Workforce Shift Planning
and Fatigue Risk Management System.

Design principle: all SAFETY-CRITICAL math (rest hours, consecutive days,
weekly hour totals, overlap detection, scoring) is computed here with
plain Python - NOT by the AI model. The AI module (ai_service.py) only
explains and phrases what this engine has already calculated. This keeps
the system auditable and avoids letting an LLM "guess" at numbers in a
safety-relevant domain.

Rules implemented (mirrors data/fatigue_rules.csv):
  R001 - Minimum rest between shifts            (>= 11 hours)
  R002 - Maximum consecutive working days        (<= 6 days)
  R003 - Maximum weekly working hours             (<= employee.max_weekly_hours)
  R004 - Maximum single shift length              (<= 12 hours)
  R005 - Maximum consecutive night shifts         (<= 3 in a row)
  R006 - Shift overlap / double-booking           (0 overlaps allowed)
  R007 - Quick turnaround / "clopening"           (close -> open < 11 hrs rest)
"""
from datetime import datetime, timedelta, date as date_cls

from src.database import get_connection

# ---- Default thresholds (overridden by fatigue_rules table when available) ----
DEFAULT_RULES = {
    "min_rest_hours": 11,
    "max_consecutive_days": 6,
    "max_weekly_hours": 48,
    "max_single_shift_hours": 12,
    "max_consecutive_nights": 3,
}

RISK_WEIGHTS = {
    "min_rest_hours": 25,
    "max_consecutive_days": 20,
    "max_weekly_hours": 25,
    "max_single_shift_hours": 10,
    "max_consecutive_nights": 20,
    "overlap": 40,
    "clopening": 15,
}


def _parse_dt(shift_date: str, time_str: str, crosses_midnight_offset_days: int = 0):
    d = datetime.strptime(shift_date, "%Y-%m-%d") + timedelta(days=crosses_midnight_offset_days)
    t = datetime.strptime(time_str, "%H:%M").time()
    return datetime.combine(d.date(), t)


def shift_start_end(shift: dict):
    """Return (start_dt, end_dt) as real datetimes, correctly handling
    shifts that cross midnight (e.g. 22:00 -> 06:00)."""
    if shift.get("shift_type") == "Rest Day":
        start_dt = _parse_dt(shift["shift_date"], "00:00")
        return start_dt, start_dt
    start_dt = _parse_dt(shift["shift_date"], shift["start_time"])
    end_dt = _parse_dt(shift["shift_date"], shift["end_time"])
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def shift_duration_hours(shift: dict) -> float:
    start_dt, end_dt = shift_start_end(shift)
    return round((end_dt - start_dt).total_seconds() / 3600, 2)


def load_rules_from_db(conn=None) -> dict:
    """Pull threshold values out of fatigue_rules table if present,
    otherwise fall back to DEFAULT_RULES."""
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True
    try:
        rows = conn.execute("SELECT rule_id, threshold_value FROM fatigue_rules").fetchall()
        mapping = {r["rule_id"]: r["threshold_value"] for r in rows}
        rules = dict(DEFAULT_RULES)
        if "R001" in mapping: rules["min_rest_hours"] = mapping["R001"]
        if "R002" in mapping: rules["max_consecutive_days"] = mapping["R002"]
        if "R003" in mapping: rules["max_weekly_hours"] = mapping["R003"]
        if "R004" in mapping: rules["max_single_shift_hours"] = mapping["R004"]
        if "R005" in mapping: rules["max_consecutive_nights"] = mapping["R005"]
        return rules
    finally:
        if close_after:
            conn.close()


class FatigueEngine:
    """Stateless engine - call methods with an employee's shift list."""

    def __init__(self, conn=None, rules: dict = None, owner_email: str = 'demo'):
        self._owns_conn = conn is None
        self.conn = conn or get_connection()
        self.rules = rules or load_rules_from_db(self.conn)
        self.owner_email = owner_email

    def close(self):
        if self._owns_conn:
            self.conn.close()

    # ---------- data access ----------
    def get_employee(self, employee_id: str):
        row = self.conn.execute(
            "SELECT * FROM employees WHERE employee_id = %s AND owner_email = %s", (employee_id, self.owner_email)
        ).fetchone()
        return dict(row) if row else None

    def get_shifts_for_employee(self, employee_id: str, start_date: str = None, end_date: str = None):
        query = "SELECT * FROM shifts WHERE employee_id = %s AND owner_email = %s"
        params = [employee_id, self.owner_email]
        if start_date:
            query += " AND shift_date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND shift_date <= %s"
            params.append(end_date)
        query += " ORDER BY shift_date, start_time"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_subjective_fatigue(self, employee_id: str, start_date: str = None, end_date: str = None):
        query = "SELECT * FROM subjective_fatigue WHERE employee_id = %s AND owner_email = %s"
        params = [employee_id, self.owner_email]
        if start_date:
            query += " AND report_date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND report_date <= %s"
            params.append(end_date)
        query += " ORDER BY report_date"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ---------- individual rule checks ----------
    def detect_overlaps(self, shifts: list) -> list:
        """Return list of (shift_a, shift_b) pairs whose time ranges overlap."""
        working_shifts = [s for s in shifts if s.get("shift_type") != "Rest Day"]
        intervals = [(s, *shift_start_end(s)) for s in working_shifts]
        intervals.sort(key=lambda x: x[1])
        conflicts = []
        for i in range(len(intervals)):
            for j in range(i + 1, len(intervals)):
                a_shift, a_start, a_end = intervals[i]
                b_shift, b_start, b_end = intervals[j]
                if b_start >= a_end:
                    break  # sorted by start; no further overlap possible with a
                if a_shift["shift_id"] == b_shift["shift_id"]:
                    continue
                if b_start < a_end and a_start < b_end:
                    conflicts.append((a_shift, b_shift))
        return conflicts

    def rest_gaps(self, shifts: list) -> list:
        """Return list of dicts describing the rest gap between every pair
        of consecutive (non-overlapping) shifts, sorted chronologically."""
        working_shifts = [s for s in shifts if s.get("shift_type") != "Rest Day"]
        if len(working_shifts) < 2:
            return []
        timed = sorted(
            [(s, *shift_start_end(s)) for s in working_shifts],
            key=lambda x: x[1],
        )
        gaps = []
        for i in range(len(timed) - 1):
            cur_shift, _, cur_end = timed[i]
            next_shift, next_start, _ = timed[i + 1]
            rest_hours = round((next_start - cur_end).total_seconds() / 3600, 2)
            gaps.append({
                "from_shift": cur_shift["shift_id"],
                "to_shift": next_shift["shift_id"],
                "from_date": cur_shift["shift_date"],
                "to_date": next_shift["shift_date"],
                "rest_hours": rest_hours,
            })
        return gaps

    def consecutive_working_day_streaks(self, shifts: list) -> list:
        """Return list of streak lengths (in calendar days) of unbroken
        consecutive working days, based on distinct shift_date values."""
        working_shifts = [s for s in shifts if s.get("shift_type") != "Rest Day"]
        if not working_shifts:
            return []
        unique_dates = sorted({datetime.strptime(s["shift_date"], "%Y-%m-%d").date() for s in working_shifts})
        streaks = []
        streak = 1
        for i in range(1, len(unique_dates)):
            if (unique_dates[i] - unique_dates[i - 1]).days == 1:
                streak += 1
            else:
                streaks.append(streak)
                streak = 1
        streaks.append(streak)
        return streaks

    def consecutive_night_streaks(self, shifts: list) -> list:
        """Return list of streak lengths of consecutive calendar days that
        include a Night shift."""
        nights = sorted(
            {datetime.strptime(s["shift_date"], "%Y-%m-%d").date()
             for s in shifts if s.get("shift_type") == "Night"}
        )
        if not nights:
            return []
        streaks = []
        streak = 1
        for i in range(1, len(nights)):
            if (nights[i] - nights[i - 1]).days == 1:
                streak += 1
            else:
                streaks.append(streak)
                streak = 1
        streaks.append(streak)
        return streaks

    def weekly_hours(self, shifts: list) -> dict:
        """Return {week_start_iso: total_hours} using rolling Mon-Sun weeks
        based on each shift's start date."""
        totals = {}
        working_shifts = [s for s in shifts if s.get("shift_type") != "Rest Day"]
        for s in working_shifts:
            start_dt, _ = shift_start_end(s)
            week_start = start_dt.date() - timedelta(days=start_dt.weekday())
            key = week_start.isoformat()
            totals[key] = totals.get(key, 0) + shift_duration_hours(s)
        return {k: round(v, 2) for k, v in totals.items()}

    def detect_clopening(self, shifts: list) -> list:
        """Flag cases where a closing/late shift is followed by an
        opening/early shift with rest below the minimum threshold."""
        flagged = []
        for gap in self.rest_gaps(shifts):
            if gap["rest_hours"] < self.rules["min_rest_hours"]:
                flagged.append(gap)
        return flagged

    # ---------- composite analysis ----------
    def analyze_employee(self, employee_id: str, start_date: str = None, end_date: str = None) -> dict:
        """Full fatigue-risk analysis for one employee across an optional
        date window (defaults to all shifts on file)."""
        employee = self.get_employee(employee_id)
        if not employee:
            return {"error": f"Employee {employee_id} not found"}

        shifts = self.get_shifts_for_employee(employee_id, start_date, end_date)
        violations = []
        score = 0

        if not shifts:
            return {
                "employee_id": employee_id,
                "employee_name": employee["name"],
                "shift_count": 0,
                "fatigue_score": 0,
                "risk_level": "Low",
                "violations": [],
                "metrics": {},
            }

        max_weekly = employee.get("max_weekly_hours") or self.rules["max_weekly_hours"]

        # R006 - overlaps
        overlaps = self.detect_overlaps(shifts)
        for a, b in overlaps:
            violations.append({
                "rule_id": "R006", "rule_name": "Shift Overlap / Double-Booking",
                "severity": "Critical",
                "detail": f"This shift ({a['shift_date']} {a['start_time']}-{a['end_time']}) "
                          f"overlaps with another shift scheduled from {b['start_time']} to {b['end_time']} on {b['shift_date']}.",
            })
            score += RISK_WEIGHTS["overlap"]

        # R001 / R007 - rest gaps & clopening
        for gap in self.rest_gaps(shifts):
            if gap["rest_hours"] < self.rules["min_rest_hours"]:
                rule_id = "R001"
                rule_name = "Minimum Rest Between Shifts"
                severity = "High"
                detail = (f"Only {gap['rest_hours']}h rest between the shift "
                          f"on {gap['from_date']} and the next shift on {gap['to_date']}; "
                          f"minimum required is {self.rules['min_rest_hours']}h.")
                violations.append({"rule_id": rule_id, "rule_name": rule_name,
                                    "severity": severity, "detail": detail})
                score += RISK_WEIGHTS["min_rest_hours"]

        # R002 - consecutive working days
        max_streak = max(self.consecutive_working_day_streaks(shifts), default=0)
        if max_streak > self.rules["max_consecutive_days"]:
            violations.append({
                "rule_id": "R002", "rule_name": "Maximum Consecutive Working Days",
                "severity": "High",
                "detail": f"Employee has a run of {max_streak} consecutive working days "
                          f"(limit is {int(self.rules['max_consecutive_days'])}).",
            })
            score += RISK_WEIGHTS["max_consecutive_days"]

        # R003 - weekly hours
        weekly = self.weekly_hours(shifts)
        breached_weeks = {wk: hrs for wk, hrs in weekly.items() if hrs > max_weekly}
        for wk, hrs in breached_weeks.items():
            violations.append({
                "rule_id": "R003", "rule_name": "Maximum Weekly Working Hours",
                "severity": "Critical",
                "detail": f"Week of {wk}: scheduled {hrs}h, exceeding the {max_weekly}h limit.",
            })
            score += RISK_WEIGHTS["max_weekly_hours"]

        # R004 - single shift length
        working_shifts = [s for s in shifts if s.get("shift_type") != "Rest Day"]
        long_shifts = [s for s in working_shifts if shift_duration_hours(s) > self.rules["max_single_shift_hours"]]
        for s in long_shifts:
            violations.append({
                "rule_id": "R004", "rule_name": "Maximum Single Shift Length",
                "severity": "Medium",
                "detail": f"The shift on {s['shift_date']} is "
                          f"{shift_duration_hours(s)}h long (limit {self.rules['max_single_shift_hours']}h).",
            })
            score += RISK_WEIGHTS["max_single_shift_hours"]

        # R005 - consecutive night shifts
        max_night_streak = max(self.consecutive_night_streaks(shifts), default=0)
        if max_night_streak > self.rules["max_consecutive_nights"]:
            violations.append({
                "rule_id": "R005", "rule_name": "Maximum Consecutive Night Shifts",
                "severity": "Critical",
                "detail": f"Employee has {max_night_streak} consecutive night shifts "
                          f"(limit is {int(self.rules['max_consecutive_nights'])}).",
            })
            score += RISK_WEIGHTS["max_consecutive_nights"]

        # R008 - Subjective Fatigue Rating
        d = datetime.strptime(end_date or datetime.today().strftime('%Y-%m-%d'), "%Y-%m-%d").date()
        w_start = (d - timedelta(days=7)).isoformat()
        subjective = self.get_subjective_fatigue(employee_id, w_start, d.isoformat())
        recent_high = [r for r in subjective if r["fatigue_rating"] >= 5]
        for r in recent_high:
            violations.append({
                "rule_id": "R008", "rule_name": "High Subjective Fatigue",
                "severity": "Critical" if r["fatigue_rating"] >= 6 else "High",
                "detail": f"Employee reported fatigue level {r['fatigue_rating']}/7 on {r['report_date']}.",
            })
            score += 20 * (r["fatigue_rating"] - 4)
            
        score = min(score, 100)
        risk_level = self._score_to_level(score)

        return {
            "employee_id": employee_id,
            "employee_name": employee["name"],
            "department": employee.get("department"),
            "shift_count": len(shifts),
            "fatigue_score": score,
            "risk_level": risk_level,
            "violations": violations,
            "metrics": {
                "max_consecutive_working_days": max_streak,
                "max_consecutive_night_shifts": max_night_streak,
                "weekly_hours": weekly,
                "longest_single_shift_hours": max((shift_duration_hours(s) for s in shifts), default=0),
            },
        }

    @staticmethod
    def _score_to_level(score: int) -> str:
        if score >= 80:
            return "Critical"
        if score >= 60:
            return "High"
        if score >= 20:
            return "Moderate"
        return "Low"

    # ---------- conflict pre-check for a NEW shift before it's saved ----------
    def validate_new_shift(self, employee_id: str, shift_date: str, start_time: str,
                            end_time: str, shift_type: str = None) -> dict:
        """Dry-run check: what would happen to this employee's fatigue
        profile if this shift were added? Does NOT write to the database.
        Used by POST /api/shifts/validate before a manager commits an
        assignment."""
        employee = self.get_employee(employee_id)
        if not employee:
            return {"error": f"Employee {employee_id} not found"}

        candidate = {
            "shift_id": "CANDIDATE", "employee_id": employee_id,
            "shift_date": shift_date, "shift_type": shift_type or "Day",
            "start_time": start_time, "end_time": end_time,
        }

        # Look at a window around the candidate date for rest/overlap checks
        d = datetime.strptime(shift_date, "%Y-%m-%d").date()
        window_start = (d - timedelta(days=7)).isoformat()
        window_end = (d + timedelta(days=7)).isoformat()
        existing = self.get_shifts_for_employee(employee_id, window_start, window_end)

        combined = existing + [candidate]
        analysis = self._analyze_shift_list(employee, combined)

        # Only report violations that involve the candidate shift specifically,
        # plus a summary of overall risk after adding it.
        relevant = [v for v in analysis["violations"] if "CANDIDATE" in v["detail"]]
        return {
            "employee_id": employee_id,
            "employee_name": employee["name"],
            "candidate_shift": {"shift_date": shift_date, "start_time": start_time, "end_time": end_time},
            "would_introduce_violations": relevant,
            "projected_fatigue_score": analysis["fatigue_score"],
            "projected_risk_level": analysis["risk_level"],
            "safe_to_assign": len(relevant) == 0,
        }

    def _analyze_shift_list(self, employee: dict, shifts: list) -> dict:
        """Same logic as analyze_employee but operating on an explicit
        shift list (used internally by validate_new_shift)."""
        violations = []
        score = 0
        max_weekly = employee.get("max_weekly_hours") or self.rules["max_weekly_hours"]

        overlaps = self.detect_overlaps(shifts)
        for a, b in overlaps:
            violations.append({
                "rule_id": "R006", "rule_name": "Shift Overlap / Double-Booking", "severity": "Critical",
                "detail": f"The shift overlaps with another shift scheduled from {b['start_time']} to {b['end_time']} on {b['shift_date']}.",
            })
            score += RISK_WEIGHTS["overlap"]

        for gap in self.rest_gaps(shifts):
            if gap["rest_hours"] < self.rules["min_rest_hours"]:
                violations.append({
                    "rule_id": "R001", "rule_name": "Minimum Rest Between Shifts", "severity": "High",
                    "detail": f"Only {gap['rest_hours']}h rest between the shift on {gap['from_date']} and the next shift on {gap['to_date']}.",
                })
                score += RISK_WEIGHTS["min_rest_hours"]

        max_streak = max(self.consecutive_working_day_streaks(shifts), default=0)
        if max_streak > self.rules["max_consecutive_days"]:
            violations.append({
                "rule_id": "R002", "rule_name": "Maximum Consecutive Working Days", "severity": "High",
                "detail": f"Run of {max_streak} consecutive working days (CANDIDATE shift included).",
            })
            score += RISK_WEIGHTS["max_consecutive_days"]

        weekly = self.weekly_hours(shifts)
        for wk, hrs in weekly.items():
            if hrs > max_weekly:
                violations.append({
                    "rule_id": "R003", "rule_name": "Maximum Weekly Working Hours", "severity": "Critical",
                    "detail": f"Week of {wk} totals {hrs}h, exceeding {max_weekly}h (CANDIDATE shift included).",
                })
                score += RISK_WEIGHTS["max_weekly_hours"]

        max_night_streak = max(self.consecutive_night_streaks(shifts), default=0)
        if max_night_streak > self.rules["max_consecutive_nights"]:
            violations.append({
                "rule_id": "R005", "rule_name": "Maximum Consecutive Night Shifts", "severity": "Critical",
                "detail": f"{max_night_streak} consecutive night shifts (CANDIDATE shift included).",
            })
            score += RISK_WEIGHTS["max_consecutive_nights"]

        score = min(score, 100)
        return {"violations": violations, "fatigue_score": score, "risk_level": self._score_to_level(score)}

    # ---------- safer-alternative suggestion (rule-based) ----------
    def suggest_safer_alternatives(self, employee_id: str, shift_date: str, start_time: str,
                                    end_time: str, shift_type: str = None, max_options: int = 3) -> list:
        """Try a small set of rule-based alternatives (shift the start time
        later, move the date forward, shorten the shift) and return the
        ones that come back clean. This is intentionally simple/greedy -
        good enough for a capstone-level suggestion engine."""
        candidates = []
        base_date = datetime.strptime(shift_date, "%Y-%m-%d").date()

        option_specs = [
            ("Push start time back by 2 hours", 0, 2, 0),
            ("Move shift to the next day", 1, 0, 0),
            ("Move shift forward by 2 days", 2, 0, 0),
            ("Shorten shift by 2 hours (start later)", 0, 2, -2),
        ]

        for label, day_offset, hour_offset, duration_adjust_hours in option_specs:
            try:
                new_date = base_date + timedelta(days=day_offset)
                start_dt = datetime.strptime(start_time, "%H:%M")
                end_dt = datetime.strptime(end_time, "%H:%M")
                duration = (end_dt - start_dt) if end_dt > start_dt else (end_dt - start_dt + timedelta(days=1))
                new_start_dt = start_dt + timedelta(hours=hour_offset)
                new_duration = duration + timedelta(hours=duration_adjust_hours)
                if new_duration.total_seconds() <= 0:
                    continue
                new_end_dt = new_start_dt + new_duration

                new_start_str = new_start_dt.strftime("%H:%M")
                new_end_str = new_end_dt.strftime("%H:%M")

                check = self.validate_new_shift(
                    employee_id, new_date.isoformat(), new_start_str, new_end_str, shift_type
                )
                if check.get("safe_to_assign"):
                    candidates.append({
                        "option": label,
                        "shift_date": new_date.isoformat(),
                        "start_time": new_start_str,
                        "end_time": new_end_str,
                        "projected_risk_level": check["projected_risk_level"],
                    })
                if len(candidates) >= max_options:
                    break
            except Exception:
                continue

        return candidates

    # ---------- workforce-wide dashboard ----------
    def dashboard_risk_summary(self) -> dict:
        employees = self.conn.execute("SELECT employee_id, name FROM employees WHERE owner_email = %s", (self.owner_email,)).fetchall()
        summary = {"Low": 0, "Medium": 0, "High": 0, "Critical": 0}
        per_employee = []
        for row in employees:
            emp_id = row["employee_id"]
            analysis = self.analyze_employee(emp_id)
            level = analysis.get("risk_level", "Low")
            summary[level] = summary.get(level, 0) + 1
            per_employee.append({
                "employee_id": emp_id,
                "employee_name": analysis.get("employee_name"),
                "fatigue_score": analysis.get("fatigue_score", 0),
                "risk_level": level,
                "violation_count": len(analysis.get("violations", [])),
            })
        per_employee.sort(key=lambda x: x["fatigue_score"], reverse=True)
        return {
            "total_employees": len(employees),
            "risk_level_counts": summary,
            "employee_risks": per_employee,
            "top_at_risk_employees": per_employee[:10],
        }

    def heatmap_data(self, start_date: str, end_date: str) -> dict:
        employees = self.conn.execute("SELECT employee_id, name FROM employees WHERE owner_email = %s", (self.owner_email,)).fetchall()
        
        d1 = datetime.strptime(start_date, "%Y-%m-%d").date()
        d2 = datetime.strptime(end_date, "%Y-%m-%d").date()
        dates = [(d1 + timedelta(days=i)).isoformat() for i in range((d2 - d1).days + 1)]
        
        # Limit to 31 days to prevent abuse/long queries
        if len(dates) > 31:
            dates = dates[:31]
            
        result = []
        for emp in employees:
            emp_id = emp["employee_id"]
            daily_risks = {}
            # Base start date a bit earlier to catch streaks
            w_start = (d1 - timedelta(days=14)).isoformat()
            
            for d in dates:
                # Cumulative risk as of day 'd'
                analysis = self.analyze_employee(emp_id, start_date=w_start, end_date=d)
                # But we only want to highlight days where there IS a shift, or the risk is inherently high.
                # Let's see if the employee actually has a shift on day d.
                shifts_on_day = [s for s in self.get_shifts_for_employee(emp_id, d, d) if s.get("shift_type") != "Rest Day"]
                
                score = analysis.get("fatigue_score", 0)
                level = analysis.get("risk_level", "Low")
                
                # If no shift on this day, risk is effectively 'rest' visually unless they are in a critical cumulative state,
                # but typically a heatmap highlights the shifts. Let's just use the score.
                
                daily_risks[d] = {
                    "score": score,
                    "level": level,
                    "has_shift": len(shifts_on_day) > 0
                }
            result.append({
                "employee_id": emp_id,
                "employee_name": emp["name"],
                "daily_risks": daily_risks
            })
        return {"dates": dates, "heatmap": result}

    # ---------- auto-generation ----------
    def generate_draft_schedule(self, open_shifts: list) -> dict:
        """
        Given a list of open shifts, greedily assign them to available employees
        to minimize total fatigue risk.
        Returns the draft assignments and unassigned shifts.
        """
        employees = self.conn.execute("SELECT * FROM employees WHERE owner_email = %s", (self.owner_email,)).fetchall()
        employees = [dict(e) for e in employees]
        
        # Pre-fetch availability
        avail_rows = self.conn.execute("SELECT * FROM availability WHERE available = 'N' AND owner_email = %s", (self.owner_email,)).fetchall()
        unavailable_map = {}
        for r in avail_rows:
            unavailable_map.setdefault(r["employee_id"], set()).add(r["date"])
            
        # We need to maintain a running list of shifts for each employee
        # to simulate the impact of assignments chronologically.
        draft_shifts = []
        unassigned = []
        
        # Sort open shifts chronologically
        open_shifts.sort(key=lambda x: (x["shift_date"], x["start_time"]))
        
        # Dictionary to cache existing shifts so we don't hammer the DB
        cached_existing = {}
        
        for shift in open_shifts:
            best_employee = None
            best_score = float('inf')
            
            # Temporary candidate shift for testing
            candidate_base = {
                "shift_id": f"DRAFT_{len(draft_shifts)}",
                "shift_date": shift["shift_date"],
                "shift_type": shift.get("shift_type", "Day"),
                "start_time": shift["start_time"],
                "end_time": shift["end_time"],
                "location": shift.get("location"),
                "department": shift.get("department")
            }
            
            for emp in employees:
                emp_id = emp["employee_id"]
                # Check availability
                if shift["shift_date"] in unavailable_map.get(emp_id, set()):
                    continue
                
                # Get existing + previously drafted shifts for this employee
                if emp_id not in cached_existing:
                    d = datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
                    w_start = (d - timedelta(days=14)).isoformat()
                    w_end = (d + timedelta(days=14)).isoformat()
                    cached_existing[emp_id] = self.get_shifts_for_employee(emp_id, w_start, w_end)
                    
                emp_drafts = [s for s in draft_shifts if s["employee_id"] == emp_id]
                combined = cached_existing[emp_id] + emp_drafts + [{**candidate_base, "employee_id": emp_id}]
                
                analysis = self._analyze_shift_list(emp, combined)
                
                # Check if it introduces violations involving the DRAFT shift
                relevant = [v for v in analysis["violations"] if "DRAFT" in v["detail"]]
                if not relevant:
                    # Safe to assign
                    if analysis["fatigue_score"] < best_score:
                        best_score = analysis["fatigue_score"]
                        best_employee = emp_id
                        
            if best_employee:
                assigned_shift = {**candidate_base, "employee_id": best_employee}
                draft_shifts.append(assigned_shift)
            else:
                unassigned.append(shift)
                
        # Format the assignments with employee names
        assignments = []
        emp_name_map = {e["employee_id"]: e["name"] for e in employees}
        for s in draft_shifts:
            assignments.append({
                **s,
                "employee_name": emp_name_map.get(s["employee_id"], "Unknown")
            })
            
        return {
            "assigned_shifts": assignments,
            "unassigned_shifts": unassigned
        }
