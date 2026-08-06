# ADR 0005: Deterministic Order-Book Reconstruction

## Status

Accepted

## Context

SRA-Nexus needs exact, historically replayable limit-order-book state before it
can test the original Shock-Resiliency Asymmetry hypothesis. Wall-clock bars
discard event sequence, individual order lifecycles, and the book transitions
needed for later event-time shock, replenishment, recovery, and liquidity
credibility research. Market feeds may also contain gaps, duplicate or
decreasing sequences, resets, out-of-order delivery, and provider-specific
schemas. Continuing silently after any such corruption would create plausible
but false research features.

Available feeds differ in granularity. Market-by-order data supplies order
identities; market-by-price data supplies only aggregate levels. Pretending an
aggregate feed has order identities would undermine future queue and order-life
research. Persisting every possible snapshot would also commit the project to a
sampling policy before research horizons are established.

## Decision

Use immutable provider-neutral `BookEvent`, `TradeEvent`, and `QuoteEvent`
contracts with exact Decimal prices and quantities, typed identifiers, venue,
sequence number, and UTC-normalized exchange, receive, and process timestamps.
Provider-shaped JSON/JSONL remains inside an offline source adapter.

Reconstruct books from events rather than bars. Sequence streams are separated
by instrument, venue, and event kind. Within a stream, sequence number is the
primary ordering key; timestamps and stable event UUID provide deterministic
inspection order only. Duplicate, regressing, or gapped ordinary book events
fail explicitly and atomically. A forward `RESET` may bridge a gap, clears all
book and order state, and establishes a new sequence baseline; it may not
duplicate or regress sequence.

Implement market-by-order reconstruction first. Maintain immutable active order
state plus exact aggregate bid/ask levels. `MODIFY` carries absolute new
remaining quantity, `CANCEL` carries the amount removed, `EXECUTE` carries the
amount executed, and `DELETE` removes all remaining quantity. All state changes
are transactional and reference-aware tick validation uses exact Decimal
modulus. The contracts label market-by-price events explicitly and never invent
fake order IDs, but MBP reconstruction is deferred and fails clearly.

Generate immutable snapshots on demand after accepted event boundaries.
Snapshots order bids descending and asks ascending and expose exact pure
functions for spread, midprice, microprice, depth, order-book imbalance, and
configured weighted depth. Do not persist every snapshot by default.

Store normalized raw market events through an append-only repository contract.
The SQLite development backend never updates events, distinguishes exact
duplicates from identifier/sequence conflicts, supports indexed sequence-range
queries, and gates historical reads by process time. Replay stops on the first
sequence or book-state corruption.

## Alternatives Considered

### Reconstruct from OHLC or fixed wall-clock bars

Rejected. Bars erase the event ordering and order/level transitions required by
the SRA research hypothesis and future event-count horizons.

### Sort primarily by receive time

Rejected. When an exchange sequence exists, arrival timing must not reverse the
venue-defined stream. Receive and process times remain essential for historical
availability, not as substitutes for stream order.

### Skip missing sequences and continue with best-effort state

Rejected. The resulting book could look valid while missing depth-changing
events, contaminating every downstream feature.

### Implement MBO and MBP through synthetic order IDs

Rejected. Synthetic identities fabricate order lifetime and queue information.
Explicitly deferring MBP reconstruction is more honest and safer.

### Persist a snapshot after every event

Deferred. On-demand snapshots and optional replay snapshots preserve exact
event boundaries without prematurely choosing storage volume, sampling, or
feature-horizon policy.

## Consequences

Offline fixtures can reproduce exact book state and supporting microstructure
features deterministically. Corruption is visible, failed transitions do not
consume sequence, and later event-time research retains the raw transition
history it needs. Storage remains replaceable and separate from book logic.

The initial causal timestamp ordering assumes sufficiently comparable clocks.
Only MBO reconstruction is available; raw trade/quote tick validation awaits a
reference-aware ingestion boundary. The replay helper reconstructs book streams
only and does not yet integrate news, shock detection, SRA feature generation,
alpha, portfolio decisions, execution, costs, taxes, or trading.
