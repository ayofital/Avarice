"""Compatibility name for the synchronous, read-only Solana public adapter."""
from avarice.chains.public import HttpClient, PublicMarketAdapter
from avarice.core.models import Chain


class SolanaAdapter(PublicMarketAdapter):
    def __init__(self, client=None, *, settings=None):
        if client is None:
            if settings is None:
                from avarice.config.settings import AvariceConfig
                settings = AvariceConfig()
            client = HttpClient(settings)
        super().__init__(Chain.SOLANA, client)