"""Tests for trade_journal.

Runs with the stdlib test runner:  python -m unittest discover tests
(or pytest, if you prefer). The sample reports are regenerated if missing.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import trade_journal as tj  # noqa: E402


def ensure_samples():
    """Generate the sample reports if they are not on disk yet."""
    sys.path.insert(0, str(HERE))
    import make_sample_report as gen
    if not (HERE / "sample_report.html").exists():
        (HERE / "sample_report.html").write_text(gen.make_positions_report(), encoding="utf-8")
    if not (HERE / "sample_report_deals.html").exists():
        (HERE / "sample_report_deals.html").write_text(gen.make_deals_report(), encoding="utf-8")


class TestParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_samples()
        cls.trades = tj.parse_report(HERE / "sample_report.html")
        cls.deal_trades = tj.parse_report(HERE / "sample_report_deals.html")

    def test_positions_report_parses_all_trades(self):
        # the generator writes 40 position rows, none of them weekends
        self.assertEqual(len(self.trades), 40)

    def test_trade_fields(self):
        t = self.trades[0]
        self.assertEqual(t.symbol, "XAUUSD")
        self.assertIn(t.side, ("buy", "sell"))
        self.assertGreater(t.volume, 0)
        self.assertGreater(t.entry, 0)
        self.assertGreater(t.sl, 0)
        self.assertGreater(t.tp, 0)
        self.assertIsInstance(t.profit, float)

    def test_balance_row_skipped(self):
        # the deposit row must not show up as a trade
        self.assertTrue(all(t.symbol == "XAUUSD" for t in self.trades))

    def test_trades_sorted_by_close_time(self):
        times = [t.close_time for t in self.trades]
        self.assertEqual(times, sorted(times))

    def test_deals_report_keeps_only_closing_deals(self):
        # one "out" deal per position, the "in" rows are skipped
        self.assertEqual(len(self.deal_trades), 25)
        for t in self.deal_trades:
            self.assertNotEqual(t.profit, 0.0)

    def test_date_parsing_formats(self):
        self.assertEqual(tj._to_datetime("2024.01.15 10:30:00"),
                         datetime(2024, 1, 15, 10, 30))
        self.assertEqual(tj._to_datetime("2024-01-15 10:30:00"),
                         datetime(2024, 1, 15, 10, 30))
        self.assertIsNone(tj._to_datetime("not a date"))

    def test_number_parsing(self):
        self.assertEqual(tj._to_float("1 234.56"), 1234.56)
        self.assertEqual(tj._to_float("-1,234.56"), -1234.56)
        self.assertIsNone(tj._to_float("balance"))


class TestStats(unittest.TestCase):
    def _trade(self, profit, hour=10, weekday=1):
        base = datetime(2024, 1, 8 + weekday, hour, 0)  # Jan 8 2024 is a Monday
        return tj.Trade(base, base, "XAUUSD", "buy", 0.1, 2000, 1995, 2010, profit)

    def test_basic_numbers(self):
        trades = [self._trade(100), self._trade(-50), self._trade(30)]
        s = tj.compute_stats(trades)
        self.assertEqual(s["total_trades"], 3)
        self.assertEqual(s["wins"], 2)
        self.assertEqual(s["losses"], 1)
        self.assertAlmostEqual(s["win_rate"], 2 / 3)
        self.assertAlmostEqual(s["net_profit"], 80)
        self.assertAlmostEqual(s["profit_factor"], 130 / 50)

    def test_max_drawdown(self):
        # +100, +100, -150 -> peak 200, trough 50, drawdown 150
        trades = [self._trade(100), self._trade(100), self._trade(-150)]
        s = tj.compute_stats(trades)
        self.assertAlmostEqual(s["max_drawdown"], 150)

    def test_hour_and_weekday_buckets(self):
        trades = [self._trade(50, hour=9), self._trade(-20, hour=9),
                  self._trade(10, hour=15, weekday=4)]
        s = tj.compute_stats(trades)
        hour9 = s["hourly"][9]
        self.assertEqual(hour9["trades"], 2)
        self.assertAlmostEqual(hour9["net"], 30)
        self.assertAlmostEqual(hour9["win_rate"], 0.5)
        self.assertEqual(s["best_hour"]["label"], 9)
        self.assertEqual(s["worst_hour"]["label"], 15)
        friday = s["daily"][4]
        self.assertEqual(friday["trades"], 1)

    def test_realized_rr(self):
        trades = [self._trade(100), self._trade(-50), self._trade(60)]
        s = tj.compute_stats(trades)
        # avg win 80 / avg loss 50
        self.assertAlmostEqual(s["realized_rr"], 1.6)

    def test_planned_rr_uses_sl_tp(self):
        t = tj.Trade(datetime(2024, 1, 8, 9, 0), datetime(2024, 1, 8, 10, 0),
                     "XAUUSD", "buy", 0.1, entry=2000, sl=1990, tp=2030, profit=10)
        s = tj.compute_stats([t])
        self.assertAlmostEqual(s["avg_planned_rr"], 3.0)


class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_samples()
        trades = tj.demo_trades(50)
        cls.stats = tj.compute_stats(trades)
        cls.html = tj.render_dashboard(cls.stats, subtitle="test", source="test")

    def test_html_contains_embedded_data(self):
        m = re.search(r"const DATA = (\{.*?\});\n", self.html, re.DOTALL)
        self.assertIsNotNone(m)
        data = json.loads(m.group(1).replace("<\\/", "</"))
        self.assertEqual(data["total_trades"], 50)
        self.assertEqual(len(data["equity"]), 50)
        self.assertEqual(len(data["hourly"]), 24)
        self.assertEqual(len(data["daily"]), 7)

    def test_html_references_chartjs(self):
        self.assertIn("cdn.jsdelivr.net/npm/chart.js", self.html)


class TestCLI(unittest.TestCase):
    def test_demo_mode_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dash.html"
            result = subprocess.run(
                [sys.executable, str(ROOT / "trade_journal.py"),
                 "--demo", "-o", str(out)],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())
            html = out.read_text(encoding="utf-8")
            self.assertIn("Trade Journal", html)
            self.assertIn("const DATA =", html)

    def test_sample_report_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dash.html"
            result = subprocess.run(
                [sys.executable, str(ROOT / "trade_journal.py"),
                 str(HERE / "sample_report.html"), "-o", str(out)],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())

    def test_no_args_is_an_error(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "trade_journal.py")],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()