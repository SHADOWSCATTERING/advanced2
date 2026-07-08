import unittest
from src.fatigue_engine import FatigueEngine

class TestFatigueEngine(unittest.TestCase):
    def setUp(self):
        self.engine = FatigueEngine(None)
        # Mock get_subjective_fatigue for isolated testing
        self.engine.get_subjective_fatigue = lambda e, s, d: []

    def test_overlap_detection(self):
        shifts = [
            {"shift_id": "s1", "shift_date": "2026-06-01", "start_time": "08:00", "end_time": "16:00"},
            {"shift_id": "s2", "shift_date": "2026-06-01", "start_time": "14:00", "end_time": "22:00"}
        ]
        overlaps = self.engine.detect_overlaps(shifts)
        self.assertEqual(len(overlaps), 1)

    def test_insufficient_rest(self):
        shifts = [
            {"shift_id": "s1", "shift_date": "2026-06-01", "start_time": "16:00", "end_time": "00:00"},
            {"shift_id": "s2", "shift_date": "2026-06-02", "start_time": "08:00", "end_time": "16:00"}
        ]
        employee = {"min_rest_hours_required": 11, "name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        violations = res["violations"]
        
        self.assertTrue(any(v["rule_id"] == "R001" for v in violations))

    def test_fatigue_score_low_1(self):
        # Score 0 (Low risk)
        shifts = [{"shift_id": "s1", "shift_date": "2026-06-01", "start_time": "08:00", "end_time": "16:00"}]
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "Low")
        self.assertEqual(res["fatigue_score"], 0)

    def test_fatigue_score_low_2(self):
        # Score 0 (Low risk)
        shifts = [
            {"shift_id": "s1", "shift_date": "2026-06-01", "start_time": "08:00", "end_time": "16:00"},
            {"shift_id": "s2", "shift_date": "2026-06-02", "start_time": "08:00", "end_time": "16:00"}
        ]
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "Low")
        self.assertEqual(res["fatigue_score"], 0)

    def test_fatigue_score_moderate_1(self):
        # Score 20 (Moderate risk due to max_consecutive_days)
        shifts = [
            {"shift_id": f"s{i}", "shift_date": f"2026-06-0{i}", "start_time": "08:00", "end_time": "10:00"}
            for i in range(1, 8)
        ]
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "Moderate")
        self.assertEqual(res["fatigue_score"], 20)

    def test_fatigue_score_moderate_2(self):
        # Score 25 (Moderate risk due to min_rest_hours)
        shifts = [
            {"shift_id": "s1", "shift_date": "2026-06-01", "start_time": "16:00", "end_time": "00:00"},
            {"shift_id": "s2", "shift_date": "2026-06-02", "start_time": "08:00", "end_time": "16:00"}
        ]
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "Moderate")
        self.assertEqual(res["fatigue_score"], 25)

    def test_fatigue_score_moderate_3(self):
        # Score 25 (Moderate risk due to max_weekly_hours)
        shifts = [
            {"shift_id": f"s{i}", "shift_date": f"2026-06-0{i}", "start_time": "08:00", "end_time": "18:00"}
            for i in range(1, 6)
        ]
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "Moderate")
        self.assertEqual(res["fatigue_score"], 25)

    def test_fatigue_score_high_1(self):
        # Score 70 (High risk: min_rest_hours 25 + max_weekly_hours 25 + max_consecutive_days 20)
        shifts = [
            {"shift_id": f"s{i}", "shift_date": f"2026-06-0{i}", "start_time": "08:00", "end_time": "16:00"}
            for i in range(1, 8)
        ]
        shifts[0] = {"shift_id": "s1", "shift_date": "2026-06-01", "start_time": "16:00", "end_time": "00:00"}
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "High")
        self.assertEqual(res["fatigue_score"], 70)

    def test_fatigue_score_high_2(self):
        # Score 65 (High risk: overlap 40 + min_rest 25)
        shifts = [
            {"shift_id": "s1", "shift_date": "2026-06-01", "start_time": "08:00", "end_time": "16:00"},
            {"shift_id": "s2", "shift_date": "2026-06-01", "start_time": "10:00", "end_time": "12:00"}
        ]
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "High")
        self.assertEqual(res["fatigue_score"], 65)

    def test_fatigue_score_critical_1(self):
        # Score 85 (Critical risk: overlap 40 + min_rest 25 + max_consecutive_days 20)
        shifts = [
            {"shift_id": f"s{i}", "shift_date": f"2026-06-0{i}", "start_time": "08:00", "end_time": "10:00"}
            for i in range(1, 8)
        ]
        shifts.append({"shift_id": "s_overlap", "shift_date": "2026-06-07", "start_time": "09:00", "end_time": "11:00"})
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "Critical")
        self.assertEqual(res["fatigue_score"], 85)

    def test_fatigue_score_critical_2(self):
        # Score 100 (Critical risk: overlap 40 + min_rest 25 + max_consecutive_days 20 + max_weekly_hours 25)
        shifts = [
            {"shift_id": f"s{i}", "shift_date": f"2026-06-0{i}", "start_time": "08:00", "end_time": "16:00"}
            for i in range(1, 8)
        ]
        shifts.append({"shift_id": "s_overlap", "shift_date": "2026-06-07", "start_time": "10:00", "end_time": "12:00"})
        employee = {"name": "Bob", "max_weekly_hours": 48}
        res = self.engine._analyze_shift_list(employee, shifts)
        self.assertEqual(res["risk_level"], "Critical")
        self.assertEqual(res["fatigue_score"], 100)

if __name__ == "__main__":
    unittest.main()
