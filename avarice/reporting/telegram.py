"""Plain-text report formatter. Delivery belongs to Hermes no-agent cron.

This module doesn't read a bot token, send a message, or claim delivery success.
"""
from datetime import timedelta
from avarice.core.models import parse_time, utcnow
from avarice.core.wallet_tracker import WalletTracker


def clean_label(value, limit=24):
    return "".join(character for character in str(value) if character.isprintable())[:limit]


def format_position_line(position, current_price=None):
    pnl = position.liquidation_value_usd - position.entry_cost_usd if position.is_open else position.pnl_usd
    state = "holding" if position.is_open else position.exit_reason
    return (f"{clean_label(position.pool.symbol)} ({position.pool.chain.value}) | "
            f"cost ${position.entry_cost_usd:.2f} | net PnL ${pnl:+.2f} | {state}")


class TelegramReporter:
    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings

    def render(self):
        generated = utcnow()
        now = parse_time(generated)
        cutoff = now - timedelta(hours=12)
        portfolio = self.storage.load_portfolio()
        summary = portfolio.summary()
        scan = self.storage.latest_scan()
        quotes = {pool.key: pool for pool in self.storage.pools()}
        wallets = WalletTracker(self.storage, self.settings).metrics(quotes)
        win = f"{summary['win_rate']:.1%}" if summary['win_rate'] is not None else "n/a (no closed trades)"
        stale = sum(position.quote_missing or
                    (now - parse_time(position.last_quote_at)).total_seconds()
                    > self.settings.quote_max_age_seconds for position in portfolio.open_positions)
        lines = [
            "Avarice | PAPER ONLY", f"Generated: {generated}",
            f"Virtual start: ${portfolio.starting_capital_usd:.2f}",
            f"Cash: ${portfolio.cash_usd:.2f} | Estimated liquidation equity: ${portfolio.equity_usd:.2f}",
            f"Net PnL: ${portfolio.total_pnl_usd:+.2f} ({summary['roi_pct']:+.2f}%)",
            f"Closed trades: {summary['closed_trades']} | Win rate: {win}",
            f"Open positions: {summary['open_positions']} | Missing/stale quotes: {stale}",
            f"Estimated fees paid: ${summary['fees_paid_usd']:.2f} | Max drawdown: {portfolio.max_drawdown_pct:.2f}%",
            f"Tracked senders: {len(wallets)} | Sample-qualified: {sum(w['qualified'] for w in wallets)}",
            f"Copy gate: {self.settings.min_wallet_trades} completed observed round trips, "
            f"{self.settings.min_wallet_tokens} tokens, win rate >= {self.settings.min_wallet_win_rate:.0%}.",
        ]
        entries = sorted(
            (position for position in portfolio.positions
             if cutoff < parse_time(position.entry_time) <= now),
            key=lambda position: parse_time(position.entry_time), reverse=True)
        exits = sorted(
            (position for position in portfolio.closed_positions
             if cutoff < parse_time(position.exit_time) <= now),
            key=lambda position: parse_time(position.exit_time), reverse=True)
        lines.extend(["", f"Last 12 hours (UTC): {cutoff.isoformat()} to {generated}",
                      f"Paper entries: {len(entries)} | Paper exits: {len(exits)} | "
                      f"Realized net PnL: ${sum(position.pnl_usd for position in exits):+.2f}"])
        if not entries and not exits:
            lines.append("No paper trades in this reporting window.")
        if entries:
            lines.append(f"Entries (showing {min(5, len(entries))} of {len(entries)}):")
            for position in entries[:5]:
                lines.append(f"BUY {clean_label(position.pool.symbol)} ({position.pool.chain.value}) | "
                             f"{position.entry_time} | qty {position.quantity:.6g} | "
                             f"sim price ${position.entry_price_usd:.8g} | debit ${position.entry_cost_usd:.2f}")
        if exits:
            lines.append(f"Exits (showing {min(5, len(exits))} of {len(exits)}):")
            for position in exits[:5]:
                lines.append(f"SELL {clean_label(position.pool.symbol)} ({position.pool.chain.value}) | "
                             f"{position.exit_time} | {format_position_line(position)}")
        if scan:
            lines.extend(["", f"Latest scan: {scan['finished_at']} | Health: {scan['health']}"])
            for chain, stats in scan["chains"].items():
                lines.append(f"{chain}: {stats['pools_fetched']} fetched, {stats['new_pools']} first-seen, "
                             f"{stats['eligible_pools']} eligible, {stats['watched_pools']} watched")
            lines.append(f"New trade observations: {scan['observations_added']} | "
                         f"Paper opened/closed: {scan['positions_opened']}/{scan['positions_closed']}")
            risk = scan.get("security")
            if risk:
                lines.append(f"Screened pool quotes (cached): limited {risk['limited_checks_passed']} | "
                             f"unsafe {risk['unsafe']} | unknown {risk['unknown']}")
            if scan["errors"]:
                lines.append(f"Data errors: {len(scan['errors'])} (showing up to 3)")
                lines.extend(clean_label(error, 140) for error in scan["errors"][:3])
        else:
            lines.extend(["", "No scans recorded; observation has not begun."])
        if portfolio.open_positions:
            opened = sorted(portfolio.open_positions,
                            key=lambda position: parse_time(position.entry_time), reverse=True)
            lines.extend(["", f"Open paper positions (showing {min(5, len(opened))} of {len(opened)}):"])
            lines.extend(format_position_line(position) for position in opened[:5])
        from avarice.core.research import PatternResearch
        research = PatternResearch(self.storage, self.settings).summary(now=generated)
        lines.extend(["", f"Research only: {research['labelled']} labelled | {research['pending']} pending | "
                      f"{research['excluded']} excluded | {research['snapshot_count']} snapshots",
                      f"Rule evaluation: {research['recommendation']} | "
                      f"selected training/validation: {research['selected_training_count']}/{research['selected_validation_count']}",
                      "Hypothetical research outcomes are not paper-account trades; no automatic rule promotion.",
                      "Sampled public-pool data is not proof of profitability or complete wallet history.",
                      "Limited checks are not proof of tradability or issuer authenticity.",
                      "Fees, slippage and gas are assumptions; sender ownership remains unverified.",
                      "No real funds, live swaps or runtime model calls."])
        return "\n".join(lines)