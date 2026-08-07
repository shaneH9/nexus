"""Deterministic Milestone K research contracts for focused tests."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sra_nexus.common.types import (
    InstrumentId,
    ResearchObservationId,
    ShockId,
    ShockPairId,
)
from sra_nexus.market_data import MarketEventKind
from sra_nexus.research import (
    BaselineFeatureSnapshot,
    FeatureAvailability,
    FeatureVersion,
    LabelUnavailableReason,
    ResearchObservation,
    SRAFeatureSnapshot,
    UnavailableForwardLabel,
)
from sra_nexus.sra import (
    FailedAggressionComparison,
    IndexedMarketStateObservation,
    MarketEventReference,
    MarketStateObservation,
    ShockDirection,
    ShockPairConfig,
    ShockPairService,
    ShockPairSpan,
)
from tests.support.market_data import INSTRUMENT, SHARED_STREAM_ID
from tests.support.sra import SRA_BASE_TIME, liquidity_shock, response_observation, snapshot
from tests.support.sra_comparison import recovery_time, resiliency_vector, shock_impact


def indexed_state(
    event_index: int,
    midprice: str = "100",
    *,
    instrument_id: InstrumentId = INSTRUMENT.instrument_id,
    venue: str = INSTRUMENT.exchange,
) -> IndexedMarketStateObservation:
    """Build one normalized-event-indexed two-sided book around an exact midprice."""
    middle = Decimal(midprice)
    exchange_time = SRA_BASE_TIME + timedelta(seconds=event_index)
    current_snapshot = snapshot(
        event_index,
        bids=((str(middle - Decimal("0.01")), "100"),),
        asks=((str(middle + Decimal("0.01")), "100"),),
        exchange_time=exchange_time,
        receive_time=exchange_time + timedelta(microseconds=1),
        process_time=exchange_time + timedelta(microseconds=2),
    )
    if instrument_id != INSTRUMENT.instrument_id or venue != INSTRUMENT.exchange:
        current_snapshot = current_snapshot.model_copy(
            update={"instrument_id": instrument_id, "venue": venue}
        )
    observation = response_observation(
        event_index,
        current_snapshot,
        exchange_time=exchange_time,
        process_time=exchange_time + timedelta(microseconds=2),
    )
    if instrument_id != INSTRUMENT.instrument_id or venue != INSTRUMENT.exchange:
        observation = MarketStateObservation(
            event_reference=observation.event_reference.model_copy(
                update={"instrument_id": instrument_id, "venue": venue}
            ),
            snapshot=current_snapshot,
        )
    return IndexedMarketStateObservation(event_index=event_index, observation=observation)


def completed_comparison(
    direction: ShockDirection = ShockDirection.SELL,
) -> FailedAggressionComparison:
    """Build a small complete pair comparison with two event horizons."""
    config = ShockPairConfig(
        required_impact_horizons_events=(1, 2),
        required_resiliency_horizons_events=(1, 2),
        required_recovery_thresholds=(Decimal("0.5"),),
    )
    shock_1 = liquidity_shock(
        direction=direction,
        normalized_aggression="0.5",
        end_time=SRA_BASE_TIME,
    )
    shock_2 = liquidity_shock(
        direction=direction,
        normalized_aggression="0.75",
        end_time=SRA_BASE_TIME + timedelta(seconds=1),
    )
    return ShockPairService(config).compare(
        shock_1=shock_1,
        shock_2=shock_2,
        span=ShockPairSpan(event_distance=1),
        impacts_1=(shock_impact(shock_1, 1, "0.02"), shock_impact(shock_1, 2, "0.03")),
        impacts_2=(shock_impact(shock_2, 1, "0.01"), shock_impact(shock_2, 2, "0.01")),
        resiliency_1=resiliency_vector(
            shock_1,
            ((1, "0.4"), (2, "0.5")),
            (recovery_time("0.5", 2),),
        ),
        resiliency_2=resiliency_vector(
            shock_2,
            ((1, "0.6"), (2, "0.8")),
            (recovery_time("0.5", 1),),
        ),
    )


def research_observation(
    anchor_index: int,
    *,
    maximum_horizon: int = 100,
    identity: int | None = None,
    instrument_id: InstrumentId = INSTRUMENT.instrument_id,
    venue: str = INSTRUMENT.exchange,
) -> ResearchObservation:
    """Build a valid compact row for split, export, and permutation tests."""
    reference = _reference(anchor_index, instrument_id, venue)
    baseline = BaselineFeatureSnapshot(
        depth_levels=1,
        spread=Decimal("0.02"),
        midprice=Decimal("100"),
        microprice=Decimal("100"),
        microprice_offset=Decimal(0),
        order_book_imbalance=Decimal(0),
        raw_bid_depth=Decimal("100"),
        raw_ask_depth=Decimal("100"),
        weighted_bid_depth=Decimal("100"),
        weighted_ask_depth=Decimal("100"),
        weighted_depth_weights=(Decimal(1),),
        backward_features=(),
    )
    availability = FeatureAvailability(
        feature_name="baseline_market_state",
        available_at_process_time=reference.process_time,
        source_data_identifier=f"event-{anchor_index}",
    )
    features = SRAFeatureSnapshot(
        direction=ShockDirection.SELL,
        normalized_aggression_1=Decimal("0.5"),
        normalized_aggression_2=Decimal("0.5"),
        aggression_ratio=Decimal(1),
        effectiveness_by_horizon=(),
        resiliency_by_horizon=(),
        recovery_time_deltas=(),
        absorption_by_horizon=(),
        liquidity_credibility=None,
        toxicity=None,
        baseline=baseline,
        feature_availability=(availability,),
        feature_available_at_process_time=reference.process_time,
    )
    label = UnavailableForwardLabel(
        horizon_events=maximum_horizon,
        direction=ShockDirection.SELL,
        prediction_anchor_event_index=anchor_index,
        prediction_anchor_event_reference=reference,
        unavailable_reason=LabelUnavailableReason.MISSING_FUTURE_EVENT,
    )
    row_identity = anchor_index + 1 if identity is None else identity
    return ResearchObservation(
        observation_id=ResearchObservationId(UUID(int=row_identity)),
        instrument_id=instrument_id,
        venue=venue,
        feature_event_reference=reference,
        feature_exchange_time=reference.exchange_time,
        feature_process_time=reference.process_time,
        feature_available_at_process_time=reference.process_time,
        prediction_anchor_event_index=anchor_index,
        prediction_anchor_event_reference=reference,
        prediction_anchor_exchange_time=reference.exchange_time,
        prediction_anchor_process_time=reference.process_time,
        shock_1_id=ShockId(UUID(int=(row_identity * 3) + 1)),
        shock_2_id=ShockId(UUID(int=(row_identity * 3) + 2)),
        pair_id=ShockPairId(UUID(int=(row_identity * 3) + 3)),
        feature_version_bundle=(FeatureVersion(feature_name="baseline", version="v1"),),
        features=features,
        labels=(label,),
        maximum_label_horizon_events=maximum_horizon,
        label_window_end_event_index=anchor_index + maximum_horizon,
        source_data_identifiers=(f"source-{row_identity}",),
    )


def _reference(
    event_index: int,
    instrument_id: InstrumentId,
    venue: str,
) -> MarketEventReference:
    current_time = SRA_BASE_TIME + timedelta(seconds=event_index)
    return MarketEventReference(
        instrument_id=instrument_id,
        venue=venue,
        sequence_stream_id=SHARED_STREAM_ID,
        sequence_number=event_index,
        event_kind=MarketEventKind.QUOTE,
        event_id=UUID(int=event_index + 1000),
        exchange_time=current_time,
        receive_time=current_time + timedelta(microseconds=1),
        process_time=current_time + timedelta(microseconds=2),
    )
