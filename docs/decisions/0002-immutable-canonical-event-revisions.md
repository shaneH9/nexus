# ADR 0002: Immutable Canonical-Event Revisions

Status: Accepted

## Context

Canonical events evolve as independent reports, material updates, and official
confirmations become available. A mutable current-state row would overwrite what
the system knew earlier and allow future source membership, wording, or lifecycle
state to leak into historical research.

## Decision

Each real-world event receives one stable `CanonicalEventId` and an append-only
series of immutable `CanonicalEventRevision` snapshots. A snapshot contains the
complete materialized canonical state, monotonically increasing revision number,
historical source membership, clustering metadata, and an `available_at` cutoff.

For Milestone C offline processing, `available_at` is exactly the triggering
`RawNewsItem.process_time`. Historical reconstruction selects the latest revision
where `available_at <= as_of`, breaking equal-time ties by revision number. Raw
items are processed by `process_time`, then `news_id`.

SQLite stores stable identities, immutable revision payloads, per-revision source
membership, per-revision anchors, and a global one-to-one assignment from
`NewsId` to canonical event. Application code depends on a
`CanonicalEventRepository` protocol rather than SQLite.

## Alternatives Considered

A single mutable canonical row was rejected because it destroys historical
knowledge state. Storing every update as a separate independent event was
rejected because it loses event identity and evolution. Recomputing all event
state from raw data for every query would preserve history but make basic local
queries unnecessarily expensive and would still require versioned classifier and
clustering configuration.

## Consequences

Historical queries cannot observe future revisions, and every source-membership
or lifecycle change remains auditable. Storage grows with event evolution and
append operations must enforce monotonic history. Late out-of-order
canonicalization older than an already stored revision requires deterministic
replay/rebuild rather than insertion into the middle of history; Milestone C
therefore processes batches chronologically. Classification, clustering weights,
and configuration must be versioned before production research comparisons.
