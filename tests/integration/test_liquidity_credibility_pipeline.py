"""Full offline accepted-MBO lifecycle-to-credibility integration coverage."""

from decimal import Decimal
from pathlib import Path

from tests.support.market_data import INSTRUMENT

from sra_nexus.backtest import MarketReplay
from sra_nexus.market_data import BookDataMode, BookEvent, BookSnapshot, OrderBook
from sra_nexus.market_data.sources import MockMarketDataSource
from sra_nexus.sra import (
    LiquidityCredibilityConfig,
    LiquidityCredibilityResult,
    LiquidityCredibilityService,
    LiquidityShock,
    OrderLifecycleTracker,
    ShockDetectionMethod,
    ShockDirection,
    market_event_reference,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "market_data" / "liquidity_credibility.json"
)


def test_offline_mbo_fixture_produces_exact_liquidity_credibility() -> None:
    """Mock source, accepted book events, tracker, and credibility should agree."""
    normalized = MockMarketDataSource(FIXTURE).read()
    replay_snapshots = MarketReplay(OrderBook(INSTRUMENT)).replay(normalized)
    assert len(replay_snapshots) == 9

    events = tuple(event for event in normalized if isinstance(event, BookEvent))
    book = OrderBook(INSTRUMENT)
    tracker = OrderLifecycleTracker()
    pre_snapshot: BookSnapshot | None = None
    for event_index, event in enumerate(events):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
        if event.sequence_number == 3:
            pre_snapshot = book.snapshot()
    lifecycles = tracker.close_observation(
        market_event_reference(events[-1]),
        event_index=8,
    )
    assert pre_snapshot is not None
    start = events[3]
    end = events[5]
    shock = LiquidityShock(
        instrument_id=INSTRUMENT.instrument_id,
        direction=ShockDirection.SELL,
        start_exchange_time=start.exchange_time,
        end_exchange_time=end.exchange_time,
        start_process_time=start.process_time,
        end_process_time=end.process_time,
        start_reference=market_event_reference(start),
        end_reference=market_event_reference(end),
        aggressive_volume=Decimal("120"),
        normalized_aggression=Decimal("0.4"),
        levels_touched=1,
        levels_consumed=0,
        pre_spread=None,
        pre_depth=Decimal("300"),
        immediate_price_change=Decimal(0),
        detection_method=ShockDetectionMethod.DETERMINISTIC_THRESHOLDS,
        detection_version="shock-detection-v1",
    )
    analysis = LiquidityCredibilityService(
        LiquidityCredibilityConfig(
            attack_depth_levels=1,
            post_shock_event_horizon=3,
        )
    ).analyze(
        shock=shock,
        book_mode=BookDataMode.MARKET_BY_ORDER,
        pre_shock_snapshot=pre_snapshot,
        lifecycles=lifecycles,
        reset_events=tracker.reset_events,
        shock_start_event_index=3,
        shock_end_event_index=5,
        observation_end_event_index=8,
        observation_end_event_reference=market_event_reference(events[-1]),
    )

    assert isinstance(analysis, LiquidityCredibilityResult)
    assert analysis.raw_displayed_depth == Decimal("300")
    assert analysis.shock_executed_fraction == Decimal("0.4")
    assert analysis.shock_withdrawal_fraction == Decimal(1) / Decimal(3)
    assert analysis.quantity_survival_fraction == Decimal(4) / Decimal(15)
    assert analysis.replenishment_count == 1
    assert analysis.replenishment_executed_fraction == Decimal("0.6")
    assert analysis.replenishment_withdrawal_fraction == Decimal("0.4")
    assert analysis.absorption_cycle_count == 1
    assert analysis.available_at_process_time == events[-1].process_time
