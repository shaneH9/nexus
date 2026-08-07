"""Focused orchestration for deterministic failed-aggression comparisons."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
)
from sra_nexus.common.types import InstrumentId, ShockId, ShockPairId
from sra_nexus.sra.absorption import (
    AbsorptionEfficiencyComparison,
    calculate_absorption_efficiency,
    compare_absorption_efficiency,
)
from sra_nexus.sra.effectiveness import (
    ShockPairEffectivenessComparison,
    calculate_aggressor_effectiveness,
    compare_aggressor_effectiveness,
)
from sra_nexus.sra.enums import (
    RecoveryComparisonUnavailableReason,
    RecoveryTimeInterpretation,
    ShockDirection,
    ShockPairIncomparabilityReason,
)
from sra_nexus.sra.impact import ShockImpact
from sra_nexus.sra.resiliency import RecoveryTime, ResiliencyObservation, ResiliencyVector
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.shock_pair import (
    ShockPair,
    ShockPairAssessment,
    ShockPairConfig,
    ShockPairIncomparability,
    ShockPairSpan,
    assess_shock_pair,
    build_shock_pair,
)

FAILED_AGGRESSION_COMPARISON_VERSION = "failed-aggression-comparison-v1"


class ResiliencyHorizonComparison(ContractModel):
    """Exact replenishment-ratio change at one event horizon."""

    pair_id: ShockPairId
    horizon_events: int = Field(gt=0)
    rr_1: ExactDecimal | None
    rr_2: ExactDecimal | None
    delta_rr: ExactDecimal | None
    available: bool
    unavailable_reason: ShockPairIncomparabilityReason | None = None
    comparison_version: NonBlankStr

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        """Keep unavailable replenishment distinct from a genuine zero delta."""
        values = (self.rr_1, self.rr_2, self.delta_rr)
        if self.available:
            if any(value is None for value in values) or self.unavailable_reason is not None:
                raise ValueError("available resiliency comparison requires RR values and delta")
            rr_1 = _required(self.rr_1)
            rr_2 = _required(self.rr_2)
            if self.delta_rr != rr_2 - rr_1:
                raise ValueError("delta_rr must equal RR_2 - RR_1")
        elif self.unavailable_reason is None or self.delta_rr is not None:
            raise ValueError("unavailable resiliency comparison requires a reason and no delta")
        return self


class RecoveryTimeComparison(ContractModel):
    """Event-, exchange-, and process-time recovery deltas for one RR threshold."""

    pair_id: ShockPairId
    threshold: PositiveDecimal
    events_to_recovery_1: int | None
    events_to_recovery_2: int | None
    delta_events: int | None
    exchange_seconds_to_recovery_1: NonNegativeDecimal | None
    exchange_seconds_to_recovery_2: NonNegativeDecimal | None
    delta_exchange_seconds: ExactDecimal | None
    process_seconds_to_recovery_1: NonNegativeDecimal | None
    process_seconds_to_recovery_2: NonNegativeDecimal | None
    delta_process_seconds: ExactDecimal | None
    available: bool
    events_interpretation: RecoveryTimeInterpretation
    exchange_interpretation: RecoveryTimeInterpretation
    process_interpretation: RecoveryTimeInterpretation
    unavailable_reason: RecoveryComparisonUnavailableReason | None = None
    comparison_version: NonBlankStr

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        """Require exact deltas or an explicit unreached-threshold state."""
        first_values = (
            self.events_to_recovery_1,
            self.exchange_seconds_to_recovery_1,
            self.process_seconds_to_recovery_1,
        )
        second_values = (
            self.events_to_recovery_2,
            self.exchange_seconds_to_recovery_2,
            self.process_seconds_to_recovery_2,
        )
        deltas = (
            self.delta_events,
            self.delta_exchange_seconds,
            self.delta_process_seconds,
        )
        if self.available:
            if any(value is None for value in (*first_values, *second_values, *deltas)):
                raise ValueError("available recovery comparison requires all recovery values")
            if self.unavailable_reason is not None:
                raise ValueError("available recovery comparison cannot have unavailable reason")
            interpretations = (
                self.events_interpretation,
                self.exchange_interpretation,
                self.process_interpretation,
            )
            if RecoveryTimeInterpretation.UNAVAILABLE in interpretations:
                raise ValueError("available recovery comparison requires speed interpretations")
            event_1 = _required(self.events_to_recovery_1)
            event_2 = _required(self.events_to_recovery_2)
            exchange_1 = _required(self.exchange_seconds_to_recovery_1)
            exchange_2 = _required(self.exchange_seconds_to_recovery_2)
            process_1 = _required(self.process_seconds_to_recovery_1)
            process_2 = _required(self.process_seconds_to_recovery_2)
            if self.delta_events != event_2 - event_1:
                raise ValueError("event recovery delta must equal tau_2 - tau_1")
            if self.delta_exchange_seconds != exchange_2 - exchange_1:
                raise ValueError("exchange recovery delta must equal tau_2 - tau_1")
            if self.delta_process_seconds != process_2 - process_1:
                raise ValueError("process recovery delta must equal tau_2 - tau_1")
            expected_interpretations = (
                _interpret_recovery_delta(event_2 - event_1),
                _interpret_recovery_delta(exchange_2 - exchange_1),
                _interpret_recovery_delta(process_2 - process_1),
            )
            if interpretations != expected_interpretations:
                raise ValueError("recovery interpretations conflict with their unit deltas")
        elif (
            any(value is not None for value in deltas)
            or self.unavailable_reason is None
            or any(
                interpretation is not RecoveryTimeInterpretation.UNAVAILABLE
                for interpretation in (
                    self.events_interpretation,
                    self.exchange_interpretation,
                    self.process_interpretation,
                )
            )
        ):
            raise ValueError("unavailable recovery comparison requires reason and no deltas")
        return self


class FailedAggressionComparison(ContractModel):
    """Combined pair features, explicitly not an alpha estimate or trade signal."""

    pair_id: ShockPairId | None
    pair: ShockPair | None
    shock_1_id: ShockId
    shock_2_id: ShockId
    instrument_id: InstrumentId | None
    direction: ShockDirection | None
    event_distance: int = Field(ge=0)
    exchange_seconds_distance: ExactDecimal
    process_seconds_distance: ExactDecimal
    aggression_ratio: PositiveDecimal
    effectiveness_by_horizon: tuple[ShockPairEffectivenessComparison, ...]
    resiliency_by_horizon: tuple[ResiliencyHorizonComparison, ...]
    recovery_comparisons: tuple[RecoveryTimeComparison, ...]
    absorption_efficiency_by_horizon: tuple[AbsorptionEfficiencyComparison, ...]
    comparison_available: bool
    reasons_unavailable: tuple[ShockPairIncomparability, ...]
    feature_version: NonBlankStr = FAILED_AGGRESSION_COMPARISON_VERSION

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        """Prevent partial feature collections from masquerading as valid output."""
        feature_groups = (
            self.effectiveness_by_horizon,
            self.resiliency_by_horizon,
            self.recovery_comparisons,
            self.absorption_efficiency_by_horizon,
        )
        if self.comparison_available:
            if self.pair is None or self.pair_id is None:
                raise ValueError("available comparison requires an immutable shock pair")
            if self.pair_id != self.pair.pair_id:
                raise ValueError("pair_id must match the embedded shock pair")
            if self.reasons_unavailable:
                raise ValueError("available comparison cannot contain unavailable reasons")
            if any(not group for group in feature_groups):
                raise ValueError("available comparison requires every configured feature group")
        elif self.pair is not None or self.pair_id is not None:
            raise ValueError("incomparable result cannot contain a materialized pair")
        elif not self.reasons_unavailable or any(feature_groups):
            raise ValueError("incomparable result requires reasons and no partial features")
        return self


class ShockPairService:
    """Compare ordered Milestone G outputs with no persistence or trading decisions."""

    def __init__(self, config: ShockPairConfig | None = None) -> None:
        """Configure explicit engineering bounds, horizons, epsilon, and tolerance."""
        self._config = ShockPairConfig() if config is None else config

    @property
    def config(self) -> ShockPairConfig:
        """Return the immutable comparison policy used for reproducible output."""
        return self._config

    def compare(
        self,
        *,
        shock_1: LiquidityShock,
        shock_2: LiquidityShock,
        span: ShockPairSpan,
        impacts_1: Sequence[ShockImpact],
        impacts_2: Sequence[ShockImpact],
        resiliency_1: ResiliencyVector | None,
        resiliency_2: ResiliencyVector | None,
    ) -> FailedAggressionComparison:
        """Return complete features or an explicit ordinary incomparability result."""
        impact_index_1 = _index_impacts(shock_1, impacts_1)
        impact_index_2 = _index_impacts(shock_2, impacts_2)
        resiliency_index_1 = _index_resiliency(shock_1, resiliency_1)
        resiliency_index_2 = _index_resiliency(shock_2, resiliency_2)

        assessment = assess_shock_pair(shock_1, shock_2, span, self._config)
        feature_reasons = _feature_incomparability_reasons(
            self._config,
            impact_index_1,
            impact_index_2,
            resiliency_index_1,
            resiliency_index_2,
            resiliency_1,
            resiliency_2,
        )
        reasons = (*assessment.reasons, *feature_reasons)
        if reasons:
            return _unavailable_comparison(shock_1, shock_2, assessment, reasons)

        complete_assessment = ShockPairAssessment(
            shock_1_id=assessment.shock_1_id,
            shock_2_id=assessment.shock_2_id,
            event_distance=assessment.event_distance,
            exchange_seconds_distance=assessment.exchange_seconds_distance,
            process_seconds_distance=assessment.process_seconds_distance,
            aggression_ratio=assessment.aggression_ratio,
            comparable=True,
            reasons=(),
        )
        pair = build_shock_pair(shock_1, shock_2, complete_assessment, self._config)
        complete_resiliency_1 = _required(resiliency_index_1)
        complete_resiliency_2 = _required(resiliency_index_2)
        effectiveness = tuple(
            _compare_effectiveness_horizon(
                pair,
                shock_1,
                shock_2,
                impact_index_1[horizon],
                impact_index_2[horizon],
                self._config,
            )
            for horizon in self._config.required_impact_horizons_events
        )
        resiliency = tuple(
            _compare_resiliency_horizon(
                pair,
                complete_resiliency_1.rr_by_horizon[horizon],
                complete_resiliency_2.rr_by_horizon[horizon],
            )
            for horizon in self._config.required_resiliency_horizons_events
        )
        recovery = tuple(
            compare_recovery_times(
                pair,
                complete_resiliency_1.recovery_by_threshold[threshold],
                complete_resiliency_2.recovery_by_threshold[threshold],
            )
            for threshold in self._config.required_recovery_thresholds
        )
        effectiveness_index = {item.horizon_events: item for item in effectiveness}
        resiliency_comparison_index = {item.horizon_events: item for item in resiliency}
        common_horizons = tuple(
            horizon
            for horizon in self._config.required_impact_horizons_events
            if horizon in resiliency_comparison_index
        )
        absorption = tuple(
            _compare_absorption_horizon(
                pair,
                effectiveness_index[horizon],
                resiliency_comparison_index[horizon],
                self._config.epsilon,
            )
            for horizon in common_horizons
        )
        return FailedAggressionComparison(
            pair_id=pair.pair_id,
            pair=pair,
            shock_1_id=shock_1.shock_id,
            shock_2_id=shock_2.shock_id,
            instrument_id=pair.instrument_id,
            direction=pair.direction,
            event_distance=pair.event_distance,
            exchange_seconds_distance=pair.exchange_seconds_distance,
            process_seconds_distance=pair.process_seconds_distance,
            aggression_ratio=pair.aggression_ratio,
            effectiveness_by_horizon=effectiveness,
            resiliency_by_horizon=resiliency,
            recovery_comparisons=recovery,
            absorption_efficiency_by_horizon=absorption,
            comparison_available=True,
            reasons_unavailable=(),
        )


class _ResiliencyIndex:
    """Validated internal lookup for one immutable resiliency vector."""

    def __init__(self, vector: ResiliencyVector) -> None:
        self.version = vector.resiliency_version
        self.depth_levels_k = vector.depth_levels_k
        self.rr_by_horizon = _unique_index(
            vector.rr_by_horizon,
            lambda item: item.horizon_events,
            "resiliency horizon",
        )
        self.recovery_by_threshold = _unique_index(
            vector.recovery_times,
            lambda item: item.threshold,
            "recovery threshold",
        )


def compare_recovery_times(
    pair: ShockPair,
    recovery_1: RecoveryTime,
    recovery_2: RecoveryTime,
) -> RecoveryTimeComparison:
    """Compare first-passage recovery in event, exchange, and process units."""
    if recovery_1.threshold != recovery_2.threshold:
        raise ValueError("recovery comparisons require the same threshold")
    if not recovery_1.recovered or not recovery_2.recovered:
        return RecoveryTimeComparison(
            pair_id=pair.pair_id,
            threshold=recovery_1.threshold,
            events_to_recovery_1=recovery_1.events_to_recovery,
            events_to_recovery_2=recovery_2.events_to_recovery,
            delta_events=None,
            exchange_seconds_to_recovery_1=recovery_1.exchange_seconds_to_recovery,
            exchange_seconds_to_recovery_2=recovery_2.exchange_seconds_to_recovery,
            delta_exchange_seconds=None,
            process_seconds_to_recovery_1=recovery_1.process_seconds_to_recovery,
            process_seconds_to_recovery_2=recovery_2.process_seconds_to_recovery,
            delta_process_seconds=None,
            available=False,
            events_interpretation=RecoveryTimeInterpretation.UNAVAILABLE,
            exchange_interpretation=RecoveryTimeInterpretation.UNAVAILABLE,
            process_interpretation=RecoveryTimeInterpretation.UNAVAILABLE,
            unavailable_reason=_recovery_unavailable_reason(recovery_1, recovery_2),
            comparison_version=pair.comparison_version,
        )
    event_1 = _required(recovery_1.events_to_recovery)
    event_2 = _required(recovery_2.events_to_recovery)
    exchange_1 = _required(recovery_1.exchange_seconds_to_recovery)
    exchange_2 = _required(recovery_2.exchange_seconds_to_recovery)
    process_1 = _required(recovery_1.process_seconds_to_recovery)
    process_2 = _required(recovery_2.process_seconds_to_recovery)
    delta_events = event_2 - event_1
    delta_exchange = exchange_2 - exchange_1
    delta_process = process_2 - process_1
    return RecoveryTimeComparison(
        pair_id=pair.pair_id,
        threshold=recovery_1.threshold,
        events_to_recovery_1=event_1,
        events_to_recovery_2=event_2,
        delta_events=delta_events,
        exchange_seconds_to_recovery_1=exchange_1,
        exchange_seconds_to_recovery_2=exchange_2,
        delta_exchange_seconds=delta_exchange,
        process_seconds_to_recovery_1=process_1,
        process_seconds_to_recovery_2=process_2,
        delta_process_seconds=delta_process,
        available=True,
        events_interpretation=_interpret_recovery_delta(delta_events),
        exchange_interpretation=_interpret_recovery_delta(delta_exchange),
        process_interpretation=_interpret_recovery_delta(delta_process),
        comparison_version=pair.comparison_version,
    )


def _compare_effectiveness_horizon(
    pair: ShockPair,
    shock_1: LiquidityShock,
    shock_2: LiquidityShock,
    impact_1: ShockImpact,
    impact_2: ShockImpact,
    config: ShockPairConfig,
) -> ShockPairEffectivenessComparison:
    ae_1 = calculate_aggressor_effectiveness(shock_1, impact_1, config.epsilon)
    ae_2 = calculate_aggressor_effectiveness(shock_2, impact_2, config.epsilon)
    return compare_aggressor_effectiveness(
        pair,
        ae_1,
        ae_2,
        config.effectiveness_stability_tolerance,
    )


def _compare_resiliency_horizon(
    pair: ShockPair,
    observation_1: ResiliencyObservation,
    observation_2: ResiliencyObservation,
) -> ResiliencyHorizonComparison:
    if observation_1.horizon_events != observation_2.horizon_events:
        raise ValueError("resiliency observations must use the same event horizon")
    rr_1 = _required(observation_1.replenishment_ratio)
    rr_2 = _required(observation_2.replenishment_ratio)
    return ResiliencyHorizonComparison(
        pair_id=pair.pair_id,
        horizon_events=observation_1.horizon_events,
        rr_1=rr_1,
        rr_2=rr_2,
        delta_rr=rr_2 - rr_1,
        available=True,
        comparison_version=pair.comparison_version,
    )


def _compare_absorption_horizon(
    pair: ShockPair,
    effectiveness: ShockPairEffectivenessComparison,
    resiliency: ResiliencyHorizonComparison,
    epsilon: Decimal,
) -> AbsorptionEfficiencyComparison:
    rr_1 = _required(resiliency.rr_1)
    rr_2 = _required(resiliency.rr_2)
    abs_eff_1 = calculate_absorption_efficiency(effectiveness.ae_1, rr_1, epsilon)
    abs_eff_2 = calculate_absorption_efficiency(effectiveness.ae_2, rr_2, epsilon)
    return compare_absorption_efficiency(pair, abs_eff_1, abs_eff_2)


def _index_impacts(
    shock: LiquidityShock,
    impacts: Sequence[ShockImpact],
) -> dict[int, ShockImpact]:
    indexed = _unique_index(tuple(impacts), lambda item: item.horizon_events, "impact horizon")
    for impact in indexed.values():
        if impact.shock_id != shock.shock_id or impact.direction is not shock.direction:
            raise ValueError("every impact must belong to its supplied shock and direction")
    return indexed


def _index_resiliency(
    shock: LiquidityShock,
    vector: ResiliencyVector | None,
) -> _ResiliencyIndex | None:
    if vector is None:
        return None
    if vector.shock_id != shock.shock_id:
        raise ValueError("resiliency vector must belong to its supplied shock")
    return _ResiliencyIndex(vector)


def _feature_incomparability_reasons(
    config: ShockPairConfig,
    impacts_1: dict[int, ShockImpact],
    impacts_2: dict[int, ShockImpact],
    resiliency_1: _ResiliencyIndex | None,
    resiliency_2: _ResiliencyIndex | None,
    vector_1: ResiliencyVector | None,
    vector_2: ResiliencyVector | None,
) -> tuple[ShockPairIncomparability, ...]:
    reasons: list[ShockPairIncomparability] = []
    for horizon in config.required_impact_horizons_events:
        impact_1 = impacts_1.get(horizon)
        impact_2 = impacts_2.get(horizon)
        if impact_1 is None or not impact_1.available:
            reasons.append(
                _horizon_reason(
                    ShockPairIncomparabilityReason.REQUIRED_IMPACT_UNAVAILABLE_SHOCK_1,
                    horizon,
                )
            )
        if impact_2 is None or not impact_2.available:
            reasons.append(
                _horizon_reason(
                    ShockPairIncomparabilityReason.REQUIRED_IMPACT_UNAVAILABLE_SHOCK_2,
                    horizon,
                )
            )
    available_versions_1 = {
        impact.impact_version
        for horizon, impact in impacts_1.items()
        if horizon in config.required_impact_horizons_events and impact.available
    }
    available_versions_2 = {
        impact.impact_version
        for horizon, impact in impacts_2.items()
        if horizon in config.required_impact_horizons_events and impact.available
    }
    if (
        len(available_versions_1) > 1
        or len(available_versions_2) > 1
        or (
            available_versions_1
            and available_versions_2
            and available_versions_1 != available_versions_2
        )
    ):
        reasons.append(_reason(ShockPairIncomparabilityReason.IMPACT_VERSION_MISMATCH))

    for horizon in config.required_resiliency_horizons_events:
        observation_1 = None if resiliency_1 is None else resiliency_1.rr_by_horizon.get(horizon)
        observation_2 = None if resiliency_2 is None else resiliency_2.rr_by_horizon.get(horizon)
        if observation_1 is None or observation_1.replenishment_ratio is None:
            reasons.append(
                _horizon_reason(
                    ShockPairIncomparabilityReason.REQUIRED_RESILIENCY_UNAVAILABLE_SHOCK_1,
                    horizon,
                )
            )
        if observation_2 is None or observation_2.replenishment_ratio is None:
            reasons.append(
                _horizon_reason(
                    ShockPairIncomparabilityReason.REQUIRED_RESILIENCY_UNAVAILABLE_SHOCK_2,
                    horizon,
                )
            )
    if vector_1 is not None and vector_2 is not None:
        if vector_1.resiliency_version != vector_2.resiliency_version:
            reasons.append(_reason(ShockPairIncomparabilityReason.RESILIENCY_VERSION_MISMATCH))
        if vector_1.depth_levels_k != vector_2.depth_levels_k:
            reasons.append(_reason(ShockPairIncomparabilityReason.RESILIENCY_DEPTH_POLICY_MISMATCH))

    for threshold in config.required_recovery_thresholds:
        recovery_1 = (
            None if resiliency_1 is None else resiliency_1.recovery_by_threshold.get(threshold)
        )
        recovery_2 = (
            None if resiliency_2 is None else resiliency_2.recovery_by_threshold.get(threshold)
        )
        if recovery_1 is None:
            reasons.append(
                _threshold_reason(
                    ShockPairIncomparabilityReason.REQUIRED_RECOVERY_THRESHOLD_MISSING_SHOCK_1,
                    threshold,
                )
            )
        if recovery_2 is None:
            reasons.append(
                _threshold_reason(
                    ShockPairIncomparabilityReason.REQUIRED_RECOVERY_THRESHOLD_MISSING_SHOCK_2,
                    threshold,
                )
            )
    return tuple(reasons)


def _unavailable_comparison(
    shock_1: LiquidityShock,
    shock_2: LiquidityShock,
    assessment: ShockPairAssessment,
    reasons: tuple[ShockPairIncomparability, ...],
) -> FailedAggressionComparison:
    return FailedAggressionComparison(
        pair_id=None,
        pair=None,
        shock_1_id=shock_1.shock_id,
        shock_2_id=shock_2.shock_id,
        instrument_id=(
            shock_1.instrument_id if shock_1.instrument_id == shock_2.instrument_id else None
        ),
        direction=shock_1.direction if shock_1.direction is shock_2.direction else None,
        event_distance=assessment.event_distance,
        exchange_seconds_distance=assessment.exchange_seconds_distance,
        process_seconds_distance=assessment.process_seconds_distance,
        aggression_ratio=assessment.aggression_ratio,
        effectiveness_by_horizon=(),
        resiliency_by_horizon=(),
        recovery_comparisons=(),
        absorption_efficiency_by_horizon=(),
        comparison_available=False,
        reasons_unavailable=reasons,
    )


def _recovery_unavailable_reason(
    recovery_1: RecoveryTime,
    recovery_2: RecoveryTime,
) -> RecoveryComparisonUnavailableReason:
    if not recovery_1.recovered and not recovery_2.recovered:
        return RecoveryComparisonUnavailableReason.BOTH_UNREACHED
    if not recovery_1.recovered:
        return RecoveryComparisonUnavailableReason.SHOCK_1_UNREACHED
    return RecoveryComparisonUnavailableReason.SHOCK_2_UNREACHED


def _interpret_recovery_delta(delta: int | Decimal) -> RecoveryTimeInterpretation:
    if delta < 0:
        return RecoveryTimeInterpretation.FASTER
    if delta > 0:
        return RecoveryTimeInterpretation.SLOWER
    return RecoveryTimeInterpretation.STABLE


def _reason(reason: ShockPairIncomparabilityReason) -> ShockPairIncomparability:
    return ShockPairIncomparability(reason=reason)


def _horizon_reason(
    reason: ShockPairIncomparabilityReason,
    horizon: int,
) -> ShockPairIncomparability:
    return ShockPairIncomparability(reason=reason, horizon_events=horizon)


def _threshold_reason(
    reason: ShockPairIncomparabilityReason,
    threshold: Decimal,
) -> ShockPairIncomparability:
    return ShockPairIncomparability(reason=reason, recovery_threshold=threshold)


def _unique_index[Item, Key](
    values: Sequence[Item],
    key: Callable[[Item], Key],
    label: str,
) -> dict[Key, Item]:
    indexed: dict[Key, Item] = {}
    for value in values:
        item_key = key(value)
        if item_key in indexed:
            raise ValueError(f"duplicate {label}: {item_key}")
        indexed[item_key] = value
    return indexed


def _required[Value](value: Value | None) -> Value:
    if value is None:
        raise AssertionError("validated comparison value is unexpectedly unavailable")
    return value
