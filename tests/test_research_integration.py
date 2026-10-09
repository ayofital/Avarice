"""Synthetic engine-to-research traces, never main account performance."""
import json
import unittest
from unittest.mock import patch
from helpers import scratch_dir
from avarice.config.settings import AvariceConfig
from avarice.core.engine import AvariceEngine
from avarice.core.research import PatternResearch
from avarice.storage.db import Storage
from test_engine import FakeMarket, fixture_security
from test_wallets import event


class ResearchIntegrationTests(unittest.TestCase):
    def test_fresh_research_survives_watch_expiry_without_account_trades(self):
        path=scratch_dir()/"learning-integration.sqlite3"
        settings=AvariceConfig(chains=("solana",),watch_hours=0.5)
        clock=["2026-10-08T10:02:00+00:00"]
        market=FakeMarket()
        market.market.created_at=market.market.observed_at=clock[0]
        with (Storage(path) as store,
              patch("avarice.core.engine.utcnow",side_effect=lambda:clock[0]),
              patch("avarice.core.pool_watcher.utcnow",side_effect=lambda:clock[0]),
              patch("avarice.core.wallet_tracker.utcnow",side_effect=lambda:clock[0]),
              patch("avarice.core.simulator.utcnow",side_effect=lambda:clock[0])):
            engine=AvariceEngine(store,settings,adapter_factory=lambda chain:market,security_checker=fixture_security)
            first=engine.scan()
            self.assertEqual(first.get("research",{}).get("snapshot_count"),1,
                             "Discovered pools must receive decision-time research snapshots")
            self.assertEqual(first["research"]["pending"],0,"First page is observation, not a trial")
            clock[0]="2026-10-08T10:03:00+00:00"
            market.market.observed_at=clock[0]
            market.market.volume_24h_usd=2000
            market.events.append(event("prospective-buy","buy",10,10,timestamp="2026-10-08T10:02:30+00:00"))
            second=engine.scan()
            self.assertEqual(second["research"]["pending"],1)
            self.assertEqual(second["positions_opened"],0,"Research must not lower copy qualification")
            self.assertEqual(store.load_portfolio().cash_usd,50)
            features=json.loads(store.conn.execute("SELECT features FROM avarice_research_snapshots WHERE trial IS NOT NULL").fetchone()[0])
            self.assertEqual(features["volume_growth"],1)
            self.assertFalse(features["prior_sender"]["qualified"])
            frozen=features.copy()
            clock[0]="2026-10-08T11:03:00+00:00"
            market.market.observed_at=clock[0]
            market.market.price_usd=1.2
            result=engine.scan(discover=False)
            self.assertEqual(result["research"]["pending"],0)
            self.assertEqual(result["research"]["labelled"],1,
                             "Expired watchlist must not strand a pending outcome")
            self.assertEqual(result["research"]["recommendation"],"insufficient_evidence")
            self.assertEqual(store.load_portfolio().positions,[])
            self.assertEqual(store.load_portfolio().cash_usd,50)
            self.assertEqual(json.loads(store.conn.execute("SELECT features FROM avarice_research_snapshots WHERE trial IS NOT NULL").fetchone()[0]),frozen)
        with Storage(path) as store:
            self.assertEqual(PatternResearch(store,settings).summary(now=clock[0])["labelled"],1)
            self.assertEqual(store.load_portfolio().cash_usd,50)
