"""Tests for atomic deterministic shock-research orchestration."""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from tests.support.market_data import INSTRUMENT, SHARED_STREAM_ID, book_event, trade_event

from sra_nexus.market_data import (
    AggressorSide,
    BookAction,
    BookEvent,
    BookSide,
    BookSnapshot,
    OrderBook,
)
from sra_nexus.sra import (
    AggressiveTradeObservation,
    BookExecutionState,
    ImpactConfig,
    MarketStateObservation,
    ResiliencyConfig,
    ShockDetectionConfig,
    ShockDirection,
    ShockResearchConfig,
    ShockResearchService,
    ShockResearchStatus,
    market_event_reference,
    reconcile_aggressive_trade_observations,
)


@dataclass(frozen=True, slots=True)
class _Episode:
    direction: ShockDirection
    observations: tuple[AggressiveTradeObservation, ...]
    pre_snapshot: BookSnapshot
    end_snapshot: BookSnapshot
    executions: tuple[BookExecutionState, ...]
    depletion_snapshots: tuple[BookSnapshot, ...]
    responses: tuple[MarketStateObservation, ...]


def _apply(
    book: OrderBook,
    event: BookEvent,
) -> tuple[BookSnapshot | None, BookSnapshot]:
    before = None if book.last_sequence is None else book.snapshot()
    book.apply(event)
    return before, book.snapshot()


