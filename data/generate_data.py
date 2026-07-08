"""
Generates starter CSV data for the Workforce Shift Planning and Fatigue
Risk Management System: employees.csv, shifts.csv, availability.csv,
fatigue_rules.csv.

Run: python3 generate_data.py
(Deterministic - uses a fixed random seed so the data is reproducible.)
"""
import csv
import random
from datetime import date, timedelta, time, datetime

random.seed(42)

OUT_DIR = "."

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Ishaan", "Kabir", "Ananya", "Diya", "Priya",
    "Riya", "Saanvi", "Rohan", "Karan", "Neha", "Pooja", "Aman", "Vikram",
    "Sanya", "Tara", "Arjun", "Meera", "Sara", "Zoya", "Dev", "Ira",
    "Nikhil", "Simran", "Yash", "Kavya", "Rahul", "Anika", "Manish", "Tanvi",
    "Suresh", "Lakshmi", "Rajesh", "Divya", "Amit", "Kiran", "Sunita", "Gaurav",
]
LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Rao", "Iyer", "Singh", "Patel", "Kumar",
    "Reddy", "Nair", "Mehta", "Joshi", "Chauhan", "Das", "Bose", "Pillai",
    "Kapoor", "Malhotra", "Bhat", "Menon",
]

ROLES = ["Nurse", "Technician", "Machine Operator", "Security Guard",
         "Warehouse Associate", "Customer Support Agent", "Supervisor"]
DEPARTMENTS = ["ICU", "General Ward", "Production Line A", "Production Line B",
               "Logistics", "Support Desk", "Facility Security"]
EMPLOYMENT_TYPES = ["Full-Time", "Part-Time", "Contract"]

SHIFT_TYPES = {
    "Morning": (time(6, 0), time(14, 0)),
    "Day": (time(9, 0), time(17, 0)),
    "Evening": (time(14, 0), time(22, 0)),
    "Night": (time(22, 0), time(6, 0)),  # crosses midnight
}

LOCATIONS = ["Site A", "Site B", "Main Plant", "Warehouse 1", "HQ"]

N_EMPLOYEES = 40
SCHEDULE_START = date(2026, 6, 1)
SCHEDULE_DAYS = 28  # 4 weeks of shifts


def gen_employees():
    rows = []
    used_names = set()
    for i in range(1, N_EMPLOYEES + 1):
        while True:
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        role = random.choice(ROLES)
        dept = random.choice(DEPARTMENTS)
        emp_type = random.choices(EMPLOYMENT_TYPES, weights=[0.7, 0.2, 0.1])[0]
        max_weekly_hours = 48 if emp_type != "Part-Time" else 24
        contracted_hours = max_weekly_hours if emp_type == "Full-Time" else random.choice([20, 24, 30])
        experience_years = round(random.uniform(0.5, 15), 1)
        rows.append({
            "employee_id": f"E{i:03d}",
            "name": name,
            "role": role,
            "department": dept,
            "employment_type": emp_type,
            "max_weekly_hours": max_weekly_hours,
            "contracted_hours": contracted_hours,
            "experience_years": experience_years,
            "min_rest_hours_required": 11,
        })
    return rows


