"""Forward-observed trade senders, not a complete historical profitability index.

Only fully observed inventory round trips count. Partial sales don't inflate the
trade count. Cost estimates, coverage gaps and unmarked holdings are explicit.
"""
from contextlib import nullcontext
import math
from avarice.core.models import parse_time, utcnow


class WalletTracker:
    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings

    def flag_gap(self, chain, pool_address):
        ledger = self.storage.get_state("wallet_ledger", {})
        for wallet in ledger.values():
            if wallet["chain"] == chain and any(
                    holding["pool_address"] == pool_address for holding in wallet["inventory"].values()):
                wallet["coverage_gaps"] = wallet.get("coverage_gaps", 0) + 1
        self.storage.set_state("wallet_ledger", ledger)

    def ingest(self, events):
        context = nullcontext() if self.storage.conn.in_transaction else self.storage.atomic()
        accepted = []
        with context:
            ledger = self.storage.get_state("wallet_ledger", {})
            for event in sorted(events, key=lambda e: (parse_time(e["timestamp"]), e["id"])):
                quantity, dollars = event["quantity"], event["usd_value"]
                if (event["side"] not in ("buy", "sell") or not event["wallet"]
                        or not event["token_address"] or not math.isfinite(quantity)
                        or not math.isfinite(dollars) or quantity <= 0 or dollars <= 0):
                    raise ValueError("Invalid observed trade")
                if not self.storage.record_event(event["chain"], event):
                    continue
                accepted.append(event)
                key = event["chain"] + ":" + event["wallet"]
                wallet = ledger.setdefault(key, dict(
                    chain=event["chain"], address=event["wallet"], inventory={},
                    completed_round_trips=0, wins=0, realized_net_usd=0.0,
                    matched_cost_usd=0.0, hold_hours=0.0, closed_tokens=[],
                    unmatched_sells=0, late_events=0, observations=0, last_at=None,
                ))
                wallet["observations"] += 1
                if event.get("coverage_gap"):
                    wallet["coverage_gaps"] = wallet.get("coverage_gaps", 0) + 1
                if wallet["last_at"] and parse_time(event["timestamp"]) < parse_time(wallet["last_at"]):
                    wallet["late_events"] += 1
                    continue
                wallet["last_at"] = event["timestamp"]
                token = event["token_address"]
                holding = wallet["inventory"].get(token)
                fee = dollars * self.settings.fee_bps / 10000 + self.settings.gas_usd[event["chain"]]
                if event["side"] == "buy":
                    if holding is None or holding["quantity"] <= 0:
                        holding = dict(quantity=0.0, cost=0.0, cycle_pnl=0.0,
                                       first_buy_at=event["timestamp"], pool_address=event["pool_address"])
                        wallet["inventory"][token] = holding
                    holding["quantity"] += quantity
                    holding["cost"] += dollars + fee
                    holding["pool_address"] = event["pool_address"]
                elif holding is None or quantity > holding["quantity"] * (1 + 1e-8):
                    wallet["unmatched_sells"] += 1
                else:
                    quantity = min(quantity, holding["quantity"])
                    basis = holding["cost"] * quantity / holding["quantity"]
                    pnl = max(0.0, dollars - fee) - basis
                    wallet["realized_net_usd"] += pnl
                    wallet["matched_cost_usd"] += basis
                    holding["cycle_pnl"] += pnl
                    holding["quantity"] -= quantity
                    holding["cost"] -= basis
                    if holding["quantity"] <= max(1e-12, quantity * 1e-8):
                        holding["quantity"] = 0.0
                        holding["cost"] = 0.0
                        wallet["completed_round_trips"] += 1
                        wallet["wins"] += holding["cycle_pnl"] > 0
                        wallet["hold_hours"] += max(0.0, (
                            parse_time(event["timestamp"]) - parse_time(holding["first_buy_at"])
                        ).total_seconds() / 3600)
                        if token not in wallet["closed_tokens"]:
                            wallet["closed_tokens"].append(token)
            self.storage.set_state("wallet_ledger", ledger)
        return accepted

    def metrics(self, quotes):
        output = []
        now = parse_time(utcnow())
        for wallet in self.storage.get_state("wallet_ledger", {}).values():
            unrealized = 0.0
            missing = 0
            for token, holding in wallet["inventory"].items():
                if holding["quantity"] <= 0:
                    continue
                quote = quotes.get((wallet["chain"], holding["pool_address"]))
                age = (now - parse_time(quote.observed_at)).total_seconds() if quote else float("inf")
                if quote is None or age < 0 or age > self.settings.quote_max_age_seconds:
                    missing += 1
                    unrealized -= holding["cost"]
                elif quote.liquidity_usd == 0:
                    # Explicit liquidity loss has no modeled liquidation proceeds.
                    unrealized -= holding["cost"]
                else:
                    gross = holding["quantity"] * quote.price_usd * (1 - self.settings.slippage_bps / 10000)
                    net = max(0.0, gross * (1 - self.settings.fee_bps / 10000)
                              - self.settings.gas_usd[wallet["chain"]])
                    unrealized += net - holding["cost"]
            count = wallet["completed_round_trips"]
            win_rate = wallet["wins"] / count if count else 0.0
            hold = wallet["hold_hours"] / count if count else 0.0
            result = {
                "chain": wallet["chain"], "address": wallet["address"],
                "completed_round_trips": count, "distinct_closed_tokens": len(wallet["closed_tokens"]),
                "win_rate": win_rate, "avg_hold_hours": hold,
                "observed_net_pnl_usd": wallet["realized_net_usd"],
                "sampled_unrealized_pnl_usd": unrealized,
                "sampled_total_pnl_usd": wallet["realized_net_usd"] + unrealized,
                "unmatched_sells": wallet["unmatched_sells"], "late_events": wallet["late_events"],
                "coverage_gaps": wallet.get("coverage_gaps", 0),
                "unmarked_holdings": missing, "observations": wallet["observations"],
                "last_active": wallet["last_at"], "coverage": "sampled_public_pool_trades_only",
                "owner_verification": "unverified_sender", "safety_status": "unverified",
            }
            reasons = []
            if count < self.settings.min_wallet_trades:
                reasons.append("insufficient_completed_round_trips")
            if len(wallet["closed_tokens"]) < self.settings.min_wallet_tokens:
                reasons.append("insufficient_distinct_tokens")
            if win_rate < self.settings.min_wallet_win_rate:
                reasons.append("win_rate_below_threshold")
            if hold > self.settings.max_wallet_hold_hours:
                reasons.append("hold_time_above_threshold")
            if result["sampled_total_pnl_usd"] <= 0 or wallet["realized_net_usd"] <= 0:
                reasons.append("not_sample_profitable_after_estimated_costs")
            if wallet["unmatched_sells"] or wallet["late_events"] or wallet.get("coverage_gaps", 0) or missing:
                reasons.append("incomplete_inventory_or_coverage")
            result["qualification_reasons"] = reasons
            result["qualified"] = not reasons
            result["score"] = round(win_rate * 60 + min(count / 100, 1) * 20
                                     + min(max(result["sampled_total_pnl_usd"], 0) / 100, 1) * 20, 2)
            output.append(result)
        return sorted(output, key=lambda row: (row["qualified"], row["score"]), reverse=True)