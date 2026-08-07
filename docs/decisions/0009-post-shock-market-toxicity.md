# ADR 0009: Post-Shock Market-Side Toxicity

## Status

Accepted

## Context

Repeated aggressive flow can remain persistently directional even while the
attacked side replenishes. Strong replenishment therefore does not, by itself,
establish a reversal. SRA-Nexus needs observable features for flow persistence,
repeated shock direction, changing directional impact, incomplete recovery,
displayed-liquidity withdrawal, spread response, and short-horizon volatility
without changing the already established SRA equations.

These measurements cannot identify an informed trader, participant intent,
manipulation, insider activity, or illegal conduct. Some required evidence
arrives after shock end, so any combined result must own exact normalized-event
windows and causal process-time availability. News context may later matter but
is not yet part of market-side toxicity.

## Decision

Version 1 is post-shock only and is named `toxicity-v1`. Pure exact-Decimal
functions implement each equation separately from typed contracts and focused
orchestration. `ToxicityService` produces either one complete immutable
`ToxicityVector` or typed `ToxicityUnavailable`; mandatory components are never
partially fabricated.

Signed flow uses one value per true normalized market event:

```text
OF_j = BUY_j - SELL_j
FP = abs(sum(OF_j)) / sum(abs(OF_j))
```

UNKNOWN volume remains excluded from signed flow and appears in explicit
unknown-share and directional-coverage diagnostics. A configurable inclusive
neutral tolerance assigns BUY, SELL, or NEUTRAL to the signed net flow. Epsilon
guards only zero denominators; positive natural denominators are used exactly.

Shock persistence uses the latest configured number of qualifying shocks with
BUY `= +1` and SELL `= -1`. The latest contiguous same-direction suffix becomes
an explicit `ShockRun` with IDs, clocks, and caller-supplied event-index span.

Impact escalation is calculated only from an existing comparable same-direction
shock pair at the configured horizon. Signed `DeltaDI`, absolute magnitude
ratio, and existing `DeltaAE` remain separate. The bounded impact component is
zero for a non-positive current directional impact and otherwise equals current
positive impact divided by prior absolute impact plus current positive impact.
This preserves evidence against aggressor effectiveness rather than allowing an
absolute value to reverse its meaning.

Canonical RR remains unchanged. Raw replenishment failure is `1 - RR`, including
negative values under over-recovery. Only a separate bounded transform,
`1 - min(max(RR, 0), 1)`, enters the optional composite. When LC is valid for the
same shock and horizon, `RR * LC` and `RR * (1 - LC)` are optional transparent
interactions. Missing LC never receives a substituted value.

Liquidity additions, withdrawals, and executions come from accepted MBO
lifecycle transitions at the original pre-shock top-K absolute prices. SELL
attacks BID and BUY attacks ASK; the opposite side is retained independently.
Same-price MODIFY uses its absolute quantity delta. A price-changing MODIFY is
an old-level withdrawal and new-level addition where those fixed prices are in
scope. Execution is never a withdrawal. Both sides expose raw additions,
withdrawals, executions, NLP, and NNLP. The composite withdrawal component uses
only attacked-side additions and withdrawals.

Spread baseline is the exact median of the previous configured valid snapshots.
A one-sided book returns unavailable spread rather than an invented extreme.
Volatility is the unannualized RMS of arithmetic midprice returns over exact
pre- and post-shock event windows. Raw spread and volatility values and ratios
remain available; nonnegative ratios used in the score are bounded by
`x / (1 + x)`.

The optional toxicity score is a configurable exact convex combination with
initial weights 0.20 flow, 0.15 shock persistence, 0.15 impact, 0.15
replenishment failure, 0.10 spread, 0.10 volatility, and 0.15 withdrawal. The
weights must sum exactly to one. They are engineering priors, not fitted values,
predictive probabilities, alpha estimates, or trading decisions.

Every window uses caller-supplied normalized event indices. Sequence numbers
and trade counts are not substituted. The complete result becomes available at
the latest process time among all included evidence. RESET, sequence corruption,
or a known data gap invalidates any component or latest-shock-run span that
crosses its event index. Optional pair comparison is exact
`DeltaToxicity = Toxicity_2 - Toxicity_1` and remains outside the mandatory
failed-aggression comparison contract.

## Alternatives Considered

### Treat strong replenishment as sufficient reversal evidence

Rejected because repeated directional aggression may eventually overwhelm
apparently resilient liquidity. Toxicity remains context alongside canonical
resiliency rather than changing RR.

### Collapse all observations immediately into one opaque score

Rejected because signs, units, coverage, missingness, and individual failure
modes would become unauditable. Raw and bounded components remain independently
available.

### Use absolute current impact as toxicity

Rejected because movement against the aggressor could then appear increasingly
toxic. The signed impact change is retained and the bounded component drops to
zero for non-positive current directional impact.

### Treat executions as cancellations or withdrawal

Rejected because consumed displayed liquidity is observably distinct from
withdrawn displayed liquidity. Both are measured separately from accepted MBO
transitions.

### Infer exact event windows from sequence numbers

Rejected because sequence domains can be provider-specific and need not equal
the count of all normalized market events.

### Include news in the first score

Deferred because causal NewsState/SRA fusion is a later milestone. The original
news-intensity and news-uncertainty concepts remain architectural context, not
fabricated Milestone J inputs.

### Implement both at-shock and post-shock toxicity

Deferred to keep their availability semantics disjoint. Version 1 is explicitly
post-shock and cannot be used at shock end when response events are required.

## Consequences

Research can audit whether persistent flow, repeated shocks, escalating impact,
weak recovery, withdrawal, spread, and volatility moved independently or
together. Exact components are deterministic, bounded score inputs are
transparent, and missing evidence remains typed. Historical replay cannot see a
vector until its latest required process time.

The implementation requires MBO lifecycle data for liquidity-flow components,
explicit event indices, a valid comparable pair for impact escalation, and
complete spread and midprice windows. These constraints intentionally make the
complete vector unavailable when evidence is insufficient. Initial windows,
weights, and transforms are engineering priors requiring future empirical
evaluation. No news fusion, alpha, regime ML, portfolio, execution, cost, tax,
provider, brokerage, or live-trading functionality is added.
