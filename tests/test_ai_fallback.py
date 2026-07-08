import unittest
from src import ai_service
import os

class TestAIFallback(unittest.TestCase):
    def test_fallback_explanation_no_violations(self):
        analysis = {"employee_name": "Test Emp", "risk_level": "Low", "violations": []}
        res = ai_service._fallback_explanation(analysis)
        self.assertEqual(res["source"], "fallback_template")
        self.assertIn("does not breach any fatigue-risk rules", res["explanation"])

    def test_fallback_explanation_with_violations(self):
        analysis = {
            "employee_name": "Test Emp",
            "risk_level": "High",
            "violations": [
                {"rule_id": "R001", "rule_name": "Rest", "severity": "High", "detail": "Test detail"}
            ]
        }
        res = ai_service._fallback_explanation(analysis)
        self.assertEqual(res["source"], "fallback_template")
        self.assertIn("Test detail", res["explanation"])

    def test_ai_fails_gracefully(self):
        # Force API key to be invalid or just let _call_anthropic fail
        original_key = ai_service.ANTHROPIC_API_KEY
        try:
            ai_service.ANTHROPIC_API_KEY = "invalid_key"
            analysis = {"employee_name": "Test Emp", "risk_level": "Low", "violations": []}
            res = ai_service.explain_fatigue_risk(analysis)
            self.assertEqual(res["source"], "fallback_template")
        finally:
            ai_service.ANTHROPIC_API_KEY = original_key

if __name__ == "__main__":
    unittest.main()
