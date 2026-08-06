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

Providers also assign sequences at different scopes: one feed may share a
sequence across book, trade, and quote messages, while another may sequence
channels or event kinds independently. Event kind is therefore not a universal
sequence identity. Book execution messages and trade prints can additionally
be two observations of the same economic execution, making naive volume sums
incorrect.

## Decision

Use immutable provider-neutral `BookEvent`, `TradeEvent`, and `QuoteEvent`
contracts with exact Decimal prices and quantities, typed identifiers, venue,
provider-normalized `SequenceStreamId`, sequence number, and UTC-normalized
exchange, receive, and process timestamps. Provider-shaped JSON/JSONL and the
mapping from provider sequence semantics remain inside an offline source
adapter.

Reconstruct books from events rather than bars. Sequence identity is the
explicit `(instrument_id, venue, sequence_stream_id)` tuple, not event kind.
Within a stream, sequence number is the primary ordering key; timestamps, event
kind, and stable event UUID provide deterministic inspection order only. Shared
streams advance continuity through book, trade, and quote observations, while
only book observations mutate book state. Duplicate, regressing, or gapped
ordinary events fail explicitly and atomically. A forward `RESET` may bridge a
gap, clears all book and order state, and establishes a new sequence baseline;
it may not duplicate or regress sequence.

Implement market-by-order reconstruction first. Maintain immutable active order
state plus exact aggregate bid/ask levels. `MODIFY` carries absolute new
remaining quantity, `CANCEL` carries the amount removed, `EXECUTE` carries the
amount executed, and `DELETE` removes all remaining quantity. All state changes
are transactional and reference-aware tick validation uses exact Decimal
modulus. The contracts label market-by-price events explicitly and never invent
fake order IDs, but MBP reconstruction is deferred and fails clearly.

Generate immutable snapshots on demand after accepted book-event boundaries.
Snapshots preserve the exact exchange, receive, and process clocks of the last
accepted `BookEvent`; there is no ambiguous generic timestamp. They order bids
descending and asks ascending and expose exact pure functions for spread,
midprice, microprice, depth, order-book imbalance, and configured weighted
depth. Do not persist every snapshot by default.

Treat `BookEvent(action=EXECUTE)` as resting-liquidity mutation and `TradeEvent`
as trade-flow observation. Use a separate conservative reconciliation policy.
A normalized common non-null trade ID marks a matched economic execution and
assigns flow-volume ownership to the trade observation once. A sole observation
owns its volume. Comparable unequal IDs are distinct. When either ID is missing
from dual observations, ownership remains unresolved rather than being guessed
or double-counted. Aggressor side is never inferred.

Store normalized raw market events through an append-only repository contract.
The SQLite development backend never updates events, distinguishes exact
duplicates from identifier/sequence conflicts, supports indexed sequence-range
queries, and gates historical reads by process time. Replay stops on the first
sequence or book-state corruption. Current replay reconstructs canonical state
by normalized exchange sequence; it does not simulate physical packet arrival,
feed jitter, network reordering, or decision latency.

## Alternatives Considered

### Reconstruct from OHLC or fixed wall-clock bars

Rejected. Bars erase the event ordering and order/level transitions required by
the SRA research hypothesis and future event-count horizons.

### Sort primarily by receive time

Rejected. When an exchange sequence exists, arrival timing must not reverse the
venue-defined stream. Receive and process times remain essential for historical
availability, not as substitutes for stream order.

### Treat event kind as the sequence-stream identity

Rejected. This creates false gaps for providers with shared sequences and false
conflicts or ordering assumptions for providers with channel-specific scopes.
Sequence scope must be normalized explicitly by each adapter.

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

### Count book executions and trade prints independently

Rejected. When both messages represent one economic execution, that doubles
traded volume. Missing common identity remains unresolved instead of being
heuristically paired by price or time.

## Consequences

Offline fixtures can reproduce exact book state and supporting microstructure
features deterministically. Corruption is visible, failed transitions do not
consume sequence, and later event-time research retains the raw transition
history it needs. Storage remains replaceable and separate from book logic.

The initial causal timestamp ordering assumes sufficiently comparable clocks.
Snapshots now permit future research to distinguish exchange-time market
response from process-time system-observable response. Shared sequence feeds no
longer create false book gaps, but adapters must supply a stable normalized
stream identity. Unresolved dual execution observations cannot contribute to a
future aggregate until an explicit policy resolves ownership.

Only MBO reconstruction is available; raw trade/quote tick validation awaits a
reference-aware ingestion boundary. The replay helper does not simulate arrival
latency and does not yet integrate news, shock detection, SRA feature
generation, alpha, portfolio decisions, execution, costs, taxes, or trading.