def _build_episode(direction: ShockDirection) -> _Episode:
    book = OrderBook(INSTRUMENT, sequence_stream_id=SHARED_STREAM_ID)
    if direction is ShockDirection.SELL:
        initial = (
            book_event(
                1,
                BookAction.ADD,
                side=BookSide.BID,
                price="100.00",
                quantity="200",
                order_id="bid-1",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
            book_event(
                2,
                BookAction.ADD,
                side=BookSide.BID,
                price="99.99",
                quantity="300",
                order_id="bid-2",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
            book_event(
                3,
                BookAction.ADD,
                side=BookSide.BID,
                price="99.98",
                quantity="400",
                order_id="bid-3",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
            book_event(
                4,
                BookAction.ADD,
                side=BookSide.ASK,
                price="100.01",
                quantity="200",
                order_id="ask-anchor",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
        )
        attacked_side = BookSide.BID
        prices = ("100.00", "99.99")
        order_ids = ("bid-1", "bid-2")
        aggressor_side = AggressorSide.SELL
    else:
        initial = (
            book_event(
                1,
                BookAction.ADD,
                side=BookSide.BID,
                price="100.00",
                quantity="200",
                order_id="bid-anchor",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
            book_event(
                2,
                BookAction.ADD,
                side=BookSide.ASK,
                price="100.01",
                quantity="200",
                order_id="ask-1",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
            book_event(
                3,
                BookAction.ADD,
                side=BookSide.ASK,
                price="100.02",
                quantity="300",
                order_id="ask-2",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
            book_event(
                4,
                BookAction.ADD,
                side=BookSide.ASK,
                price="100.03",
                quantity="400",
                order_id="ask-3",
                sequence_stream_id=SHARED_STREAM_ID,
            ),
        )
        attacked_side = BookSide.ASK
        prices = ("100.01", "100.02")
        order_ids = ("ask-1", "ask-2")
        aggressor_side = AggressorSide.BUY
    for event in initial:
        book.apply(event)
    pre_snapshot = book.snapshot()

    observations: list[AggressiveTradeObservation] = []
    execution_states: list[BookExecutionState] = []
    depletion_snapshots: list[BookSnapshot] = []
    for execution_sequence, trade_sequence, price, quantity, order_id, trade_id in (
        (5, 6, prices[0], "200", order_ids[0], "economic-1"),
        (7, 8, prices[1], "100", order_ids[1], "economic-2"),
    ):
        execution = book_event(
            execution_sequence,
            BookAction.EXECUTE,
            side=attacked_side,
            price=price,
            quantity=quantity,
            order_id=order_id,
            trade_id=trade_id,
            sequence_stream_id=SHARED_STREAM_ID,
        )
        before, after = _apply(book, execution)
        assert before is not None
        execution_states.append(
            BookExecutionState(
                event=execution,
                pre_snapshot=before,
                post_snapshot=after,
            )
        )
        depletion_snapshots.append(after)
        trade = trade_event(
            trade_sequence,
            trade_id=trade_id,
            price=price,
            quantity=quantity,
            aggressor_side=aggressor_side,
            sequence_stream_id=SHARED_STREAM_ID,
        )
        book.observe_non_book_event(trade)
        observations.extend(reconcile_aggressive_trade_observations(execution, trade).observations)

    end_snapshot = book.snapshot()
    replenish_price = prices[0]
    responses: list[MarketStateObservation] = []
    for sequence, quantity, order_id in (
        (9, "100", "replenish-1"),
        (10, "200", "replenish-2"),
    ):
        event = book_event(
            sequence,
            BookAction.ADD,
            side=attacked_side,
            price=replenish_price,
            quantity=quantity,
            order_id=order_id,
            sequence_stream_id=SHARED_STREAM_ID,
        )
        book.apply(event)
        responses.append(
            MarketStateObservation(
                event_reference=market_event_reference(event),
                snapshot=book.snapshot(),
            )
        )
    return _Episode(
        direction=direction,
        observations=tuple(observations),
        pre_snapshot=pre_snapshot,
        end_snapshot=end_snapshot,
        executions=tuple(execution_states),
        depletion_snapshots=tuple(depletion_snapshots),
        responses=tuple(responses),
    )


def _service(*, minimum_normalized_aggression: str = "0.5") -> ShockResearchService:
    return ShockResearchService(
        ShockResearchConfig(
            shock_detection=ShockDetectionConfig(
                minimum_normalized_aggression=Decimal(minimum_normalized_aggression),
                minimum_aggressive_volume=Decimal("300"),
                minimum_levels_consumed=1,
            ),
            impact=ImpactConfig(horizons_events=(1, 2, 3)),
            resiliency=ResiliencyConfig(
                depth_levels_k=3,
                recovery_horizons_events=(1, 2, 3),
                multi_level_weights=(Decimal("0.5"), Decimal("0.3"), Decimal("0.2")),
            ),
        )
    )


@pytest.mark.parametrize("direction", (ShockDirection.SELL, ShockDirection.BUY))
def test_basic_directional_shock_fixture_is_symmetric(direction: ShockDirection) -> None:
    """Mirrored bid and ask episodes should produce the same exact SRA primitives."""
    episode = _build_episode(direction)

    result = _service().analyze_episode(
        direction=direction,
        aggressive_observations=episode.observations,
        pre_snapshot=episode.pre_snapshot,
        end_snapshot=episode.end_snapshot,
        book_executions=episode.executions,
        depletion_snapshots=episode.depletion_snapshots,
        response_observations=episode.responses,
    )

    assert result.status is ShockResearchStatus.SHOCK_CANDIDATE
    directional_volume = (
        result.flow_window.sell_volume
        if direction is ShockDirection.SELL
        else result.flow_window.buy_volume
    )
    assert directional_volume == Decimal("300")
    assert result.normalized_aggression is not None
    assert result.normalized_aggression.normalized_aggression == Decimal("300") / Decimal("450")
    assert result.level_penetration is not None
    assert result.level_penetration.levels_touched == 2
    assert result.level_penetration.levels_consumed == 1
    assert result.liquidity_shock is not None
    assert result.liquidity_shock.aggressive_volume == Decimal("300")
    assert result.resiliency is not None
    assert result.resiliency.baseline_depth == Decimal("900")
    assert result.resiliency.minimum_depth == Decimal("600")
    assert result.resiliency.consumed_depth == Decimal("300")
    assert result.resiliency.rr_by_horizon[0].replenishment_ratio == Decimal("1") / Decimal("3")
    assert result.resiliency.rr_by_horizon[1].replenishment_ratio == Decimal("1")
    assert result.resiliency.rr_by_horizon[2].replenishment_ratio is None
    assert result.impacts[2].available is False


def test_below_threshold_episode_does_not_materialize_responses() -> None:
    """Raw features should remain inspectable while shock/impact/resiliency stay absent."""
    episode = _build_episode(ShockDirection.SELL)

    result = _service(minimum_normalized_aggression="10").analyze_episode(
        direction=episode.direction,
        aggressive_observations=episode.observations,
        pre_snapshot=episode.pre_snapshot,
        end_snapshot=episode.end_snapshot,
        book_executions=episode.executions,
        depletion_snapshots=episode.depletion_snapshots,
        response_observations=episode.responses,
    )

    assert result.status is ShockResearchStatus.BELOW_THRESHOLDS
    assert result.shock_features is not None
    assert result.liquidity_shock is None
    assert result.impacts == ()
    assert result.resiliency is None


def test_known_corrupt_directional_episode_fails_without_partial_result() -> None:
    """Mismatched attacked-side executions should raise before any valid result is emitted."""
    sell_episode = _build_episode(ShockDirection.SELL)
    buy_episode = _build_episode(ShockDirection.BUY)

    with pytest.raises(ValueError, match="conflicts with shock direction"):
        _service().analyze_episode(
            direction=ShockDirection.SELL,
            aggressive_observations=sell_episode.observations,
            pre_snapshot=sell_episode.pre_snapshot,
            end_snapshot=sell_episode.end_snapshot,
            book_executions=buy_episode.executions,
            depletion_snapshots=sell_episode.depletion_snapshots,
            response_observations=sell_episode.responses,
        )
