"""Deterministic historical aggression-episode construction and SRA delegation tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tests.support.market_data import SHARED_STREAM_ID, book_event, trade_event
from tests.support.sra import response_observation, snapshot

from sra_nexus.market_data import AggressorSide, BookAction, BookSide
from sra_nexus.research import (
    AggressionEpisodeBuilder,
    AggressionEpisodeConfig,
    ReconciledAggressiveExecution,
    analyze_historical_aggression_episode,
)
from sra_nexus.sra import (
    ImpactConfig,
    ResiliencyConfig,
    ShockDetectionConfig,
    ShockResearchConfig,
    ShockResearchService,
    ShockResearchStatus,
)
from sra_nexus.sra.shock import BookExecutionState
from sra_nexus.sra.windows import reconcile_aggressive_trade_observations


def test_three_same_direction_executions_form_one_episode() -> None:
    """Two and one intervening market events remain one configured SELL burst."""
    records = (
        _record(10, 0, "100", AggressorSide.SELL),
        _record(20, 4, "200", AggressorSide.SELL),
        _record(30, 7, "150", AggressorSide.SELL),
    )

    episodes = AggressionEpisodeBuilder(_config(maximum_event_gap=2)).build(records)

    assert len(episodes) == 1
    episode = episodes[0]
    assert len(episode.observations) == 3
    assert sum((item.quantity for item in episode.observations), Decimal(0)) == Decimal("450")
    assert episode.start_event_index == 0
    assert episode.end_event_index == 8
    assert episode.pre_snapshot == records[0].execution.pre_snapshot
    assert episode.end_snapshot == records[-1].execution.post_snapshot
    assert episode.end_process_time == records[-1].observation.process_time


def test_direction_change_starts_a_new_episode() -> None:
    """SELL/SELL/BUY/BUY produces exactly two direction-pure episodes."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL),
        _record(20, 2, "10", AggressorSide.SELL),
        _record(30, 4, "10", AggressorSide.BUY),
        _record(40, 6, "10", AggressorSide.BUY),
    )

    episodes = AggressionEpisodeBuilder(_config()).build(records)

    assert tuple(item.direction.value for item in episodes) == ("SELL", "BUY")
    assert tuple(len(item.observations) for item in episodes) == (2, 2)


def test_market_event_gap_above_limit_starts_a_new_episode() -> None:
    """All normalized events between reconciled executions count toward the gap."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL),
        _record(20, 5, "10", AggressorSide.SELL),
    )

    episodes = AggressionEpisodeBuilder(_config(maximum_event_gap=2)).build(records)

    assert tuple(len(item.observations) for item in episodes) == (1, 1)


def test_exchange_time_gap_above_limit_starts_a_new_episode() -> None:
    """A close event index cannot bypass the separate exchange-clock gap."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL),
        _record(100, 2, "10", AggressorSide.SELL),
    )

    episodes = AggressionEpisodeBuilder(_config(maximum_exchange_gap="0.050")).build(records)

    assert tuple(len(item.observations) for item in episodes) == (1, 1)


def test_maximum_episode_market_event_span_starts_a_new_episode() -> None:
    """A locally close execution starts fresh when the inclusive span would exceed max."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL),
        _record(20, 2, "10", AggressorSide.SELL),
    )

    episodes = AggressionEpisodeBuilder(_config(maximum_episode_market_events=3)).build(records)

    assert tuple(len(item.observations) for item in episodes) == (1, 1)


def test_maximum_episode_exchange_duration_starts_a_new_episode() -> None:
    """The total episode clock span is enforced separately from each local gap."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL),
        _record(20, 2, "10", AggressorSide.SELL),
        _record(30, 4, "10", AggressorSide.SELL),
    )

    episodes = AggressionEpisodeBuilder(_config(maximum_episode_exchange_seconds="0.015")).build(
        records
    )

    assert tuple(len(item.observations) for item in episodes) == (2, 1)


