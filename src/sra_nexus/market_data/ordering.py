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
) -> tuple[str, str, str, int, datetime, datetime, datetime, int, str]:
    """Return canonical stream-first, sequence-first deterministic ordering.

    Stream identity is the provider-normalized ``sequence_stream_id`` within an
    instrument/venue. Equal sequences use clocks, kind, and stable identity only
    for deterministic inspection; validation still rejects the duplicate.
    """
    return (
        str(event.instrument_id),
        event.venue,
        str(event.sequence_stream_id),
        event.sequence_number,
        event.exchange_time,
        event.receive_time,
        event.process_time,
        _KIND_ORDER[event.event_kind],
        str(market_event_id(event)),
    )
