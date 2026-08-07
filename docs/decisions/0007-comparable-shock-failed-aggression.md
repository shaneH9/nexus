# ADR 0007: Comparable-Shock Failed-Aggression Features

## Context

Milestone G produces immutable directional liquidity shocks plus event-horizon
price impact, raw-depth replenishment, and recovery-time features. The core SRA
hypothesis needs to compare repeated same-direction shocks to ask whether an
aggressor is losing price-moving effectiveness while opposing liquidity is
recovering more strongly. A comparison is not meaningful merely because two
shocks share an instrument: direction, distance, data continuity, aggression
magnitude, feature horizons, and feature policy must also be compatible.

The earlier architecture expressed aggressor effectiveness as signed directional
impact divided by normalized aggression and expressed absorption conceptually as
RR divided by signed effectiveness. The signed absorption denominator can cross
or approach zero, creating unstable signs and magnitudes. The feature layer also
needs an exact definition of event distance, an unavailable-state policy, and a
pairing policy that does not generate every historical pair.

## Decision

Milestone H is a pure, immutable research-feature layer under `sra_nexus.sra`.
It consumes `LiquidityShock`, `ShockImpact`, and `ResiliencyVector` values. It
does not ingest data, persist features, fuse news or regimes, estimate alpha,
allocate capital, model costs, or emit a trade or order.

Only ordered same-instrument, same-direction shocks are comparable. Shock 2 may
not regress in exchange or process time, and both distances may not be zero.
The upstream pipeline supplies `event_distance` as the count of all normalized
market events strictly between shock 1 end and shock 2 start. Directional trade
count and sequence-number subtraction are not substitutes for this span.

Initial inclusive engineering bounds are 500 normalized events and 60 exchange
seconds. The default aggression-ratio policy requires:

```text
0.5 <= NormalizedAggression_2 / NormalizedAggression_1 <= 2.0
```

Both ratio bounds can be disabled together. They are not empirically optimized.
An explicit `RESET`, sequence corruption, or material data gap anywhere in the
span invalidates comparison. Required impact and RR horizons default to 5, 10,
25, and 50 events; required recovery thresholds default to 25%, 50%, 75%, and
100%. Missing or policy-incompatible required features make the ordinary result
incomparable. Malformed ownership and duplicate horizons remain integrity
errors.

`ShockPair` uses a deterministic order-sensitive UUIDv5 derived from shock 1 ID,
shock 2 ID, and `shock-pair-v1`. Reversing the shocks changes the identity, and a
shock cannot pair with itself. Exchange and process distances remain separate
exact Decimal seconds alongside the caller-supplied event distance.

At each required impact horizon:

```text
AE_k(h) = DirectionalPriceImpact_k(h)
          / (NormalizedAggression_k + epsilon)

DeltaAE(h) = AE_2(h) - AE_1(h)

RelativeAEChange(h) = DeltaAE(h) / (abs(AE_1(h)) + epsilon)
```

The default centralized epsilon is `0.000001`. Directional impact is signed in
the aggressor direction, so positive AE is movement with the aggressor and
negative AE is movement against it. AE has instrument price units. DeltaAE below
negative tolerance means `WEAKENING`; above positive tolerance means
`STRENGTHENING`; the inclusive band is `STABLE`. The initial tolerance is
`0.000001` instrument price units. BUY and SELL shocks use identical semantics.

Replenishment and recovery comparisons remain uncompressed:

```text
DeltaRR(h) = RR_2(h) - RR_1(h)
DeltaTau_events(q) = Tau_2_events(q) - Tau_1_events(q)
DeltaTau_exchange_seconds(q) = Tau_2_exchange_seconds(q) - Tau_1_exchange_seconds(q)
DeltaTau_process_seconds(q) = Tau_2_process_seconds(q) - Tau_1_process_seconds(q)
```

Each recovery unit receives its own faster/stable/slower interpretation. If
either shock does not reach a recorded threshold, all deltas for that threshold
are unavailable rather than replaced with a sentinel.

Milestone H does not emit the earlier signed absorption ratio. Its stable primary
representation is:

```text
AbsEffMagnitude_k(h) = RR_k(h) / (abs(AE_k(h)) + epsilon)
DeltaAbsEffMagnitude(h) = AbsEffMagnitude_2(h) - AbsEffMagnitude_1(h)
```

This has inverse-price units. Epsilon makes zero or near-zero AE finite, but a
small denominator can still produce a deliberately large value. No clamping or
winsorization is applied. The signed historical formula remains documented so
the change in representation is explicit rather than silent.

`ShockPairService` returns either one complete `FailedAggressionComparison` or
an incomparable result with typed reasons and no partial pair features. Feature
versions are `shock-pair-v1`, `aggressor-effectiveness-v1`,
`absorption-efficiency-v1`, and `failed-aggression-comparison-v1`.

The initial upstream search policy compares each shock with the most recent
prior comparable shock of the same direction. The comparison service accepts
one already selected ordered candidate and does not generate O(N^2) pairs. This
adjacent-prior choice is an initial research policy, not an empirical optimum.

## Alternatives Considered

### Compare Every Same-Instrument Shock

Rejected because opposite-direction, distant, discontinuous, or radically
different-aggression episodes do not test the intended repeated-direction
hypothesis on a comparable basis.

### Derive Event Distance from Directional Trade Count

Rejected because trade observations are only a subset of normalized market
events. The upstream all-event count is explicit and cannot be fabricated from
Milestone G trade windows.

### Use Absolute Price Impact in Aggressor Effectiveness

Rejected because it would erase reversal against the aggressor. Signed
directional impact preserves symmetric BUY/SELL meaning and permits negative AE.

### Use One Horizonless Effectiveness Scalar

Rejected because impact and replenishment evolve through event time. Every AE,
DeltaAE, RR delta, and absorption value retains its horizon.

### Emit Signed Absorption Efficiency

Rejected for the implemented primary feature because `AE + epsilon` can cross
zero, reverse sign, or create a singular ordering. Magnitude normalization keeps
the denominator positive while retaining the signed AE feature separately.

### Clamp Near-Zero Absorption Values

Rejected because an arbitrary fixture-era cap would silently alter the research
distribution. Later statistical modeling may adopt a versioned winsorization or
robust transformation policy using historical evidence.

### Generate All Historical Shock Pairs

Rejected as premature and potentially quadratic. Adjacent prior comparable
same-direction shocks provide a deterministic initial lag policy.

### Convert the Comparison into a Trading Signal

Rejected because `DeltaAE < 0`, strengthening RR or recovery, and increasing
absorption are hypotheses requiring statistical validation. Alpha, allocation,
risk, and execution remain separate architectural layers.

## Consequences

Failed-aggression features are deterministic, exact-arithmetic, horizon-specific,
direction-symmetric, and reproducible under explicit versions. Researchers can
attribute comparison availability and inspect every raw component rather than
receiving an opaque score. Negative AE and RR above one remain valid, while
missing features and unreached recovery stay distinct from zero.

The upstream research pipeline must supply a trustworthy all-normalized-event
span, select adjacent candidate shocks, and aggregate structural-break knowledge
across that span. The current service does not discover pairs, infer market
regimes, or independently detect a reset between already materialized shocks.

Magnitude absorption can be very large near zero effectiveness. That behavior is
finite and intentional but will require explicit distributional treatment in a
future statistical milestone. No liquidity credibility, toxicity, news fusion,
regime fusion, alpha, portfolio, execution, cost, tax, storage, or live-trading
functionality is introduced by this decision.
