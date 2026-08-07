# ADR 0006: Event-Horizon Shock and Resiliency Primitives

## Context

SRA-Nexus now reconstructs deterministic MBO state, preserves three market-data
clocks, and separates book execution mutation from trade-flow volume ownership.
The next research layer must test the original Shock-Resiliency Asymmetry thesis
without introducing a statistical shock model, news-driven orders, or trading
logic.

The initial primitives need explicit answers to several ambiguous questions:

* whether response horizons count events or milliseconds;
* whether an unclassified execution is forced into BUY or SELL flow;
* whether primary replenishment uses raw or weighted depth;
* whether replenishment above the pre-shock baseline is truncated;
* whether shock thresholds are statistically calibrated; and
* whether a post-shock rank level is assumed to be the same price as its
  pre-shock rank.

## Decision

Milestone G is an immutable, exact-arithmetic derived-feature layer under
`sra_nexus.sra`. It consumes normalized/reconciled market observations,
`BookEvent.EXECUTE` transition boundaries, and `BookSnapshot` state. It has no
persistence, provider, news, alpha, allocation, execution, cost, or tax
dependency.

Event horizons are primary. An impact or recovery horizon of `h` selects the
`h`-th normalized market event after shock end. Exchange- and process-time
elapsed seconds remain separate metadata. This keeps response sampling robust
to changing message intensity and preserves observable-time analysis without
confusing it with market time.

UNKNOWN aggression stays UNKNOWN. The established reconciliation policy alone
selects economic-execution volume owners. Matching book/trade observations
produce one trade-owned volume. A book-only owner has no authoritative
aggressor field and remains UNKNOWN. Unresolved dual observations own no volume
until a future explicit source policy can decide them.

Normalized aggression reuses existing weighted depth. Primary replenishment
ratio instead uses raw aggregate depth over configured current-rank K levels:

```text
D0 = pre-shock attacked-side raw K-level depth
D_min = minimum of D0 and the explicitly supplied depletion snapshots
RR(h) = (Depth(t_end+h) - D_min) / (D0 - D_min)
```

This separates shock normalization from the economically transparent question
of how much displayed quantity returned. A zero depletion denominator is
unavailable. RR values above 1 are preserved because they identify a book that
became deeper than its pre-shock baseline; truncation would discard research
information.

Multi-level depletion/recovery uses the original absolute attacked-side prices
from the pre-shock snapshot. Current rank may refer to a different price after a
shock, so treating rank identity as price identity would misattribute
replenishment. Levels that did not deplete have unavailable level RR.

Shock classification uses explainable inclusive engineering thresholds with
versioned configuration. No rolling z-score, fitted coefficients, or statistical
shock probability is implemented. Raw components remain available so later
calibration can be performed on historical distributions rather than fixtures.

## Alternatives Considered

### Millisecond-Only Horizons

Rejected as the primary definition because market activity varies sharply and
the original strategy studies market response after aggressive events. Clock
elapsed values are still retained for comparison.

### Infer Aggressor Side from Resting Book Side

Rejected for the initial generic contract. Some provider-normalized feeds may
support that policy later, but silently doing it here would turn unavailable
classification into false directional evidence.

### Weighted Depth for Primary RR

Rejected because weighted depth is already the explicit aggression
normalization denominator. Raw K-level depth makes returned quantity and
over-recovery directly interpretable. A separately named weighted-resiliency
feature can be added later.

### Clamp RR to the Unit Interval

Rejected because `RR > 1` is valid over-recovery, not invalid input. Clamping
would erase a potentially important distinction in the original hypothesis.

### Calibrate a Shock Score Now

Rejected because the repository does not yet contain the historical
distribution infrastructure needed for rolling z-scores or defensible fitted
thresholds. Fixture-tuned calibration would be premature.

### Compare Relative Rank Levels Across Time

Rejected for per-level attribution because rank identity can move to a different
absolute price after depletion. Original-price-level recovery answers whether
liquidity returned to the prices that were actually attacked.

## Consequences

The shock/recovery math remains deterministic, auditable, provider-independent,
and reproducible under explicit versions. Exact unavailable states prevent
missing futures, zero denominators, and unknown flow from becoming fabricated
zeros or infinities. Event and clock recovery can be compared without mixing
units.

Callers must provide a correctly bounded aggression episode, the matching
pre-window snapshot, atomic execution transition boundaries, within-episode
depletion snapshots, and ordered post-shock market-state observations. This is
more explicit than a stateful streaming engine, but it prevents hidden window
and baseline choices during the research phase.

Statistical shock calibration, persistence, live streaming, shock-pair
comparison, aggressor-effectiveness deltas, absorption-efficiency deltas,
liquidity credibility, toxicity, news fusion, alpha, allocation, and execution
remain deliberately deferred.
