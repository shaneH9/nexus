# ADR 0004: Deterministic Event Scoring and On-Demand NewsState

## Status

Accepted

## Context

Milestones B through D established immutable raw observations, canonical-event
revisions, revision-specific entity links, and instrument exposure graphs.
SRA-Nexus now needs to answer what structured information state was available
for one instrument at a historical cutoff without allowing later sources or
revisions to change the answer.

The project does not yet have enough validated outcomes to justify fitted
sentiment, credibility, severity, novelty, uncertainty, or decay models. Using
opaque ML at this stage would make historical failures difficult to audit and
could disguise look-ahead. Current aliases and some reference mappings also
lack independent information-availability versioning.

## Decision

Use immutable typed initial engineering priors and small deterministic formulas
for event scoring. Each `EventScore` identifies the exact canonical revision,
availability time, source provenance, score/state policy version,
reference-data policy, method per component, contributing factor values and
weights, rule references, and explanations.

Keep `EventScoringService` repository-free. It scores explicitly supplied exact
revision evidence. Keep `NewsStateService` separate and dependent only on
repository protocols. It selects instrument exposures visible by `as_of`, loads
their exact canonical revisions and graph records, process-time-gates the
revision source observations, applies subtype-specific exponential decay, and
aggregates state on demand. No state cache or persistence is introduced in this
milestone.

Include exposure magnitude in event intensity. Use exposure direction only for
directional intensity. Aggregate type risk, novelty, and uncertainty through
bounded union rather than clamped sums. Compute state confidence as a
relevance/magnitude/decay-weighted mean. Count unique raw `NewsId` values for
volume and acceleration.

Treat a latest visible `RETRACTED` revision as inactive. It contributes no
ordinary direction, risk, volume, or exposure after retraction availability;
prior `as_of` queries remain unchanged. Resolved revisions remain eligible
until decay falls below the configured threshold.

Expose `CURRENT_REFERENCE_DATA` versus `HISTORICAL_REFERENCE_DATA`. The former
explicitly identifies retrospective enrichment; the latter is a declaration
that upstream links came from a timestamp-safe mapping snapshot or repository.
The service does not claim current aliases were historically available.

These features remain contextual inputs. They do not generate orders,
positions, allocation, alpha predictions, or changes to SRA mathematics.

## Alternatives Considered

### Train statistical or ML scorers now

Rejected. There is no validated labeled dataset or calibration process yet, and
opaque scores would make causal availability and fixture behavior harder to
audit.

### Store scalar scores directly on mutable canonical events

Rejected. Policy changes would blur immutable source-derived event state with
derived features and could silently rewrite historical research.

### Persist every NewsState snapshot

Deferred. On-demand computation is simpler and avoids premature cache
invalidation, materialization schedules, and duplicated historical truth.

### Use current event or reference state at query time

Rejected. Current canonical revisions create direct look-ahead. Current
reference enrichment is permitted only when explicitly labeled
`CURRENT_REFERENCE_DATA` and must not be misrepresented as historically known.

### Remove speculative observations

Rejected. `SPECULATIVE` is valid provenance with a configurable lower initial
credibility prior. It may carry useful information and uncertainty but receives
no automatic direction.

## Consequences

Historical results are deterministic, revision-safe, testable with exact
formulas, and auditable down to each source and factor. Policy version strings
make later score changes explicit. Unknown sentiment and surprise remain unknown
instead of being fabricated.

The engineering priors are not empirical probabilities and must not be
interpreted as calibrated edge. Reference-data look-ahead is identifiable but
not fully eliminated until aliases and mappings gain availability versioning or
historical snapshots. Retracted-item correction risk, score persistence,
empirical calibration, real providers, StockNest, SRA, alpha, portfolio,
execution, costs, and tax/accounting remain future milestones.
