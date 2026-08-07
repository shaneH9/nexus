# ADR 0011: Historical Provider Isolation and Preregistered Research Execution

## Status

Accepted

## Context

Milestones G through K define deterministic SRA features, comparable shocks,
causal research observations, chronological walk-forward splits, and corrected
block-label permutation tests. The next question is whether the already stated
SRA hypothesis survives contact with real historical MBO data. Changing its
mathematics after seeing outcomes would turn that question into an unreported
optimization exercise.

Provider files introduce separate risks: proprietary schemas can leak into the
domain, raw bytes can change without notice, snapshots and resets can create
false continuity, timestamps can be misinterpreted, and malformed order
lifecycle or sequence data can corrupt every downstream statistic. A research
runner also needs to disclose every preregistered hypothesis, including
unavailable or unfavorable results.

## Decision

The existing SRA hypothesis is frozen before historical execution. The equations,
engineering-prior weights, thresholds, horizons, and semantic versions already
accepted by prior ADRs are not tuned by Milestone L. Any later modification is a
new versioned hypothesis.

Provider parsing is isolated behind `HistoricalMarketDataAdapter`. The first
format is Databento historical MBO CSV because it provides documented L3 order
IDs, native actions and sequences, exchange/receive timestamps, and trade-side
semantics. The implementation uses the standard library and converts provider
records to existing canonical market events. No provider field is added to the
SRA contracts.

Normalization is strict. Raw files receive SHA-256 and byte-count identities;
experiment configuration declares the expected identities. Pre-flight reports
schema, scope, clock, sequence, duplicate, and mode findings without repair.
Invalid order values, MBP/top-of-book masquerading as MBO, bad-book flags,
unexplained gaps, regressions, and malformed lifecycle transitions fail.

Provider recovery snapshots are explicit: their documented non-contiguous
original sequences and `F_SNAPSHOT | F_BAD_TS_RECV` flags do not represent an
ordinary live-sequence gap. The snapshot clear/add sequence is rebuilt into a
new contiguous canonical stream. Session association for such snapshots uses an
explicit configured date offset rather than a machine-timezone heuristic.

All canonical clocks are UTC. Historical process time follows one declared
deterministic policy and is marked synthetic. File-read wall time is never
treated as original availability. Sessions, resets, and configured halts or
corporate actions are structural boundaries. Previous-day book state and shock
windows do not cross them by default.

`HistoricalResearchRunner` performs chronological normalization, replay,
existing SRA calculations, existing shock comparison, existing causal dataset
construction, existing walk-forward splitting, and existing corrected
permutation testing. It does not contain provider parsing or reimplement those
mathematics.

The runner constructs provider-independent aggression episodes before invoking
the SRA service. A completed episode may contain multiple same-direction
reconciled executions. The initial inclusive continuation bounds are four
normalized events between completed execution/trade boundaries, 0.050 exchange
seconds between executions, 20 normalized events in the inclusive episode span,
and 0.250 exchange seconds for total episode duration. A direction or structural
segment change, or exceeding any bound, starts a new episode. These are
preregistered engineering values and were not selected from historical-return
results.

The existing SRA maximum observation count is enforced independently of these
all-market-event and clock spans; the two units are not conflated.

UNKNOWN aggression remains outside directional episodes and terminates an open
episode when it interrupts continuity. The episode baseline is the snapshot
immediately before its first execution. Its end snapshot is immediately after
its last included book execution because the paired normalized trade event does
not mutate displayed state. All included observations/executions and all
within-episode post-execution depletion snapshots pass unchanged to
`ShockResearchService`. Response horizons start only after the final paired
trade observation, and the completed episode is not available before that
observation's process time.

For each current shock, the runner searches prior candidates in reverse
chronological order within the same structural segment and stops at the first
candidate accepted by `ShockPairService`. An intervening opposite-direction or
otherwise incomparable shock does not block an older comparable same-direction
shock. Event distance remains the count of all normalized events strictly
between the prior shock end and current shock start; no all-pairs result set is
created.

`ResearchExperimentSpec` preregisters exact source bytes, scope, SRA/dataset
configuration, aggression-episode configuration, purge/embargo, named null
configurations, hypotheses, output policy, and seed. Canonical JSON SHA-256 is
the `ExperimentHash`, so any event-gap, clock-gap, or maximum-span change changes
experiment identity. `ResearchRunId` derives from experiment hash, deterministic
dataset manifest identity, and code revision.

Only test-fold observations contribute to statistical evidence. Permutation
settings pass unchanged to Milestone K. Fold results remain separate. An
optional pooled permutation uses fold ID as a null stratum; p-values are not
averaged. Raw, Bonferroni, and Benjamini-Hochberg values and effect sizes are all
reported for the runnable declared family. Every declaration has `RUN`,
`UNAVAILABLE`, or `FAILED_VALIDATION` status.

Reports use neutral statistical language and retain data-quality limitations.
They test gross market response before any cost model. Milestone L adds no alpha
model, threshold search, random train/test split, NewsState fusion, portfolio
allocation, execution simulator, broker integration, or live trading.

## Alternatives Considered

### Treat every reconciled execution as a complete aggression episode

Rejected because it collapses the intended aggressive-flow window to one trade
and prevents aggregate normalized aggression and multi-execution level
penetration from being evaluated by the existing SRA service.

### Compare only adjacent chronological shock candidates

Rejected because an intervening opposite-direction or otherwise incomparable
shock is not the most recent prior comparable same-direction shock required by
the accepted Milestone H policy.

### Modify SRA thresholds after viewing historical returns

Rejected because it would invalidate the initial falsifiable hypothesis and
understate multiple testing.

### Put Databento fields directly on canonical events

Rejected because downstream research would become provider-coupled and a second
provider would require domain churn.

### Hash filenames or configuration only

Rejected because equal names do not imply equal source bytes.

### Silently repair gaps or order lifecycle errors

Rejected because reconstructed depth, resiliency, and every later feature would
be unauditable.

### Random train/test split or IID row shuffle

Rejected for the chronology, overlap, and serial-dependence reasons in ADR 0010.

### Average fold p-values or report the best block size

Rejected because neither has a declared null here and both can hide multiplicity.

### Fit an alpha model immediately

Rejected because the handcrafted hypothesis should first face a transparent
out-of-sample permutation test.

### Deduct costs before testing statistical information

Rejected for this milestone. Labels remain gross, while later economic
viability must use the separate historical CostModel architecture.

## Consequences

Identical spec, code revision, and source bytes produce identical substantive
artifacts and a stable run ID. Modified bytes or hypotheses create new identities.
Completed results cannot be overwritten by conflicting output.

Strictness can reject files that might be repairable manually. This is
intentional: repair must become a separately versioned, auditable data process.
Snapshot conventions and venue-specific edge cases require explicit adapter
configuration and documentation review.

The adapter streams, but the runner currently retains one session to service
event-indexed response and label access. Checkpoints, normalized caches, dataset
caches, status-schema halt discovery, and full corporate-action adjustment are
future work.

The framework can produce weak, null, or unavailable findings without changing
strategy definitions. That is the intended empirical outcome path, not a runner
failure. Real-data validation remains incomplete until licensed historical data
is supplied and successfully processed.

This correction executes the flow-window concept already intended by the frozen
SRA hypothesis; it does not alter any equation, threshold, engineering-prior
weight, or permutation method. Because no real-data experiment has yet run, the
episode grouping remains part of the preregistered first real-data experiment.
Once outcomes are observed, a grouping-policy change requires a new experiment
and hypothesis version.
