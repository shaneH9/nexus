# ADR 0001: SQLite Raw Storage and Process-Time Availability

Status: Accepted

## Context

Milestone B needs deterministic, offline persistence for immutable
`RawNewsItem` records. The storage choice must remain replaceable, support exact
raw reconstruction and duplicate constraints, and prevent historical replay from
seeing information before SRA-Nexus made it usable.

## Decision

SRA-Nexus uses a `RawNewsRepository` protocol as the application boundary and a
standard-library SQLite implementation for initial development. Schema creation
is explicit. Inserts never overwrite raw records and classify provider-identity,
content-hash, and internal-ID conflicts with typed results.

A raw record becomes historically available at its UTC `process_time`.
`list_available_as_of(as_of)` therefore filters on `process_time <= as_of` and
orders by `process_time`, then `news_id`. It does not infer availability from
`event_time` or `receive_time`.

## Alternatives Considered

An in-memory-only repository would be simple but would not exercise durable
serialization, constraints, or realistic historical queries. SQLAlchemy and a
migration framework would add abstraction and operational weight before the
storage requirements justify them. Using `event_time` or `receive_time` for
availability would expose records before downstream processing completed.

## Consequences

Tests and local research run offline with no external database or new dependency.
Provider adapters and ingestion orchestration do not depend on SQLite, so a later
storage backend can implement the same protocol. SQLite schema evolution remains
manual during this early milestone. Historical results are conservative with
respect to processing latency and avoid event-time look-ahead bias.
