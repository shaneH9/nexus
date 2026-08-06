"""Canonical deterministic ordering helpers for normalized market events."""

from __future__ import annotations

from datetime import datetime

from sra_nexus.common.types import BookEventId, QuoteEventId, TradeEventId
from sra_nexus.market_data.enums import MarketEventKind
from sra_nexus.market_data.events import BookEvent, MarketEvent, TradeEvent

_KIND_ORDER = {
    MarketEventKind.BOOK: 0,
    MarketEventKind.TRADE: 1,
    MarketEventKind.QUOTE: 2,
}


def market_event_id(event: MarketEvent) -> BookEventId | TradeEventId | QuoteEventId:
    """Return the typed internal identity from any normalized market event."""
    if isinstance(event, BookEvent):
        return event.event_id
    if isinstance(event, TradeEvent):
        return event.trade_event_id
    return event.quote_event_id


def market_event_sort_key(
    event: MarketEvent,
) -> tuple[str, str, int, int, datetime, datetime, datetime, str]:
    """Return canonical stream-first, sequence-first deterministic ordering.

    Stream identity is ``(instrument_id, venue, event_kind)``. Within a stream,
    sequence number is primary. Equal sequences use exchange, receive, process,
    and stable internal event identity only as deterministic inspection order;
    sequence validation still rejects the duplicate during reconstruction.
    """
    return (
        str(event.instrument_id),
        event.venue,
        _KIND_ORDER[event.event_kind],
        event.sequence_number,
        event.exchange_time,
        event.receive_time,
        event.process_time,
        str(market_event_id(event)),
    )