def test_existing_sra_observation_cap_remains_a_separate_bound() -> None:
    """Do not confuse the volume-owning observation cap with market-event span."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL),
        _record(20, 2, "10", AggressorSide.SELL),
        _record(30, 4, "10", AggressorSide.SELL),
    )

    episodes = AggressionEpisodeBuilder(_config(), maximum_observations=2).build(records)

    assert tuple(len(item.observations) for item in episodes) == (2, 1)


def test_structural_segment_change_starts_a_new_episode() -> None:
    """A reset-derived segment boundary prevents directional continuation."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL, segment=0),
        _record(20, 2, "10", AggressorSide.SELL, segment=1),
    )

    episodes = AggressionEpisodeBuilder(_config()).build(records)

    assert tuple(item.segment for item in episodes) == (0, 1)
    assert tuple(len(item.observations) for item in episodes) == (1, 1)


def test_unknown_aggression_terminates_and_does_not_join_directional_episodes() -> None:
    """UNKNOWN interrupts continuity without being inferred from the resting side."""
    records = (
        _record(10, 0, "10", AggressorSide.SELL),
        _record(20, 2, "10", AggressorSide.UNKNOWN),
        _record(30, 4, "10", AggressorSide.SELL),
    )

    episodes = AggressionEpisodeBuilder(_config()).build(records)

    assert tuple(len(item.observations) for item in episodes) == (1, 1)
    assert all(
        observation.aggressor_side is AggressorSide.SELL
        for episode in episodes
        for observation in episode.observations
    )


def test_historical_episode_delegates_aggregate_normalized_aggression() -> None:
    """The runner bridge supplies all three trades to the frozen SRA equation."""
    records = (
        _record(
            10,
            0,
            "100",
            AggressorSide.SELL,
            pre_bids=(("100", "900"),),
            post_bids=(("100", "800"),),
        ),
        _record(
            20,
            2,
            "200",
            AggressorSide.SELL,
            pre_bids=(("100", "800"),),
            post_bids=(("100", "600"),),
        ),
        _record(
            30,
            4,
            "150",
            AggressorSide.SELL,
            pre_bids=(("100", "600"),),
            post_bids=(("100", "450"),),
        ),
    )
    episode = AggressionEpisodeBuilder(_config()).build(records)[0]
    future_snapshot = snapshot(
        40,
        bids=(("100", "675"),),
        asks=(("101", "100"),),
    )
    service = _service(minimum_levels_consumed=None)

    result = analyze_historical_aggression_episode(
        episode,
        (response_observation(40, future_snapshot),),
        service,
    )

    assert result.status is ShockResearchStatus.SHOCK_CANDIDATE
    assert result.flow_window.sell_volume == Decimal("450")
    assert len(result.flow_window.observations) == 3
    assert result.normalized_aggression is not None
    assert result.normalized_aggression.weighted_opposite_depth == Decimal("900")
    assert result.normalized_aggression.normalized_aggression == Decimal("0.5")


def test_multi_execution_episode_preserves_multi_level_penetration() -> None:
    """Partial, complete, then second-level execution yields touched=2/consumed=1."""
    records = (
        _record(
            10,
            0,
            "40",
            AggressorSide.SELL,
            price="100",
            pre_bids=(("100", "100"), ("99", "100")),
            post_bids=(("100", "60"), ("99", "100")),
        ),
        _record(
            20,
            2,
            "60",
            AggressorSide.SELL,
            price="100",
            pre_bids=(("100", "60"), ("99", "100")),
            post_bids=(("99", "100"),),
        ),
        _record(
            30,
            4,
            "30",
            AggressorSide.SELL,
            price="99",
            pre_bids=(("99", "100"),),
            post_bids=(("99", "70"),),
        ),
    )
    episode = AggressionEpisodeBuilder(_config()).build(records)[0]
    future_snapshot = snapshot(
        40,
        bids=(("100", "50"), ("99", "100")),
        asks=(("101", "100"),),
    )

    result = analyze_historical_aggression_episode(
        episode,
        (response_observation(40, future_snapshot),),
        _service(minimum_levels_consumed=1),
    )

    assert result.status is ShockResearchStatus.SHOCK_CANDIDATE
    assert result.level_penetration is not None
    assert result.level_penetration.levels_touched == 2
    assert result.level_penetration.levels_consumed == 1
    assert result.level_penetration.touched_prices == (Decimal("100"), Decimal("99"))
    assert result.level_penetration.consumed_prices == (Decimal("100"),)


