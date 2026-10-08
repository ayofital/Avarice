"""A bounded paper ledger. Cost assumptions are explicit, not live quotes."""
import math
import uuid
from avarice.core.models import VirtualPosition, utcnow, parse_time


class Simulator:
    def __init__(self, portfolio, settings):
        self.portfolio = portfolio
        self.settings = settings

    def open_position(self, pool, wallet):
        settings = self.settings
        portfolio = self.portfolio
        try:
            quote_age = (parse_time(utcnow()) - parse_time(pool.observed_at)).total_seconds()
        except (ValueError, TypeError):
            return None
        if quote_age < 0 or quote_age > settings.quote_max_age_seconds:
            return None
        if (len(portfolio.open_positions) >= settings.max_concurrent_positions
                or portfolio.max_drawdown_pct >= settings.max_drawdown_pct
                or not math.isfinite(pool.price_usd) or pool.price_usd <= 0
                or not math.isfinite(pool.liquidity_usd)
                or pool.liquidity_usd < settings.min_liquidity_usd):
            return None
        if any(p.pool.chain == pool.chain and p.pool.token_address == pool.token_address for p in portfolio.positions):
            return None
        budget = min(portfolio.cash_usd, min(portfolio.equity_usd, portfolio.starting_capital_usd)
                     * settings.max_position_pct / 100)
        gas = settings.gas_usd[pool.chain.value]
        if budget < settings.min_position_usd or budget <= gas:
            return None
        impact_bps = min(1000.0, budget / max(pool.liquidity_usd / 2, 1) * 10000)
        slip = min(9999.0, settings.slippage_bps + impact_bps)
        price = pool.price_usd * (1 + slip / 10000)
        notional = (budget - gas) / (1 + settings.fee_bps / 10000)
        fee = notional * settings.fee_bps / 10000 + gas
        if not math.isfinite(notional / price) or notional / price <= 0:
            return None
        timestamp = utcnow()
        position = VirtualPosition(
            id=uuid.uuid4().hex, pool=pool, quantity=notional / price,
            entry_cost_usd=budget, entry_price_usd=price, entry_fee_usd=fee,
            entry_time=timestamp, wallet_followed=wallet,
            fee_bps=settings.fee_bps, slippage_bps=slip, gas_usd=gas,
            last_price_usd=pool.price_usd, last_quote_at=pool.observed_at,
        )
        portfolio.cash_usd -= budget
        portfolio.positions.append(position)
        portfolio.mark()
        return position

    def update_positions(self, quotes, now=None, followed_sells=None):
        timestamp = parse_time(now or utcnow())
        closed = []
        followed_sells = followed_sells or set()
        for position in self.portfolio.open_positions:
            quote = quotes.get(position.pool.key)
            try:
                age = (timestamp - parse_time(quote.observed_at)).total_seconds() if quote else float("inf")
            except (ValueError, TypeError):
                age = float("inf")
            if (quote is None or age < 0 or age > self.settings.quote_max_age_seconds
                    or not math.isfinite(quote.price_usd) or quote.price_usd < 0):
                position.quote_missing = True
                continue
            position.quote_missing = False
            position.last_price_usd = quote.price_usd
            position.last_quote_at = quote.observed_at
            position.pool = quote
            reason = None
            if quote.liquidity_usd == 0 or quote.price_usd == 0:
                reason = "liquidity_lost_writeoff"
            elif quote.price_usd <= position.entry_price_usd * (1 - self.settings.stop_loss_pct / 100):
                reason = "stop_loss"
            elif quote.price_usd >= position.entry_price_usd * (1 + self.settings.take_profit_pct / 100):
                reason = "take_profit"
            elif (timestamp - parse_time(position.entry_time)).total_seconds() >= self.settings.max_hold_hours * 3600:
                reason = "max_hold"
            elif (position.pool.chain.value, position.wallet_followed, position.pool.token_address) in followed_sells:
                reason = "followed_wallet_sell"
            if reason:
                price = 0.0 if reason == "liquidity_lost_writeoff" else quote.price_usd
                self.close_position(position, price, reason)
                closed.append(position)
        self.portfolio.mark()
        return closed

    def close_position(self, position, price, reason):
        if not position.is_open or not math.isfinite(price) or price < 0:
            return False
        gross = position.quantity * price * (1 - position.slippage_bps / 10000)
        net = max(0.0, gross * (1 - position.fee_bps / 10000) - position.gas_usd)
        position.exit_fee_usd = min(gross, gross * position.fee_bps / 10000 + position.gas_usd)
        position.exit_price_usd = price * (1 - position.slippage_bps / 10000)
        position.last_price_usd = price
        position.exit_reason = reason
        position.exit_time = utcnow()
        position.pnl_usd = net - position.entry_cost_usd
        self.portfolio.cash_usd += net
        self.portfolio.mark()
        return True