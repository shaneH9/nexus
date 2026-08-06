"""Deterministic shared market-data fixtures for unit tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sra_nexus.common.types import InstrumentId
from sra_nexus.market_data import BookAction, BookDataMode, BookEvent, BookSide
from sra_nexus.reference import AssetType, Instrument

BASE_TIME = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)
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
) -> BookEvent:
    """Build one causally timed exact-decimal event for the shared instrument."""
    event_time = BASE_TIME + timedelta(milliseconds=sequence_number)
    data: dict[str, object] = {
        "instrument_id": INSTRUMENT.instrument_id,
        "venue": INSTRUMENT.exchange,
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
