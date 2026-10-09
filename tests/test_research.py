"""Explicitly synthetic prospective research fixtures, never live market evidence."""
import json
import unittest
from helpers import scratch_dir
from avarice.config.settings import AvariceConfig
from datetime import timedelta
from avarice.core.models import Chain, Pool, parse_time
from avarice.storage.db import Storage


START = "2026-10-01T00:00:00+00:00"


def at(minutes=0):
    return (parse_time(START) + timedelta(minutes=minutes)).isoformat()


def pool(now=START, **changes):
    values = dict(chain=Chain.SOLANA, address="RawPool", token_address="RawToken",
                  symbol="SYNTHETIC", price_usd=1.0, liquidity_usd=10000.0,
                  volume_24h_usd=1000.0, created_at=at(-60), observed_at=now,
                  source="synthetic-test", safety_status="limited_checks_passed")
    return Pool(**{**values, **changes})


def signal(identity="synthetic-buy", now=START, **changes):
    event = dict(id=identity, timestamp=now, side="buy", chain="solana", wallet="RawSender",
                pool_address="RawPool", token_address="RawToken", quantity=2.0,
                usd_value=2.0, price_usd=1.0, provider="synthetic-test", tx_hash=identity)
    return {**event, **changes}


def decisions(store):
    return [(json.loads(features), json.loads(trial) if trial else None) for features, trial in
            store.conn.execute("SELECT features,trial FROM avarice_research_snapshots "
                               "WHERE event_id IS NOT NULL ORDER BY id")]


def outcomes(store):
    return [json.loads(row[0]) for row in store.conn.execute(
        "SELECT payload FROM avarice_research_outcomes ORDER BY snapshot_id")]


def episode(research, minute, liquidity=10000, exit_price=2.0, volume=1000, chain=Chain.SOLANA):
    """A complete SYNTHETIC prospective entry followed by a later market quote."""
    entry = pool(at(minute), chain=chain, liquidity_usd=liquidity, volume_24h_usd=volume)
    research.advance({entry.key: entry}, [signal(f"synthetic-{chain.value}-{minute}", at(minute),
                     chain=chain.value)], {}, now=at(minute))
    later = pool(at(minute + 60), chain=chain, price_usd=exit_price,
                 liquidity_usd=liquidity, volume_24h_usd=volume)
    research.advance({later.key: later}, [], {}, now=at(minute + 60))


def synthetic_training(research):
    episode(research, 0, liquidity=1000, exit_price=0)
    for index in range(20):
        episode(research, (index + 1) * 120)


def research_type(test):
    try:
        from avarice.core.research import PatternResearch
    except ImportError as exc:
        test.fail(f"Prospective research API is missing: {exc}")
    return PatternResearch


