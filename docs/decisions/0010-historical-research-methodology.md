# ADR 0010: Causal Historical Research and Block-Permutation Methodology

## Status

Accepted

## Context

SRA-Nexus now has deterministic market-data reconstruction and descriptive SRA
features, including shock pairs, aggressor effectiveness, resiliency,
absorption, liquidity credibility, and market-side toxicity. Those features are
plausible research hypotheses, but plausibility and an in-sample pattern do not
establish predictive information.

Market observations are not independent and identically distributed. They have
serial dependence, volatility and shock clustering, intraday structure,
overlapping forward-return labels, regimes, and cross-sectional dependence.
Features also become available at different process times because several use
post-shock evidence. A random train/test split, a shock-time label anchor, or an
unrestricted row shuffle can therefore manufacture lookahead or misleading
significance.

Milestone K needs a deterministic, falsifiable evaluation substrate before any
alpha model, portfolio optimizer, cost model, or execution simulator is built.

## Decision

We will represent one immutable `ResearchObservation` as a logically separate
feature snapshot and collection of gross future labels. Each included feature
family records its process-time availability and source identity. The overall
feature availability is the maximum required process time. The prediction
anchor is the first supplied normalized market state at or after that
availability and the minimum feature event boundary. Every feature must satisfy
`available_at_process_time <= prediction_anchor_process_time`.

Feature construction and label construction remain separate modules. The
feature builder may inspect completed SRA objects and market states only through
the anchor. Only `LabelBuilder` receives future market states. Forward returns,
reversal-adjusted returns, MFE, nonnegative `MaximumAdverseExcursion`, time to
MFE, and strict-positive reversal success all begin at the prediction anchor.
An incomplete exact horizon is unavailable rather than truncated.

Evaluation will use chronological walk-forward folds. Expanding and rolling
training windows are both supported; there is no random train/test helper.
Within each instrument/venue, a training row is purged when its maximum label
end index touches or crosses the first retained test anchor. A configurable
event-index embargo and optional exchange-time embargo exclude candidate test
rows immediately after the candidate training boundary. Excluded rows are not
moved into training.

Permutation testing is the primary initial significance framework. The default
null keeps feature rows fixed and permutes whole chronological label blocks
within the applicable pool. Order inside each source label block is preserved.
For `NORMALIZED_EVENT_COUNT`, each `(instrument_id, venue)` coordinate stratum
uses the minimum prediction-anchor event index as its deterministic origin, and
assigns a row to `(anchor_index - origin) // block_size_events`. Missing market
events do not create rows, so blocks can be sparse and contain unequal research
row counts. Venue is part of the default market identity because event indices
are venue-specific coordinates.

Cross-instrument permutation is opt-in and remains within venue. Its event
boundaries and origins are still calculated independently for each
instrument/venue before whole blocks enter a shared pool. Real caller-supplied
session or generic stratum metadata further restrict exchangeability and create
independent origins; sessions and regimes are not inferred.

The prior row-count behavior remains available only as
`RESEARCH_OBSERVATION_COUNT`. Because research-row count and event-horizon count
are different units, this mode requires explicit caller acceptance of overlap
risk. The `block_size >= max_label_horizon_events` validation applies only to
`NORMALIZED_EVENT_COUNT`.

Seeded Monte Carlo mode samples a configured number of valid block orders with
replacement. Duplicate draws and identity draws are intentional. Its p-value
includes ties and uses `(1 + extreme_count) / (B + 1)`. Exact mode enumerates
each allowed Cartesian-product block ordering exactly once, including identity,
only below an explicit safety limit; its p-value is
`extreme_count / total_exact_permutations`, without a Monte Carlo correction.

Two-sided extremeness is measured relative to the arithmetic mean of the
generated null: `abs(T_perm - null_mean) >= abs(T_observed - null_mean)`. This
center is retained in result metadata. A statistic for which that center is not
appropriate must use a predeclared one-sided test until an explicit
statistic-specific center policy exists.

Results retain null summaries, raw effect size, optional standardized effect,
fold, horizon, block unit, size in that unit, seed, instrument scope, mode,
permutation count, typed p-value method, and feature/condition metadata.
The semantic correction is identified by `block-label-permutation-v2`.
Bonferroni and Benjamini-Hochberg corrections are reported alongside raw
p-values when multiple hypotheses are evaluated. Individual walk-forward fold
results remain visible; this ADR does not select a sophisticated p-value
combination rule.

Dataset version, contributing feature versions, immutable configuration,
source-data identifiers, and optional code revision are retained in a typed
manifest. Initial export is deterministic standard-library JSONL with explicit
row ordering and JSON `null` for missing values.

Labels remain gross market responses. No spread crossing, fee, tax, slippage,
impact, or other execution cost is deducted in this milestone. Cost-aware
economic viability is a later layer and must not be confused with the first
question of predictive information.

## Alternatives Considered

### Random train/test split

Rejected because future regimes and overlapping labels can leak into earlier
training data and because it ignores chronology.

### Ordinary IID row permutation

Rejected as the primary null because it destroys local serial dependence,
volatility clustering, session structure, and overlapping-label behavior.

### Anchor every observation at shock 2

Rejected because liquidity credibility, toxicity, and other post-shock features
may not yet be observable at shock 2 time.

### Truncate unavailable forward horizons

Rejected because the resulting labels would not have consistent meanings.

### Start with parametric significance tests or a fitted alpha model

Rejected because distributional assumptions are not yet justified and model
training would obscure the immediate hypothesis test. Transparent statistics
and market-aware permutation nulls come first.

### Deduct current broker costs from initial labels

Rejected because it couples historical market response to one time-varying
execution assumption before predictive content has been established. Gross and
cost-aware questions remain separate and attributable.

## Consequences

Research rows have a later, honest availability boundary whenever a selected
feature consumes post-shock evidence. This can shorten usable label history and
make some horizons unavailable, but prevents hidden lookahead.

Purging and embargo reduce training/test sample sizes. Instrument/session strata
and larger blocks reduce the number of independent block arrangements. Exact
tests can become infeasible, so reproducible Monte Carlo sampling is necessary.

Block permutation preserves more local label dependence than row shuffle but
does not prove full exchangeability. Normalized-event-span blocks are the
initial method; exchange-time/session block construction and regime-aware
restrictions remain future extensions. Sensitivity across predeclared block
sizes must be disclosed, not optimized for significance.

The framework can falsify simple SRA and baseline relationships and report
effect size with multiplicity disclosure. It does not prove economic viability,
causality, robustness across every regime, or that SRA adds incremental value
beyond a formally fitted baseline model. Those require later research using the
preserved dataset.
