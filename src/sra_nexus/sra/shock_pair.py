"""Explicit comparability policy and immutable ordered shock-pair contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
)
from sra_nexus.common.types import InstrumentId, ShockId, ShockPairId
from sra_nexus.sra.enums import (
    ShockDirection,
    ShockPairIncomparabilityReason,
    StructuralBreakKind,
)
from sra_nexus.sra.shock import LiquidityShock

SHOCK_PAIR_VERSION = "shock-pair-v1"
SHOCK_PAIR_NAMESPACE = UUID("ce205c64-8670-58b0-9dde-2b2f44caf3b4")


def _default_comparison_horizons() -> tuple[int, ...]:
    return (5, 10, 25, 50)


def _default_recovery_thresholds() -> tuple[Decimal, ...]:
    return (Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))


class ShockPairConfig(ContractModel):
    """Initial engineering bounds and formula policies for comparable shocks.

    ``effectiveness_stability_tolerance`` is measured in instrument price units.
    The ratio and distance bounds are inclusive and are not empirically optimized.
    """

    max_event_distance: int = Field(default=500, ge=0)
    max_exchange_seconds: NonNegativeDecimal = Decimal("60")
    min_normalized_aggression_ratio: PositiveDecimal | None = Decimal("0.5")
    max_normalized_aggression_ratio: PositiveDecimal | None = Decimal("2.0")
    require_same_direction: Literal[True] = True
    required_impact_horizons_events: tuple[int, ...] = Field(
        default_factory=_default_comparison_horizons
    )
    required_resiliency_horizons_events: tuple[int, ...] = Field(
        default_factory=_default_comparison_horizons
    )
    required_recovery_thresholds: tuple[PositiveDecimal, ...] = Field(
        default_factory=_default_recovery_thresholds
    )
    epsilon: PositiveDecimal = Decimal("0.000001")
    effectiveness_stability_tolerance: NonNegativeDecimal = Decimal("0.000001")
    comparison_version: NonBlankStr = SHOCK_PAIR_VERSION

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        """Require ordered unique horizons and coherent optional ratio bounds."""
        _validate_positive_ordered_values(
            self.required_impact_horizons_events,
            "required impact horizons",
        )
        _validate_positive_ordered_values(
            self.required_resiliency_horizons_events,
            "required resiliency horizons",
        )
        if not set(self.required_impact_horizons_events).intersection(
            self.required_resiliency_horizons_events
        ):
            raise ValueError("impact and resiliency horizons must overlap for absorption")
        if not self.required_recovery_thresholds:
            raise ValueError("at least one recovery threshold is required")
        if (
            tuple(sorted(set(self.required_recovery_thresholds)))
            != self.required_recovery_thresholds
        ):
            raise ValueError("recovery thresholds must be unique and increasing")
        ratio_bounds = (
            self.min_normalized_aggression_ratio,
            self.max_normalized_aggression_ratio,
        )
        if (ratio_bounds[0] is None) != (ratio_bounds[1] is None):
            raise ValueError("normalized-aggression ratio bounds must both be set or disabled")
        if ratio_bounds[0] is not None and ratio_bounds[1] is not None:
            if ratio_bounds[0] > ratio_bounds[1]:
                raise ValueError("minimum aggression ratio cannot exceed maximum ratio")
        return self


class ShockPairSpan(ContractModel):
    """Pipeline-supplied normalized-event span and known structural break.

    ``event_distance`` is the count of all normalized market events strictly
    between shock 1 end and shock 2 start. It is never inferred from trade counts.
    """

    event_distance: int = Field(ge=0)
    structural_break: StructuralBreakKind | None = None


class ShockPairIncomparability(ContractModel):
    """One typed reason a candidate ordered pair is unavailable for research."""

    reason: ShockPairIncomparabilityReason
    horizon_events: int | None = Field(default=None, gt=0)
    recovery_threshold: PositiveDecimal | None = None
    structural_break: StructuralBreakKind | None = None

    @model_validator(mode="after")
    def validate_reason_metadata(self) -> Self:
        """Attach a break kind only to structural-break reasons."""
        is_structural = self.reason is ShockPairIncomparabilityReason.STRUCTURAL_BREAK
        if is_structural != (self.structural_break is not None):
            raise ValueError("structural-break reason and kind must be supplied together")
        return self


class ShockPairAssessment(ContractModel):
    """Explicit outcome of ordinary pair-comparability checks."""

    shock_1_id: ShockId
    shock_2_id: ShockId
    event_distance: int = Field(ge=0)
    exchange_seconds_distance: ExactDecimal
    process_seconds_distance: ExactDecimal
    aggression_ratio: PositiveDecimal
    comparable: bool
    reasons: tuple[ShockPairIncomparability, ...]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        """Keep comparable and unavailable assessment shapes disjoint."""
        if self.comparable and self.reasons:
            raise ValueError("comparable assessment cannot contain reasons")
        if not self.comparable and not self.reasons:
            raise ValueError("incomparable assessment requires at least one reason")
        if self.comparable and (
            self.exchange_seconds_distance < 0 or self.process_seconds_distance < 0
        ):
            raise ValueError("comparable shocks cannot have negative clock distances")
        if self.comparable and (
            self.exchange_seconds_distance == 0 and self.process_seconds_distance == 0
        ):
            raise ValueError("shock 2 must occur after shock 1")
        return self


class ShockPair(ContractModel):
    """Immutable ordered identity and measured span for two comparable shocks."""

    pair_id: ShockPairId
    instrument_id: InstrumentId
    direction: ShockDirection
    shock_1_id: ShockId
    shock_2_id: ShockId
    event_distance: int = Field(ge=0)
    exchange_seconds_distance: NonNegativeDecimal
    process_seconds_distance: NonNegativeDecimal
    aggression_ratio: PositiveDecimal
    comparison_version: NonBlankStr

    @model_validator(mode="after")
    def validate_distinct_shocks(self) -> Self:
        """Reject self-comparison and noncanonical deterministic identity."""
        if self.shock_1_id == self.shock_2_id:
            raise ValueError("a shock cannot be paired with itself")
        expected_id = derive_shock_pair_id(
            self.shock_1_id,
            self.shock_2_id,
            self.comparison_version,
        )
        if self.pair_id != expected_id:
            raise ValueError("pair_id must match the ordered shocks and comparison version")
        return self


def assess_shock_pair(
    shock_1: LiquidityShock,
    shock_2: LiquidityShock,
    span: ShockPairSpan,
    config: ShockPairConfig | None = None,
) -> ShockPairAssessment:
    """Apply basic identity, order, distance, break, and aggression-ratio policy."""
    policy = ShockPairConfig() if config is None else config
    exchange_distance = _signed_elapsed_seconds(
        shock_1.end_exchange_time,
        shock_2.start_exchange_time,
    )
    process_distance = _signed_elapsed_seconds(
        shock_1.end_process_time,
        shock_2.start_process_time,
    )
    aggression_ratio = shock_2.normalized_aggression / shock_1.normalized_aggression
    reasons: list[ShockPairIncomparability] = []
    if shock_1.shock_id == shock_2.shock_id:
        reasons.append(_reason(ShockPairIncomparabilityReason.SAME_SHOCK))
    if shock_1.instrument_id != shock_2.instrument_id:
        reasons.append(_reason(ShockPairIncomparabilityReason.INSTRUMENT_MISMATCH))
    if shock_1.direction is not shock_2.direction:
        reasons.append(_reason(ShockPairIncomparabilityReason.DIRECTION_MISMATCH))
    if (
        exchange_distance < 0
        or process_distance < 0
        or (exchange_distance == 0 and process_distance == 0)
    ):
        reasons.append(_reason(ShockPairIncomparabilityReason.SHOCK_ORDER_INVALID))
    if span.event_distance > policy.max_event_distance:
        reasons.append(_reason(ShockPairIncomparabilityReason.EVENT_DISTANCE_EXCEEDED))
    if exchange_distance > policy.max_exchange_seconds:
        reasons.append(_reason(ShockPairIncomparabilityReason.EXCHANGE_DISTANCE_EXCEEDED))
    if span.structural_break is not None:
        reasons.append(
            ShockPairIncomparability(
                reason=ShockPairIncomparabilityReason.STRUCTURAL_BREAK,
                structural_break=span.structural_break,
            )
        )
    lower = policy.min_normalized_aggression_ratio
    upper = policy.max_normalized_aggression_ratio
    if lower is not None and upper is not None and not lower <= aggression_ratio <= upper:
        reasons.append(_reason(ShockPairIncomparabilityReason.AGGRESSION_RATIO_OUTSIDE_BOUNDS))
    return ShockPairAssessment(
        shock_1_id=shock_1.shock_id,
        shock_2_id=shock_2.shock_id,
        event_distance=span.event_distance,
        exchange_seconds_distance=exchange_distance,
        process_seconds_distance=process_distance,
        aggression_ratio=aggression_ratio,
        comparable=not reasons,
        reasons=tuple(reasons),
    )


def derive_shock_pair_id(
    shock_1_id: ShockId,
    shock_2_id: ShockId,
    comparison_version: str,
) -> ShockPairId:
    """Derive an order-sensitive stable UUID from both shock IDs and version."""
    identity = f"{shock_1_id}|{shock_2_id}|{comparison_version}"
    return ShockPairId(uuid5(SHOCK_PAIR_NAMESPACE, identity))


def build_shock_pair(
    shock_1: LiquidityShock,
    shock_2: LiquidityShock,
    assessment: ShockPairAssessment,
    config: ShockPairConfig | None = None,
) -> ShockPair:
    """Materialize an ordered pair after every required comparison check passes."""
    policy = ShockPairConfig() if config is None else config
    if not assessment.comparable:
        raise ValueError("cannot build an incomparable shock pair")
    if assessment.shock_1_id != shock_1.shock_id or assessment.shock_2_id != shock_2.shock_id:
        raise ValueError("assessment shock identities do not match candidate shocks")
    if shock_1.instrument_id != shock_2.instrument_id or shock_1.direction is not shock_2.direction:
        raise ValueError("comparable shock pair requires the same instrument and direction")
    expected_assessment = assess_shock_pair(
        shock_1,
        shock_2,
        ShockPairSpan(event_distance=assessment.event_distance),
        policy,
    )
    if not expected_assessment.comparable or (
        assessment.exchange_seconds_distance != expected_assessment.exchange_seconds_distance
        or assessment.process_seconds_distance != expected_assessment.process_seconds_distance
        or assessment.aggression_ratio != expected_assessment.aggression_ratio
    ):
        raise ValueError("assessment values do not match the ordered shocks and policy")
    return ShockPair(
        pair_id=derive_shock_pair_id(
            shock_1.shock_id,
            shock_2.shock_id,
            policy.comparison_version,
        ),
        instrument_id=shock_1.instrument_id,
        direction=shock_1.direction,
        shock_1_id=shock_1.shock_id,
        shock_2_id=shock_2.shock_id,
        event_distance=assessment.event_distance,
        exchange_seconds_distance=assessment.exchange_seconds_distance,
        process_seconds_distance=assessment.process_seconds_distance,
        aggression_ratio=assessment.aggression_ratio,
        comparison_version=policy.comparison_version,
    )


def _signed_elapsed_seconds(start: datetime, end: datetime) -> Decimal:
    delta = end - start
    return Decimal(delta.days * 86_400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(
        1_000_000
    )


def _reason(reason: ShockPairIncomparabilityReason) -> ShockPairIncomparability:
    return ShockPairIncomparability(reason=reason)


def _validate_positive_ordered_values(values: tuple[int, ...], name: str) -> None:
    if not values or any(value <= 0 for value in values):
        raise ValueError(f"{name} must contain positive values")
    if tuple(sorted(set(values))) != values:
        raise ValueError(f"{name} must be unique and increasing")
