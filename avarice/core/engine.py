"""Bounded read-only scans; never execute a blockchain transaction or an LLM."""
from datetime import timedelta
from avarice.core.models import Chain, parse_time, utcnow
from avarice.core.pool_watcher import PoolWatcher
from avarice.core.simulator import Simulator
from avarice.core.wallet_tracker import WalletTracker


class AvariceEngine:
    def __init__(self, storage, settings, adapter_factory=None):
        self.storage = storage
        self.settings = settings
        self.adapter_factory = adapter_factory

    def wallet_metrics(self):
        quotes = {pool.key: pool for pool in self.storage.pools()}
        return WalletTracker(self.storage, self.settings).metrics(quotes)

    def scan(self, discover=True):
        if self.adapter_factory is None:
            from avarice.chains.public import HttpClient, PublicMarketAdapter
            client = HttpClient(self.settings)
            factory = lambda chain: PublicMarketAdapter(chain, client)
        else:
            factory = self.adapter_factory
        started = utcnow()
        result = dict(timestamp=started, chains={}, observations_added=0,
                      positions_opened=0, positions_closed=0, errors=[])
        watcher = PoolWatcher(self.settings)
        with self.storage.atomic():
            simulator = Simulator(self.storage.load_portfolio(), self.settings)
            tracker = WalletTracker(self.storage, self.settings)
            previous = self.storage.latest_scan()
            watchlist = self.storage.get_state("watchlist", {})
            trade_pages = self.storage.get_state("trade_pages", {})
            quotes, events = {}, []
            names = list(dict.fromkeys([*self.settings.chains,
                          *(p.pool.chain.value for p in simulator.portfolio.open_positions)]))
            for name in names:
                chain = Chain(name)
                adapter = factory(chain)
                diagnostic_start = len(getattr(adapter, "errors", []))
                stats = dict(pools_fetched=0, eligible_pools=0, new_pools=0,
                             watched_pools=0, trade_events_fetched=0, errors=[])
                result["chains"][name] = stats
                discovered = []
                if discover and name in self.settings.chains:
                    try:
                        discovered = adapter.new_pools()
                        stats["pools_fetched"] = len(discovered)
                        for pool in discovered:
                            quotes[pool.key] = pool
                            stats["new_pools"] += self.storage.save_pool(pool)
                    except Exception as exc:
                        stats["errors"].append(f"discovery: {type(exc).__name__}: {exc}")
                eligible = [pool for pool in discovered if watcher._passes_filters(pool)]
                stats["eligible_pools"] = len(eligible)
                tracked = watchlist.setdefault(name, {})
                active = [address for address, item in tracked.items()
                          if parse_time(item["expires_at"]) > parse_time(started)][:self.settings.watched_pools_per_chain]
                for pool in sorted(eligible, key=lambda item: item.volume_24h_usd, reverse=True):
                    if len(active) >= self.settings.watched_pools_per_chain:
                        break
                    if pool.address not in active:
                        active.append(pool.address)
                        tracked[pool.address] = dict(
                            expires_at=(parse_time(started) + timedelta(hours=self.settings.watch_hours)).isoformat())
                held = [p.pool.address for p in simulator.portfolio.open_positions if p.pool.chain == chain]
                targets = list(dict.fromkeys([*active, *held]))
                stats["watched_pools"] = len(targets)
                for address in targets:
                    try:
                        pool = quotes.get((name, address)) or adapter.pool(address)
                        if pool is None:
                            stats["errors"].append(f"pool unavailable: {address}")
                            continue
                        quotes[pool.key] = pool
                        self.storage.save_pool(pool)
                        batch = adapter.trades(pool)
                        page_key = name + ":" + address
                        prior_ids = set(trade_pages.get(page_key, []))
                        current_ids = {event["id"] for event in batch}
                        if prior_ids and current_ids and not prior_ids.intersection(current_ids):
                            tracker.flag_gap(name, address)
                            batch = [{**event, "coverage_gap": True} for event in batch]
                        if current_ids:
                            trade_pages[page_key] = sorted(current_ids)
                        events.extend(batch)
                        stats["trade_events_fetched"] += len(batch)
                    except Exception as exc:
                        stats["errors"].append(f"pool {address}: {type(exc).__name__}: {exc}")
                stats["errors"].extend(getattr(adapter, "errors", [])[diagnostic_start:])
                result["errors"].extend(f"{name}: {error}" for error in stats["errors"])
            # Qualification uses only evidence accumulated BEFORE this batch.
            before = {(row["chain"], row["address"]): row for row in tracker.metrics(quotes)}
            accepted = tracker.ingest(events)
            result["observations_added"] = len(accepted)
            # Current evidence may veto integrity, never promote statistics.
            metrics = tracker.metrics(quotes)
            incomplete_senders = {(row["chain"], row["address"]) for row in metrics
                                  if any(row[field] for field in (
                                      "unmatched_sells", "late_events", "coverage_gaps", "unmarked_holdings"))}
            timestamp = parse_time(utcnow())
            signals = {}
            if previous:
                for event in accepted:
                    age = (timestamp - parse_time(event["timestamp"])).total_seconds()
                    if (0 <= age <= self.settings.signal_max_age_seconds
                            and parse_time(event["timestamp"]) > parse_time(previous["finished_at"])):
                        signals[(event["chain"], event["wallet"], event["token_address"])] = event
            sells = {key for key, event in signals.items() if event["side"] == "sell"}
            result["positions_closed"] = len(simulator.update_positions(quotes, followed_sells=sells))
            for key, event in signals.items():
                if event["side"] != "buy" or event["chain"] not in self.settings.chains:
                    continue
                prior_metrics = before.get((event["chain"], event["wallet"]))
                pool = quotes.get((event["chain"], event["pool_address"]))
                if (prior_metrics and prior_metrics["qualified"] and key[:2] not in incomplete_senders
                        and pool and watcher._passes_filters(pool)):
                    if simulator.open_position(pool, event["wallet"]):
                        result["positions_opened"] += 1
            result["wallets_tracked"] = len(metrics)
            result["wallets_qualified"] = sum(row["qualified"] for row in metrics)
            result["portfolio"] = simulator.portfolio.summary()
            result["finished_at"] = utcnow()
            result["health"] = "degraded" if result["errors"] else "ok"
            self.storage.save_portfolio(simulator.portfolio)
            self.storage.set_state("watchlist", watchlist)
            self.storage.set_state("trade_pages", trade_pages)
            self.storage.save_scan(result)
        return result