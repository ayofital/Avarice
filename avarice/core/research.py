"""Hypothetical fixed-horizon research, separate from the paper portfolio."""
from contextlib import nullcontext
from collections import Counter
from datetime import timedelta
import json
import math
from avarice.core.models import Chain, parse_time, utcnow

VERSION = "prospective_fixed_horizon_v1"
RULES = (("baseline", None, None), ("higher_liquidity", "liquidity_usd", 5000.0),
         ("high_volume_to_liquidity", "volume_to_liquidity", 0.5))
LIMITATIONS = ["Hypotheses, not proven strategies; never automatically promoted.",
               "Sampled public pools and unverified senders; incomplete market coverage.",
               "Hypothetical $5 entries; estimated costs, not executable fills.",
               "Limited screening is not a safety guarantee; writeoffs are not successful sales.",
               "Unknown outcomes are excluded, not zeroes; incomplete labels can bias comparisons.",
               "Chronological validation is descriptive evidence, not proof of future profitability."]


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return None if isinstance(value, float) and not math.isfinite(value) else value


def encode(value):
    return json.dumps(clean(value), allow_nan=False, sort_keys=True)


def time_of(value):
    try:
        return parse_time(value)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def clock(now):
    timestamp = time_of(utcnow() if now is None else now)
    if timestamp is None:
        raise ValueError("Invalid research decision time")
    return timestamp


