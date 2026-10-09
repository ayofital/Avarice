# Prospective pattern research and contract screening

## Execution boundary

Paper only, anonymous read-only data, no signing, purchases or runtime LLM calls.
The account is not reset to improve apparent performance. Research samples are
not account trades and do not debit the $50 portfolio. Existing wallet evidence,
cost and drawdown/position gates remain in force.

## Learning acceptance criteria

- Capture decision-time contract/pool identity, pool birth age, volume, liquidity,
  rolling-volume/liquidity changes and prior observed-sender context.
- Retain both positive and negative outcomes and immutable source features.
- Evaluate fresh signals prospectively; no retroactive trials at historical prices.
- Measure a fixed-horizon hypothetical $5 entry after estimated entry/exit costs.
- Mark missing/stale/late/security-unknown exit evidence as unavailable rather
  than fabricate an executable fill. Explicit fresh zero liquidity or unsafe
  transfer evidence can receive a labelled conservative full-cost writeoff.
- Predeclare a small set of fixed volume/liquidity hypotheses. Select using earlier
  training data, purge outcomes overlapping the validation boundary, and validate
  only on later entries. Do not choose a hypothesis using validation performance.
- Require sufficient selected training and validation samples and multiple
  validation days before recommending a change. Preserve failed experiments.
- Recommendations do not automatically change copy gates, risk limits or trading
  rules. A recommendation is not proof of future profitability.
- Persist all snapshots/samples; do not delete history or rewrite entry features.

## Risk-screening acceptance criteria

- Bind evidence to the exact chain and token/mint address, never the ticker.
- Preserve unknown/missing fields as unknown, including empty tax strings.
- Block unsafe or unknown evidence from authorizing a paper entry; reject stale,
  future-dated, wrong-token/chain or malformed screening results.
- EVM checks include provider honeypot/transfer restrictions, source/proxy/admin
  controls and taxes. Taxed tokens are excluded by the current cost model rather
  than automatically accused of being honeypots.
- Solana checks cover reported mint/freeze, balance/transfer authority and
  transfer-fee/hook controls. A pass is limited authority screening, not a
  complete buy/sell simulation or issuer-authenticity verification.
- Keep evidence history, provider, reasons, missing checks and received-at time.
- Cache briefly and bound provider calls. An unavailable endpoint is not safety.
- Do not equate a limited pass with 'legit', liquidity locked, verified ownership,
  a clean rug history, or a guaranteed sell. Project identity requires separate
  authoritative contract-address corroboration; popularity is not corroboration.

## Data limits

New-pool feeds are first-page observations, not complete detection of every early
contract. Rolling 24-hour volume changes are not exact interval trading volume.
Senders can be routers/bots, and public trade pages can miss transfers/events.
Quoted prices and modeled costs are not guaranteed execution. Safety providers
can miss address-specific restrictions, dynamic controls and changing state.

Implementation and deployment must be verified independently before this design
is reported as an operational learning system. Synthetic fixture outcomes must
never be inserted into or reported as the real account's results.
