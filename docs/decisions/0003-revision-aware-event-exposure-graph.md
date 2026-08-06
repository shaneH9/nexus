# ADR 0003: Revision-Aware Event Exposure Graph

## Context

Canonical events evolve through immutable revisions. Entity knowledge and
economic relationships discovered in a later revision must not appear in an
earlier historical query. Tickers, aliases, and structural relationships can be
ambiguous or time-dependent, while indirect exposure must remain bounded and
explainable.

## Decision

Use explicit repository protocols for entities, instruments, entity-instrument
links, structural relationships, event-entity links, materialized exposures,
and exposure paths.

Generate exposures for one exact canonical revision. Persist `event_id`,
`revision_id`, `revision_number`, and `available_at` on every derived link, path,
and materialized exposure. Store immutable per-revision run markers so an empty
processed result differs from an unprocessed revision.

Use deterministic exact metadata/name matching with typed unresolved and
ambiguity results. Traverse a centralized relationship policy with a default
maximum depth of two, path-local cycle prevention, validity filtering, and
conservative sign propagation. Retain each `ExposurePath`, then combine
magnitude and confidence with bounded union while explicitly flagging
conflicting deterministic directions.

Use standard-library SQLite as the initial development backend. Keep SQL out of
entity linking and graph services.

## Alternatives Considered

* Mutating `CanonicalEvent.entity_ids` and `instrument_ids`: rejected because it
  would erase revision-specific derivation evidence.
* Resolving ambiguous names or tickers by insertion order: rejected because it
  is not auditable or deterministic across datasets.
* Recursive unbounded graph traversal: rejected because cycles and weak distant
  relationships would produce unstable, opaque exposure.
* Storing only final `EventExposure` values: rejected because it prevents path
  explanation, conflict analysis, and formula verification.
* Statistical NLP, embeddings, or external symbol lookup: deferred until a
  later milestone has an evidence-based need.

## Consequences

Historical exposure queries cannot see later canonical revisions or
relationships outside their validity intervals. Reruns are idempotent, path
evidence is reproducible, and reference ambiguities remain explicit.

The initial magnitudes, confidence values, decay parameters, traversal policy,
and limited direction rules are engineering priors rather than calibrated
market truth. Reference entities and aliases are not yet independently
versioned, asynchronous graph-processing latency is not yet modeled separately,
and no NewsState aggregation or source-credibility scoring is included.
