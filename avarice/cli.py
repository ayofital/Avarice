"""Deterministic CLI. No signing keys, live swaps, or model calls."""
import argparse


def parser():
    result = argparse.ArgumentParser(description="Avarice paper-only memecoin scout")
    result.add_argument("--settings", help="Local JSON settings file (default: config.json)")
    result.add_argument("--db", help="Local SQLite file (default: data/avarice.sqlite3)")
    result.add_argument("--chains", help="Comma-separated chains; overrides local settings")
    result.add_argument("--json", action="store_true", help="Print machine-readable output")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("scan", "update", "status", "positions", "wallets", "report", "config"):
        commands.add_parser(name)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    return run_command(args)


def run_command(args):
    import json
    from pathlib import Path
    import sqlite3
    import sys
    from avarice.config.settings import ROOT, load_config
    from avarice.core.engine import AvariceEngine
    from avarice.storage.db import Storage
    try:
        settings = load_config(args.settings, args.chains.split(",") if args.chains else None)
        if args.command == "config":
            print(json.dumps(settings.to_dict(), indent=2, allow_nan=False))
            return 0
        db = Path(args.db) if args.db else ROOT / "data/avarice.sqlite3"
        with Storage(db, settings.virtual_capital_usd) as store:
            engine = AvariceEngine(store, settings)
            if args.command in ("scan", "update"):
                output = engine.scan(discover=args.command == "scan")
            elif args.command == "positions":
                output = [position.to_dict() for position in store.load_portfolio().positions]
            elif args.command == "wallets":
                output = engine.wallet_metrics()
            elif args.command == "report":
                from avarice.reporting.telegram import TelegramReporter
                print(TelegramReporter(store, settings).render())
                return 0
            else:
                output = store.load_portfolio().summary()
                output["chains"] = list(settings.chains)
                output["latest_scan"] = store.latest_scan()
            print(json.dumps(output, indent=None if args.json else 2, allow_nan=False))
            return 2 if isinstance(output, dict) and output.get("errors") else 0
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(f"Avarice error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
