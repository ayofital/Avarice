"""Real-time alert script for fresh qualified buy signals."""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from avarice.config.settings import ROOT as CFG_ROOT, load_config
from avarice.storage.db import Storage
from avarice.core.engine import AvariceEngine
from avarice.chains.public import HttpClient, PublicMarketAdapter

settings = load_config()
db = CFG_ROOT / "data/avarice.sqlite3"

with Storage(db, settings.virtual_capital_usd) as store:
    client = HttpClient(settings)
    factory = lambda chain: PublicMarketAdapter(chain, client)
    engine = AvariceEngine(store, settings, adapter_factory=factory)
    alerts = engine.check_fresh_signals()

if alerts:
    for alert in alerts:
        print(f"ALERT: {alert['chain']} | {alert['symbol']} | wallet={alert['wallet'][:8]}... | "
              f"price=${alert['price_usd']:.6f} | liq=${alert['liquidity_usd']:,.0f} | "
              f"vol=${alert['volume_24h_usd']:,.0f} | age={alert['signal_age_seconds']:.0f}s")
else:
    print("No fresh qualified signals")
