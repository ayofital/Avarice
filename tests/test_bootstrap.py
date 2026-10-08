"""Regression tests for the interrupted package entry point."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_offline_status_persists_a_fifty_dollar_paper_account(self):
        import json
        from helpers import scratch_dir
        db = scratch_dir()/"cli.sqlite3"
        command = [sys.executable, "-m", "avarice", "--db", str(db), "--json", "status"]
        for _ in range(2):
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            status = json.loads(result.stdout)
            self.assertEqual(status["mode"], "paper")
            self.assertEqual(status["cash_usd"], 50)
            self.assertEqual(status["equity_usd"], 50)
        self.assertTrue(db.exists())


    def test_report_distinguishes_no_trades_from_verified_profitability(self):
        from helpers import scratch_dir
        result = subprocess.run([sys.executable, "-m", "avarice", "--db",
                                 str(scratch_dir()/"report.sqlite3"), "report"],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PAPER ONLY", result.stdout)
        self.assertIn("$50.00", result.stdout)
        self.assertIn("not proof of profitability", result.stdout)
        self.assertIn("No scans recorded", result.stdout)
        self.assertNotIn("Report sent", result.stdout)

    def test_module_help_runs_without_hermes_or_third_party_packages(self):
        result = subprocess.run(
            [sys.executable, "-m", "avarice", "--help"], cwd=ROOT,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("scan", result.stdout)
        self.assertIn("paper", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