def gen_shifts(employees):
    """Assign each employee a realistic, MOSTLY COMPLIANT shift pattern
    over SCHEDULE_DAYS (a standard 5-on/2-off rotation per employee, with
    light randomness). A small, deliberately chosen subset of employees
    get realistic rule violations injected on top (back-to-back shifts,
    too many consecutive nights, weekly-hour overload) so the fatigue
    engine has real, demonstrable risk cases to detect - while the rest
    of the workforce stays low/medium risk, like a real organization."""
    rows = []
    shift_id = 1

    # Indices (0-based) of employees who will be deliberately made risky.
    # Each is assigned a different kind of violation so the demo covers
    # every rule type.
    risky_idx = {2, 5, 9, 14, 19, 24}  # 6 employees out of 40

    for idx, emp in enumerate(employees):
        emp_id = emp["employee_id"]
        preferred = random.choice(list(SHIFT_TYPES.keys()))
        is_risky = idx in risky_idx

        # Standard rotation: work 5 days, rest 2, repeating, starting on a
        # random offset so not everyone is on the exact same pattern.
        offset = random.randint(0, 6)
        for d in range(SCHEDULE_DAYS):
            current_day = SCHEDULE_START + timedelta(days=d)
            cycle_pos = (d + offset) % 7
            works_today = cycle_pos < 5  # 5 on, 2 off

            # small realistic variation: ~6% chance of an unplanned swap day
            if works_today and random.random() < 0.06 and not is_risky:
                continue

            if not works_today:
                continue

            # Keep each employee on a consistent shift type so normal
            # rotations don't accidentally create unrealistic back-to-back
            # rest violations (e.g. Night ending 06:00 -> Morning starting
            # 06:00). Deliberate violations are injected separately below.
            shift_type = preferred
            start_t, end_t = SHIFT_TYPES[shift_type]

            rows.append({
                "shift_id": f"S{shift_id:04d}",
                "employee_id": emp_id,
                "shift_date": current_day.isoformat(),
                "shift_type": shift_type,
                "start_time": start_t.strftime("%H:%M"),
                "end_time": end_t.strftime("%H:%M"),
                "location": random.choice(LOCATIONS),
                "department": emp["department"],
            })
            shift_id += 1

        # Inject ONE clear, distinct violation pattern per risky employee
        if is_risky:
            pattern = list(risky_idx).index(idx) % 4
            stretch_start = SCHEDULE_START + timedelta(days=12)

            if pattern == 0:
                # 5 consecutive night shifts (exceeds R005 limit of 3)
                for k in range(5):
                    d2 = stretch_start + timedelta(days=k)
                    rows.append({
                        "shift_id": f"S{shift_id:04d}", "employee_id": emp_id,
                        "shift_date": d2.isoformat(), "shift_type": "Night",
                        "start_time": "22:00", "end_time": "06:00",
                        "location": random.choice(LOCATIONS), "department": emp["department"],
                    })
                    shift_id += 1

            elif pattern == 1:
                # 9 consecutive working days (exceeds R002 limit of 6)
                for k in range(9):
                    d2 = stretch_start + timedelta(days=k)
                    rows.append({
                        "shift_id": f"S{shift_id:04d}", "employee_id": emp_id,
                        "shift_date": d2.isoformat(), "shift_type": "Day",
                        "start_time": "09:00", "end_time": "17:00",
                        "location": random.choice(LOCATIONS), "department": emp["department"],
                    })
                    shift_id += 1

            elif pattern == 2:
                # "Clopening": close shift then early open next day, 3 times in a row
                for k in range(3):
                    d2 = stretch_start + timedelta(days=k * 2)
                    d3 = d2 + timedelta(days=1)
                    rows.append({
                        "shift_id": f"S{shift_id:04d}", "employee_id": emp_id,
                        "shift_date": d2.isoformat(), "shift_type": "Evening",
                        "start_time": "16:00", "end_time": "23:30",
                        "location": random.choice(LOCATIONS), "department": emp["department"],
                    })
                    shift_id += 1
                    rows.append({
                        "shift_id": f"S{shift_id:04d}", "employee_id": emp_id,
                        "shift_date": d3.isoformat(), "shift_type": "Morning",
                        "start_time": "06:00", "end_time": "14:00",
                        "location": random.choice(LOCATIONS), "department": emp["department"],
                    })
                    shift_id += 1

            else:
                # Weekly hour overload: 6 long (12h) shifts in one week
                for k in range(6):
                    d2 = stretch_start + timedelta(days=k)
                    rows.append({
                        "shift_id": f"S{shift_id:04d}", "employee_id": emp_id,
                        "shift_date": d2.isoformat(), "shift_type": "Day",
                        "start_time": "07:00", "end_time": "19:00",
                        "location": random.choice(LOCATIONS), "department": emp["department"],
                    })
                    shift_id += 1

    # Inject a couple of explicit double-booking conflicts for testing
    if len(employees) >= 2:
        conflict_emp = employees[1]["employee_id"]
        conflict_day = (SCHEDULE_START + timedelta(days=3)).isoformat()
        rows.append({
            "shift_id": f"S{shift_id:04d}", "employee_id": conflict_emp,
            "shift_date": conflict_day, "shift_type": "Evening",
            "start_time": "14:00", "end_time": "22:00",
            "location": "Site A", "department": employees[1]["department"],
        })
        shift_id += 1
        rows.append({
            "shift_id": f"S{shift_id:04d}", "employee_id": conflict_emp,
            "shift_date": conflict_day, "shift_type": "Night",
            "start_time": "20:00", "end_time": "23:59",
            "location": "Site B", "department": employees[1]["department"],
        })
        shift_id += 1

    return rows


