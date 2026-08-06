"""Tests for deterministic transactional market-by-order reconstruction."""

from decimal import Decimal

import pytest
from tests.support.market_data import INSTRUMENT, SHARED_STREAM_ID, book_event, trade_event

from sra_nexus.common.types import MarketOrderId
from sra_nexus.market_data import (
    BookAction,
    BookDataMode,
    BookSide,
    CrossedBookError,
    DuplicateOrderError,
    DuplicateSequenceError,
    OrderBook,
    QuantityExceedsRemainingError,
    SequenceGapError,
    SequenceRegressionError,
    TickAlignmentError,
    UnknownOrderError,
    UnsupportedBookModeError,
)


def _order_id(value: str) -> MarketOrderId:
    return MarketOrderId.model_validate(value)


def _remaining(book: OrderBook, order_id: MarketOrderId) -> Decimal:
    state = book.get_order(order_id)
    if state is None:
        raise AssertionError(f"expected active order {order_id}")
    return state.remaining_quantity


def _only_bid_quantity(book: OrderBook) -> Decimal:
    snapshot = book.snapshot()
    assert len(snapshot.bid_levels) == 1
    return snapshot.bid_levels[0].aggregate_quantity


def test_basic_book_has_exact_prices_depths_and_ordering() -> None:
    """Four additions should reconstruct the documented two-level book exactly."""
    book = OrderBook(INSTRUMENT)
    events = (
        book_event(1, BookAction.ADD, price="100.00", quantity="200", order_id="bid-1"),
        book_event(2, BookAction.ADD, price="99.99", quantity="300", order_id="bid-2"),
        book_event(
            3,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.01",
            quantity="150",
            order_id="ask-1",
        ),
        book_event(
            4,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.02",
            quantity="250",
            order_id="ask-2",
        ),
    )
    for event in events:
        book.apply(event)

    snapshot = book.snapshot()

    assert tuple(level.price for level in snapshot.bid_levels) == (
        Decimal("100.00"),
        Decimal("99.99"),
    )
    assert tuple(level.price for level in snapshot.ask_levels) == (
        Decimal("100.01"),
        Decimal("100.02"),
    )
    assert snapshot.best_bid == Decimal("100.00")
    assert snapshot.best_ask == Decimal("100.01")
    assert snapshot.spread == Decimal("0.01")
    assert snapshot.midprice == Decimal("100.005")
    assert snapshot.bid_depth_n(2) == Decimal("500")
    assert snapshot.ask_depth_n(2) == Decimal("400")
    assert snapshot.order_book_imbalance(2) == Decimal("100") / Decimal("900")


def test_snapshot_preserves_all_clocks_from_last_accepted_book_event() -> None:
    """A snapshot must expose the exact causal clocks of its producing BookEvent."""
    book = OrderBook(INSTRUMENT)
    event = book_event(
        1,
        BookAction.ADD,
        order_id="clock-order",
        sequence_stream_id=SHARED_STREAM_ID,
    )

    book.apply(event)
    book.observe_non_book_event(
        trade_event(
            2,
            trade_id="later-trade",
            sequence_stream_id=SHARED_STREAM_ID,
        )
    )
    snapshot = book.snapshot()

    assert snapshot.exchange_time == event.exchange_time
    assert snapshot.receive_time == event.receive_time
    assert snapshot.process_time == event.process_time
    assert snapshot.sequence_number == event.sequence_number
    assert book.last_sequence == 2


