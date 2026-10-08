"""Synthetic ledger fixtures; never presented as live wallet performance."""
import unittest
from unittest.mock import patch
from helpers import scratch_dir


def event(identity, side, quantity, dollars, chain="solana", token="mint", timestamp=None):
    return dict(id=identity, side=side, quantity=quantity, usd_value=dollars,
                price_usd=dollars/quantity, chain=chain, token_address=token,
                wallet="wallet", pool_address="pool", tx_hash=identity,
                provider="synthetic-test", timestamp=timestamp or "2026-10-08T10:00:00+00:00")


class WalletTests(unittest.TestCase):
    def test_polling_coverage_gaps_disqualify_apparent_winners(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.wallet_tracker import WalletTracker
        from avarice.storage.db import Storage
        settings = AvariceConfig(min_wallet_trades=1, min_wallet_tokens=1)
        with Storage(scratch_dir()/"gap.sqlite3") as store:
            tracker = WalletTracker(store, settings)
            tracker.ingest([event("b1", "buy", 10, 10),
                            event("s1", "sell", 10, 15, timestamp="2026-10-08T10:01:00+00:00")])
            self.assertTrue(tracker.metrics({})[0]["qualified"])
            tracker.ingest([{**event("b2", "buy", 10, 10, timestamp="2026-10-08T10:02:00+00:00"),
                             "coverage_gap": True},
                            event("s2", "sell", 10, 15, timestamp="2026-10-08T10:03:00+00:00")])
            self.assertFalse(tracker.metrics({})[0]["qualified"])


    def test_zero_liquidity_inventory_has_no_liquidation_proceeds(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool
        from avarice.core.wallet_tracker import WalletTracker
        from avarice.storage.db import Storage
        settings = AvariceConfig(min_wallet_trades=1, min_wallet_tokens=1)
        now = "2026-10-08T10:03:00+00:00"
        quote = Pool(Chain.SOLANA, "stranded-pool", "stranded-mint", "STRANDED",
                     1, 10000, 1000, now, observed_at=now)
        with (Storage(scratch_dir()/"zero-liquidity.sqlite3") as store,
              patch("avarice.core.wallet_tracker.utcnow", return_value=now)):
            tracker = WalletTracker(store, settings)
            tracker.ingest([event("winner-buy", "buy", 10, 10),
                            event("winner-sell", "sell", 10, 115,
                                  timestamp="2026-10-08T10:01:00+00:00")])
            prior = tracker.metrics({})[0]
            self.assertTrue(prior["qualified"])
            tracker.ingest([{**event("stranded-buy", "buy", 1000, 1000, token="stranded-mint",
                                    timestamp="2026-10-08T10:02:00+00:00"),
                             "pool_address": "stranded-pool"}])
            quotes = {quote.key: quote}
            self.assertTrue(tracker.metrics(quotes)[0]["qualified"])
            holding = store.get_state("wallet_ledger")["solana:wallet"]["inventory"]["stranded-mint"]
            quote.liquidity_usd = 0
            metrics = tracker.metrics(quotes)[0]
            self.assertEqual(metrics["unmarked_holdings"], 0)
            self.assertEqual(metrics["observed_net_pnl_usd"], prior["observed_net_pnl_usd"])
            self.assertLess(metrics["sampled_total_pnl_usd"], 0)
            self.assertAlmostEqual(metrics["sampled_unrealized_pnl_usd"], -holding["cost"])
            self.assertAlmostEqual(metrics["sampled_total_pnl_usd"],
                                   prior["observed_net_pnl_usd"] - holding["cost"])
            self.assertFalse(metrics["qualified"])
            self.assertIn("not_sample_profitable_after_estimated_costs", metrics["qualification_reasons"])

    def test_unavailable_inventory_quotes_preserve_integrity_gate(self):
        from dataclasses import replace
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool
        from avarice.core.wallet_tracker import WalletTracker
        from avarice.storage.db import Storage
        settings = AvariceConfig(min_wallet_trades=1, min_wallet_tokens=1)
        now = "2026-10-08T10:03:00+00:00"
        quote = Pool(Chain.SOLANA, "pool", "mint", "TEST", 1, 10000, 1000,
                     now, observed_at=now)
        with (Storage(scratch_dir()/"unavailable-quotes.sqlite3") as store,
              patch("avarice.core.wallet_tracker.utcnow", return_value=now)):
            tracker = WalletTracker(store, settings)
            tracker.ingest([event("buy", "buy", 10, 10),
                            event("sell", "sell", 10, 15, timestamp="2026-10-08T10:01:00+00:00"),
                            event("open-buy", "buy", 1, 1, timestamp="2026-10-08T10:02:00+00:00")])
            self.assertTrue(tracker.metrics({quote.key: quote})[0]["qualified"])
            holding = store.get_state("wallet_ledger")["solana:wallet"]["inventory"]["mint"]
            for unavailable, quotes in (
                    ("missing", {}),
                    ("wrong_chain", {("base", "pool"): replace(quote, chain=Chain.BASE)}),
                    ("stale_zero_liquidity", {quote.key: replace(
                        quote, observed_at="2026-10-08T09:57:00+00:00", liquidity_usd=0)}),
                    ("future", {quote.key: replace(quote, observed_at="2026-10-08T10:04:00+00:00")})):
                with self.subTest(unavailable=unavailable):
                    metrics = tracker.metrics(quotes)[0]
                    self.assertEqual(metrics["unmarked_holdings"], 1)
                    self.assertAlmostEqual(metrics["sampled_unrealized_pnl_usd"], -holding["cost"])
                    self.assertGreater(metrics["sampled_total_pnl_usd"], 0)
                    self.assertFalse(metrics["qualified"])
                    self.assertIn("incomplete_inventory_or_coverage", metrics["qualification_reasons"])

    def test_complete_observed_round_trip_is_cost_adjusted_deduped_and_persisted(self):
        from avarice.config.settings import AvariceConfig
        from avarice.storage.db import Storage
        try:
            from avarice.core.wallet_tracker import WalletTracker
        except ImportError as exc:
            self.fail(f"Wallet tracker must not require paid indexers: {exc}")
        db = scratch_dir()/"wallets.sqlite3"
        settings = AvariceConfig()
        events = [event("buy", "buy", 10, 10),
                  event("sell", "sell", 10, 15, timestamp="2026-10-08T10:01:00+00:00")]
        with Storage(db) as store:
            tracker = WalletTracker(store, settings)
            self.assertEqual(len(tracker.ingest(events)), 2)
            self.assertEqual(len(tracker.ingest(events)), 0)
            metrics = tracker.metrics({})[0]
            self.assertEqual(metrics["completed_round_trips"], 1)
            self.assertGreater(metrics["observed_net_pnl_usd"], 0)
            self.assertLess(metrics["observed_net_pnl_usd"], 5)
            self.assertFalse(metrics["qualified"])
        with Storage(db) as store:
            metrics = WalletTracker(store, settings).metrics({})[0]
            self.assertEqual(metrics["completed_round_trips"], 1)
            self.assertEqual(metrics["chain"], "solana")
