import unittest
import os
import tempfile
import json
from fastapi.testclient import TestClient

import journal_db as db
from journal_backend import app

class TestJournalApp(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Ensure db initialized
        db.init_db()

    def test_get_stats_endpoint(self):
        res = self.client.get("/api/stats")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("total_trades", data)
        self.assertIn("win_rate", data)
        self.assertIn("total_realized_r", data)
        self.assertIn("equity_curve", data)

    def test_get_trades_endpoint(self):
        res = self.client.get("/api/trades")
        self.assertEqual(res.status_code, 200)
        trades = res.json()
        self.assertIsInstance(trades, list)
        self.assertGreater(len(trades), 0)

    def test_trade_crud_lifecycle(self):
        # Create
        payload = {
            "trade_date": "2026-08-14",
            "ny_time": "10:30 AM",
            "symbol": "EURUSD",
            "action": "BUY",
            "setup_type": "OR Breakout (Candle 2 FVG)",
            "htf_bias": "BULLISH",
            "entry_price": 1.1520,
            "stop_loss": 1.1500,
            "take_profit": 1.1560,
            "rr_ratio": 2.0,
            "outcome": "WIN",
            "realized_r": 2.0,
            "realized_pnl": 400.0,
            "notes": "Test trade lifecycle"
        }
        res = self.client.post("/api/trades", json=payload)
        self.assertEqual(res.status_code, 200)
        trade_id = res.json()["id"]

        # Read
        res = self.client.get(f"/api/trades/{trade_id}")
        self.assertEqual(res.status_code, 200)
        trade = res.json()
        self.assertEqual(trade["symbol"], "EURUSD")
        self.assertEqual(trade["outcome"], "WIN")

        # Update
        update_payload = {"notes": "Updated note during test", "outcome": "BREAK-EVEN", "realized_r": 0.0}
        res = self.client.put(f"/api/trades/{trade_id}", json=update_payload)
        self.assertEqual(res.status_code, 200)

        res = self.client.get(f"/api/trades/{trade_id}")
        self.assertEqual(res.json()["notes"], "Updated note during test")
        self.assertEqual(res.json()["outcome"], "BREAK-EVEN")

        # Delete
        res = self.client.delete(f"/api/trades/{trade_id}")
        self.assertEqual(res.status_code, 200)

        res = self.client.get(f"/api/trades/{trade_id}")
        self.assertEqual(res.status_code, 404)

    def test_export_csv(self):
        res = self.client.get("/api/export/csv")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn("trade_date", res.text)

    def test_ui_serves_html(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("9:30 NY ICT Strategy Journal", res.text)

if __name__ == "__main__":
    unittest.main()