def gen_availability(employees):
    rows = []
    for emp in employees:
        for d in range(SCHEDULE_DAYS):
            current_day = SCHEDULE_START + timedelta(days=d)
            # Most days available; occasionally mark unavailable (leave/preference)
            if random.random() < 0.12:
                reason = random.choice(["Personal Leave", "Sick Leave", "Preferred Off", "Training"])
                available = "N"
            else:
                reason = ""
                available = "Y"
            rows.append({
                "employee_id": emp["employee_id"],
                "date": current_day.isoformat(),
                "available": available,
                "reason": reason,
            })
    return rows


def gen_fatigue_rules():
    return [
        {
            "rule_id": "R001",
            "rule_name": "Minimum Rest Between Shifts",
            "description": "Employee must have at least 11 hours of rest between the end of one shift and the start of the next.",
            "threshold_value": 11,
            "unit": "hours",
            "severity": "High",
        },
        {
            "rule_id": "R002",
            "rule_name": "Maximum Consecutive Working Days",
            "description": "Employee should not work more than 6 consecutive days without a rest day.",
            "threshold_value": 6,
            "unit": "days",
            "severity": "High",
        },
        {
            "rule_id": "R003",
            "rule_name": "Maximum Weekly Working Hours",
            "description": "Total scheduled hours in any rolling 7-day window should not exceed the employee's max weekly hours (default 48).",
            "threshold_value": 48,
            "unit": "hours/week",
            "severity": "Critical",
        },
        {
            "rule_id": "R004",
            "rule_name": "Maximum Single Shift Length",
            "description": "A single shift should not exceed 12 hours.",
            "threshold_value": 12,
            "unit": "hours",
            "severity": "Medium",
        },
        {
            "rule_id": "R005",
            "rule_name": "Maximum Consecutive Night Shifts",
            "description": "Employee should not work more than 3 consecutive night shifts in a row.",
            "threshold_value": 3,
            "unit": "shifts",
            "severity": "Critical",
        },
        {
            "rule_id": "R006",
            "rule_name": "Shift Overlap / Double-Booking",
            "description": "An employee cannot be scheduled for two overlapping shifts on the same day.",
            "threshold_value": 0,
            "unit": "overlap_count",
            "severity": "Critical",
        },
        {
            "rule_id": "R007",
            "rule_name": "Quick Turnaround (Clopening)",
            "description": "Closing shift followed by an opening shift the next day with less than 11 hours rest is flagged as a clopening risk.",
            "threshold_value": 11,
            "unit": "hours",
            "severity": "Medium",
        },
    ]


def write_csv(filename, rows, fieldnames):
    with open(f"{OUT_DIR}/{filename}", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {filename}")


if __name__ == "__main__":
    employees = gen_employees()
    shifts = gen_shifts(employees)
    availability = gen_availability(employees)
    fatigue_rules = gen_fatigue_rules()

    write_csv("employees.csv", employees, list(employees[0].keys()))
    write_csv("shifts.csv", shifts, list(shifts[0].keys()))
    write_csv("availability.csv", availability, list(availability[0].keys()))
    write_csv("fatigue_rules.csv", fatigue_rules, list(fatigue_rules[0].keys()))

    print("\nDone. Files generated in:", OUT_DIR)
