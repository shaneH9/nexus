"""Post-shock toxicity orchestration, availability, and break invalidation tests."""

from decimal import Decimal
from typing import NamedTuple

import pytest
from tests.support.market_data import (
    INSTRUMENT,
    SHARED_STREAM_ID,
    book_event,
    quote_event,
    trade_event,
)
from tests.support.sra import snapshot
from tests.support.sra_comparison import recovery_time, resiliency_vector, shock_impact

from sra_nexus.market_data import (
    AggressorSide,
    BookAction,
    BookEvent,
    BookSide,
    MarketEvent,
    OrderBook,
    TradeEvent,
)
from sra_nexus.sra import (
    TOXICITY_VERSION,
    FailedAggressionComparison,
    FlowDirection,
    IndexedAggressiveFlowObservation,
    IndexedLiquidityShock,
    IndexedMarketStateObservation,
    LiquidityShock,
    MarketStateObservation,
    OrderLifecycle,
    OrderLifecycleTracker,
    ShockDetectionMethod,
    ShockDirection,
    ShockPairConfig,
    ShockPairService,
    ShockPairSpan,
    StructuralBreakKind,
    ToxicityAnalysis,
    ToxicityComparison,
    ToxicityConfig,
    ToxicityService,
    ToxicityStructuralBreak,
    ToxicityUnavailable,
    ToxicityUnavailableReason,
    ToxicityVector,
    calculate_bounded_excess_ratio,
    market_event_reference,
    reconcile_aggressive_trade_observations,
)


class _ToxicityInputs(NamedTuple):
    current_shock: IndexedLiquidityShock
    shock_history: tuple[IndexedLiquidityShock, ...]
    flow_observations: tuple[IndexedAggressiveFlowObservation, ...]
    market_observations: tuple[IndexedMarketStateObservation, ...]
    lifecycles: tuple[OrderLifecycle, ...]
    comparison: FailedAggressionComparison


def _config() -> ToxicityConfig:
    return ToxicityConfig(
        flow_window_events=3,
        shock_window_count=2,
        impact_horizon_events=2,
        replenishment_horizon_events=2,
        spread_baseline_events=2,
        volatility_baseline_events=2,
        volatility_response_events=2,
        withdrawal_depth_levels=1,
    )


def _shock(reference_event: MarketEvent) -> LiquidityShock:
    reference = market_event_reference(reference_event)
    return LiquidityShock(
        instrument_id=INSTRUMENT.instrument_id,
        direction=ShockDirection.SELL,
        start_exchange_time=reference.exchange_time,
        end_exchange_time=reference.exchange_time,
        start_process_time=reference.process_time,
        end_process_time=reference.process_time,
        start_reference=reference,
        end_reference=reference,
        aggressive_volume=Decimal("100"),
        normalized_aggression=Decimal("0.5"),
        levels_touched=1,
        levels_consumed=1,
        pre_spread=Decimal("0.01"),
        pre_depth=Decimal("200"),
        immediate_price_change=Decimal("-0.01"),
        detection_method=ShockDetectionMethod.DETERMINISTIC_THRESHOLDS,
        detection_version="shock-detection-v1",
    )


def _market_state(
    event_index: int,
    event: MarketEvent,
    *,
    bid: str,
    ask: str,
) -> IndexedMarketStateObservation:
    current_snapshot = snapshot(
        event.sequence_number,
        bids=((bid, "100"),),
        asks=((ask, "100"),),
        exchange_time=event.exchange_time,
        receive_time=event.receive_time,
        process_time=event.process_time,
    )
    return IndexedMarketStateObservation(
        event_index=event_index,
        observation=MarketStateObservation(
            event_reference=market_event_reference(event),
            snapshot=current_snapshot,
        ),
    )


def _liquidity_lifecycles(
    events: tuple[BookEvent, BookEvent, BookEvent],
    intermediate_event: TradeEvent,
    observation_end_event: TradeEvent,
) -> tuple[OrderLifecycle, ...]:
    indices = (3, 4, 6)
    book = OrderBook(INSTRUMENT, sequence_stream_id=SHARED_STREAM_ID)
    tracker = OrderLifecycleTracker()
    for event_index, event in zip(indices[:2], events[:2], strict=True):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
    book.observe_non_book_event(intermediate_event)
    book.apply(events[2])
    tracker.observe_accepted(events[2], event_index=indices[2])
    return tracker.close_observation(
        market_event_reference(observation_end_event),
        event_index=7,
    )


