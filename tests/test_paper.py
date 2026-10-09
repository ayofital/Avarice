"""Cash conservation, fees and persistence for a paper round trip."""
import unittest
from helpers import scratch_dir


class PaperTests(unittest.TestCase):
    def test_wrong_token_or_chain_payload_cannot_mark_or_exit_a_holding(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool, VirtualPortfolio
        from avarice.core.simulator import Simulator
        for wrong_chain,wrong_token in ((Chain.BASE,"synthetic-mint"),(Chain.SOLANA,"other-mint")):
            with self.subTest(chain=wrong_chain,token=wrong_token):
                sim=Simulator(VirtualPortfolio.new(50),AvariceConfig())
                quote=Pool(Chain.SOLANA,"synthetic-pool","synthetic-mint","SYN",1,10000,1000,None,
                           safety_status="limited_checks_passed")
                pos=sim.open_position(quote,"synthetic-sender")
                original_equity=sim.portfolio.equity_usd
                wrong=Pool(wrong_chain,"synthetic-pool",wrong_token,"WRONG",0.1,10000,1000,None,
                           safety_status="unsafe")
                self.assertEqual(sim.update_positions({quote.key:wrong}),[])
                self.assertTrue(pos.is_open)
                self.assertTrue(pos.quote_missing)
                self.assertEqual(sim.portfolio.equity_usd,original_equity)


    def test_newly_unsafe_quote_is_a_full_cost_writeoff_not_a_profitable_sell(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool, VirtualPortfolio
        from avarice.core.simulator import Simulator
        sim=Simulator(VirtualPortfolio.new(50),AvariceConfig())
        quote=Pool(Chain.SOLANA,"synthetic-pool","synthetic-mint","SYN",1,10000,1000,None,
                   safety_status="limited_checks_passed")
        position=sim.open_position(quote,"synthetic-sender")
        quote.price_usd=3
        quote.safety_status="unsafe"
        self.assertEqual(len(sim.update_positions({quote.key:quote})),1)
        self.assertEqual(position.exit_reason,"unsafe_token_writeoff")
        self.assertEqual(position.pnl_usd,-position.entry_cost_usd)
        self.assertEqual(position.exit_fee_usd,0)
        self.assertEqual(sim.portfolio.cash_usd,45)


    def test_simulator_rejects_unknown_and_unsafe_entries_even_without_engine(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool, VirtualPortfolio
        from avarice.core.simulator import Simulator
        for status in ("unverified","unknown","unsafe"):
            with self.subTest(status=status):
                sim=Simulator(VirtualPortfolio.new(50),AvariceConfig())
                quote=Pool(Chain.SOLANA,"synthetic-pool","synthetic-mint","SYN",1,10000,1000,None,
                           safety_status=status)
                self.assertIsNone(sim.open_position(quote,"synthetic-sender"))
                self.assertEqual(sim.portfolio.cash_usd,50)


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
                    created_at="2026-10-08T00:00:00+00:00", safety_status="limited_checks_passed")
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
        bad = Pool(Chain.SOLANA, "pool", "token", "TEST", 1, float("inf"), 100, None, safety_status="limited_checks_passed")
        self.assertIsNone(sim.open_position(bad, "wallet"))
        stale = Pool(Chain.SOLANA, "pool2", "token2", "TEST", 1, 10000, 100, None,
                     observed_at="2000-01-01T00:00:00+00:00", safety_status="limited_checks_passed")
        self.assertIsNone(sim.open_position(stale, "wallet"))
        self.assertAlmostEqual(sim.portfolio.cash_usd, 50)

    def test_missing_or_wrong_chain_quotes_do_not_exit_or_zero_a_position(self):
        from avarice.config.settings import AvariceConfig
        from avarice.core.models import Chain, Pool, VirtualPortfolio
        from avarice.core.simulator import Simulator
        settings = AvariceConfig(chains=("base", "ethereum"))
        sim = Simulator(VirtualPortfolio.new(50), settings)
        pool = Pool(Chain.BASE, "same-pool", "same-token", "TEST", 2, 100000, 1000, None, safety_status="limited_checks_passed")
        pos = sim.open_position(pool, "wallet")
        wrong = Pool(Chain.ETHEREUM, "same-pool", "same-token", "TEST", 0.1, 100000, 1000, None, safety_status="limited_checks_passed")
        equity = sim.portfolio.equity_usd
        self.assertEqual(sim.update_positions({wrong.key: wrong}), [])
        self.assertTrue(pos.is_open)
        self.assertTrue(pos.quote_missing)
        self.assertAlmostEqual(sim.portfolio.equity_usd, equity)
        real = Pool(Chain.BASE, "same-pool", "same-token", "TEST", 0.1, 100000, 1000, None, safety_status="limited_checks_passed")
        self.assertEqual(len(sim.update_positions({real.key: real})), 1)
        self.assertEqual(pos.exit_reason, "stop_loss")
        self.assertLess(pos.pnl_usd, 0)
