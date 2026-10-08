# Accepted scope and next milestones

## Phase 0 — free, local paper scout

Implemented and exercised locally:
- Standard-library CLI and persistent $50 virtual account.
- Public pool/quote/recent-trade adapters for Solana, Base and Robinhood.
- Optional Ethereum/BSC adapter mapping; fixture-tested, not live-verified yet.
- Observed sender scoring, inventory reconciliation, deduplication and coverage gates.
- Prior-evidence/fresh-signal copy simulation with modeled round-trip costs.
- Stop/take-profit/max-hold exits, conservative missing-quote handling and risk caps.
- Cached 12-hour trade/performance reports at 08:00/20:00, no-agent scripts and job declarations.
- Offline unit/integration suite; synthetic results never enter the real paper account.

Release acceptance still requires verifying the current independent review,
GitHub commit and CI, and the exact enabled local schedules/delivery outcome.
Those are deployment facts: don't infer them from this document.

## Known limits

Anonymous feeds sometimes rate-limit or omit liquidity. Page-limited pool data
is not complete launch coverage or full wallet history. Polling gaps disqualify
senders, and one watched pool per chain limits cross-token evidence. No qualifying
sender or paper trade is promised on startup. Sender ownership and token safety
are not verified. Modeled fee/gas/slippage assumptions are not real fill quotes.

## Next milestone — evidence quality

- Add verified zero-cost fallback data sources without treating token profiles as pairs.
- Measure stale data, polling gaps, request budgets and missed launches.
- Extend inventory reconciliation to transfers and router/sender attribution.
- Compare sampled sender scores with complete independently retrievable history.
- Increase coverage only within measured free endpoint budgets.
- Study observed entry liquidity, holding duration and sender entry/exit patterns
  against subsequent paper returns with costs and later-time validation. No
  automatic strategy promotion or always-profitable claim is authorized.

## Later — strategy laboratory

Research internet-derived tactics with citations and explicit executable rules.
Evaluate fixed versions with latency/cost assumptions, unseen time windows,
no lookahead and no account resets. Promote nothing merely because a blog or
training sample claims high returns. Automatic learning is not implemented.

## Deferred

NEAR/Fomo expansion, verified safety analysis and any live execution. Real funds,
signing keys, purchases and live transactions require a new explicit scope;
this repository currently implements none of them.
