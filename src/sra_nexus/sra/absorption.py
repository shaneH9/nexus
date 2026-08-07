"""Stable magnitude-normalized absorption-efficiency research features."""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel, ExactDecimal, NonBlankStr, PositiveDecimal
from sra_nexus.common.types import ShockId, ShockPairId
from sra_nexus.sra.effectiveness import AggressorEffectiveness
from sra_nexus.sra.shock_pair import ShockPair

ABSORPTION_EFFICIENCY_VERSION = "absorption-efficiency-v1"


class AbsorptionEfficiency(ContractModel):
    """Replenishment achieved per absolute aggressor-effectiveness magnitude.

    The primary stable representation is ``RR / (abs(AE) + epsilon)``. Because
    RR is dimensionless and AE is measured in price units, the result has units
    of inverse instrument price. Values are not clamped.
    """

    shock_id: ShockId
    horizon_events: int = Field(gt=0)
    replenishment_ratio: ExactDecimal
    aggressor_effectiveness: ExactDecimal
    epsilon: PositiveDecimal
    magnitude_efficiency: ExactDecimal
    absorption_version: NonBlankStr = ABSORPTION_EFFICIENCY_VERSION

    @model_validator(mode="after")
    def validate_formula(self) -> Self:
        """Require exact magnitude-normalized absorption efficiency."""
        expected = self.replenishment_ratio / (abs(self.aggressor_effectiveness) + self.epsilon)
        if self.magnitude_efficiency != expected:
            raise ValueError("absorption efficiency does not match its exact formula")
        return self


class AbsorptionEfficiencyComparison(ContractModel):
    """Change in magnitude-normalized absorption efficiency at one horizon."""

    pair_id: ShockPairId
    horizon_events: int = Field(gt=0)
    abs_eff_1: AbsorptionEfficiency
    abs_eff_2: AbsorptionEfficiency
    delta_absorption_efficiency: ExactDecimal
    comparison_version: NonBlankStr

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        """Require aligned horizons and exact ``AbsEff_2 - AbsEff_1``."""
        if (
            self.abs_eff_1.horizon_events != self.horizon_events
            or self.abs_eff_2.horizon_events != self.horizon_events
        ):
            raise ValueError("absorption horizons must match the comparison horizon")
        expected = self.abs_eff_2.magnitude_efficiency - self.abs_eff_1.magnitude_efficiency
        if self.delta_absorption_efficiency != expected:
            raise ValueError("absorption delta must equal AbsEff_2 - AbsEff_1")
        return self


def calculate_absorption_efficiency(
    aggressor_effectiveness: AggressorEffectiveness,
    replenishment_ratio: Decimal,
    epsilon: Decimal,
    *,
    absorption_version: str = ABSORPTION_EFFICIENCY_VERSION,
) -> AbsorptionEfficiency:
    """Calculate finite ``RR / (abs(AE) + epsilon)`` without clamping."""
    if epsilon <= 0:
        raise ValueError("absorption-efficiency epsilon must be positive")
    value = replenishment_ratio / (abs(aggressor_effectiveness.effectiveness) + epsilon)
    return AbsorptionEfficiency(
        shock_id=aggressor_effectiveness.shock_id,
        horizon_events=aggressor_effectiveness.horizon_events,
        replenishment_ratio=replenishment_ratio,
        aggressor_effectiveness=aggressor_effectiveness.effectiveness,
        epsilon=epsilon,
        magnitude_efficiency=value,
        absorption_version=absorption_version,
    )


def compare_absorption_efficiency(
    pair: ShockPair,
    abs_eff_1: AbsorptionEfficiency,
    abs_eff_2: AbsorptionEfficiency,
) -> AbsorptionEfficiencyComparison:
    """Compare ordered absorption features without interpreting them as a signal."""
    if abs_eff_1.shock_id != pair.shock_1_id or abs_eff_2.shock_id != pair.shock_2_id:
        raise ValueError("absorption inputs must follow the ordered shock pair")
    if abs_eff_1.horizon_events != abs_eff_2.horizon_events:
        raise ValueError("absorption inputs must use the same event horizon")
    if abs_eff_1.epsilon != abs_eff_2.epsilon:
        raise ValueError("absorption inputs must use the same epsilon")
    return AbsorptionEfficiencyComparison(
        pair_id=pair.pair_id,
        horizon_events=abs_eff_1.horizon_events,
        abs_eff_1=abs_eff_1,
        abs_eff_2=abs_eff_2,
        delta_absorption_efficiency=(
            abs_eff_2.magnitude_efficiency - abs_eff_1.magnitude_efficiency
        ),
        comparison_version=pair.comparison_version,
    )
