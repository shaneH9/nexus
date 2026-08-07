# ADR 0008: MBO Liquidity Credibility

## Status

Accepted

## Context

Displayed depth alone cannot distinguish quantity that rests and executes from
quantity that rapidly withdraws. Around a liquidity shock, those observable
outcomes may be useful additional research context. Market-by-price data does
not retain stable order identity, generic MBO identifiers do not identify a
participant, and lifecycle outcomes continue to arrive after shock end. Any
credibility feature therefore needs explicit data sufficiency, conservative
attribution, and availability semantics.

The existing SRA mathematics already defines raw depth, normalized aggression,
impact, replenishment ratio, recovery, aggressor effectiveness, and absorption
efficiency. This decision must not redefine those features or turn descriptive
book behavior into a trading conclusion.

## Decision

Liquidity credibility is MBO-only. MBP input returns typed unavailability. An
`OrderLifecycleTracker` observes canonical `BookEvent` values only after the
`OrderBook` or replay pipeline accepts them, so rejected events cannot create a
divergent credibility history.

Lifecycle accounting uses `ObservedAddedQuantity`: initial quantity plus only
positive absolute-quantity MODIFY deltas. A MODIFY decrease and DELETE of the
remainder are withdrawals. RESET and observation-window closure preserve
unresolved quantity and are not inferred voluntary cancellations. Execution,
withdrawal, and unresolved quantities conserve exactly; fractions are never
clamped. Exchange, process, and caller-supplied normalized-event lifetimes
remain separate.

The attacked set is the orders present immediately before shock onset at the
original top-K attacked-side absolute prices: BID for a SELL shock and ASK for a
BUY shock. Full execution differs from cancellation and is not treated as failed
survival. Order and side scores are optional, transparent deterministic
engineering priors with centralized exact weights. Every raw component remains
available.

Replenishment is modeled as a price-level episode after execution at an original
attacked price. It never claims the new order belongs to the same participant.
Exact order-level attribution is used where available. A MODIFY-up episode is
retained but marked attribution-incomplete when subsequent old and added units
cannot be distinguished.

Side aggregation is quantity weighted. `QWOC`, raw depth, credible depth,
credible-depth ratio, execution/withdrawal quantities and fractions, both order
and quantity survival, replenishment outcomes, and absorption cycles remain
separate. Credible depth is an additional experimental feature and does not
replace raw depth in prior SRA formulas.

Credibility uses an explicit configured post-shock horizon over true normalized
market-event indices. The default is 25 events. A result becomes available at
the process time of the exact observation-end reference, and transitions after
that boundary are excluded even when a later-completed lifecycle is supplied.
Shock-pair comparison may additionally expose exact `DeltaLC = LC_2 - LC_1`
when both optional scores exist; it does not alter mandatory failed-aggression
comparison features.

## Alternatives Considered

### Infer order behavior from MBP depth changes

Rejected because aggregate level changes cannot safely distinguish individual
execution, cancellation, modification, and identity.

### Treat disappearance, RESET, and execution uniformly

Rejected because executed liquidity is economically distinct from withdrawn
liquidity, while RESET leaves the terminal cause unresolved.

### Link replenishment by participant identity

Rejected because generic provider order IDs identify orders, not traders or
beneficial owners.

### Average order percentages equally

Rejected because a small order would receive the same influence as a large
displayed order. Side measures use exact quantities.

### Use all eventual lifecycle outcomes

Rejected because later execution or cancellation would leak future evidence
into a feature purportedly available at shock end or an earlier horizon.

### Replace raw SRA depth with credible depth

Rejected because doing so would silently change established normalized
aggression and resiliency mathematics before empirical validation.

## Consequences

MBO order histories can produce reproducible, auditable lifecycle and
shock-region credibility features. MBP and incomplete attribution remain
explicitly unavailable rather than receiving false precision. Full execution,
withdrawal, structural reset, and right-censoring remain distinct. Feature
availability is later than shock end whenever post-shock evidence is required.

The initial weights, taus, attack depth, burst gap, and event horizon are
engineering priors, not fitted parameters or predictive claims. MBO feed
semantics, order-ID reuse policy, and exact normalized event indices remain
upstream responsibilities. This milestone adds no toxicity, news fusion, alpha,
portfolio, execution, cost, tax, provider, or live-trading behavior.
