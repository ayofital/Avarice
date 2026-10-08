"""Pool eligibility is market hygiene, not a token-safety certification."""
import math
from avarice.core.models import parse_time, utcnow


class PoolWatcher:
    def __init__(self, settings):
        self.settings = settings

    def _passes_filters(self, pool):
        if not pool.address or not pool.token_address or not pool.created_at:
            return False
        numbers = (pool.price_usd, pool.liquidity_usd, pool.volume_24h_usd)
        if any(not math.isfinite(number) for number in numbers):
            return False
        try:
            age = (parse_time(utcnow()) - parse_time(pool.created_at)).total_seconds() / 3600
        except (ValueError, TypeError):
            return False
        return (0 <= age <= self.settings.max_pool_age_hours
                and pool.price_usd > 0
                and pool.liquidity_usd >= self.settings.min_liquidity_usd
                and pool.volume_24h_usd >= self.settings.min_volume_usd)