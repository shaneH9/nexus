"""Horizon-specific signed aggressor-effectiveness research features."""

from __future__ import annotations

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
from sra_nexus.common.types import ShockId, ShockPairId
from sra_nexus.sra.enums import EffectivenessInterpretation, ShockDirection
from sra_nexus.sra.impact import ShockImpact
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.shock_pair import ShockPair

AGGRESSOR_EFFECTIVENESS_VERSION = "aggressor-effectiveness-v1"


class AggressorEffectiveness(ContractModel):
    """Signed price movement per dimensionless normalized-aggression unit.

    ``directional_price_impact`` and ``effectiveness`` are both measured in
    instrument price units. The impact horizon is an event count after shock end.
    """

    shock_id: ShockId
    direction: ShockDirection
    horizon_events: int = Field(gt=0)
    directional_price_impact: ExactDecimal
    normalized_aggression: PositiveDecimal
    epsilon: PositiveDecimal
    effectiveness: ExactDecimal
    impact_version: NonBlankStr
    effectiveness_version: NonBlankStr = AGGRESSOR_EFFECTIVENESS_VERSION

    @model_validator(mode="after")
    def validate_formula(self) -> Self:
        """Require exact ``AE = DI / (normalized aggression + epsilon)``."""
        expected = self.directional_price_impact / (self.normalized_aggression + self.epsilon)
        if self.effectiveness != expected:
            raise ValueError("aggressor effectiveness does not match its exact formula")
        return self


class ShockPairEffectivenessComparison(ContractModel):
    """Exact change in signed aggressor effectiveness at one event horizon."""

    pair_id: ShockPairId
    horizon_events: int = Field(gt=0)
    ae_1: AggressorEffectiveness
    ae_2: AggressorEffectiveness
    delta_ae: ExactDecimal
    relative_ae_change: ExactDecimal
    stability_tolerance: NonNegativeDecimal
    interpretation: EffectivenessInterpretation
    comparison_version: NonBlankStr

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        """Require horizon alignment, exact deltas, and tolerance interpretation."""
        if (
            self.ae_1.horizon_events != self.horizon_events
            or self.ae_2.horizon_events != self.horizon_events
        ):
            raise ValueError("effectiveness horizons must match the comparison horizon")
        expected_delta = self.ae_2.effectiveness - self.ae_1.effectiveness
        if self.delta_ae != expected_delta:
            raise ValueError("delta_ae must equal AE_2 - AE_1")
        expected_relative = expected_delta / (abs(self.ae_1.effectiveness) + self.ae_1.epsilon)
        if self.relative_ae_change != expected_relative:
            raise ValueError("relative AE change does not match its exact formula")
        expected_interpretation = interpret_effectiveness_change(
            expected_delta,
            self.stability_tolerance,
        )
        if self.interpretation is not expected_interpretation:
            raise ValueError("effectiveness interpretation conflicts with configured tolerance")
        return self


def calculate_aggressor_effectiveness(
    shock: LiquidityShock,
    impact: ShockImpact,
    epsilon: Decimal,
    *,
    effectiveness_version: str = AGGRESSOR_EFFECTIVENESS_VERSION,
) -> AggressorEffectiveness:
    """Calculate exact signed ``AE_k(h)`` from one available directional impact."""
    if epsilon <= 0:
        raise ValueError("aggressor-effectiveness epsilon must be positive")
    if impact.shock_id != shock.shock_id or impact.direction is not shock.direction:
        raise ValueError("impact must belong to the supplied shock and direction")
    if not impact.available or impact.directional_price_impact is None:
        raise ValueError("aggressor effectiveness requires available directional impact")
    effectiveness = impact.directional_price_impact / (shock.normalized_aggression + epsilon)
    return AggressorEffectiveness(
        shock_id=shock.shock_id,
        direction=shock.direction,
        horizon_events=impact.horizon_events,
        directional_price_impact=impact.directional_price_impact,
        normalized_aggression=shock.normalized_aggression,
        epsilon=epsilon,
        effectiveness=effectiveness,
        impact_version=impact.impact_version,
        effectiveness_version=effectiveness_version,
    )


def compare_aggressor_effectiveness(
    pair: ShockPair,
    ae_1: AggressorEffectiveness,
    ae_2: AggressorEffectiveness,
    stability_tolerance: Decimal,
) -> ShockPairEffectivenessComparison:
    """Return absolute and epsilon-regularized relative AE changes for one horizon."""
    if stability_tolerance < 0:
        raise ValueError("effectiveness stability tolerance must be non-negative")
    if ae_1.shock_id != pair.shock_1_id or ae_2.shock_id != pair.shock_2_id:
        raise ValueError("effectiveness inputs must follow the ordered shock pair")
    if ae_1.direction is not pair.direction or ae_2.direction is not pair.direction:
        raise ValueError("effectiveness directions must match the shock pair")
    if ae_1.horizon_events != ae_2.horizon_events:
        raise ValueError("effectiveness inputs must use the same event horizon")
    if ae_1.epsilon != ae_2.epsilon:
        raise ValueError("effectiveness inputs must use the same epsilon")
    delta = ae_2.effectiveness - ae_1.effectiveness
    relative = delta / (abs(ae_1.effectiveness) + ae_1.epsilon)
    return ShockPairEffectivenessComparison(
        pair_id=pair.pair_id,
        horizon_events=ae_1.horizon_events,
        ae_1=ae_1,
        ae_2=ae_2,
        delta_ae=delta,
        relative_ae_change=relative,
        stability_tolerance=stability_tolerance,
        interpretation=interpret_effectiveness_change(delta, stability_tolerance),
        comparison_version=pair.comparison_version,
    )


def interpret_effectiveness_change(
    delta_ae: Decimal,
    tolerance: Decimal,
) -> EffectivenessInterpretation:
    """Classify AE weakening/strengthening using an inclusive stable band."""
    if tolerance < 0:
        raise ValueError("effectiveness tolerance must be non-negative")
    if delta_ae < -tolerance:
        return EffectivenessInterpretation.WEAKENING
    if delta_ae > tolerance:
        return EffectivenessInterpretation.STRENGTHENING
    return EffectivenessInterpretation.STABLE