def _inputs() -> _ToxicityInputs:
    prior_event = trade_event(
        101,
        aggressor_side=AggressorSide.SELL,
        sequence_stream_id=SHARED_STREAM_ID,
    )
    flow_events = (
        trade_event(
            105,
            quantity="50",
            aggressor_side=AggressorSide.SELL,
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        trade_event(
            107,
            quantity="70",
            aggressor_side=AggressorSide.SELL,
            sequence_stream_id=SHARED_STREAM_ID,
        ),
    )
    lifecycle_events = (
        book_event(
            103,
            BookAction.ADD,
            side=BookSide.BID,
            order_id="tox-bid",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        book_event(
            104,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.01",
            order_id="tox-ask",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        book_event(
            106,
            BookAction.MODIFY,
            side=BookSide.BID,
            quantity="80",
            order_id="tox-bid",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
    )
    state_events: dict[int, MarketEvent] = {
        2: quote_event(102, sequence_stream_id=SHARED_STREAM_ID),
        3: lifecycle_events[0],
        4: lifecycle_events[1],
        5: flow_events[0],
        6: lifecycle_events[2],
        7: flow_events[1],
    }
    prices = {
        2: ("100.00", "100.01"),
        3: ("100.01", "100.02"),
        4: ("100.00", "100.01"),
        5: ("100.00", "100.01"),
        6: ("100.02", "100.03"),
        7: ("100.00", "100.03"),
    }
    market_observations = tuple(
        _market_state(index, state_events[index], bid=prices[index][0], ask=prices[index][1])
        for index in range(2, 8)
    )
    flow_observations = tuple(
        IndexedAggressiveFlowObservation(
            event_index=index,
            observation=reconcile_aggressive_trade_observations(
                None,
                event,
            ).observations[0],
        )
        for index, event in zip((5, 7), flow_events, strict=True)
    )
    shock_1 = _shock(prior_event)
    shock_2 = _shock(flow_events[0])
    indexed_1 = IndexedLiquidityShock(
        shock=shock_1,
        start_event_index=1,
        end_event_index=1,
    )
    indexed_2 = IndexedLiquidityShock(
        shock=shock_2,
        start_event_index=5,
        end_event_index=5,
    )
    pair_config = ShockPairConfig(
        max_event_distance=10,
        max_exchange_seconds=Decimal("1"),
        required_impact_horizons_events=(2,),
        required_resiliency_horizons_events=(2,),
        required_recovery_thresholds=(Decimal("0.5"),),
    )
    comparison = ShockPairService(pair_config).compare(
        shock_1=shock_1,
        shock_2=shock_2,
        span=ShockPairSpan(event_distance=3),
        impacts_1=(shock_impact(shock_1, 2, "0.01"),),
        impacts_2=(shock_impact(shock_2, 2, "0.02"),),
        resiliency_1=resiliency_vector(
            shock_1,
            ((2, "0.3"),),
            (recovery_time("0.5", 2),),
        ),
        resiliency_2=resiliency_vector(
            shock_2,
            ((2, "0.8"),),
            (recovery_time("0.5", 1),),
        ),
    )
    assert comparison.comparison_available
    return _ToxicityInputs(
        current_shock=indexed_2,
        shock_history=(indexed_1, indexed_2),
        flow_observations=flow_observations,
        market_observations=market_observations,
        lifecycles=_liquidity_lifecycles(
            lifecycle_events,
            flow_events[0],
            flow_events[1],
        ),
        comparison=comparison,
    )


def _analyze(
    inputs: _ToxicityInputs,
    *,
    market_observations: tuple[IndexedMarketStateObservation, ...] | None = None,
    flow_observations: tuple[IndexedAggressiveFlowObservation, ...] | None = None,
    structural_breaks: tuple[ToxicityStructuralBreak, ...] = (),
) -> ToxicityAnalysis:
    return ToxicityService(_config()).analyze(
        current_shock=inputs.current_shock,
        shock_history=inputs.shock_history,
        flow_observations=(
            inputs.flow_observations if flow_observations is None else flow_observations
        ),
        market_observations=(
            inputs.market_observations if market_observations is None else market_observations
        ),
        lifecycles=inputs.lifecycles,
        comparison=inputs.comparison,
        structural_breaks=structural_breaks,
    )


def test_service_builds_complete_post_shock_vector_at_latest_required_process_time() -> None:
    """Mandatory response components should make the vector observable only at event 7."""
    inputs = _inputs()

    result = _analyze(inputs)

    assert isinstance(result, ToxicityVector)
    assert result.feature_version == TOXICITY_VERSION
    assert result.observation_start_event_index == 1
    assert result.observation_end_event_index == 7
    assert (
        result.available_at_process_time
        == inputs.market_observations[-1].observation.event_reference.process_time
    )
    assert (
        result.as_of_event_reference == inputs.market_observations[-1].observation.event_reference
    )
    assert result.flow.flow_persistence == 1
    assert result.flow.net_flow_direction is FlowDirection.SELL
    assert result.shock_persistence.same_direction_run_length == 2
    assert result.replenishment.raw_replenishment_failure == Decimal("0.2")
    assert result.liquidity.attacked_side is BookSide.BID
    assert result.liquidity.attacked.executed_quantity == 0
    assert result.market_state.spread.spread_expansion_ratio == Decimal("3")
    assert result.market_state.spread.bounded_spread_expansion == Decimal(2) / Decimal(3)
    assert result.market_state.volatility.bounded_volatility_jump == (
        calculate_bounded_excess_ratio(result.market_state.volatility.volatility_jump_ratio)
    )
    assert not result.credibility.available
    assert result.credibility.liquidity_credibility is None
    assert Decimal(0) <= result.toxicity_score <= Decimal(1)
    assert not hasattr(result, "signal")
    assert not hasattr(result, "order")


def test_service_does_not_read_flow_after_configured_observation_end() -> None:
    """A large later BUY observation must not leak backward into event-7 toxicity."""
    inputs = _inputs()
    baseline = _analyze(inputs)
    assert isinstance(baseline, ToxicityVector)
    later_event = trade_event(
        108,
        quantity="1000000",
        aggressor_side=AggressorSide.BUY,
        sequence_stream_id=SHARED_STREAM_ID,
    )
    later_state = _market_state(8, later_event, bid="100.00", ask="100.01")
    later_flow = IndexedAggressiveFlowObservation(
        event_index=8,
        observation=reconcile_aggressive_trade_observations(
            None,
            later_event,
        ).observations[0],
    )

    with_later_evidence = _analyze(
        inputs,
        market_observations=(*inputs.market_observations, later_state),
        flow_observations=(*inputs.flow_observations, later_flow),
    )

    assert isinstance(with_later_evidence, ToxicityVector)
    assert with_later_evidence.toxicity_score == baseline.toxicity_score
    assert with_later_evidence.flow == baseline.flow
    assert with_later_evidence.observation_end_event_index == 7


@pytest.mark.parametrize("kind", tuple(StructuralBreakKind))
def test_each_known_structural_break_invalidates_a_spanning_window(
    kind: StructuralBreakKind,
) -> None:
    """RESET, corruption, and data gaps should never be crossed by toxicity windows."""
    inputs = _inputs()
    break_reference = inputs.market_observations[1].observation.event_reference

    result = _analyze(
        inputs,
        structural_breaks=(
            ToxicityStructuralBreak(
                kind=kind,
                event_index=3,
                event_reference=break_reference,
            ),
        ),
    )

    assert isinstance(result, ToxicityUnavailable)
    assert ToxicityUnavailableReason.STRUCTURAL_BREAK in result.reasons
    assert result.structural_breaks[0].kind is kind


def test_missing_true_event_index_returns_typed_unavailability() -> None:
    """Sequence numbers must not be substituted for absent normalized-event indices."""
    inputs = _inputs()
    missing_index = inputs.market_observations[0].model_copy(update={"event_index": None})

    result = _analyze(inputs, market_observations=(missing_index, *inputs.market_observations[1:]))

    assert isinstance(result, ToxicityUnavailable)
    assert result.reasons == (ToxicityUnavailableReason.MISSING_EVENT_INDEX,)


def test_one_sided_book_returns_unavailable_without_fabricating_spread() -> None:
    """A missing ask should retain a one-sided flag and no artificial large spread."""
    inputs = _inputs()
    original = inputs.market_observations[-1]
    reference = original.observation.event_reference
    one_sided_snapshot = snapshot(
        reference.sequence_number,
        bids=(("100.00", "100"),),
        asks=(),
        exchange_time=reference.exchange_time,
        receive_time=reference.receive_time,
        process_time=reference.process_time,
    )
    one_sided = IndexedMarketStateObservation(
        event_index=original.event_index,
        observation=MarketStateObservation(
            event_reference=reference,
            snapshot=one_sided_snapshot,
        ),
    )

    result = _analyze(
        inputs,
        market_observations=(*inputs.market_observations[:-1], one_sided),
    )

    assert isinstance(result, ToxicityUnavailable)
    assert ToxicityUnavailableReason.MISSING_SPREAD in result.reasons
    assert result.one_sided_book


def test_pair_delta_contract_is_exact() -> None:
    """Optional pair toxicity change should remain a signed descriptive comparison."""
    inputs = _inputs()
    pair = inputs.comparison.pair
    assert pair is not None

    comparison = ToxicityComparison(
        pair_id=pair.pair_id,
        shock_1_id=pair.shock_1_id,
        shock_2_id=pair.shock_2_id,
        toxicity_1=Decimal("0.75"),
        toxicity_2=Decimal("0.40"),
        delta_toxicity=Decimal("-0.35"),
    )

    assert comparison.delta_toxicity == Decimal("-0.35")
