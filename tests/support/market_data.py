"""Deterministic shared market-data fixtures for unit tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sra_nexus.common.types import InstrumentId, SequenceStreamId
from sra_nexus.market_data import (
    AggressorSide,
    BookAction,
    BookDataMode,
    BookEvent,
    BookSide,
    QuoteEvent,
    TradeEvent,
)
from sra_nexus.reference import AssetType, Instrument

BASE_TIME = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)
BOOK_STREAM_ID = SequenceStreamId.model_validate("book-primary")
TRADE_STREAM_ID = SequenceStreamId.model_validate("trade-primary")
QUOTE_STREAM_ID = SequenceStreamId.model_validate("quote-primary")
SHARED_STREAM_ID = SequenceStreamId.model_validate("shared-primary")
INSTRUMENT = Instrument(
    instrument_id=InstrumentId.model_validate("20000000-0000-4000-8000-000000000101"),
    ticker="NVDA",
    exchange="NASDAQ",
    asset_type=AssetType.EQUITY,
    currency="USD",
    tick_size=Decimal("0.01"),
    lot_size=Decimal("1"),
)


def book_event(
    sequence_number: int,
    action: BookAction,
    *,
    side: BookSide | None = BookSide.BID,
    price: str | None = "100.00",
    quantity: str | None = "100",
    order_id: str | None = "order-1",
    trade_id: str | None = None,
    book_mode: BookDataMode = BookDataMode.MARKET_BY_ORDER,
    sequence_stream_id: SequenceStreamId | str = BOOK_STREAM_ID,
) -> BookEvent:
    """Build one causally timed exact-decimal event for the shared instrument."""
    event_time = BASE_TIME + timedelta(milliseconds=sequence_number)
    data: dict[str, object] = {
        "instrument_id": INSTRUMENT.instrument_id,
        "venue": INSTRUMENT.exchange,
        "sequence_stream_id": sequence_stream_id,
        "exchange_time": event_time,
        "receive_time": event_time + timedelta(microseconds=1),
        "process_time": event_time + timedelta(microseconds=2),
        "sequence_number": sequence_number,
        "action": action,
        "side": side,
        "price": price,
        "quantity": quantity,
        "order_id": order_id,
        "trade_id": trade_id,
        "book_mode": book_mode,
    }
    if action is BookAction.RESET:
        data.update(
            {
                "side": None,
                "price": None,
                "quantity": None,
                "order_id": None,
                "trade_id": None,
            }
        )
    if action is BookAction.DELETE:
        data["quantity"] = None
    return BookEvent.model_validate(data)


def trade_event(
    sequence_number: int,
    *,
    trade_id: str | None = None,
    price: str = "100.00",
    quantity: str = "10",
    aggressor_side: AggressorSide = AggressorSide.UNKNOWN,
    sequence_stream_id: SequenceStreamId | str = TRADE_STREAM_ID,
) -> TradeEvent:
    """Build one exact trade observation in an explicit sequence domain."""
    event_time = BASE_TIME + timedelta(milliseconds=sequence_number)
    return TradeEvent.model_validate(
        {
            "instrument_id": INSTRUMENT.instrument_id,
            "venue": INSTRUMENT.exchange,
            "sequence_stream_id": sequence_stream_id,
            "exchange_time": event_time,
            "receive_time": event_time + timedelta(microseconds=1),
            "process_time": event_time + timedelta(microseconds=2),
            "sequence_number": sequence_number,
            "trade_id": trade_id,
            "price": price,
            "quantity": quantity,
            "aggressor_side": aggressor_side,
        }
    )


def quote_event(
    sequence_number: int,
    *,
    bid_price: str = "100.00",
    bid_quantity: str = "10",
    ask_price: str = "100.01",
    ask_quantity: str = "10",
    sequence_stream_id: SequenceStreamId | str = QUOTE_STREAM_ID,
) -> QuoteEvent:
    """Build one exact top-of-book observation in an explicit sequence domain."""
    event_time = BASE_TIME + timedelta(milliseconds=sequence_number)
    return QuoteEvent.model_validate(
        {
            "instrument_id": INSTRUMENT.instrument_id,
            "venue": INSTRUMENT.exchange,
            "sequence_stream_id": sequence_stream_id,
            "exchange_time": event_time,
            "receive_time": event_time + timedelta(microseconds=1),
            "process_time": event_time + timedelta(microseconds=2),
            "sequence_number": sequence_number,
            "bid_price": bid_price,
            "bid_quantity": bid_quantity,
            "ask_price": ask_price,
            "ask_quantity": ask_quantity,
        }
    )
