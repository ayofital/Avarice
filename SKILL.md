---
name: avarice
description: Use when operating a memecoin paper-trading scout.
version: 0.1.0
author: Fital719 (ayofital), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [paper-trading, solana, base, robinhood, scout]
    related_skills: []
---

# Avarice

Operate the read-only scout and persistent paper portfolio. Do not interpret
sampled trade senders as verified profitable wallet owners. No signing, live
swaps, API credentials or model inference are implemented.

## When to Use
- Continue development or inspect the virtual portfolio.
- Run a market scan, inspect sampled wallet evidence, or render a report.
- Maintain free, deterministic no-agent schedules.
- Do not use for live trading or historical profit certification.

## Prerequisites
Python 3.11+ with SQLite; no pip dependencies. Run from the repository checkout.
Read README.md and docs/ROADMAP.md before changing scope or reporting completion.
Respect protected data stores and the user's no-deletion policy.

## How to Run
Use `terminal(command="python -m avarice --json status", workdir=PROJECT_ROOT)`.
For a live scan use `terminal(command="python -m avarice --json scan", workdir=PROJECT_ROOT, timeout=240)`.
For a cached report use `terminal(command="python -m avarice report", workdir=PROJECT_ROOT)`.
Other commands: `positions`, `wallets`, `update`, `config`. Global options precede
the command: `--db`, `--settings`, `--chains`, `--json`.

## Procedure
1. Inspect Git status, roadmap and the narrow tests. Preserve existing local
   data; never reset the account to improve apparent results.
2. Run `python -m unittest discover -s tests -v` before changing behavior.
   Add a failing regression test before fixes or new features.
3. Use the real public adapters. Treat token profiles as profiles, not pairs;
   preserve chain-qualified identities and actual provider pool birth dates.
4. Check cash PLUS marked liquidation value, round-trip costs and restored
   positions. Allocate at most 10% per position and five open positions.
5. Admit only new buy signals from previously sample-qualified senders. Do not
   trade on backfill or use the current batch's profit to qualify that batch.
6. Keep scheduled collection deterministic. Write deployment wrappers under the
   active `$HERMES_HOME/scripts/` and use `cronjob_manage` with `no_agent=true`.
   List jobs before creating/updating them. Verify the exact target and run state.
7. Before publication, review staged paths. Exclude config.json, data/, DBs,
   credentials, private chat IDs, machine paths and live wallet runtime state.
8. Run tests, review the diff, and read back GitHub commit/CI state before claiming
   publication or CI success. Report unresolved acceptance criteria explicitly.

## Pitfalls
- Public APIs can return 429, null liquidity or partial recent-trade pages.
  Use conservative pacing and a small watch budget; never turn missing data into
  a zero-price exit or pretend an incomplete scan is healthy.
- Birth dates and fetch timestamps are different. Missing birth dates are not
  evidence of a new launch. Fetch recency isn't a guarantee of onchain freshness.
- Score completed inventory round trips, not partial-sale count. Deduplicate by
  event ID; transaction hashes can contain multiple swaps.
- Unmatched sells, late events, nonoverlapping pages or unmarked holdings block
  copy qualification. Incomplete history is not a zero-rug certification.
- Cash allocation is not a loss; drawdown uses total liquidation equity.
- Fees, gas and slippage are configurable assumptions, not measured live costs.
- `report` prints text only. Hermes owns delivery; command exit 0 isn't proof the
  Telegram message arrived. Check delivery outcome separately.
- The local PC and Hermes gateway must be running for scheduled work.
- Automatic internet-derived strategy learning is not implemented.

## Verification
Run the complete offline suite and module help. Exercise a real bounded scan
without resetting `data/avarice.sqlite3`; read back the persisted account and
counters. Use `cronjob_manage(action="list")` to verify no-agent scripts, targets,
paused/enabled status and delivery errors. Distinguish fixtures from live results.