def test_order_lifecycle_uses_absolute_modify_and_partial_reductions() -> None:
    """ADD, MODIFY, EXECUTE, CANCEL, and DELETE should preserve exact remaining state."""
    book = OrderBook(INSTRUMENT)
    order_id = _order_id("life-1")
    book.apply(book_event(1, BookAction.ADD, quantity="100", order_id=str(order_id)))
    assert _remaining(book, order_id) == Decimal("100")
    assert _only_bid_quantity(book) == Decimal("100")

    book.apply(
        book_event(
            2,
            BookAction.MODIFY,
            price="100.01",
            quantity="120",
            order_id=str(order_id),
        )
    )
    modified = book.get_order(order_id)
    assert modified is not None
    assert modified.price == Decimal("100.01")
    assert modified.remaining_quantity == Decimal("120")
    assert _only_bid_quantity(book) == Decimal("120")

    book.apply(
        book_event(
            3,
            BookAction.EXECUTE,
            price="100.01",
            quantity="20",
            order_id=str(order_id),
            trade_id="trade-1",
        )
    )
    assert _remaining(book, order_id) == Decimal("100")
    assert _only_bid_quantity(book) == Decimal("100")
    book.apply(
        book_event(
            4,
            BookAction.CANCEL,
            price="100.01",
            quantity="25",
            order_id=str(order_id),
        )
    )
    assert _remaining(book, order_id) == Decimal("75")
    assert _only_bid_quantity(book) == Decimal("75")
    book.apply(
        book_event(
            5,
            BookAction.DELETE,
            price="100.01",
            order_id=str(order_id),
        )
    )

    assert book.get_order(order_id) is None
    assert book.snapshot().bid_levels == ()


def test_multiple_orders_aggregate_at_one_price_with_exact_order_count() -> None:
    """MBO orders at the same price should create one exact aggregate level."""
    book = OrderBook(INSTRUMENT)
    book.apply(book_event(1, BookAction.ADD, quantity="40", order_id="aggregate-1"))
    book.apply(book_event(2, BookAction.ADD, quantity="60", order_id="aggregate-2"))

    level = book.snapshot().bid_levels[0]

    assert level.aggregate_quantity == Decimal("100")
    assert level.order_count == 2


def test_price_move_modify_removes_old_level_and_keeps_order_identity() -> None:
    """Absolute MODIFY should move remaining quantity without creating another order."""
    book = OrderBook(INSTRUMENT)
    order_id = _order_id("move-1")
    book.apply(book_event(1, BookAction.ADD, price="99.99", order_id=str(order_id)))
    book.apply(
        book_event(
            2,
            BookAction.MODIFY,
            price="100.00",
            quantity="80",
            order_id=str(order_id),
        )
    )

    snapshot = book.snapshot()
    state = book.get_order(order_id)
    assert state is not None and state.price == Decimal("100.00")
    assert tuple(level.price for level in snapshot.bid_levels) == (Decimal("100.00"),)
    assert snapshot.bid_levels[0].aggregate_quantity == Decimal("80")


def test_invalid_execution_is_atomic_and_does_not_consume_sequence() -> None:
    """Over-execution must leave order, levels, snapshot time, and sequence unchanged."""
    book = OrderBook(INSTRUMENT)
    order_id = _order_id("exec-1")
    book.apply(book_event(1, BookAction.ADD, quantity="100", order_id=str(order_id)))
    before = book.snapshot()

    with pytest.raises(QuantityExceedsRemainingError):
        book.apply(
            book_event(
                2,
                BookAction.EXECUTE,
                quantity="120",
                order_id=str(order_id),
                trade_id="too-large",
            )
        )

    assert book.snapshot() == before
    assert book.last_sequence == 1
    book.apply(
        book_event(
            2,
            BookAction.EXECUTE,
            quantity="20",
            order_id=str(order_id),
            trade_id="valid",
        )
    )
    state = book.get_order(order_id)
    assert state is not None and state.remaining_quantity == Decimal("80")


def test_oversized_cancel_is_atomic() -> None:
    """A cancel amount greater than remaining quantity must preserve the book."""
    book = OrderBook(INSTRUMENT)
    book.apply(book_event(1, BookAction.ADD, quantity="100", order_id="cancel-1"))
    before = book.snapshot()

    with pytest.raises(QuantityExceedsRemainingError):
        book.apply(
            book_event(
                2,
                BookAction.CANCEL,
                quantity="120",
                order_id="cancel-1",
            )
        )

    assert book.snapshot() == before
    assert book.last_sequence == 1


