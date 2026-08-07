"""Accepted-MBO liquidity additions, withdrawals, and executions around shocks."""

from decimal import Decimal

from tests.support.market_data import INSTRUMENT, book_event

from sra_nexus.market_data import BookAction, BookEvent, BookSide, BookSnapshot, OrderBook
from sra_nexus.sra import (
    LiquidityFlowFeatures,
    OrderLifecycle,
    OrderLifecycleTracker,
    ShockDirection,
    calculate_liquidity_flow_features,
    market_event_reference,
)


def _liquidity_scenario() -> tuple[
    tuple[BookEvent, ...],
    BookSnapshot,
    tuple[OrderLifecycle, ...],
]:
    events = (
        book_event(1, BookAction.ADD, order_id="bid-base", quantity="100"),
        book_event(
            2,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.01",
            order_id="ask-base",
            quantity="100",
        ),
        book_event(3, BookAction.ADD, order_id="bid-new", quantity="20"),
        book_event(4, BookAction.CANCEL, order_id="bid-base", quantity="80"),
        book_event(5, BookAction.EXECUTE, order_id="bid-base", quantity="20"),
        book_event(
            6,
            BookAction.CANCEL,
            side=BookSide.ASK,
            price="100.01",
            order_id="ask-base",
            quantity="40",
        ),
    )
    book = OrderBook(INSTRUMENT)
    tracker = OrderLifecycleTracker()
    pre_shock_snapshot = None
    for event_index, event in enumerate(events):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
        if event_index == 1:
            pre_shock_snapshot = book.snapshot()
    boundary = book_event(7, BookAction.RESET)
    lifecycles = tracker.close_observation(
        market_event_reference(boundary),
        event_index=6,
    )
    assert pre_shock_snapshot is not None
    return events, pre_shock_snapshot, lifecycles


def _features(direction: ShockDirection) -> LiquidityFlowFeatures:
    events, pre_shock_snapshot, lifecycles = _liquidity_scenario()
    return calculate_liquidity_flow_features(
        direction=direction,
        pre_shock_snapshot=pre_shock_snapshot,
        lifecycles=lifecycles,
        window_start_event_index=2,
        window_end_event_index=5,
        window_start_reference=market_event_reference(events[2]),
        window_end_reference=market_event_reference(events[5]),
        depth_levels=1,
        epsilon=Decimal("0.000001"),
    )


def test_sell_shock_tracks_bid_withdrawal_without_counting_execution() -> None:
    """SELL attacks BID; executed quantity remains separate from withdrawal pressure."""
    result = _features(ShockDirection.SELL)

    assert result.attacked_side is BookSide.BID
    assert result.opposite_side is BookSide.ASK
    assert result.attacked.added_quantity == Decimal("20")
    assert result.attacked.withdrawn_quantity == Decimal("80")
    assert result.attacked.executed_quantity == Decimal("20")
    assert result.attacked.net_liquidity_provision == Decimal("-60")
    assert result.attacked.normalized_net_liquidity_provision == Decimal("-0.6")
    assert result.withdrawal_pressure == Decimal("0.8")
    assert result.opposite.withdrawn_quantity == Decimal("40")
    assert result.opposite.normalized_net_liquidity_provision == Decimal("-1")


def test_attacked_and_opposite_sides_are_symmetric_for_buy_shock() -> None:
    """BUY attacks ASK while preserving separate BID activity as opposite-side context."""
    result = _features(ShockDirection.BUY)

    assert result.attacked_side is BookSide.ASK
    assert result.opposite_side is BookSide.BID
    assert result.attacked.withdrawn_quantity == Decimal("40")
    assert result.attacked.executed_quantity == 0
    assert result.withdrawal_pressure == 1
    assert result.opposite.added_quantity == Decimal("20")
    assert result.opposite.withdrawn_quantity == Decimal("80")
    assert result.opposite.executed_quantity == Decimal("20")


def test_price_changing_modify_is_a_fixed_level_relocation() -> None:
    """Moving away from an original attacked price withdraws its full remainder."""
    events = (
        book_event(1, BookAction.ADD, order_id="moves", quantity="100"),
        book_event(
            2,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.01",
            order_id="ask",
            quantity="100",
        ),
        book_event(
            3,
            BookAction.MODIFY,
            price="99.99",
            order_id="moves",
            quantity="100",
        ),
    )
    book = OrderBook(INSTRUMENT)
    tracker = OrderLifecycleTracker()
    pre_shock_snapshot = None
    for event_index, event in enumerate(events):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
        if event_index == 1:
            pre_shock_snapshot = book.snapshot()
    boundary = book_event(4, BookAction.RESET)
    lifecycles = tracker.close_observation(
        market_event_reference(boundary),
        event_index=3,
    )
    assert pre_shock_snapshot is not None

    result = calculate_liquidity_flow_features(
        direction=ShockDirection.SELL,
        pre_shock_snapshot=pre_shock_snapshot,
        lifecycles=lifecycles,
        window_start_event_index=2,
        window_end_event_index=2,
        window_start_reference=market_event_reference(events[2]),
        window_end_reference=market_event_reference(events[2]),
        depth_levels=1,
        epsilon=Decimal("0.000001"),
    )

    assert result.attacked.original_price_levels == (Decimal("100.00"),)
    assert result.attacked.withdrawn_quantity == Decimal("100")
    assert result.attacked.added_quantity == 0
    assert result.attacked.executed_quantity == 0
