# Avarice

Read-only memecoin scouting and a persistent **$50 paper portfolio**.
No real money, wallet signing, live swaps, paid APIs, or LLM calls in the runtime.

## Current scope

- Solana, Base and Robinhood Chain enabled by default.
- Ethereum and BSC adapters available as optional secondary chains.
- Public GeckoTerminal new-pool discovery, exact-pool quotes and recent swaps.
- Observed sender inventory, cost-adjusted sampled PnL, deduplication and coverage gates.
- Fresh-signal paper copying after sufficient prior evidence; persistence across restarts.
- Plain-text reports and optional Hermes script-only scheduling.

Robinhood Chain is an Ethereum-compatible network; its documented mainnet chain
ID is 4663. This project scouts that chain, not a Robinhood brokerage account.[1]

This is Phase 0, not a finished autonomous trading system. NEAR/Fomo integrations,
complete wallet histories, token safety auditing and automatic strategy learning
are not implemented. Read [the roadmap](docs/ROADMAP.md).

## Run

Python **3.11+**, standard library only. No API key or `pip install` is required.

```bash
python -m avarice --help
python -m avarice --json status
python -m avarice --json scan
python -m avarice wallets
python -m avarice positions
python -m avarice report
python -m avarice --json update
python -m unittest discover -s tests -v
```

`report` renders cached state; it does not claim to send a message. `update`
refreshes watched/held pools without discovering new ones. Global options go
before the command:

```bash
python -m avarice --chains solana,base,robinhood,ethereum,bsc --json scan
python -m avarice --db data/experiment.sqlite3 --json status
python -m avarice --settings config.json --json scan
```

The default account is `data/avarice.sqlite3`, anchored to the checkout rather
than the shell's working directory. Set local overrides in `config.json` using
`config.example.json` as a starting point. Changing starting capital later does
not reset an existing account. Never reset state to improve apparent results.

## Paper accounting and risk

- Cash and estimated liquidation equity are separate; allocating cash is not a loss.
- Position budget includes entry fees/gas and is capped at 10% of the smaller of
  starting balance and current equity. At most five positions can be open.
- Estimated fees, gas, slippage and a bounded liquidity-impact approximation are
  included on entry and exit. They are **assumptions**, not measured execution costs.
- Defaults: 100 bps fee, 100 bps baseline slippage, per-chain estimated gas.
- Stop loss 30%, take profit 100%, max hold 24 hours, drawdown entry halt 20%.
- Missing or stale quotes never become invented zeroes. Reported equity can
  retain a stale mark; stale/missing quotes are flagged and cannot trigger fills.
- Explicit zero liquidity/price can cause a labelled conservative writeoff;
  this is not a claim that a real sell executed.
- Token safety and sender ownership remain unverified. Taxed tokens, honeypots,
  MEV, failed transactions and polling latency can make real results worse.

## Wallet evidence, not a historical leaderboard

The feed exposes sampled trade senders. A sender may be a router or bot; Avarice
does not certify ownership or full-wallet profit. It counts completed observed
inventory round trips, not split sales. Default copying requires:

- At least 20 completed observed round trips across three tokens.
- At least 55% sample win rate and positive sample PnL after estimated costs.
- Average sample hold no longer than six hours.
- No recorded unmatched sells, late events, coverage gaps or unmarked holdings.

Qualification uses only prior batches. The first scan is observation-only;
historical backfill never opens a position. Fresh later buys may be copied,
and fresh observed sells may close a matching paper position. A lack of
qualified senders means **no trade**, not fabricated candidates or lowered gates.

## Free operation and practical limits

Runtime collection is deterministic Python; it consumes no model tokens. Public
endpoints are anonymous but can return HTTP 429 or omit fields. Access is not a
promise of unlimited availability. Discovery uses the first page only, and recent
trades are not complete historical indexing. A scan can be degraded while still
saving valid observations.

Conservative defaults watch one pool per chain, pace requests at 6.5 seconds,
and cap a scan's HTTP lifetime at 150 seconds. A small watchlist can take days
to accumulate enough cross-token evidence. This is not a high-frequency sniper.

## Optional Telegram scheduling with Hermes

Hermes supports `no_agent=true`: scripts run on a schedule, stdout is delivered
verbatim, and no inference layer is invoked.[2] The PC and gateway must stay on.

Use templates under `scripts/` to create deployment wrappers under the active
`$HERMES_HOME/scripts/`. Configure the checkout path in those **local** wrappers;
do not publish it or a private chat target. Job declarations are in
`avarice/cron/jobs.py`: collection every 10 minutes (local output), cached report
at 08:00 and 20:00 in the scheduler's local timezone. Supply the report destination at
deployment. The report covers the last 12 hours of paper entries/exits, net
realized PnL and current holdings; detail lists are capped while totals cover the
full window. List existing jobs before creating near-duplicates, test delivery,
and check `last_status`, `last_delivery_error` and `last_delivery_unverified`.

## Contributing

Use failing regression tests before behavior changes. Tests use explicitly
synthetic fixtures and isolated state, never the main account. CI exercises the
standard-library suite on Windows/Linux, Python 3.11/3.14. Live API smoke tests
are manual so CI does not consume public quotas or manufacture market results.

Keep credentials, local DBs, chat IDs and runtime wallet data out of commits.
No live-trading extension is authorized by this codebase's paper-only scope.

## Sources

[1] https://docs.robinhood.com/chain/connecting
[2] https://hermes-agent.nousresearch.com/docs/user-guide/features/cron