def test_duplicate_and_unknown_orders_fail_without_silent_repair() -> None:
    """Order identity corruption must remain explicit."""
    book = OrderBook(INSTRUMENT)
    book.apply(book_event(1, BookAction.ADD, order_id="same"))

    with pytest.raises(DuplicateOrderError):
        book.apply(book_event(2, BookAction.ADD, price="99.99", order_id="same"))
    with pytest.raises(UnknownOrderError):
        book.apply(book_event(2, BookAction.CANCEL, order_id="missing", quantity="1"))
    assert book.last_sequence == 1


@pytest.mark.parametrize(
    "action",
    [BookAction.MODIFY, BookAction.CANCEL, BookAction.DELETE],
)
def test_state_changes_reject_unknown_order(action: BookAction) -> None:
    """MODIFY, CANCEL, and DELETE may never fabricate missing MBO state."""
    book = OrderBook(INSTRUMENT)

    with pytest.raises(UnknownOrderError):
        book.apply(book_event(1, action, order_id="missing"))

    assert book.last_sequence is None


def test_sequence_gap_duplicate_and_regression_are_distinct() -> None:
    """Corrupt stream shapes should not be collapsed into one generic failure."""
    gap_book = OrderBook(INSTRUMENT)
    gap_book.apply(book_event(100, BookAction.ADD, order_id="gap-100"))
    gap_book.apply(book_event(101, BookAction.ADD, price="99.99", order_id="gap-101"))
    with pytest.raises(SequenceGapError, match="expected sequence_number 102"):
        gap_book.apply(
            book_event(
                103,
                BookAction.ADD,
                side=BookSide.ASK,
                price="100.01",
                order_id="gap-103",
            )
        )

    duplicate_book = OrderBook(INSTRUMENT)
    duplicate_book.apply(book_event(100, BookAction.ADD, order_id="dup-1"))
    with pytest.raises(DuplicateSequenceError):
        duplicate_book.apply(book_event(100, BookAction.ADD, order_id="dup-2"))

    regression_book = OrderBook(INSTRUMENT)
    regression_book.apply(book_event(100, BookAction.ADD, order_id="reg-1"))
    regression_book.apply(book_event(101, BookAction.ADD, price="99.99", order_id="reg-2"))
    with pytest.raises(SequenceRegressionError):
        regression_book.apply(book_event(99, BookAction.ADD, order_id="reg-3"))


def test_reset_clears_state_and_can_bridge_forward_gap() -> None:
    """RESET establishes a clean state baseline while sequence numbers stay monotonic."""
    book = OrderBook(INSTRUMENT)
    book.apply(book_event(100, BookAction.ADD, order_id="before-reset"))
    book.apply(book_event(105, BookAction.RESET))
    empty = book.snapshot()

    assert empty.bid_levels == () and empty.ask_levels == ()
    assert empty.best_bid is None and empty.best_ask is None
    assert empty.spread is None and empty.midprice is None and empty.microprice is None
    assert book.get_order(_order_id("before-reset")) is None
    book.apply(book_event(106, BookAction.ADD, price="99.99", order_id="after-reset"))
    assert book.snapshot().best_bid == Decimal("99.99")


def test_mbp_reconstruction_is_explicitly_deferred() -> None:
    """An aggregate event must fail clearly rather than receive a fake order ID."""
    book = OrderBook(INSTRUMENT)
    event = book_event(
        1,
        BookAction.ADD,
        order_id=None,
        book_mode=BookDataMode.MARKET_BY_PRICE,
    )

    with pytest.raises(UnsupportedBookModeError, match="intentionally deferred"):
        book.apply(event)
    assert book.last_sequence is None


def test_tick_misalignment_and_crossed_book_are_rejected_atomically() -> None:
    """Reference-aware invalid prices must not corrupt the accepted book."""
    book = OrderBook(INSTRUMENT)
    with pytest.raises(TickAlignmentError):
        book.apply(book_event(1, BookAction.ADD, price="100.005", order_id="bad-tick"))
    assert book.last_sequence is None

    book.apply(book_event(1, BookAction.ADD, price="100.00", order_id="bid"))
    with pytest.raises(CrossedBookError):
        book.apply(
            book_event(
                2,
                BookAction.ADD,
                side=BookSide.ASK,
                price="99.99",
                order_id="crossed-ask",
            )
        )
    assert book.last_sequence == 1
