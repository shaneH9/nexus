"""Full offline normalized-market-data to Milestone G research path."""

from decimal import Decimal
from pathlib import Path

from tests.support.market_data import INSTRUMENT, SHARED_STREAM_ID

from sra_nexus.backtest import MarketReplay
from sra_nexus.common.types import MarketTradeId
from sra_nexus.market_data import (
    BookAction,
    BookEvent,
    BookSnapshot,
    OrderBook,
    TradeEvent,
)
from sra_nexus.market_data.sources import MockMarketDataSource
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

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "market_data" / "sell_shock_recovery.json"
)


def test_offline_market_fixture_produces_exact_shock_impact_and_resiliency() -> None:
    """Mock source, replay/book, reconciliation, and SRA service should agree exactly."""
    events = MockMarketDataSource(FIXTURE).read()
    replay_snapshots = MarketReplay(
        OrderBook(INSTRUMENT, sequence_stream_id=SHARED_STREAM_ID)
    ).replay(events)
    assert len(replay_snapshots) == 8

    book = OrderBook(INSTRUMENT, sequence_stream_id=SHARED_STREAM_ID)
    pre_snapshot: BookSnapshot | None = None
    end_snapshot: BookSnapshot | None = None
    executions_by_trade_id: dict[MarketTradeId, BookEvent] = {}
    execution_states: list[BookExecutionState] = []
    depletion_snapshots: list[BookSnapshot] = []
    aggressive_observations: list[AggressiveTradeObservation] = []
    responses: list[MarketStateObservation] = []
    for event in events:
        if isinstance(event, BookEvent):
            before = None if book.last_sequence is None else book.snapshot()
            book.apply(event)
            after = book.snapshot()
            if event.sequence_number == 4:
                pre_snapshot = after
            if event.action is BookAction.EXECUTE:
                assert before is not None
                assert event.trade_id is not None
                executions_by_trade_id[event.trade_id] = event
                execution_states.append(
                    BookExecutionState(
                        event=event,
                        pre_snapshot=before,
                        post_snapshot=after,
                    )
                )
                depletion_snapshots.append(after)
            if event.sequence_number in (9, 10):
                responses.append(
                    MarketStateObservation(
                        event_reference=market_event_reference(event),
                        snapshot=after,
                    )
                )
        elif isinstance(event, TradeEvent):
            book.observe_non_book_event(event)
            assert event.trade_id is not None
            execution = executions_by_trade_id[event.trade_id]
            aggressive_observations.extend(
                reconcile_aggressive_trade_observations(execution, event).observations
            )
            if event.sequence_number == 8:
                end_snapshot = book.snapshot()

    assert pre_snapshot is not None
    assert end_snapshot is not None
    service = ShockResearchService(
        ShockResearchConfig(
            shock_detection=ShockDetectionConfig(
                minimum_normalized_aggression=Decimal("0.5"),
                minimum_aggressive_volume=Decimal("300"),
                minimum_levels_consumed=1,
            ),
            impact=ImpactConfig(horizons_events=(1, 2)),
            resiliency=ResiliencyConfig(
                depth_levels_k=3,
                recovery_horizons_events=(1, 2),
                multi_level_weights=(Decimal("0.5"), Decimal("0.3"), Decimal("0.2")),
            ),
        )
    )

    result = service.analyze_episode(
        direction=ShockDirection.SELL,
        aggressive_observations=aggressive_observations,
        pre_snapshot=pre_snapshot,
        end_snapshot=end_snapshot,
        book_executions=execution_states,
        depletion_snapshots=depletion_snapshots,
        response_observations=responses,
    )

    assert result.status is ShockResearchStatus.SHOCK_CANDIDATE
    assert result.flow_window.sell_volume == Decimal("300")
    assert result.flow_window.unknown_volume == 0
    assert result.normalized_aggression is not None
    assert result.normalized_aggression.normalized_aggression == Decimal("2") / Decimal("3")
    assert result.level_penetration is not None
    assert result.level_penetration.levels_touched == 2
    assert result.level_penetration.levels_consumed == 1
    assert result.resiliency is not None
    assert result.resiliency.baseline_depth == Decimal("900")
    assert result.resiliency.minimum_depth == Decimal("600")
    assert result.resiliency.consumed_depth == Decimal("300")
    assert result.resiliency.rr_by_horizon[0].replenishment_ratio == Decimal("1") / Decimal("3")
    assert result.resiliency.rr_by_horizon[1].replenishment_ratio == Decimal("1")
    assert result.impacts[0].raw_price_impact == 0
    assert result.impacts[1].raw_price_impact == 0
