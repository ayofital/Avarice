"""Engine fixtures exercise the real ledger; market inputs are synthetic."""
import unittest
from unittest.mock import patch
from helpers import scratch_dir
from avarice.core.models import Chain, Pool, utcnow
from avarice.config.settings import AvariceConfig
from avarice.storage.db import Storage
from test_wallets import event


class FakeMarket:
    def __init__(self):
        self.errors = []
        self.market = Pool(Chain.SOLANA, "pool", "mint", "TEST", 1, 10000, 1000, utcnow())
        self.events = [event("b", "buy", 10, 10),
                       event("s", "sell", 10, 15, timestamp="2026-10-08T10:01:00+00:00")]

    def new_pools(self):
        return [self.market]

    def pool(self, address):
        return self.market

    def trades(self, pool):
        return self.events


class EngineTests(unittest.TestCase):
    def test_shared_transport_diagnostics_are_attributed_only_to_the_current_chain(self):
        from avarice.core.engine import AvariceEngine
        shared = []
        def factory(chain):
            market = FakeMarket()
            market.errors = shared
            market.market.chain = chain
            market.events = []
            if chain == Chain.SOLANA:
                market.new_pools = lambda: (shared.append("solana parse warning") or [market.market])
            return market
        settings = AvariceConfig(chains=("solana", "base"), watched_pools_per_chain=1)
        with Storage(scratch_dir()/"diagnostics.sqlite3") as store:
            result = AvariceEngine(store, settings, adapter_factory=factory).scan()
            self.assertIn("solana parse warning", result["chains"]["solana"]["errors"])
            self.assertEqual(result["chains"]["base"]["errors"], [])


    def test_reduced_watch_budget_limits_preexisting_watchlists(self):
        from datetime import timedelta
        from avarice.core.models import parse_time
        from avarice.core.engine import AvariceEngine
        market = FakeMarket()
        expiry = (parse_time(utcnow()) + timedelta(hours=1)).isoformat()
        settings = AvariceConfig(chains=("solana",), watched_pools_per_chain=1)
        with Storage(scratch_dir()/"budget.sqlite3") as store:
            store.set_state("watchlist", {"solana": {address: {"expires_at": expiry}
                            for address in ("pool", "pool2", "pool3")}})
            result = AvariceEngine(store, settings, adapter_factory=lambda chain: market).scan()
            self.assertEqual(result["chains"]["solana"]["watched_pools"], 1)
            self.assertEqual(len(store.get_state("watchlist")["solana"]), 3)

    def test_fresh_buy_copies_a_prior_qualified_sender_then_its_sell_closes(self):
        from avarice.core.engine import AvariceEngine
        settings = AvariceConfig(chains=("solana",), watched_pools_per_chain=1,
                                 min_wallet_trades=1, min_wallet_tokens=1)
        market = FakeMarket()
        with Storage(scratch_dir()/"copy.sqlite3") as store:
            engine = AvariceEngine(store, settings, adapter_factory=lambda chain: market)
            engine.scan()
            market.events.append(event("fresh-buy", "buy", 10, 10, timestamp=utcnow()))
            result = engine.scan()
            self.assertEqual(result["positions_opened"], 1)
            self.assertLess(result["portfolio"]["cash_usd"], 50)
            self.assertEqual(store.load_portfolio().open_positions[0].wallet_followed, "wallet")
            self.assertEqual(engine.scan()["positions_opened"], 0)
            market.market.price_usd = 1.2
            market.market.observed_at = utcnow()
            market.events.append(event("fresh-sell", "sell", 10, 12, timestamp=utcnow()))
            result = engine.scan()
            self.assertEqual(result["positions_closed"], 1)
            closed = store.load_portfolio().closed_positions[0]
            self.assertEqual(closed.exit_reason, "followed_wallet_sell")
            self.assertGreater(closed.pnl_usd, 0)

    def test_current_batch_integrity_failures_veto_prior_qualified_sender(self):
        from avarice.core.engine import AvariceEngine
        settings = AvariceConfig(chains=("solana",), watched_pools_per_chain=2,
                                 min_wallet_trades=1, min_wallet_tokens=1)
        for failure in ("unmatched_sells", "late_events", "coverage_gaps", "unmarked_holdings"):
            with self.subTest(failure=failure):
                clock = ["2026-10-08T10:02:00+00:00"]
                market = FakeMarket()
                market.market.created_at = market.market.observed_at = clock[0]
                other_pool = Pool(Chain.SOLANA, "other-pool", "other-mint", "OTHER",
                                  1, 10000, 500, clock[0], observed_at=clock[0])
                if failure == "unmarked_holdings":
                    other_pool.observed_at = "2026-10-08T09:56:00+00:00"
                anchor = {**event("other-anchor", "buy", 1, 1, token="other-mint"),
                          "pool_address": "other-pool", "wallet": "other-wallet"}
                pages = {"pool": market.events, "other-pool": [anchor]}
                pools = {"pool": market.market, "other-pool": other_pool}
                market.new_pools = lambda: list(pools.values())
                market.pool = lambda address: pools.get(address)
                market.trades = lambda pool: pages[pool.address]
                with (Storage(scratch_dir()/"integrity.sqlite3") as store,
                      patch("avarice.core.engine.utcnow", side_effect=lambda: clock[0]),
                      patch("avarice.core.pool_watcher.utcnow", side_effect=lambda: clock[0]),
                      patch("avarice.core.wallet_tracker.utcnow", side_effect=lambda: clock[0]),
                      patch("avarice.core.simulator.utcnow", side_effect=lambda: clock[0])):
                    engine = AvariceEngine(store, settings, adapter_factory=lambda chain: market)
                    first = engine.scan()
                    prior = next(row for row in engine.wallet_metrics() if row["address"] == "wallet")
                    self.assertTrue(prior["qualified"])
                    self.assertEqual(first["portfolio"]["cash_usd"], 50)
                    if failure == "unmatched_sells":
                        pages["pool"].append(event("unmatched-sell", "sell", 10, 10,
                                                   timestamp="2026-10-08T10:02:10+00:00"))
                    elif failure == "late_events":
                        pages["pool"].append(event("late-buy", "buy", 1, 1,
                                                   timestamp="2026-10-08T09:59:00+00:00"))
                    else:
                        other_buy = {**event("other-buy", "buy", 1, 1, token="other-mint",
                                              timestamp="2026-10-08T10:02:10+00:00"),
                                     "pool_address": "other-pool"}
                        if failure == "coverage_gaps":
                            # No overlap on a pool this sender had not held: the
                            # engine tags the new event only during this batch.
                            pages["other-pool"] = [other_buy]
                        else:
                            pages["other-pool"].append(other_buy)
                    pages["pool"].append(event("fresh-buy", "buy", 10, 10,
                                               timestamp="2026-10-08T10:02:30+00:00"))
                    clock[0] = "2026-10-08T10:03:00+00:00"
                    result = engine.scan()
                    current = next(row for row in engine.wallet_metrics() if row["address"] == "wallet")
                    self.assertEqual(current[failure], 1)
                    self.assertFalse(current["qualified"])
                    self.assertEqual(current["completed_round_trips"], prior["completed_round_trips"])
                    self.assertEqual(result["positions_opened"], 0)
                    self.assertEqual(result["portfolio"]["cash_usd"], 50)
                    self.assertEqual(store.load_portfolio().cash_usd, 50)
                    self.assertEqual(store.load_portfolio().open_positions, [])

    def test_current_batch_statistics_do_not_promote_a_sender(self):
        from avarice.core.engine import AvariceEngine
        for criterion, required_trades, prior_sale in (("trade_count", 2, 15), ("profit", 1, 10)):
            with self.subTest(criterion=criterion):
                clock = ["2026-10-08T10:02:00+00:00"]
                settings = AvariceConfig(chains=("solana",), watched_pools_per_chain=1,
                                         min_wallet_trades=required_trades, min_wallet_tokens=1)
                market = FakeMarket()
                market.market.created_at = market.market.observed_at = clock[0]
                market.events[1] = event("s", "sell", 10, prior_sale,
                                         timestamp="2026-10-08T10:01:00+00:00")
                with (Storage(scratch_dir()/"no-lookahead.sqlite3") as store,
                      patch("avarice.core.engine.utcnow", side_effect=lambda: clock[0]),
                      patch("avarice.core.pool_watcher.utcnow", side_effect=lambda: clock[0]),
                      patch("avarice.core.wallet_tracker.utcnow", side_effect=lambda: clock[0]),
                      patch("avarice.core.simulator.utcnow", side_effect=lambda: clock[0])):
                    engine = AvariceEngine(store, settings, adapter_factory=lambda chain: market)
                    engine.scan()
                    self.assertFalse(engine.wallet_metrics()[0]["qualified"])
                    market.events.extend([
                        event("current-buy", "buy", 10, 10, timestamp="2026-10-08T10:02:10+00:00"),
                        event("current-sell", "sell", 10, 15, timestamp="2026-10-08T10:02:20+00:00"),
                        event("current-buy-2", "buy", 10, 10, timestamp="2026-10-08T10:02:21+00:00"),
                        event("current-sell-2", "sell", 10, 15, timestamp="2026-10-08T10:02:22+00:00"),
                        event("fresh-buy", "buy", 10, 10, timestamp="2026-10-08T10:02:30+00:00"),
                    ])
                    clock[0] = "2026-10-08T10:03:00+00:00"
                    result = engine.scan()
                    self.assertTrue(engine.wallet_metrics()[0]["qualified"])
                    self.assertEqual(result["positions_opened"], 0)
                    self.assertEqual(result["portfolio"]["cash_usd"], 50)
                    self.assertEqual(store.load_portfolio().cash_usd, 50)
                    self.assertEqual(store.load_portfolio().open_positions, [])

    def test_nonoverlapping_trade_pages_block_a_copy_signal(self):
        from avarice.core.engine import AvariceEngine
        settings = AvariceConfig(chains=("solana",), watched_pools_per_chain=1,
                                 min_wallet_trades=1, min_wallet_tokens=1)
        market = FakeMarket()
        with Storage(scratch_dir()/"engine-gap.sqlite3") as store:
            engine = AvariceEngine(store, settings, adapter_factory=lambda chain: market)
            engine.scan()
            market.events = [event("late-page", "buy", 10, 10, timestamp=utcnow())]
            result = engine.scan()
            self.assertEqual(result["positions_opened"], 0)
            self.assertFalse(engine.wallet_metrics()[0]["qualified"])


    def test_first_live_style_scan_is_observation_only_and_repeat_dedupes(self):
        try:
            from avarice.core.engine import AvariceEngine
        except ImportError as exc:
            self.fail(f"Engine must use the verified paper interfaces: {exc}")
        market = FakeMarket()
        settings = AvariceConfig(chains=("solana",), watched_pools_per_chain=1)
        with Storage(scratch_dir()/"engine.sqlite3") as store:
            engine = AvariceEngine(store, settings, adapter_factory=lambda chain: market)
            first = engine.scan()
            self.assertEqual(first["observations_added"], 2)
            self.assertEqual(first["positions_opened"], 0)
            self.assertEqual(first["portfolio"]["cash_usd"], 50)
            self.assertEqual(first["chains"]["solana"]["new_pools"], 1)
            second = engine.scan()
            self.assertEqual(second["observations_added"], 0)
            self.assertEqual(second["chains"]["solana"]["new_pools"], 0)
            self.assertEqual(second["portfolio"]["equity_usd"], 50)
            self.assertEqual(len(engine.wallet_metrics()), 1)
