"""Synthetic risk-gate probes; never trade or inspect the main paper account."""
import unittest
from helpers import scratch_dir
from avarice.config.settings import AvariceConfig
from avarice.core.engine import AvariceEngine
from avarice.core.models import utcnow
from avarice.storage.db import Storage
from test_engine import FakeMarket
from test_wallets import event


class SafetyIntegrationTests(unittest.TestCase):
    def test_partial_provider_pass_is_blocked_but_its_actual_evidence_is_retained(self):
        from avarice.chains.security import TokenSecurity
        from test_security import SOLANA, SyntheticClient, envelope, synthetic_solana
        market=FakeMarket()
        market.market.token_address=SOLANA
        market.events=[{**row,"token_address":SOLANA} for row in market.events]
        settings=AvariceConfig(chains=("solana",))
        checker=TokenSecurity(settings,client=SyntheticClient(envelope(SOLANA,synthetic_solana())))
        with Storage(scratch_dir()/"partial-risk.sqlite3") as store:
            result=AvariceEngine(store,settings,adapter_factory=lambda chain:market,security_checker=checker.check).scan()
            check=store.latest_security("solana",SOLANA)
            self.assertEqual(check["status"],"unknown")
            self.assertEqual(check["provider"],"goplus","Stricter admission must not erase provider provenance")
            self.assertEqual(check["provider_status"],"limited_checks_passed")
            self.assertEqual(check["checks"]["mintable"],0)
            self.assertIn("trusted_token",check["missing_fields"])
            self.assertEqual(result["security"]["unknown"],1)
            self.assertEqual(store.load_portfolio().cash_usd,50)

    def test_real_screener_contract_controls_the_paper_entry_gate(self):
        from avarice.chains.security import TokenSecurity
        from test_security import SOLANA, SyntheticClient, envelope, synthetic_solana
        for case in ("complete", "freezable", "missing", "frozen"):
            with self.subTest(case=case), Storage(scratch_dir()/"provider-risk.sqlite3") as store:
                market=FakeMarket()
                market.market.token_address=SOLANA
                market.events=[{**row,"token_address":SOLANA} for row in market.events]
                data=synthetic_solana(metadata_mutable={"status":"0","authority":[]},trusted_token="0")
                if case=="freezable":
                    data["freezable"]={"status":"1","authority":["synthetic-authority"]}
                elif case=="missing":
                    data.pop("mintable")
                elif case=="frozen":
                    data["default_account_state"]="2"
                client=SyntheticClient(*[envelope(SOLANA,data) for _ in range(2)])
                settings=AvariceConfig(chains=("solana",),min_wallet_trades=1,min_wallet_tokens=1)
                checker=TokenSecurity(settings,client=client)
                engine=AvariceEngine(store,settings,adapter_factory=lambda chain:market,security_checker=checker.check)
                first=engine.scan()
                self.assertEqual(first["positions_opened"],0)
                market.events.append(event("fresh-buy","buy",10,10,token=SOLANA,timestamp=utcnow()))
                result=engine.scan()
                self.assertEqual(result["positions_opened"],int(case=="complete"))
                saved=store.latest_security("solana",SOLANA)
                self.assertEqual(saved["provider"],"goplus")
                self.assertEqual(saved["checks"]["default_account_state"],2 if case=="frozen" else 1)
                if case!="complete":
                    self.assertEqual(store.load_portfolio().cash_usd,50)
                else:
                    self.assertLess(store.load_portfolio().cash_usd,50)
                    self.assertEqual(len(client.urls),1,"A fresh complete check should be cached")

    def test_incomplete_screening_evidence_cannot_authorize_an_entry(self):
        from test_engine import fixture_security
        for mutation in ("missing_provider", "missing_checks", "unverified_provider", "missing_fields"):
            with self.subTest(mutation=mutation), Storage(scratch_dir()/"incomplete-risk.sqlite3") as store:
                market=FakeMarket()
                def checker(pool, now=None):
                    check=fixture_security(pool,now)
                    if mutation=="missing_provider":
                        check.pop("provider")
                    elif mutation=="missing_checks":
                        check.pop("checks")
                    elif mutation=="unverified_provider":
                        check["provider"]="unverified"
                    else:
                        check["missing_fields"]=["sell_tax"]
                    return check
                settings=AvariceConfig(chains=("solana",),min_wallet_trades=1,min_wallet_tokens=1)
                engine=AvariceEngine(store,settings,adapter_factory=lambda chain:market,security_checker=checker)
                engine.scan()
                self.assertTrue(engine.wallet_metrics()[0]["qualified"])
                market.events.append(event("fresh-buy","buy",10,10,timestamp=utcnow()))
                self.assertEqual(engine.scan()["positions_opened"],0)
                self.assertEqual(store.load_portfolio().cash_usd,50)

    def test_unscreened_tokens_cannot_be_paper_copied(self):
        for status in ("unverified", "unknown", "unsafe"):
            with self.subTest(status=status), Storage(scratch_dir()/"risk-gate.sqlite3") as store:
                market = FakeMarket()
                market.market.safety_status = status
                settings = AvariceConfig(chains=("solana",), min_wallet_trades=1, min_wallet_tokens=1)
                engine = AvariceEngine(store, settings, adapter_factory=lambda chain: market)
                engine.scan()
                market.events.append(event("fresh-buy", "buy", 10, 10, timestamp=utcnow()))
                result = engine.scan()
                self.assertEqual(result["positions_opened"], 0)
                self.assertEqual(store.load_portfolio().cash_usd, 50)
                self.assertEqual(store.load_portfolio().open_positions, [])
