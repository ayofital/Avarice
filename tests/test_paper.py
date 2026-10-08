"""Cash conservation, fees and persistence for a paper round trip."""
import unittest
from helpers import scratch_dir


class PaperTests(unittest.TestCase):
    def test_round_trip_persists_positions_and_charges_both_sides(self):
        try:
            from avarice.config.settings import AvariceConfig
            from avarice.core.models import Chain, Pool
            from avarice.core.simulator import Simulator
            from avarice.storage.db import Storage
        except ImportError as exc:
            self.fail(f"Paper core must run with the standard library: {exc}")
        settings = AvariceConfig()
        db = scratch_dir() / "paper.sqlite3"
        pool = Pool(chain=Chain.SOLANA, address="pool", token_address="mint", symbol="TEST",
                    price_usd=2.0, liquidity_usd=100000, volume_24h_usd=1000,
                    created_at="2026-10-08T00:00:00+00:00")
        with Storage(db, settings.virtual_capital_usd) as store:
            sim = Simulator(store.load_portfolio(), settings)
            pos = sim.open_position(pool, wallet="observed-wallet")
            self.assertIsNotNone(pos)
            self.assertLessEqual(pos.entry_cost_usd, 5.0)
            self.assertAlmostEqual(sim.portfolio.cash_usd + pos.entry_cost_usd, 50.0)
            self.assertLess(sim.portfolio.equity_usd, 50.0)
            self.assertLess(sim.portfolio.max_drawdown_pct, 5.0)
            store.save_portfolio(sim.portfolio)
        with Storage(db, settings.virtual_capital_usd) as store:
            sim = Simulator(store.load_portfolio(), settings)
            pos = sim.portfolio.open_positions[0]
            self.assertEqual(pos.wallet_followed, "observed-wallet")
            self.assertEqual(pos.pool.chain, Chain.SOLANA)
            sim.close_position(pos, 2.0, "manual")
            self.assertLess(pos.pnl_usd, 0.0)
            self.assertGreater(pos.exit_fee_usd, 0.0)
            self.assertAlmostEqual(sim.portfolio.cash_usd, 50.0 + pos.pnl_usd)
            self.assertEqual(len(sim.portfolio.closed_positions), 1)
            store.save_portfolio(sim.portfolio)
        with Storage(db, settings.virtual_capital_usd) as store:
            restored = store.load_portfolio()
            self.assertEqual(len(restored.open_positions), 0)
            self.assertEqual(len(restored.closed_positions), 1)
            self.assertAlmostEqual(restored.equity_usd, 50.0 + pos.pnl_usd)

    def test_nonfinite_liquidity_and_stale_quotes_cannot_open_positions(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool, VirtualPortfolio
        from avarice.core.simulator import Simulator
        sim = Simulator(VirtualPortfolio.new(50), AvariceConfig())
        bad = Pool(Chain.SOLANA, "pool", "token", "TEST", 1, float("inf"), 100, None)
        self.assertIsNone(sim.open_position(bad, "wallet"))
        stale = Pool(Chain.SOLANA, "pool2", "token2", "TEST", 1, 10000, 100, None,
                     observed_at="2000-01-01T00:00:00+00:00")
        self.assertIsNone(sim.open_position(stale, "wallet"))
        self.assertAlmostEqual(sim.portfolio.cash_usd, 50)

    def test_missing_or_wrong_chain_quotes_do_not_exit_or_zero_a_position(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool, VirtualPortfolio
        from avarice.core.simulator import Simulator
        settings = AvariceConfig(chains=("base", "ethereum"))
        sim = Simulator(VirtualPortfolio.new(50), settings)
        pool = Pool(Chain.BASE, "same-pool", "same-token", "TEST", 2, 100000, 1000, None)
        pos = sim.open_position(pool, "wallet")
        wrong = Pool(Chain.ETHEREUM, "same-pool", "same-token", "TEST", 0.1, 100000, 1000, None)
        equity = sim.portfolio.equity_usd
        self.assertEqual(sim.update_positions({wrong.key: wrong}), [])
        self.assertTrue(pos.is_open)
        self.assertTrue(pos.quote_missing)
        self.assertAlmostEqual(sim.portfolio.equity_usd, equity)
        real = Pool(Chain.BASE, "same-pool", "same-token", "TEST", 0.1, 100000, 1000, None)
        self.assertEqual(len(sim.update_positions({real.key: real})), 1)
        self.assertEqual(pos.exit_reason, "stop_loss")
        self.assertLess(pos.pnl_usd, 0)