def _record(
    event_sequence: int,
    execution_event_index: int,
    quantity: str,
    aggressor_side: AggressorSide,
    *,
    price: str = "100",
    segment: int = 0,
    pre_bids: tuple[tuple[str, str], ...] = (("100", "1000"),),
    post_bids: tuple[tuple[str, str], ...] = (("100", "900"),),
) -> ReconciledAggressiveExecution:
    side = BookSide.ASK if aggressor_side is AggressorSide.BUY else BookSide.BID
    pre_asks = ((price, "1000"),) if side is BookSide.ASK else (("101", "1000"),)
    post_asks = ((price, "900"),) if side is BookSide.ASK else (("101", "1000"),)
    effective_pre_bids = (("99", "1000"),) if side is BookSide.ASK else pre_bids
    effective_post_bids = (("99", "1000"),) if side is BookSide.ASK else post_bids
    trade_id = f"trade-{event_sequence}"
    event = book_event(
        event_sequence,
        BookAction.EXECUTE,
        side=side,
        price=price,
        quantity=quantity,
        order_id=f"order-{event_sequence}",
        trade_id=trade_id,
        sequence_stream_id=SHARED_STREAM_ID,
    )
    execution = BookExecutionState(
        event=event,
        pre_snapshot=snapshot(
            event_sequence - 1,
            bids=effective_pre_bids,
            asks=pre_asks,
            exchange_time=event.exchange_time - timedelta(microseconds=1),
            receive_time=event.receive_time - timedelta(microseconds=1),
            process_time=event.process_time - timedelta(microseconds=1),
        ),
        post_snapshot=snapshot(
            event_sequence,
            bids=effective_post_bids,
            asks=post_asks,
            exchange_time=event.exchange_time,
            receive_time=event.receive_time,
            process_time=event.process_time,
        ),
    )
    trade = trade_event(
        event_sequence + 1,
        trade_id=trade_id,
        price=price,
        quantity=quantity,
        aggressor_side=aggressor_side,
        sequence_stream_id=SHARED_STREAM_ID,
    )
    observation = reconcile_aggressive_trade_observations(event, trade).observations[0]
    return ReconciledAggressiveExecution(
        observation=observation,
        execution=execution,
        execution_event_index=execution_event_index,
        observation_event_index=execution_event_index + 1,
        segment=segment,
    )


def _config(
    *,
    maximum_event_gap: int = 4,
    maximum_exchange_gap: str = "1",
    maximum_episode_market_events: int = 20,
    maximum_episode_exchange_seconds: str = "1",
) -> AggressionEpisodeConfig:
    return AggressionEpisodeConfig(
        maximum_market_event_gap_between_executions=maximum_event_gap,
        maximum_exchange_time_gap_between_executions=Decimal(maximum_exchange_gap),
        maximum_episode_market_events=maximum_episode_market_events,
        maximum_episode_exchange_seconds=Decimal(maximum_episode_exchange_seconds),
    )


def _service(*, minimum_levels_consumed: int | None) -> ShockResearchService:
    return ShockResearchService(
        ShockResearchConfig(
            shock_detection=ShockDetectionConfig(
                minimum_normalized_aggression=Decimal("0.5"),
                minimum_aggressive_volume=Decimal("100"),
                minimum_levels_consumed=minimum_levels_consumed,
            ),
            impact=ImpactConfig(horizons_events=(1,)),
            resiliency=ResiliencyConfig(
                recovery_horizons_events=(1,),
                recovery_thresholds=(Decimal("0.5"),),
            ),
        )
    )