class PatternResearch:
    """Append-only research; use only engine-vetted fresh accepted events, never stored backfill.

    All entry features/costs and later outcomes persist independently of portfolio state.
    The first admissible post-training trial freezes the candidate and chronological split.
    Baseline is a control; non-baseline hypotheses compete only on earlier known outcomes.
    """
    def __init__(self, storage, settings):
        self.storage, self.settings = storage, settings

    def _exists(self, table="avarice_research_snapshots"):
        return self.storage.conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                                         (table,)).fetchone() is not None

    def _schema(self):
        for sql in (
            """CREATE TABLE IF NOT EXISTS avarice_research_snapshots(
                id INTEGER PRIMARY KEY, chain TEXT, pool TEXT, token TEXT, entry_at TEXT,
                observed_at TEXT, event_id TEXT, features TEXT NOT NULL, trial TEXT,
                UNIQUE(chain,event_id))""",
            """CREATE TABLE IF NOT EXISTS avarice_research_outcomes(
                snapshot_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS avarice_research_selection(
                version TEXT PRIMARY KEY, payload TEXT NOT NULL)"""):
            self.storage.conn.execute(sql)

    def _pending(self):
        return self.storage.conn.execute("""SELECT s.id,s.chain,s.pool,s.token,s.trial
            FROM avarice_research_snapshots s LEFT JOIN avarice_research_outcomes o ON o.snapshot_id=s.id
            WHERE s.trial IS NOT NULL AND o.snapshot_id IS NULL ORDER BY s.id""").fetchall() if self._exists() else []

    def pending_pools(self):
        """Exact chain/pool targets needed for outstanding trials, at most one per chain."""
        return [(row[1], row[2]) for row in self._pending()]

    def _quality(self, quote, key, token, timestamp, market=True):
        if quote is None:
            return "missing_quote"
        if (getattr(quote.chain, "value", None), quote.address) != key or quote.token_address != token:
            return "wrong_identity"
        observed = time_of(quote.observed_at)
        if observed is None:
            return "invalid_quote_time"
        age = (timestamp - observed).total_seconds()
        if age < 0:
            return "future_quote"
        if age > self.settings.quote_max_age_seconds:
            return "stale_quote"
        if market and any(number(value) is None or value < 0 for value in (quote.price_usd, quote.liquidity_usd)):
            return "invalid_market_values"
        return None

    def _signal_valid(self, event, timestamp):
        observed = time_of(event.get("timestamp"))
        return (all(isinstance(event.get(key), str) and event[key].strip()
                    for key in ("id", "wallet", "pool_address", "token_address", "chain"))
                and event["chain"] in {chain.value for chain in Chain}
                and event["chain"] in self.settings.chains and event.get("side") in ("buy", "sell")
                and observed is not None and 0 <= (timestamp - observed).total_seconds() <= self.settings.signal_max_age_seconds
                and all(number(event.get(key)) is not None and event[key] > 0
                        for key in ("quantity", "usd_value", "price_usd")))

    def _features(self, quote, event, prior, timestamp):
        observed, created = time_of(quote.observed_at), time_of(quote.created_at)
        volume, liquidity = number(quote.volume_24h_usd), number(quote.liquidity_usd)
        previous = self.storage.conn.execute("""SELECT features FROM avarice_research_snapshots
            WHERE chain=? AND pool=? AND token=? AND observed_at<? AND entry_at<?
            ORDER BY observed_at DESC,id DESC LIMIT 1""", (quote.chain.value, quote.address, quote.token_address,
            observed.isoformat() if observed else "", timestamp.isoformat())).fetchone()
        previous = json.loads(previous[0]) if previous else {}
        growth = {}
        for feature, current, name in (("volume_24h_usd", volume, "volume_growth"),
                                       ("liquidity_usd", liquidity, "liquidity_growth")):
            old = number(previous.get(feature))
            growth[name] = number(current / old - 1) if (current is not None and current >= 0
                           and old is not None and old > 0 and previous.get("quote_health") is None) else None
        return dict(version=VERSION, chain=quote.chain.value, pool_address=quote.address,
                    token_address=quote.token_address, entry_at=timestamp.isoformat(), created_at=quote.created_at,
                    observed_at=quote.observed_at, source=quote.source, source_event=event,
                    sender=event.get("wallet") if event else None, prior_sender=prior,
                    pool_age_minutes=(timestamp - created).total_seconds() / 60 if created and created <= timestamp else None,
                    price_usd=number(quote.price_usd), liquidity_usd=liquidity, volume_24h_usd=volume,
                    volume_to_liquidity=number(volume / liquidity) if volume is not None and liquidity is not None and liquidity > 0 else None,
                    quote_health=self._quality(quote, quote.key, quote.token_address, timestamp),
                    safety_status=quote.safety_status, **growth)

    def _entry(self, quote, timestamp):
        gas = number(self.settings.gas_usd.get(quote.chain.value))
        fee, slip = number(self.settings.fee_bps), number(self.settings.slippage_bps)
        if gas is None or not 0 <= gas < 5 or fee is None or slip is None or not 0 <= fee < 10000 or not 0 <= slip < 10000:
            return None
        impact = min(1000.0, 5.0 / max(quote.liquidity_usd / 2, 1) * 10000)
        effective_slip = min(9999.0, slip + impact)
        notional = (5.0 - gas) / (1 + fee / 10000)
        quantity = notional / (quote.price_usd * (1 + effective_slip / 10000))
        if number(quantity) is None or quantity <= 0:
            return None
        return dict(mode="hypothetical_fixed_horizon_research", entry_at=timestamp.isoformat(),
                    due_at=(timestamp + timedelta(minutes=getattr(self.settings, "research_horizon_minutes", 60))).isoformat(),
                    deadline=(timestamp + timedelta(minutes=getattr(self.settings, "research_horizon_minutes", 60)
                              + getattr(self.settings, "research_max_label_delay_minutes", 30))).isoformat(),
                    entry_cost_usd=5.0, entry_fee_usd=notional * fee / 10000 + gas,
                    quantity=quantity,
                    fee_bps=fee, slippage_bps=slip, gas_usd=gas, entry_slippage_bps=effective_slip)

    def _snapshot(self, quote, event, prior, timestamp, trial=None):
        features = self._features(quote, event, prior, timestamp)
        return self.storage.conn.execute("""INSERT OR IGNORE INTO avarice_research_snapshots
            (chain,pool,token,entry_at,observed_at,event_id,features,trial) VALUES (?,?,?,?,?,?,?,?)""",
            (quote.chain.value, quote.address, quote.token_address, timestamp.isoformat(),
             time_of(quote.observed_at).isoformat() if time_of(quote.observed_at) else None,
             event["id"] if event else None, encode(features), encode(trial) if trial else None))

    def _settle(self, quotes, timestamp):
        for identity, chain, address, token, payload in self._pending():
            trial = json.loads(payload)
            due, deadline = parse_time(trial["due_at"]), parse_time(trial["deadline"])
            if timestamp < due:
                continue
            quote = quotes.get((chain, address))
            reason = self._quality(quote, (chain, address), token, timestamp, market=False)
            if reason is None and time_of(quote.observed_at) < due:
                reason = "before_due_quote"
            writeoff = reason is None and (number(quote.price_usd) == 0 or number(quote.liquidity_usd) == 0 or quote.safety_status == "unsafe")
            if reason is None and not writeoff:
                reason = self._quality(quote, (chain, address), token, timestamp)
            if reason is None and not writeoff and quote.safety_status != "limited_checks_passed":
                reason = "unknown_security"
            if timestamp > deadline:
                outcome = dict(status="excluded", reason=reason or "label_window_expired", pnl_usd=None)
            elif reason:
                continue
            elif writeoff:
                outcome = dict(status="labelled", reason="unsafe_writeoff" if quote.safety_status == "unsafe" else "zero_market_writeoff",
                               pnl_usd=-5.0, net_proceeds_usd=0.0, exit_fee_usd=0.0, exit_model="conservative_writeoff_no_sell")
            else:
                impact = min(1000.0, trial["quantity"] * quote.price_usd / max(quote.liquidity_usd / 2, 1) * 10000)
                slip = min(9999.0, trial["slippage_bps"] + impact)
                gross = trial["quantity"] * quote.price_usd * (1 - slip / 10000)
                if number(gross) is None:
                    continue
                net = max(0.0, gross * (1 - trial["fee_bps"] / 10000) - trial["gas_usd"])
                outcome = dict(status="labelled", reason="horizon_observation", pnl_usd=net - 5.0, exit_model="hypothetical_liquidation",
                               net_proceeds_usd=net, exit_fee_usd=min(gross, gross * trial["fee_bps"] / 10000 + trial["gas_usd"]))
            outcome.update(settled_at=timestamp.isoformat(), observed_at=quote.observed_at if quote else None,
                           market_observation=quote.to_dict() if quote else None)
            self.storage.conn.execute("INSERT INTO avarice_research_outcomes(snapshot_id,payload) VALUES (?,?)",
                                      (identity, encode(outcome)))

    def _samples(self, timestamp):
        if not self._exists():
            return []
        result = []
        for features, trial, outcome in self.storage.conn.execute("""SELECT s.features,s.trial,o.payload
            FROM avarice_research_snapshots s LEFT JOIN avarice_research_outcomes o ON o.snapshot_id=s.id
            WHERE s.trial IS NOT NULL ORDER BY s.entry_at,s.id"""):
            features, trial = json.loads(features), json.loads(trial)
            if parse_time(trial["entry_at"]) > timestamp:
                continue
            outcome = json.loads(outcome) if outcome else None
            if outcome and parse_time(outcome["settled_at"]) > timestamp:
                outcome = None
            result.append(dict(features=features, trial=trial, outcome=outcome))
        return result

    def advance(self, quotes, signals, prior_wallets, now=None):
        """Join the caller's transaction or open one; settle, snapshot, then admit fresh buys."""
        timestamp = clock(now)
        with nullcontext() if self.storage.conn.in_transaction else self.storage.atomic():
            self._schema()
            latest = self.storage.conn.execute("SELECT MAX(entry_at) FROM avarice_research_snapshots").fetchone()[0]
            if latest and parse_time(latest) > timestamp:
                raise ValueError("Research decision time cannot move backwards")
            self._settle(quotes, timestamp)
            for key, quote in sorted(quotes.items()):
                self._snapshot(quote, None, None, timestamp)
            busy = {chain for chain, _ in self.pending_pools()}
            valid = [event for event in signals if self._signal_valid(event, timestamp)]
            for event in sorted(valid, key=lambda row: (time_of(row["timestamp"]), row["chain"], row["id"])):
                quote = quotes.get((event["chain"], event["pool_address"]))
                if quote is None or quote.key != (event["chain"], event["pool_address"]) or quote.token_address != event["token_address"]:
                    continue
                trial = self._entry(quote, timestamp) if (getattr(self.settings, "research_enabled", True)
                         and self._quality(quote, quote.key, event["token_address"], timestamp) is None
                         and event["side"] == "buy" and event["chain"] not in busy
                         and quote.price_usd > 0 and quote.liquidity_usd > 0
                         and quote.safety_status == "limited_checks_passed") else None
                cursor = self._snapshot(quote, event, prior_wallets.get((event["chain"], event["wallet"])), timestamp, trial)
                if cursor.rowcount and trial:
                    busy.add(event["chain"])
                    self._select(timestamp)

    @staticmethod
    def _stats(samples, rule):
        _, feature, minimum = rule
        selected = [row for row in samples if feature is None or (
                    number(row["features"].get(feature)) is not None and row["features"][feature] >= minimum)]
        pnl = [row["outcome"]["pnl_usd"] for row in selected]
        return dict(count=len(pnl), wins=sum(value > 0 for value in pnl), losses=sum(value < 0 for value in pnl),
                    net_pnl_usd=number(sum(pnl)), mean_pnl_usd=math.fsum(value / len(pnl) for value in pnl) if pnl else None,
                    coverage=len(pnl) / len(samples) if samples else None,
                    utc_dates=len({parse_time(row["trial"]["entry_at"]).date().isoformat() for row in selected}))

    def _selection(self):
        if not self._exists("avarice_research_selection"):
            return None
        row = self.storage.conn.execute("SELECT payload FROM avarice_research_selection WHERE version=?", (VERSION,)).fetchone()
        return json.loads(row[0]) if row else None

    def _select(self, timestamp):
        if self._selection():
            return
        training = [row for row in self._samples(timestamp) if row["outcome"] and
                    row["outcome"]["status"] == "labelled" and parse_time(row["outcome"]["settled_at"]) < timestamp]
        eligible = [(self._stats(training, rule), rule) for rule in RULES[1:]]  # Baseline is the control.
        eligible = [(stats, rule) for stats, rule in eligible if stats["count"] >= getattr(self.settings, "research_min_samples", 20)]
        if eligible:
            _, rule = max(eligible, key=lambda item: item[0]["mean_pnl_usd"])
            self.storage.conn.execute("INSERT INTO avarice_research_selection(version,payload) VALUES (?,?)",
                (VERSION, encode(dict(candidate_rule=rule[0], first_validation_entry=timestamp.isoformat()))))

    def summary(self, now=None):
        """Read-only, JSON-safe evidence as known at now; never create tables or promote rules."""
        timestamp = clock(now)
        samples, selection = self._samples(timestamp), self._selection()
        snapshots = [json.loads(row[0]) for row in self.storage.conn.execute(
            "SELECT features FROM avarice_research_snapshots WHERE entry_at<=?", (timestamp.isoformat(),))] if self._exists() else []
        excluded = [row for row in samples if row["outcome"] and row["outcome"]["status"] == "excluded"]
        split = parse_time(selection["first_validation_entry"]) if selection else None
        if split and split > timestamp:
            selection, split = None, None
        labelled = [row for row in samples if row["outcome"] and row["outcome"]["status"] == "labelled"]
        training = [row for row in labelled if split is None or (parse_time(row["trial"]["entry_at"]) < split
                    and parse_time(row["outcome"]["settled_at"]) < split)]
        validation = [row for row in labelled if split and parse_time(row["trial"]["entry_at"]) >= split]
        rules = [dict(name=rule[0], feature=rule[1], minimum=rule[2], training=self._stats(training, rule),
                      validation=self._stats(validation, rule)) for rule in RULES]
        candidate = next((rule for rule in rules if selection and rule["name"] == selection["candidate_rule"]), None)
        recommendation = "insufficient_evidence"
        if (candidate and candidate["training"]["count"] >= getattr(self.settings, "research_min_samples", 20)
                and candidate["validation"]["count"] >= getattr(self.settings, "research_min_samples", 20)
                and candidate["validation"]["utc_dates"] >= getattr(self.settings, "research_min_validation_days", 3)):
            recommendation = "candidate_for_review" if (number(candidate["validation"]["net_pnl_usd"]) is not None
                and candidate["validation"]["net_pnl_usd"] > 0
                and candidate["validation"]["mean_pnl_usd"] > rules[0]["validation"]["mean_pnl_usd"]) else "no_validated_improvement"
        return dict(version=VERSION, mode="hypothetical_fixed_horizon_research",
                    pending=sum(row["outcome"] is None for row in samples), labelled=len(labelled),
                    excluded=len(excluded), excluded_reasons=dict(Counter(row["outcome"]["reason"] for row in excluded)),
                    snapshot_count=len(snapshots), data_health=dict(
                        safety_status_counts=dict(Counter(row["safety_status"] if row["safety_status"] in
                            ("limited_checks_passed", "unsafe") else "unknown" for row in snapshots)),
                        quote_issues=dict(Counter(row["quote_health"] for row in snapshots if row["quote_health"]))),
                    training_count=len(training), validation_count=len(validation),
                    purged_training_count=len(labelled) - len(training) - len(validation),
                    selected_training_count=candidate["training"]["count"] if candidate else 0,
                    selected_validation_count=candidate["validation"]["count"] if candidate else 0,
                    candidate_rule=selection["candidate_rule"] if selection else None,
                    first_validation_entry=selection["first_validation_entry"] if selection else None,
                    recommendation=recommendation, rules=rules, baseline_comparison=rules[0],
                    comparison_metric="cost_adjusted_mean_pnl_per_selected_trial",
                    limitations=list(LIMITATIONS))


