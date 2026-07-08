import unittest
import json
import os
from app.app import app
from src.database import init_db, seed_from_csv

class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(reset=True)
        seed_from_csv()
        app.testing = True
        cls.client = app.test_client()

    def test_health_endpoint(self):
        res = self.client.get('/api/health')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "ok")

    def test_get_employees(self):
        res = self.client.get('/api/employees')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(len(data) > 0)

if __name__ == "__main__":
    unittest.main()
