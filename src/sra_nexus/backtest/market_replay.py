"""Deterministic safe replay of immutable book-event streams."""

from collections.abc import Iterable
from datetime import datetime

from sra_nexus.market_data.book import OrderBook
from sra_nexus.market_data.enums import MarketEventKind
from sra_nexus.market_data.events import BookEvent
from sra_nexus.market_data.ordering import market_event_sort_key
from sra_nexus.market_data.snapshots import BookSnapshot
from sra_nexus.market_data.sources import MarketDataSource
from sra_nexus.storage.market_data import MarketEventQuery, RawMarketEventRepository


class MarketReplay:
    """Apply sequence-ordered BookEvents and stop at the first corruption."""

    def __init__(self, order_book: OrderBook) -> None:
        """Configure one in-memory reconstruction target."""
        self._order_book = order_book

    def replay(
        self,
        events: Iterable[BookEvent],
        *,
        snapshots_after_each: bool = True,
    ) -> tuple[BookSnapshot, ...]:
        """Sort canonically, apply sequentially, and optionally retain each snapshot."""
        ordered = sorted(events, key=market_event_sort_key)
        snapshots: list[BookSnapshot] = []
        for event in ordered:
            self._order_book.apply(event)
            if snapshots_after_each:
                snapshots.append(self._order_book.snapshot())
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
        """Retrieve one indexed book stream and replay only historically visible events."""
        events = repository.list_stream(
            MarketEventQuery(
                instrument_id=self._order_book.instrument_id,
                venue=self._order_book.venue,
                event_kind=MarketEventKind.BOOK,
                start_sequence=start_sequence,
                end_sequence=end_sequence,
                as_of=as_of,
            )
        )
        book_events = tuple(event for event in events if isinstance(event, BookEvent))
        return self.replay(book_events, snapshots_after_each=snapshots_after_each)

    def replay_source(
        self,
        source: MarketDataSource,
        *,
        snapshots_after_each: bool = True,
    ) -> tuple[BookSnapshot, ...]:
        """Select the target book stream from a source and replay it deterministically."""
        book_events = tuple(
            event
            for event in source.read()
            if isinstance(event, BookEvent)
            and event.instrument_id == self._order_book.instrument_id
            and event.venue == self._order_book.venue
        )
        return self.replay(book_events, snapshots_after_each=snapshots_after_each)
