"""Past-only construction of typed SRA and baseline feature snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel, NonBlankStr, UtcDatetime
from sra_nexus.research.models import (
    AbsorptionFeature,
    BackwardMarketFeature,
    BaselineFeatureSnapshot,
    CredibilityRawComponents,
    EffectivenessFeature,
    FeatureAvailability,
    FeatureSnapshotConfig,
    LiquidityCredibilityFeature,
    RecoveryTimeFeature,
    ResiliencyFeature,
    SRAFeatureSnapshot,
    ToxicityFeature,
)
from sra_nexus.sra.comparison import FailedAggressionComparison
from sra_nexus.sra.credibility import (
    LiquidityCredibilityComparison,
    LiquidityCredibilityResult,
)
from sra_nexus.sra.state import MarketEventReference
from sra_nexus.sra.toxicity import (
    IndexedMarketStateObservation,
    ToxicityComparison,
    ToxicityVector,
)
from sra_nexus.sra.toxicity_math import calculate_event_time_realized_volatility


class SRAFeatureInput(ContractModel):
    """Completed upstream feature objects and their explicit availability boundary."""

    comparison: FailedAggressionComparison
    comparison_event_index: int = Field(ge=0)
    comparison_event_reference: MarketEventReference
    comparison_available_at_process_time: UtcDatetime
    comparison_source_data_identifier: NonBlankStr
    liquidity_credibility_1: LiquidityCredibilityResult | None = None
    liquidity_credibility_2: LiquidityCredibilityResult | None = None
    liquidity_credibility_comparison: LiquidityCredibilityComparison | None = None
    toxicity: ToxicityVector | None = None
    toxicity_comparison: ToxicityComparison | None = None

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        """Require complete, aligned selected feature families and explicit timing."""
        comparison = self.comparison
        if not comparison.comparison_available or comparison.pair is None:
            raise ValueError("research features require a complete failed-aggression comparison")
        if self.comparison_event_reference.instrument_id != comparison.instrument_id:
            raise ValueError("comparison event reference must share instrument_id")
        if self.comparison_available_at_process_time != (
            self.comparison_event_reference.process_time
        ):
            raise ValueError("comparison availability must be owned by its event reference")
        credibility_values = (
            self.liquidity_credibility_1,
            self.liquidity_credibility_2,
            self.liquidity_credibility_comparison,
        )
        if any(value is not None for value in credibility_values) and not all(
            value is not None for value in credibility_values
        ):
            raise ValueError("liquidity credibility requires two results and their comparison")
        if all(value is not None for value in credibility_values):
            first = _required(self.liquidity_credibility_1)
            second = _required(self.liquidity_credibility_2)
            delta = _required(self.liquidity_credibility_comparison)
            if (
                first.shock_id != comparison.shock_1_id
                or second.shock_id != comparison.shock_2_id
                or delta.pair_id != comparison.pair_id
            ):
                raise ValueError("liquidity credibility inputs must follow the shock pair")
            if (
                first.instrument_id != comparison.instrument_id
                or second.instrument_id != comparison.instrument_id
            ):
                raise ValueError("liquidity credibility inputs must share instrument_id")
            if first.credibility_score is not None and (
                delta.liquidity_credibility_1 != first.credibility_score
            ):
                raise ValueError("first credibility score must match its pair comparison")
            if second.credibility_score is not None and (
                delta.liquidity_credibility_2 != second.credibility_score
            ):
                raise ValueError("second credibility score must match its pair comparison")
        if self.toxicity is not None:
            if (
                self.toxicity.instrument_id != comparison.instrument_id
                or self.toxicity.shock_id != comparison.shock_2_id
            ):
                raise ValueError("toxicity must describe the pair's second shock")
        if self.toxicity_comparison is not None:
            if self.toxicity is None or self.toxicity_comparison.pair_id != comparison.pair_id:
                raise ValueError("toxicity comparison requires an aligned current vector")
            if self.toxicity_comparison.toxicity_2 != self.toxicity.toxicity_score:
                raise ValueError("current toxicity score must match its pair comparison")
        return self


class FeatureBuildResult(ContractModel):
    """One causal snapshot paired with its selected normalized-event anchor."""

    features: SRAFeatureSnapshot
    prediction_anchor_event_index: int = Field(ge=0)
    prediction_anchor_event_reference: MarketEventReference

    @model_validator(mode="after")
    def validate_anchor(self) -> Self:
        """Reject any feature family whose availability crosses the anchor."""
        anchor_time = self.prediction_anchor_event_reference.process_time
        if self.features.feature_available_at_process_time > anchor_time:
            raise ValueError("feature availability cannot follow prediction anchor")
        if any(
            item.available_at_process_time > anchor_time
            for item in self.features.feature_availability
        ):
            raise ValueError("individual feature availability cannot follow prediction anchor")
        return self


class SRAFeatureSnapshotBuilder:
    """Flatten completed SRA objects and past book states without reading labels."""

    def __init__(
        self,
        config: FeatureSnapshotConfig | None = None,
    ) -> None:
        """Configure transparent depth and backward-looking event horizons."""
        self._config = FeatureSnapshotConfig() if config is None else config

    @property
    def config(self) -> FeatureSnapshotConfig:
        """Return the immutable feature policy."""
        return self._config

    def build(
        self,
        feature_input: SRAFeatureInput,
        market_states: Sequence[IndexedMarketStateObservation],
    ) -> FeatureBuildResult:
        """Build one snapshot at the first state observing every selected feature."""
        states = _ordered_states(
            market_states,
            feature_input.comparison_event_reference,
        )
        upstream_availability = feature_availability_from_input(feature_input)
        anchor = select_prediction_anchor(
            states,
            upstream_availability,
            minimum_event_index=feature_input.comparison_event_index,
        )
        anchor_index = _required_event_index(anchor)
        if any(_required_event_index(state) > anchor_index for state in states):
            raise ValueError("feature builder accepts market states only through prediction anchor")
        baseline = _build_baseline(
            states,
            anchor_index,
            self._config,
        )
        availability = (
            *upstream_availability,
            FeatureAvailability(
                feature_name="baseline_market_state",
                available_at_process_time=anchor.observation.event_reference.process_time,
                source_data_identifier=str(anchor.observation.event_reference.event_id),
            ),
        )
        comparison = feature_input.comparison
        first_effectiveness = comparison.effectiveness_by_horizon[0]
        normalized_1 = first_effectiveness.ae_1.normalized_aggression
        normalized_2 = first_effectiveness.ae_2.normalized_aggression
        features = SRAFeatureSnapshot(
            direction=_required(comparison.direction),
            normalized_aggression_1=normalized_1,
            normalized_aggression_2=normalized_2,
            aggression_ratio=comparison.aggression_ratio,
            effectiveness_by_horizon=tuple(
                EffectivenessFeature(
                    horizon_events=item.horizon_events,
                    ae_1=item.ae_1.effectiveness,
                    ae_2=item.ae_2.effectiveness,
                    delta_ae=item.delta_ae,
                    relative_ae_change=item.relative_ae_change,
                )
                for item in comparison.effectiveness_by_horizon
            ),
            resiliency_by_horizon=tuple(
                ResiliencyFeature(
                    horizon_events=item.horizon_events,
                    rr_1=_required(item.rr_1),
                    rr_2=_required(item.rr_2),
                    delta_rr=_required(item.delta_rr),
                )
                for item in comparison.resiliency_by_horizon
                if item.available
            ),
            recovery_time_deltas=tuple(
                RecoveryTimeFeature(
                    threshold=item.threshold,
                    delta_events=item.delta_events,
                    delta_exchange_seconds=item.delta_exchange_seconds,
                    delta_process_seconds=item.delta_process_seconds,
                    available=item.available,
                )
                for item in comparison.recovery_comparisons
            ),
            absorption_by_horizon=tuple(
                AbsorptionFeature(
                    horizon_events=item.horizon_events,
                    absorption_efficiency_1=item.abs_eff_1.magnitude_efficiency,
                    absorption_efficiency_2=item.abs_eff_2.magnitude_efficiency,
                    delta_absorption_efficiency=item.delta_absorption_efficiency,
                )
                for item in comparison.absorption_efficiency_by_horizon
            ),
            liquidity_credibility=_credibility_feature(feature_input),
            toxicity=_toxicity_feature(feature_input),
            baseline=baseline,
            feature_availability=availability,
            feature_available_at_process_time=max(
                item.available_at_process_time for item in availability
            ),
            feature_version=self._config.feature_version,
        )
        return FeatureBuildResult(
            features=features,
            prediction_anchor_event_index=anchor_index,
            prediction_anchor_event_reference=anchor.observation.event_reference,
        )


def evaluate_failed_aggression_condition(
    snapshot: SRAFeatureSnapshot,
    *,
    effectiveness_horizon_events: int,
    resiliency_horizon_events: int,
    delta_ae_threshold: Decimal = Decimal(0),
    delta_rr_threshold: Decimal = Decimal(0),
) -> bool:
    """Evaluate caller-declared ``DeltaAE < threshold`` and ``DeltaRR > threshold``."""
    effectiveness = next(
        (
            item
            for item in snapshot.effectiveness_by_horizon
            if item.horizon_events == effectiveness_horizon_events
        ),
        None,
    )
    resiliency = next(
        (
            item
            for item in snapshot.resiliency_by_horizon
            if item.horizon_events == resiliency_horizon_events
        ),
        None,
    )
    if effectiveness is None or resiliency is None:
        raise ValueError("failed-aggression condition requires configured feature horizons")
    return effectiveness.delta_ae < delta_ae_threshold and resiliency.delta_rr > delta_rr_threshold


def select_prediction_anchor(
    market_states: Sequence[IndexedMarketStateObservation],
    feature_availability: Sequence[FeatureAvailability],
    *,
    minimum_event_index: int = 0,
) -> IndexedMarketStateObservation:
    """Select the first normalized state at which all selected features are usable."""
    states = tuple(sorted(market_states, key=_required_event_index))
    indices = tuple(_required_event_index(state) for state in states)
    if len(indices) != len(set(indices)):
        raise ValueError("prediction-anchor states require unique event indices")
    availability = tuple(feature_availability)
    if not availability:
        raise ValueError("prediction anchor requires feature availability")
    if minimum_event_index < 0:
        raise ValueError("minimum prediction-anchor event index must be non-negative")
    latest = max(item.available_at_process_time for item in availability)
    anchor = next(
        (
            state
            for state in states
            if state.observation.event_reference.process_time >= latest
            and _required_event_index(state) >= minimum_event_index
        ),
        None,
    )
    if anchor is None:
        raise ValueError("no normalized market state observes all selected features")
    return anchor


def feature_availability_from_input(
    feature_input: SRAFeatureInput,
) -> tuple[FeatureAvailability, ...]:
    """Return causal availability provenance for every selected upstream family."""
    values = [
        FeatureAvailability(
            feature_name="failed_aggression_comparison",
            available_at_process_time=feature_input.comparison_available_at_process_time,
            source_data_identifier=feature_input.comparison_source_data_identifier,
        )
    ]
    if feature_input.liquidity_credibility_1 is not None:
        first = feature_input.liquidity_credibility_1
        second = _required(feature_input.liquidity_credibility_2)
        values.extend(
            (
                FeatureAvailability(
                    feature_name="liquidity_credibility_1",
                    available_at_process_time=first.available_at_process_time,
                    source_data_identifier=str(first.shock_id),
                ),
                FeatureAvailability(
                    feature_name="liquidity_credibility_2",
                    available_at_process_time=second.available_at_process_time,
                    source_data_identifier=str(second.shock_id),
                ),
            )
        )
    if feature_input.toxicity is not None:
        values.append(
            FeatureAvailability(
                feature_name="toxicity",
                available_at_process_time=feature_input.toxicity.available_at_process_time,
                source_data_identifier=str(feature_input.toxicity.shock_id),
            )
        )
    return tuple(values)


def _ordered_states(
    market_states: Sequence[IndexedMarketStateObservation],
    feature_reference: MarketEventReference,
) -> tuple[IndexedMarketStateObservation, ...]:
    relevant = tuple(
        item
        for item in market_states
        if item.observation.event_reference.instrument_id == feature_reference.instrument_id
        and item.observation.event_reference.venue == feature_reference.venue
    )
    if not relevant or any(item.event_index is None for item in relevant):
        raise ValueError("research market states require true normalized-event indices")
    ordered = tuple(sorted(relevant, key=_required_event_index))
    indices = tuple(_required_event_index(item) for item in ordered)
    if len(set(indices)) != len(indices):
        raise ValueError("research market-state event indices must be unique")
    process_times = tuple(item.observation.event_reference.process_time for item in ordered)
    if process_times != tuple(sorted(process_times)):
        raise ValueError("research market-state process time cannot regress")
    return ordered


def _build_baseline(
    states: tuple[IndexedMarketStateObservation, ...],
    anchor_index: int,
    config: FeatureSnapshotConfig,
) -> BaselineFeatureSnapshot:
    state_index = {_required_event_index(item): item for item in states}
    anchor = state_index[anchor_index].observation.snapshot
    backward = tuple(
        _backward_feature(state_index, anchor_index, horizon)
        for horizon in config.backward_horizons_events
    )
    microprice_offset = (
        None
        if anchor.midprice is None or anchor.microprice is None
        else (anchor.microprice - anchor.midprice) / anchor.midprice
    )
    return BaselineFeatureSnapshot(
        depth_levels=config.depth_levels,
        spread=anchor.spread,
        midprice=anchor.midprice,
        microprice=anchor.microprice,
        microprice_offset=microprice_offset,
        order_book_imbalance=anchor.order_book_imbalance(config.depth_levels),
        raw_bid_depth=anchor.bid_depth_n(config.depth_levels),
        raw_ask_depth=anchor.ask_depth_n(config.depth_levels),
        weighted_bid_depth=anchor.weighted_bid_depth(config.weighted_depth_config),
        weighted_ask_depth=anchor.weighted_ask_depth(config.weighted_depth_config),
        weighted_depth_weights=config.weighted_depth_config.weights,
        backward_features=backward,
    )


def _backward_feature(
    state_index: dict[int, IndexedMarketStateObservation],
    anchor_index: int,
    horizon: int,
) -> BackwardMarketFeature:
    anchor_midprice = state_index[anchor_index].observation.snapshot.midprice
    prior = state_index.get(anchor_index - horizon)
    prior_midprice = None if prior is None else prior.observation.snapshot.midprice
    recent_return = (
        None
        if anchor_midprice is None or prior_midprice is None
        else (anchor_midprice - prior_midprice) / prior_midprice
    )
    path = tuple(
        state_index.get(index) for index in range(anchor_index - horizon, anchor_index + 1)
    )
    path_midprices = tuple(
        None if item is None else item.observation.snapshot.midprice for item in path
    )
    recent_volatility = (
        None
        if any(value is None for value in path_midprices)
        else calculate_event_time_realized_volatility(
            tuple(_required(value) for value in path_midprices)
        )
    )
    return BackwardMarketFeature(
        horizon_events=horizon,
        recent_return=recent_return,
        recent_volatility=recent_volatility,
    )


def _credibility_feature(feature_input: SRAFeatureInput) -> LiquidityCredibilityFeature | None:
    if feature_input.liquidity_credibility_1 is None:
        return None
    first = feature_input.liquidity_credibility_1
    second = _required(feature_input.liquidity_credibility_2)
    comparison = _required(feature_input.liquidity_credibility_comparison)
    if first.credibility_score is None or second.credibility_score is None:
        raise ValueError("selected liquidity credibility requires both optional side scores")
    return LiquidityCredibilityFeature(
        liquidity_credibility_1=first.credibility_score,
        liquidity_credibility_2=second.credibility_score,
        delta_liquidity_credibility=comparison.delta_liquidity_credibility,
        raw_components_1=_credibility_components(first),
        raw_components_2=_credibility_components(second),
    )


def _credibility_components(result: LiquidityCredibilityResult) -> CredibilityRawComponents:
    return CredibilityRawComponents(
        quantity_weighted_order_credibility=result.quantity_weighted_order_credibility,
        shock_executed_fraction=result.shock_executed_fraction,
        shock_withdrawal_fraction=result.shock_withdrawal_fraction,
        order_survival_fraction=result.order_survival_fraction,
        quantity_survival_fraction=result.quantity_survival_fraction,
        replenishment_component=result.replenishment_component,
        cycle_component=result.cycle_component,
        credible_depth=result.credible_depth,
        credible_depth_ratio=result.credible_depth_ratio,
    )


def _toxicity_feature(feature_input: SRAFeatureInput) -> ToxicityFeature | None:
    vector = feature_input.toxicity
    if vector is None:
        return None
    comparison = feature_input.toxicity_comparison
    return ToxicityFeature(
        flow_persistence=vector.flow.flow_persistence,
        shock_persistence=vector.shock_persistence.shock_persistence,
        directional_flow_coverage=vector.flow.directional_flow_coverage,
        unknown_flow_share=vector.flow.unknown_flow_share,
        raw_replenishment_failure=vector.replenishment.raw_replenishment_failure,
        bounded_replenishment_failure=vector.replenishment.bounded_replenishment_failure,
        attacked_nnlp=vector.liquidity.attacked.normalized_net_liquidity_provision,
        opposite_nnlp=vector.liquidity.opposite.normalized_net_liquidity_provision,
        withdrawal_pressure=vector.liquidity.withdrawal_pressure,
        spread_expansion_ratio=vector.market_state.spread.spread_expansion_ratio,
        bounded_spread_expansion=vector.market_state.spread.bounded_spread_expansion,
        volatility_jump_ratio=vector.market_state.volatility.volatility_jump_ratio,
        bounded_volatility_jump=vector.market_state.volatility.bounded_volatility_jump,
        toxicity_score=vector.toxicity_score,
        delta_toxicity=None if comparison is None else comparison.delta_toxicity,
    )


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("required research feature value is unavailable")
    return value


def _required_event_index(item: IndexedMarketStateObservation) -> int:
    return _required(item.event_index)
