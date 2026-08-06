"""Tests for execution-mutation and trade-observation reconciliation."""

from tests.support.market_data import book_event, trade_event

from sra_nexus.market_data import (
    AggressorSide,
    BookAction,
    ExecutionReconciliationPolicy,
    ExecutionVolumeOwner,
    TradeIdExecutionReconciler,
    TradeReconciliationStatus,
)


def test_common_trade_id_reconciles_two_observations_to_one_execution() -> None:
    """Matching IDs should give trade flow one owner without losing book mutation."""
    book_execution = book_event(
        1,
        BookAction.EXECUTE,
        quantity="10",
        order_id="resting-order",
        trade_id="economic-trade-1",
    )
    trade_observation = trade_event(
        2,
        trade_id="economic-trade-1",
        quantity="10",
        aggressor_side=AggressorSide.UNKNOWN,
    )
    policy: ExecutionReconciliationPolicy = TradeIdExecutionReconciler()

    result = policy.reconcile(book_execution, trade_observation)

    assert result.status is TradeReconciliationStatus.MATCHED
    assert result.common_trade_id == book_execution.trade_id
    assert result.volume_owners == (ExecutionVolumeOwner.TRADE_EVENT,)
    assert result.economic_execution_count == 1
    assert trade_observation.aggressor_side is AggressorSide.UNKNOWN


def test_single_observation_owns_its_execution_volume() -> None:
    """Book-only and trade-only providers should retain one countable observation."""
    reconciler = TradeIdExecutionReconciler()
    book_execution = book_event(
        1,
        BookAction.EXECUTE,
        order_id="book-only-order",
        trade_id=None,
    )
    trade_observation = trade_event(1, trade_id=None)

    book_only = reconciler.reconcile(book_execution, None)
    trade_only = reconciler.reconcile(None, trade_observation)

    assert book_only.status is TradeReconciliationStatus.BOOK_ONLY
    assert book_only.volume_owners == (ExecutionVolumeOwner.BOOK_EVENT,)
    assert book_only.economic_execution_count == 1
    assert trade_only.status is TradeReconciliationStatus.TRADE_ONLY
    assert trade_only.volume_owners == (ExecutionVolumeOwner.TRADE_EVENT,)
    assert trade_only.economic_execution_count == 1


def test_dual_observations_without_comparable_ids_withhold_volume_ownership() -> None:
    """Missing normalized identity must remain unresolved rather than double-counted."""
    result = TradeIdExecutionReconciler().reconcile(
        book_event(1, BookAction.EXECUTE, order_id="unknown-order", trade_id=None),
        trade_event(2, trade_id=None),
    )

    assert result.status is TradeReconciliationStatus.UNRESOLVED
    assert result.volume_owners == ()
    assert result.economic_execution_count is None


def test_different_normalized_trade_ids_are_distinct_executions() -> None:
    """Comparable unequal IDs should retain separate volume ownership."""
    result = TradeIdExecutionReconciler().reconcile(
        book_event(1, BookAction.EXECUTE, order_id="distinct-order", trade_id="trade-a"),
        trade_event(2, trade_id="trade-b"),
    )

    assert result.status is TradeReconciliationStatus.DISTINCT
    assert result.volume_owners == (
        ExecutionVolumeOwner.BOOK_EVENT,
        ExecutionVolumeOwner.TRADE_EVENT,
    )
    assert result.economic_execution_count == 2
