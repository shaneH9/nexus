"""Tests for reconciled aggressive-flow event windows."""

from decimal import Decimal

from tests.support.market_data import book_event, trade_event

from sra_nexus.market_data import AggressorSide, BookAction, TradeReconciliationStatus
from sra_nexus.sra import (
    AggressiveTradeObservation,
    ShockDirection,
    build_aggressive_flow_window,
    directional_aggressive_volume,
    reconcile_aggressive_trade_observations,
    signed_aggressive_flow,
)


def _trade_observation(
    sequence_number: int,
    quantity: str,
    side: AggressorSide,
) -> AggressiveTradeObservation:
    batch = reconcile_aggressive_trade_observations(
        None,
        trade_event(sequence_number, quantity=quantity, aggressor_side=side),
    )
    return batch.observations[0]


def test_aggressive_flow_keeps_unknown_separate_from_signed_flow() -> None:
    """UNKNOWN quantity should not leak into BUY, SELL, or signed order flow."""
    window = build_aggressive_flow_window(
        (
            _trade_observation(1, "30", AggressorSide.BUY),
            _trade_observation(2, "20", AggressorSide.SELL),
            _trade_observation(3, "50", AggressorSide.UNKNOWN),
        )
    )

    assert window.buy_volume == Decimal("30")
    assert window.sell_volume == Decimal("20")
    assert window.unknown_volume == Decimal("50")
    assert window.buy_trade_count == 1
    assert window.sell_trade_count == 1
    assert window.unknown_trade_count == 1
    assert signed_aggressive_flow(window) == Decimal("10")
    assert directional_aggressive_volume(window, ShockDirection.BUY) == Decimal("30")


def test_matched_book_and_trade_execution_owns_volume_once() -> None:
    """A common trade ID should count only the explicit TradeEvent volume owner."""
    batch = reconcile_aggressive_trade_observations(
        book_event(
            1,
            BookAction.EXECUTE,
            quantity="25",
            order_id="resting-1",
            trade_id="execution-1",
        ),
        trade_event(
            2,
            trade_id="execution-1",
            quantity="25",
            aggressor_side=AggressorSide.SELL,
        ),
    )
    window = build_aggressive_flow_window(batch.observations)

    assert batch.reconciliation.status is TradeReconciliationStatus.MATCHED
    assert len(batch.observations) == 1
    assert window.sell_volume == Decimal("25")
    assert window.sell_trade_count == 1


def test_book_only_execution_preserves_unknown_aggressor() -> None:
    """Resting book side should not silently become authoritative aggressor side."""
    batch = reconcile_aggressive_trade_observations(
        book_event(1, BookAction.EXECUTE, quantity="12", order_id="resting-2"),
        None,
    )
    window = build_aggressive_flow_window(batch.observations)

    assert window.unknown_volume == Decimal("12")
    assert window.buy_volume == 0
    assert window.sell_volume == 0
    assert signed_aggressive_flow(window) == 0


def test_last_n_event_window_uses_explicit_event_count_not_clock_duration() -> None:
    """Last-N selection should retain exactly the final volume-owning observations."""
    observations = tuple(
        _trade_observation(index, str(index), AggressorSide.BUY) for index in range(1, 5)
    )

    window = build_aggressive_flow_window(observations, last_event_count=2)

    assert window.relevant_event_count == 2
    assert window.buy_volume == Decimal("7")
    assert window.start_reference == observations[2].reference
    assert window.end_reference == observations[3].reference
