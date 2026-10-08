"""Compatibility name for read-only public EVM markets, including Robinhood."""
from avarice.chains.public import HttpClient, PublicMarketAdapter
from avarice.core.models import Chain


class EVMAdapter(PublicMarketAdapter):
    def __init__(self, chain: Chain, client=None, *, settings=None):
        chain = Chain(chain)
        if chain is Chain.SOLANA:
            raise ValueError("EVMAdapter requires an EVM chain")
        if client is None:
            if settings is None:
                from avarice.config.settings import AvariceConfig
                settings = AvariceConfig()
            client = HttpClient(settings)
        super().__init__(chain, client)