class ResearchTests(unittest.TestCase):
    def test_empty_summary_is_honest_read_only_without_schema_creation(self):
        with Storage(scratch_dir() / "research.sqlite3") as store:
            before = store.conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
            research = research_type(self)(store, AvariceConfig())
            report = research.summary(now="2026-10-01T00:00:00+00:00")
            self.assertEqual(report["recommendation"], "insufficient_evidence")
            self.assertIsNone(report["candidate_rule"])
            for name in ("pending", "labelled", "excluded", "training_count", "validation_count"):
                self.assertEqual(report[name], 0)
            self.assertTrue(report["version"])
            self.assertTrue(report["limitations"])
            self.assertEqual(len(report["rules"]), 3)
            json.dumps(report, allow_nan=False)
            self.assertEqual(research.pending_pools(), [])
            self.assertEqual(before, store.conn.execute(
                "SELECT name,sql FROM sqlite_master ORDER BY name").fetchall())


    def test_fresh_trial_persists_without_mutating_cash_or_legacy_events(self):
        path = scratch_dir() / "research.sqlite3"
        settings = AvariceConfig()
        prior = {("solana", "RawSender"): dict(chain="solana", address="RawSender",
                 completed_round_trips=25, win_rate=0.6, qualified=True,
                 coverage="sampled_public_pool_trades_only", observed_net_pnl_usd=12.0)}
        with Storage(path) as store:
            before = store.get_state("portfolio")
            quote = pool()
            store.save_pool(quote)
            store.record_event("solana", signal("legacy"))
            research = research_type(self)(store, settings)
            self.assertTrue(callable(getattr(research, "advance", None)), "advance API must exist")
            research.advance({quote.key: quote}, [], prior, now=START)
            self.assertEqual(research.pending_pools(), [])
            research.advance({quote.key: quote}, [signal(), signal("second")], prior, now=START)
            self.assertEqual(research.pending_pools(), [("solana", "RawPool")])
            snapshots = decisions(store)
            self.assertEqual(len(snapshots), 2)
            features, trial = snapshots[0]
            self.assertEqual(features["source_event"]["provider"], "synthetic-test")
            self.assertEqual(features["sender"], "RawSender")
            self.assertEqual(features["prior_sender"]["observed_net_pnl_usd"], 12)
            self.assertEqual(features["pool_age_minutes"], 60)
            self.assertEqual(features["volume_to_liquidity"], 0.1)
            self.assertIsNone(features["volume_growth"])
            self.assertIsNone(features["liquidity_growth"])
            self.assertEqual((features["chain"], features["pool_address"], features["token_address"]),
                             ("solana", "RawPool", "RawToken"))
            self.assertEqual(trial["entry_at"], START)
            self.assertEqual(trial["due_at"], at(60))
            self.assertEqual(trial["entry_cost_usd"], 5.0)
            self.assertGreater(trial["entry_fee_usd"], settings.gas_usd["solana"])
            self.assertLess(trial["quantity"], 5.0)
            self.assertIsNone(snapshots[1][1])
            self.assertEqual(store.get_state("portfolio"), before)
        with Storage(path) as store:
            research = research_type(self)(store, settings)
            research.advance({quote.key: quote}, [signal()], prior, now=START)
            self.assertEqual(len(decisions(store)), 2)
            self.assertEqual(research.summary(START)["pending"], 1)
            self.assertEqual(store.get_state("portfolio"), before)


    def test_maturity_uses_later_exact_fresh_quote_and_costs_on_both_sides(self):
        settings = AvariceConfig()
        with Storage(scratch_dir() / "maturity.sqlite3") as store:
            research = research_type(self)(store, settings)
            quote = pool()
            research.advance({quote.key: quote}, [signal()], {}, now=START)
            frozen = decisions(store)[0]
            trial = frozen[1]
            for observed, clock in ((at(59), at(59)), (at(61), at(60)), (at(59), at(60))):
                quote.price_usd, quote.observed_at = 100.0, observed
                research.advance({quote.key: quote}, [], {}, now=clock)
                self.assertEqual(outcomes(store), [], "early/future quotes must not label trials")
            quote.price_usd, quote.observed_at = 1.2, at(60)
            research.advance({quote.key: quote}, [], {}, now=at(60))
            self.assertEqual(len(outcomes(store)), 1, "mature observations must settle")
            result = outcomes(store)[0]
            self.assertEqual(result["status"], "labelled")
            self.assertEqual(result["reason"], "horizon_observation")
            self.assertEqual(result["observed_at"], at(60))
            impact = min(1000, trial["quantity"] * 1.2 / (quote.liquidity_usd / 2) * 10000)
            gross = trial["quantity"] * 1.2 * (1 - (settings.slippage_bps + impact) / 10000)
            expected = max(0, gross * (1 - settings.fee_bps / 10000) - settings.gas_usd["solana"])
            self.assertAlmostEqual(result["pnl_usd"], expected - 5.0)
            self.assertGreater(result["exit_fee_usd"], 0)
            self.assertEqual(research.pending_pools(), [])
            self.assertEqual(research.summary(at(60))["labelled"], 1)
            self.assertEqual(research.summary(at(59))["labelled"], 0)
            research.advance({quote.key: quote}, [], {}, now=at(61))
            self.assertEqual(len(outcomes(store)), 1)
            self.assertEqual(decisions(store)[0], frozen)
            self.assertEqual(store.load_portfolio().cash_usd, 50.0)


    def test_only_fresh_finite_identity_matched_buy_signals_open_trials(self):
        invalid = [dict(timestamp=at(-3)), dict(timestamp=at(1)), dict(timestamp="not-time"),
                   dict(chain="base"), dict(token_address="different"), dict(pool_address="different"),
                   dict(side="sell"), dict(quantity=0), dict(quantity=float("nan")),
                   dict(usd_value=float("inf")), dict(price_usd=-1), dict(id=""), dict(wallet="")]
        for changes in invalid:
            with self.subTest(changes=changes), Storage(scratch_dir() / "invalid.sqlite3") as store:
                research = research_type(self)(store, AvariceConfig())
                quote = pool()
                research.advance({quote.key: quote}, [signal(**changes)], {}, now=START)
                self.assertEqual(research.pending_pools(), [])
        for quote in (pool(chain=Chain.BASE), pool(token_address="OtherRawToken"),
                      pool(observed_at=at(-6)), pool(observed_at=at(1)),
                      pool(price_usd=float("nan")), pool(liquidity_usd=float("inf"))):
            with self.subTest(quote=quote), Storage(scratch_dir() / "quote.sqlite3") as store:
                research = research_type(self)(store, AvariceConfig())
                research.advance({("solana", "RawPool"): quote}, [signal()], {}, now=START)
                self.assertEqual(research.pending_pools(), [])


    def test_unsafe_or_unknown_screening_records_context_but_no_costed_trial(self):
        for status in ("unsafe", "unverified", "unknown", None):
            with self.subTest(status=status), Storage(scratch_dir() / "risk.sqlite3") as store:
                research = research_type(self)(store, AvariceConfig())
                quote = pool(safety_status=status)
                research.advance({quote.key: quote}, [signal()], {}, now=START)
                self.assertEqual(research.pending_pools(), [], "only limited checks passed admits a trial")
                features, trial = decisions(store)[0]
                self.assertEqual(features["safety_status"], status)
                self.assertIsNone(features["prior_sender"])
                self.assertIsNone(trial)
                self.assertEqual(store.load_portfolio().cash_usd, 50.0)


    def test_missing_stale_mismatched_or_unknown_labels_expire_without_invented_pnl(self):
        cases = {
            "missing_quote": lambda minute: {},
            "wrong_chain_key": lambda minute: {("base", "RawPool"): pool(at(minute), chain=Chain.BASE)},
            "wrong_identity": lambda minute: {("solana", "RawPool"): pool(at(minute), token_address="Other")},
            "wrong_chain_payload": lambda minute: {("solana", "RawPool"): pool(at(minute), chain=Chain.BASE)},
            "stale_quote": lambda minute: {("solana", "RawPool"): pool(at(minute - 6))},
            "future_quote": lambda minute: {("solana", "RawPool"): pool(at(minute + 1))},
            "unknown_security": lambda minute: {("solana", "RawPool"): pool(at(minute), safety_status="unverified")},
            "invalid_market_values": lambda minute: {("solana", "RawPool"): pool(at(minute), price_usd=float("nan"))},
        }
        for reason, quotes in cases.items():
            with self.subTest(reason=reason), Storage(scratch_dir() / "exclude.sqlite3") as store:
                research = research_type(self)(store, AvariceConfig())
                entry = pool()
                research.advance({entry.key: entry}, [signal()], {}, now=START)
                research.advance(quotes(60), [], {}, now=at(60))
                self.assertEqual(outcomes(store), [], "unavailable observations must not invent outcomes")
                research.advance(quotes(91), [], {}, now=at(91))
                self.assertEqual(len(outcomes(store)), 1, "expired trials must be explicitly excluded")
                result = outcomes(store)[0]
                self.assertEqual(result["status"], "excluded")
                expected = "missing_quote" if reason == "wrong_chain_key" else (
                    "wrong_identity" if reason == "wrong_chain_payload" else reason)
                self.assertEqual(result["reason"], expected)
                self.assertIsNone(result["pnl_usd"])
                self.assertEqual(research.summary(at(91))["excluded"], 1)
                self.assertEqual(research.summary(at(91))["labelled"], 0)
                self.assertEqual(research.pending_pools(), [])
                self.assertEqual(len(decisions(store)), 1)
                self.assertEqual(store.load_portfolio().cash_usd, 50.0)


    def test_fresh_zero_market_or_newly_unsafe_is_full_cost_writeoff_not_a_sell(self):
        for changes in (dict(liquidity_usd=0), dict(price_usd=0), dict(safety_status="unsafe")):
            with self.subTest(changes=changes), Storage(scratch_dir() / "writeoff.sqlite3") as store:
                research = research_type(self)(store, AvariceConfig())
                entry = pool()
                research.advance({entry.key: entry}, [signal()], {}, now=START)
                quote = pool(at(60), **changes)
                research.advance({quote.key: quote}, [], {}, now=at(60))
                self.assertEqual(len(outcomes(store)), 1, "observed inability to exit is a labelled writeoff")
                result = outcomes(store)[0]
                self.assertEqual(result["status"], "labelled")
                self.assertEqual(result["pnl_usd"], -5.0)
                self.assertEqual(result["net_proceeds_usd"], 0.0)
                self.assertEqual(result.get("exit_model"), "conservative_writeoff_no_sell")
                self.assertIn("writeoff", result["reason"])
                self.assertEqual(research.summary(at(60))["labelled"], 1)
                self.assertEqual(store.load_portfolio().cash_usd, 50)


    def test_growth_and_prior_sender_features_are_immutable_decision_time_evidence(self):
        with Storage(scratch_dir() / "features.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            previous = pool(at(-10))
            research.advance({previous.key: previous}, [], {}, now=at(-10))
            quote = pool(volume_24h_usd=2000, liquidity_usd=20000)
            prior = {("solana", "RawSender"): dict(chain="solana", address="RawSender",
                     qualified=False, completed_round_trips=4, observed_net_pnl_usd=-10,
                     qualification_reasons=["insufficient_completed_round_trips"], coverage_gaps=2)}
            research.advance({quote.key: quote}, [signal()], prior, now=START)
            frozen = decisions(store)[0][0]
            self.assertEqual(frozen["volume_growth"], 1.0)
            self.assertEqual(frozen["liquidity_growth"], 1.0)
            prior[("solana", "RawSender")].update(qualified=True, observed_net_pnl_usd=1000)
            quote.price_usd, quote.volume_24h_usd, quote.observed_at = 10, 99999, at(10)
            research.advance({quote.key: quote}, [signal("new", at(10))], prior, now=at(10))
            self.assertEqual(decisions(store)[0][0], frozen)
            self.assertFalse(frozen["prior_sender"]["qualified"])
            self.assertEqual(frozen["prior_sender"]["observed_net_pnl_usd"], -10)
            self.assertEqual(frozen["price_usd"], 1)


    def test_training_selection_freezes_before_validation_and_purges_overlapping_outcomes(self):
        settings = AvariceConfig()
        settings.research_min_samples = 2  # Deliberately tiny SYNTHETIC chronology fixture.
        with Storage(scratch_dir() / "chronology.sqlite3") as store:
            research = research_type(self)(store, settings)
            episode(research, 0, liquidity=1000, exit_price=0.5)
            episode(research, 120)
            third = pool(at(240))
            research.advance({third.key: third}, [signal("synthetic-third", at(240))], {}, now=at(240))
            overlap = pool(at(250), chain=Chain.BASE)
            research.advance({overlap.key: overlap}, [signal("synthetic-overlap", at(250), chain="base")], {}, now=at(250))
            third.price_usd, third.observed_at = 2, at(300)
            research.advance({third.key: third}, [], {}, now=at(300))
            entry = pool(at(320))
            research.advance({entry.key: entry}, [signal("synthetic-validation", at(320))], {}, now=at(320))
            report = research.summary(at(320))
            self.assertEqual(report["candidate_rule"], "higher_liquidity")
            self.assertEqual(report["first_validation_entry"], at(320))
            self.assertEqual(report["training_count"], 3)
            self.assertEqual(report["selected_training_count"], 2)
            overlap.price_usd, overlap.observed_at = 100, at(330)
            research.advance({overlap.key: overlap}, [], {}, now=at(330))
            later = pool(at(380), price_usd=0.5)
            research.advance({later.key: later}, [], {}, now=at(380))
            report = research.summary(at(380))
            self.assertEqual(report["candidate_rule"], "higher_liquidity")
            self.assertEqual(report["training_count"], 3)
            self.assertEqual(report["validation_count"], 1)
            self.assertEqual(report["purged_training_count"], 1)
            self.assertEqual(report["recommendation"], "insufficient_evidence")
            selected = next(rule for rule in report["rules"] if rule["name"] == "higher_liquidity")
            self.assertEqual(selected["training"]["wins"], 2)
            self.assertEqual(selected["validation"]["losses"], 1)


    def test_synthetic_later_positive_improvement_needs_twenty_selected_samples_and_three_dates(self):
        """Entirely synthetic evidence: does NOT demonstrate live strategy profitability."""
        with Storage(scratch_dir() / "synthetic-validation.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            synthetic_training(research)
            count = 0
            for day, winners in enumerate((7, 7, 6)):
                base = 4320 + day * 1440
                episode(research, base, liquidity=1000, exit_price=0.2, volume=100)
                for index in range(winners):
                    minute = base + (index + 1) * 120
                    episode(research, minute)
                    count += 1
                    report = research.summary(at(minute + 60))
                    if count < 20:
                        self.assertEqual(report["recommendation"], "insufficient_evidence")
            self.assertEqual(report["recommendation"], "candidate_for_review")
            self.assertEqual(report["candidate_rule"], "higher_liquidity")
            self.assertEqual(report["training_count"], 21)
            self.assertEqual(report["selected_training_count"], 20)
            self.assertEqual(report["validation_count"], 23)
            self.assertEqual(report["selected_validation_count"], 20)
            selected = next(rule for rule in report["rules"] if rule["name"] == "higher_liquidity")
            self.assertEqual(selected["validation"]["utc_dates"], 3)
            self.assertEqual(selected["validation"]["wins"], 20)
            self.assertEqual(report["baseline_comparison"]["validation"]["losses"], 3)
            self.assertGreater(selected["validation"]["net_pnl_usd"], 0)
            self.assertGreater(selected["validation"]["mean_pnl_usd"],
                               report["baseline_comparison"]["validation"]["mean_pnl_usd"])
            self.assertEqual(store.load_portfolio().cash_usd, 50)
            json.dumps(report, allow_nan=False)


    def test_validation_losses_or_no_baseline_improvement_cannot_reselect_or_promote(self):
        for exit_price in (0.5, 2.0):
            with self.subTest(exit_price=exit_price), Storage(scratch_dir() / "no-improvement.sqlite3") as store:
                research = research_type(self)(store, AvariceConfig())
                synthetic_training(research)
                for index in range(20):
                    minute = 4320 + index * 240
                    episode(research, minute, exit_price=exit_price)
                    if exit_price == 0.5:
                        # This previously ineligible rule wins LATER: it must not replace the candidate.
                        episode(research, minute + 120, liquidity=1000, volume=800, exit_price=10)
                report = research.summary(at(minute + 180))
                self.assertEqual(report["candidate_rule"], "higher_liquidity")
                self.assertEqual(report["selected_validation_count"], 20)
                self.assertEqual(report["recommendation"], "no_validated_improvement")
                selected = next(rule for rule in report["rules"] if rule["name"] == "higher_liquidity")
                if exit_price == 0.5:
                    self.assertEqual(selected["validation"]["losses"], 20)
                    self.assertLess(selected["validation"]["net_pnl_usd"], 0)
                    alternate = next(rule for rule in report["rules"] if rule["name"] == "high_volume_to_liquidity")
                    self.assertEqual(alternate["training"]["count"], 1)
                    self.assertEqual(alternate["validation"]["wins"], 20)
                else:
                    self.assertEqual(selected["validation"]["mean_pnl_usd"],
                                     report["baseline_comparison"]["validation"]["mean_pnl_usd"])


    def test_disabled_research_settles_old_trials_but_never_opens_fresh_costed_trials(self):
        settings = AvariceConfig()
        with Storage(scratch_dir() / "disabled.sqlite3") as store:
            research = research_type(self)(store, settings)
            entry = pool()
            research.advance({entry.key: entry}, [signal()], {}, now=START)
            settings.research_enabled = False
            later = pool(at(60), price_usd=2)
            research.advance({later.key: later}, [signal("synthetic-disabled", at(60))], {}, now=at(60))
            self.assertEqual(research.pending_pools(), [])
            self.assertEqual(len(outcomes(store)), 1)
            self.assertIsNone(decisions(store)[-1][1])
            settings.research_enabled = True
            later.observed_at = at(61)
            research.advance({later.key: later}, [signal("synthetic-disabled", at(60))], {}, now=at(61))
            self.assertEqual(research.pending_pools(), [], "a previously recorded decision cannot be backfilled")


    def test_finite_inputs_cannot_persist_overflowed_entry_or_exit_arithmetic(self):
        with Storage(scratch_dir() / "overflow-entry.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            quote = pool(price_usd=5e-324)
            research.advance({quote.key: quote}, [signal()], {}, now=START)
            self.assertEqual(research.pending_pools(), [], "infinite hypothetical quantity must be rejected")
            self.assertIsNone(decisions(store)[0][1])
        with Storage(scratch_dir() / "overflow-exit.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            quote = pool(price_usd=1e-307)
            research.advance({quote.key: quote}, [signal()], {}, now=START)
            self.assertEqual(len(research.pending_pools()), 1)
            later = pool(at(60), price_usd=100)
            research.advance({later.key: later}, [], {}, now=at(60))
            self.assertEqual(outcomes(store), [], "overflow is unavailable, never infinite profit")
            later.observed_at = at(91)
            research.advance({later.key: later}, [], {}, now=at(91))
            self.assertEqual(outcomes(store)[0]["status"], "excluded")
            self.assertIsNone(outcomes(store)[0]["pnl_usd"])
            json.dumps(research.summary(at(91)), allow_nan=False)


    def test_explicit_liquidity_loss_or_unsafe_evidence_does_not_need_an_invented_price(self):
        for changes in (dict(liquidity_usd=0, price_usd=None, safety_status="unknown"),
                        dict(liquidity_usd=None, price_usd=None, safety_status="unsafe")):
            with self.subTest(changes=changes), Storage(scratch_dir() / "known-loss.sqlite3") as store:
                research = research_type(self)(store, AvariceConfig())
                entry = pool()
                research.advance({entry.key: entry}, [signal()], {}, now=START)
                later = pool(at(60), **changes)
                research.advance({later.key: later}, [], {}, now=at(60))
                self.assertEqual(len(outcomes(store)), 1)
                self.assertEqual(outcomes(store)[0]["pnl_usd"], -5.0)
                self.assertEqual(outcomes(store)[0]["exit_model"], "conservative_writeoff_no_sell")


    def test_unknown_market_and_sender_values_remain_unknown_in_decision_snapshots(self):
        with Storage(scratch_dir() / "unknowns.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            quote = pool(price_usd=None, liquidity_usd=None, volume_24h_usd=None,
                         created_at=None, safety_status="unknown")
            prior = {("solana", "RawSender"): dict(chain="solana", address="RawSender",
                     qualified=None, completed_round_trips=None, observed_net_pnl_usd=float("nan"), coverage=None)}
            research.advance({quote.key: quote}, [signal()], prior, now=START)
            self.assertEqual(len(decisions(store)), 1, "fresh decisions retain unknown quote/risk context")
            features, trial = decisions(store)[0]
            for name in ("price_usd", "liquidity_usd", "volume_24h_usd", "volume_to_liquidity", "pool_age_minutes"):
                self.assertIsNone(features[name])
            self.assertIsNone(features["prior_sender"]["observed_net_pnl_usd"])
            self.assertIsNone(features["prior_sender"]["qualified"])
            self.assertIsNone(trial)
            report = research.summary(START)
            self.assertEqual(report.get("snapshot_count"), 2)
            self.assertEqual(report["data_health"]["safety_status_counts"]["unknown"], 2)
            self.assertEqual(report["data_health"]["quote_issues"]["invalid_market_values"], 2)
            json.dumps(report, allow_nan=False)


    def test_invalid_or_backwards_decision_times_cannot_create_prospective_evidence(self):
        with Storage(scratch_dir() / "clock.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            quote = pool()
            for invalid in ("", "not-time", float("nan")):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    research.advance({quote.key: quote}, [signal()], {}, now=invalid)
            research.advance({quote.key: quote}, [signal()], {}, now=START)
            count = store.conn.execute("SELECT COUNT(*) FROM avarice_research_snapshots").fetchone()[0]
            with self.assertRaises(ValueError):
                research.advance({quote.key: quote}, [signal("backdated", at(-1))], {}, now=at(-1))
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM avarice_research_snapshots").fetchone()[0], count)
            self.assertEqual(research.summary(at(-1))["snapshot_count"], 0)


    def test_unknown_or_nonfinite_cost_assumptions_do_not_start_trials(self):
        for cost in ("slippage_bps", "fee_bps", "gas_usd"):
            with self.subTest(cost=cost), Storage(scratch_dir() / "invalid-cost.sqlite3") as store:
                settings = AvariceConfig()
                if cost == "gas_usd":
                    settings.gas_usd["solana"] = None
                else:
                    setattr(settings, cost, float("inf"))
                research = research_type(self)(store, settings)
                quote = pool()
                research.advance({quote.key: quote}, [signal()], {}, now=START)
                self.assertEqual(research.pending_pools(), [])
                self.assertEqual(len(decisions(store)), 1)
                self.assertIsNone(decisions(store)[0][1])


    def test_summary_preserves_unrepresentable_aggregate_as_unknown_not_infinite_profit(self):
        import math
        with Storage(scratch_dir() / "aggregate.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            episode(research, 0, exit_price=3e307)
            episode(research, 120, exit_price=3e307)
            report = research.summary(at(180))
            baseline = report["baseline_comparison"]["training"]
            self.assertIsNone(baseline["net_pnl_usd"])
            self.assertTrue(math.isfinite(baseline["mean_pnl_usd"]))
            self.assertEqual(report["recommendation"], "insufficient_evidence")
            json.dumps(report, allow_nan=False)


    def test_label_retains_exact_immutable_market_observation_for_audit(self):
        with Storage(scratch_dir() / "label-audit.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            entry = pool()
            research.advance({entry.key: entry}, [signal()], {}, now=START)
            later = pool(at(60), price_usd=1.2)
            research.advance({later.key: later}, [], {}, now=at(60))
            label = outcomes(store)[0]
            self.assertEqual(label.get("market_observation"), later.to_dict())
            later.price_usd, later.observed_at, later.safety_status = 100, at(61), "unsafe"
            research.advance({later.key: later}, [], {}, now=at(61))
            self.assertEqual(outcomes(store)[0], label)


    def test_existing_transaction_is_joined_without_committing_or_erasing_history(self):
        with Storage(scratch_dir() / "atomic.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            quote = pool()
            with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
                with store.atomic():
                    research.advance({quote.key: quote}, [signal()], {}, now=START)
                    self.assertTrue(store.conn.in_transaction)
                    raise RuntimeError("synthetic rollback")
            self.assertEqual(research.pending_pools(), [])
            self.assertEqual(research.summary(START)["snapshot_count"], 0)
            with store.atomic():
                research.advance({quote.key: quote}, [signal()], {}, now=START)
                self.assertTrue(store.conn.in_transaction)
            self.assertEqual(len(research.pending_pools()), 1)
            frozen = decisions(store)[0]
            store.conn.execute("PRAGMA query_only=ON")
            report = research.summary(START)
            self.assertEqual(report["pending"], 1)
            self.assertEqual(decisions(store)[0], frozen)

    def test_twenty_positive_validation_samples_on_only_two_dates_are_insufficient(self):
        with Storage(scratch_dir() / "dates.sqlite3") as store:
            research = research_type(self)(store, AvariceConfig())
            synthetic_training(research)
            episode(research, 4320, liquidity=1000, volume=100, exit_price=0)
            for index in range(20):
                minute = 4440 + index * 120
                episode(research, minute)
            report = research.summary(at(minute + 60))
            self.assertEqual(report["selected_validation_count"], 20)
            selected = next(rule for rule in report["rules"] if rule["name"] == report["candidate_rule"])
            self.assertEqual(selected["validation"]["utc_dates"], 2)
            self.assertEqual(report["recommendation"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
