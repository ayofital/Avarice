"""Synthetic paper reports; fixture trades never enter the main account."""
from datetime import timedelta
import unittest
from unittest.mock import patch
from helpers import scratch_dir
from avarice.config.settings import AvariceConfig
from avarice.core.models import Chain, Pool, VirtualPosition, parse_time
from avarice.reporting.telegram import TelegramReporter
from avarice.storage.db import Storage

NOW = "2026-10-09T19:00:00+00:00"


def position(symbol, entry_hours_ago, exit_hours_ago=None, pnl=0.0):
    now = parse_time(NOW)
    return VirtualPosition(
        id=symbol, pool=Pool(Chain.SOLANA, symbol, symbol, symbol,
                            1.0, 10000.0, 1000.0, NOW, observed_at=NOW),
        quantity=1.0, entry_cost_usd=2.0, entry_price_usd=1.0,
        entry_fee_usd=0.05, entry_time=(now-timedelta(hours=entry_hours_ago)).isoformat(),
        wallet_followed="synthetic-sender", fee_bps=100.0,
        slippage_bps=100.0, gas_usd=0.02, last_price_usd=1.0, last_quote_at=NOW,
        exit_time=(now-timedelta(hours=exit_hours_ago)).isoformat() if exit_hours_ago is not None else None,
        exit_reason="followed_wallet_sell" if exit_hours_ago is not None else None,
        exit_fee_usd=0.05 if exit_hours_ago is not None else 0.0, pnl_usd=pnl,
    )


class ReportingTests(unittest.TestCase):
    def test_report_contains_only_recent_entry_exit_details_and_net_realized_pnl(self):
        with Storage(scratch_dir()/"report-window.sqlite3") as store:
            portfolio = store.load_portfolio()
            portfolio.positions = [position("OLD-CLOSED", 30, 13, pnl=5.0),
                                   position("RECENT-CLOSED", 14, 1, pnl=0.4),
                                   position("ROUND-TRIP", 3, 2, pnl=-0.1),
                                   position("NEW-HOLD", 1)]
            store.save_portfolio(portfolio)
            with patch("avarice.reporting.telegram.utcnow", return_value=NOW):
                report = TelegramReporter(store, AvariceConfig()).render()
            self.assertIn("Last 12 hours", report)
            self.assertIn("Paper entries: 2 | Paper exits: 2", report)
            self.assertIn("Realized net PnL: $+0.30", report)
            self.assertIn("BUY NEW-HOLD", report)
            self.assertIn("BUY ROUND-TRIP", report)
            self.assertIn("SELL RECENT-CLOSED", report)
            self.assertIn("SELL ROUND-TRIP", report)
            self.assertIn("followed_wallet_sell", report)
            self.assertNotIn("BUY RECENT-CLOSED", report)
            self.assertNotIn("OLD-CLOSED", report)

    def test_empty_reporting_window_is_explicit_and_keeps_an_older_open_holding_visible(self):
        with Storage(scratch_dir()/"report-empty.sqlite3") as store:
            portfolio = store.load_portfolio()
            portfolio.positions = [position("OLD-HOLD", 30)]
            store.save_portfolio(portfolio)
            with patch("avarice.reporting.telegram.utcnow", return_value=NOW):
                report = TelegramReporter(store, AvariceConfig()).render()
            self.assertIn("Paper entries: 0 | Paper exits: 0", report)
            self.assertIn("No paper trades in this reporting window.", report)
            self.assertIn("Open paper positions", report)
            self.assertIn("OLD-HOLD", report)
            self.assertNotIn("BUY OLD-HOLD", report)

    def test_entry_display_is_bounded_but_reports_the_complete_window_count(self):
        with Storage(scratch_dir()/"report-limit.sqlite3") as store:
            portfolio = store.load_portfolio()
            portfolio.positions = [position(f"ENTRY-{n}", n) for n in range(1,8)]
            store.save_portfolio(portfolio)
            with patch("avarice.reporting.telegram.utcnow", return_value=NOW):
                report = TelegramReporter(store, AvariceConfig()).render()
            self.assertIn("Paper entries: 7 | Paper exits: 0", report)
            self.assertIn("Entries (showing 5 of 7)", report)
            self.assertEqual(report.count("BUY ENTRY-"), 5)
