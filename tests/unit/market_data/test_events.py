"""Tests for immutable normalized market-event contracts."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from tests.support.market_data import BASE_TIME, INSTRUMENT, book_event

from sra_nexus.market_data import (
    AggressorSide,
    BookAction,
    BookDataMode,
    BookEvent,
    BookSide,
    QuoteEvent,
    TradeEvent,
)


def _timing() -> dict[str, object]:
    return {
        "instrument_id": INSTRUMENT.instrument_id,
        "venue": INSTRUMENT.exchange,
        "sequence_stream_id": "test-stream",
        "exchange_time": BASE_TIME,
        "receive_time": BASE_TIME + timedelta(microseconds=1),
        "process_time": BASE_TIME + timedelta(microseconds=2),
        "sequence_number": 1,
    }


def test_market_timestamps_normalize_aware_offsets_to_utc() -> None:
    """A non-UTC exchange timestamp should retain its instant in UTC."""
    eastern = timezone(timedelta(hours=-4))
    event = TradeEvent.model_validate(
        {
            **_timing(),
            "exchange_time": datetime(2026, 8, 1, 10, 0, tzinfo=eastern),
            "trade_id": "trade-1",
            "price": "100.00",
            "quantity": "10",
            "aggressor_side": "UNKNOWN",
        }
    )

    assert event.exchange_time == BASE_TIME
    assert event.exchange_time.tzinfo is UTC


def test_market_timestamps_reject_naive_and_noncausal_values() -> None:
    """The initial research clock assumption must be explicit and enforced."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        TradeEvent.model_validate(
            {
                **_timing(),
                "exchange_time": datetime(2026, 8, 1, 14, 0),
                "trade_id": "trade-1",
                "price": "100.00",
                "quantity": "10",
            }
        )
    with pytest.raises(ValidationError, match="exchange_time must not be after"):
        TradeEvent.model_validate(
            {
                **_timing(),
                "exchange_time": BASE_TIME + timedelta(seconds=1),
                "trade_id": "trade-1",
                "price": "100.00",
                "quantity": "10",
            }
        )


@pytest.mark.parametrize("aggressor", list(AggressorSide))
def test_trade_event_preserves_explicit_aggressor_side(aggressor: AggressorSide) -> None:
    """BUY, SELL, and especially UNKNOWN must round-trip without inference."""
    event = TradeEvent.model_validate(
        {
            **_timing(),
            "trade_id": "trade-1",
            "price": "100.00",
            "quantity": "10",
            "aggressor_side": aggressor,
        }
    )

    assert event.aggressor_side is aggressor


def test_sequence_stream_identity_is_required_and_normalized() -> None:
    """Provider sequence scope must be explicit, typed, and whitespace-normalized."""
    payload = {
        **_timing(),
        "sequence_stream_id": "  shared-channel-7  ",
        "trade_id": None,
        "price": "100.00",
        "quantity": "10",
    }

    event = TradeEvent.model_validate(payload)

    assert event.sequence_stream_id.root == "shared-channel-7"
    assert event.trade_id is None
    payload.pop("sequence_stream_id")
    with pytest.raises(ValidationError, match="sequence_stream_id"):
        TradeEvent.model_validate(payload)


def test_quote_allows_locked_but_rejects_crossed_market() -> None:
    """Locked quotes are explicit valid observations; crossed quotes are not repaired."""
    locked = QuoteEvent.model_validate(
        {
            **_timing(),
            "bid_price": "100.00",
            "bid_quantity": "10",
            "ask_price": "100.00",
            "ask_quantity": "20",
        }
    )

    assert locked.bid_price == locked.ask_price
    with pytest.raises(ValidationError, match="crossed quote"):
        QuoteEvent.model_validate(
            {
                **_timing(),
                "bid_price": "100.01",
                "bid_quantity": "10",
                "ask_price": "100.00",
                "ask_quantity": "20",
            }
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("bid_price", "0"),
        ("ask_price", "-1"),
        ("bid_quantity", "-0.01"),
        ("ask_quantity", "-1"),
    ],
)
def test_quote_rejects_invalid_price_and_quantity(field_name: str, value: str) -> None:
    """Top-of-book prices must be positive and quantities non-negative."""
    payload = {
        **_timing(),
        "bid_price": "100.00",
        "bid_quantity": "10",
        "ask_price": "100.01",
        "ask_quantity": "20",
        field_name: value,
    }
    with pytest.raises(ValidationError):
        QuoteEvent.model_validate(payload)


def test_exact_market_values_reject_float_construction() -> None:
    """Prices and quantities must not silently inherit binary float error."""
    with pytest.raises(ValidationError, match="must not be constructed from float"):
        TradeEvent.model_validate(
            {
                **_timing(),
                "trade_id": "trade-1",
                "price": 100.01,
                "quantity": "10",
            }
        )


def test_book_event_action_shapes_are_explicit() -> None:
    """RESET/DELETE and quantity-bearing actions should not have ambiguous fields."""
    reset = book_event(1, BookAction.RESET)
    delete = book_event(2, BookAction.DELETE)

    assert reset.side is None and reset.order_id is None
    assert delete.quantity is None
    with pytest.raises(ValidationError, match="requires positive quantity"):
        book_event(3, BookAction.CANCEL, quantity="0")
    with pytest.raises(ValidationError, match="DELETE removes all"):
        BookEvent.model_validate(
            {
                **_timing(),
                "action": BookAction.DELETE,
                "side": BookSide.BID,
                "price": "100.00",
                "quantity": "1",
                "order_id": "order-1",
            }
        )


def test_mbp_contract_does_not_invent_order_identity() -> None:
    """Aggregate events are representable only when explicitly labeled and ID-free."""
    event = book_event(
        1,
        BookAction.ADD,
        order_id=None,
        book_mode=BookDataMode.MARKET_BY_PRICE,
    )

    assert event.order_id is None
    with pytest.raises(ValidationError, match="MBP book events cannot contain"):
        book_event(2, BookAction.ADD, book_mode=BookDataMode.MARKET_BY_PRICE)
