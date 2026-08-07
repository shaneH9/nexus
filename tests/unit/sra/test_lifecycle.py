"""Exact accepted-MBO order-lifecycle accounting tests."""

from decimal import Decimal

from tests.support.market_data import INSTRUMENT, book_event

from sra_nexus.market_data import BookAction, BookEvent, OrderBook
from sra_nexus.sra import (
    ORDER_LIFECYCLE_VERSION,
    OrderLifecycle,
    OrderLifecycleTerminalReason,
    OrderLifecycleTracker,
    market_event_reference,
)


def _track(*events: BookEvent) -> tuple[OrderLifecycle, ...]:
    book = OrderBook(INSTRUMENT)
    tracker = OrderLifecycleTracker()
    for event_index, event in enumerate(events):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
    return tracker.completed_lifecycles


def _track_and_close(*events: BookEvent) -> tuple[OrderLifecycle, ...]:
    book = OrderBook(INSTRUMENT)
    tracker = OrderLifecycleTracker()
    for event_index, event in enumerate(events):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
    boundary = book_event(len(events) + 1, BookAction.RESET)
    return tracker.close_observation(
        market_event_reference(boundary),
        event_index=len(events),
    )


def test_exact_execution_and_cancellation_fixture() -> None:
    """ADD 100, execute 70, and cancel 30 should account exactly."""
    lifecycle = _track(
        book_event(1, BookAction.ADD, quantity="100", order_id="order-a"),
        book_event(2, BookAction.EXECUTE, quantity="40", order_id="order-a"),
        book_event(3, BookAction.EXECUTE, quantity="30", order_id="order-a"),
        book_event(4, BookAction.CANCEL, quantity="30", order_id="order-a"),
    )[0]

    assert lifecycle.feature_version == ORDER_LIFECYCLE_VERSION
    assert lifecycle.observed_added_quantity == Decimal("100")
    assert lifecycle.total_executed_quantity == Decimal("70")
    assert lifecycle.total_withdrawn_quantity == Decimal("30")
    assert lifecycle.executed_fraction == Decimal("0.70")
    assert lifecycle.withdrawn_fraction == Decimal("0.30")
    assert lifecycle.terminal_reason is OrderLifecycleTerminalReason.CANCELLED


def test_modify_up_uses_only_positive_absolute_quantity_delta() -> None:
    """An absolute 100-to-150 MODIFY adds 50, not another 150."""
    lifecycle = _track_and_close(
        book_event(1, BookAction.ADD, quantity="100", order_id="modify-up"),
        book_event(2, BookAction.MODIFY, quantity="150", order_id="modify-up"),
        book_event(3, BookAction.EXECUTE, quantity="75", order_id="modify-up"),
    )[0]

    assert lifecycle.initial_quantity == Decimal("100")
    assert lifecycle.observed_added_quantity == Decimal("150")
    assert lifecycle.total_executed_quantity == Decimal("75")
    assert lifecycle.executed_fraction == Decimal("0.5")
    assert lifecycle.unresolved_remaining_quantity == Decimal("75")


def test_modify_down_is_explicit_withdrawal() -> None:
    """An absolute 100-to-60 MODIFY withdraws exactly 40 units."""
    lifecycle = _track_and_close(
        book_event(1, BookAction.ADD, quantity="100", order_id="modify-down"),
        book_event(2, BookAction.MODIFY, quantity="60", order_id="modify-down"),
    )[0]

    assert lifecycle.total_withdrawn_quantity == Decimal("40")
    assert lifecycle.unresolved_remaining_quantity == Decimal("60")
    assert lifecycle.withdrawn_fraction == Decimal("0.4")


def test_price_modify_preserves_identity_without_adding_quantity() -> None:
    """A same-quantity price move remains one lifecycle and one addition."""
    lifecycle = _track_and_close(
        book_event(
            1,
            BookAction.ADD,
            price="99.99",
            quantity="100",
            order_id="move",
        ),
        book_event(
            2,
            BookAction.MODIFY,
            price="100.00",
            quantity="100",
            order_id="move",
        ),
    )[0]

    assert lifecycle.initial_price == Decimal("99.99")
    assert lifecycle.final_price == Decimal("100.00")
    assert lifecycle.observed_added_quantity == Decimal("100")
    assert lifecycle.price_change_count == 1
    assert lifecycle.transitions[0].event.event_index == 0
    assert lifecycle.event_lifetime == 2


def test_full_execution_is_terminal_without_withdrawal() -> None:
    """Fully consumed liquidity ends as EXECUTED and has execution fraction one."""
    lifecycle = _track(
        book_event(1, BookAction.ADD, quantity="100", order_id="executed"),
        book_event(2, BookAction.EXECUTE, quantity="100", order_id="executed"),
    )[0]

    assert lifecycle.terminal_reason is OrderLifecycleTerminalReason.EXECUTED
    assert lifecycle.executed_fraction == Decimal(1)
    assert lifecycle.withdrawn_fraction == Decimal(0)
    assert lifecycle.unresolved_remaining_quantity == Decimal(0)


def test_delete_withdraws_all_remaining_displayed_quantity() -> None:
    """DELETE is an observable withdrawal of the remaining displayed amount."""
    lifecycle = _track(
        book_event(1, BookAction.ADD, quantity="100", order_id="deleted"),
        book_event(2, BookAction.EXECUTE, quantity="25", order_id="deleted"),
        book_event(3, BookAction.DELETE, order_id="deleted"),
    )[0]

    assert lifecycle.terminal_reason is OrderLifecycleTerminalReason.DELETED
    assert lifecycle.total_executed_quantity == Decimal("25")
    assert lifecycle.total_withdrawn_quantity == Decimal("75")


def test_reset_right_censors_remaining_quantity_without_withdrawal() -> None:
    """RESET is structural uncertainty, not inferred voluntary cancellation."""
    lifecycle = _track(
        book_event(1, BookAction.ADD, quantity="100", order_id="reset-order"),
        book_event(2, BookAction.RESET),
    )[0]

    assert lifecycle.terminal_reason is OrderLifecycleTerminalReason.RESET
    assert lifecycle.total_withdrawn_quantity == Decimal(0)
    assert lifecycle.unresolved_remaining_quantity == Decimal("100")


def test_observation_end_right_censors_remaining_quantity() -> None:
    """Research-window closure leaves the active remainder unresolved."""
    lifecycle = _track_and_close(
        book_event(1, BookAction.ADD, quantity="100", order_id="open-order"),
    )[0]

    assert lifecycle.terminal_reason is OrderLifecycleTerminalReason.OBSERVATION_END
    assert lifecycle.total_withdrawn_quantity == Decimal(0)
    assert lifecycle.unresolved_remaining_quantity == Decimal("100")
    assert lifecycle.event_lifetime == 1
