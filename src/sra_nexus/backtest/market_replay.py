"""Deterministic canonical replay of immutable normalized market streams."""

from collections.abc import Iterable
from datetime import datetime

from sra_nexus.common.types import SequenceStreamId
from sra_nexus.market_data.book import OrderBook
from sra_nexus.market_data.events import BookEvent, MarketEvent, QuoteEvent, TradeEvent
from sra_nexus.market_data.exceptions import AmbiguousBookStreamError
from sra_nexus.market_data.ordering import market_event_sort_key
from sra_nexus.market_data.snapshots import BookSnapshot
from sra_nexus.market_data.sources import MarketDataSource
from sra_nexus.storage.market_data import MarketEventQuery, RawMarketEventRepository


class MarketReplay:
    """Reconstruct canonical state by normalized sequence, failing on corruption.

    This is not physical packet-arrival, feed-jitter, network-reordering, or
    decision-latency simulation. Those future modes require receive/process-time
    replay rather than canonical sequence reconstruction.
    """

    def __init__(self, order_book: OrderBook) -> None:
        """Configure one in-memory reconstruction target."""
        self._order_book = order_book

    def replay(
        self,
        events: Iterable[MarketEvent],
        *,
        snapshots_after_each: bool = True,
    ) -> tuple[BookSnapshot, ...]:
        """Replay one explicit sequence domain and snapshot book mutations only."""
        available = tuple(events)
        stream_id = self._select_sequence_stream(available)
        if stream_id is None:
            return ()
        ordered = sorted(
            (
                event
                for event in available
                if event.instrument_id == self._order_book.instrument_id
                and event.venue == self._order_book.venue
                and event.sequence_stream_id == stream_id
            ),
            key=market_event_sort_key,
        )
        snapshots: list[BookSnapshot] = []
        for event in ordered:
            if isinstance(event, BookEvent):
                self._order_book.apply(event)
                if snapshots_after_each:
                    snapshots.append(self._order_book.snapshot())
            elif isinstance(event, (TradeEvent, QuoteEvent)):
                self._order_book.observe_non_book_event(event)
        return tuple(snapshots)

    def replay_repository(
        self,
        repository: RawMarketEventRepository,
        *,
        start_sequence: int | None = None,
        end_sequence: int | None = None,
        as_of: datetime | None = None,
        snapshots_after_each: bool = True,
    ) -> tuple[BookSnapshot, ...]:
        """Retrieve one explicit sequence stream and replay historical visibility."""
        available = repository.list_for_instrument(self._order_book.instrument_id, as_of)
        stream_id = self._select_sequence_stream(available)
        if stream_id is None:
            return ()
        events = repository.list_stream(
            MarketEventQuery(
                instrument_id=self._order_book.instrument_id,
                venue=self._order_book.venue,
                sequence_stream_id=stream_id,
                start_sequence=start_sequence,
                end_sequence=end_sequence,
                as_of=as_of,
            )
        )
        return self.replay(events, snapshots_after_each=snapshots_after_each)

    def replay_source(
        self,
        source: MarketDataSource,
        *,
        snapshots_after_each: bool = True,
    ) -> tuple[BookSnapshot, ...]:
        """Select and replay the target normalized stream from a source."""
        return self.replay(
            source.read(),
            snapshots_after_each=snapshots_after_each,
        )

    def _select_sequence_stream(
        self,
        events: tuple[MarketEvent, ...],
    ) -> SequenceStreamId | None:
        configured = self._order_book.sequence_stream_id
        if configured is not None:
            return configured
        candidates = {
            event.sequence_stream_id
            for event in events
            if isinstance(event, BookEvent)
            and event.instrument_id == self._order_book.instrument_id
            and event.venue == self._order_book.venue
        }
        if len(candidates) > 1:
            raise AmbiguousBookStreamError(
                "multiple book sequence streams require explicit OrderBook configuration"
            )
        return next(iter(candidates), None)
