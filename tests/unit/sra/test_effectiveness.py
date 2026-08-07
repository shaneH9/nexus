"""Tests for signed horizon-specific aggressor effectiveness."""

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.support.sra import SRA_BASE_TIME, liquidity_shock
from tests.support.sra_comparison import shock_impact

from sra_nexus.sra import (
    EffectivenessInterpretation,
    ShockDirection,
    ShockPairConfig,
    ShockPairSpan,
    assess_shock_pair,
    build_shock_pair,
    calculate_aggressor_effectiveness,
    compare_aggressor_effectiveness,
    interpret_effectiveness_change,
)

EPSILON = Decimal("0.000001")


@pytest.mark.parametrize("direction", (ShockDirection.SELL, ShockDirection.BUY))
def test_declining_directional_impact_weakens_buy_and_sell_aggressors(
    direction: ShockDirection,
) -> None:
    """Mirrored directions must share the same signed DeltaAE convention."""
    shock_1 = liquidity_shock(
        direction=direction,
        normalized_aggression="0.5",
        end_time=SRA_BASE_TIME,
    )
    shock_2 = liquidity_shock(
        direction=direction,
        normalized_aggression="0.5",
        end_time=SRA_BASE_TIME + timedelta(seconds=1),
    )
    config = ShockPairConfig(
        required_impact_horizons_events=(25,),
        required_resiliency_horizons_events=(25,),
        required_recovery_thresholds=(Decimal("0.75"),),
        epsilon=EPSILON,
    )
    assessment = assess_shock_pair(
        shock_1,
        shock_2,
        ShockPairSpan(event_distance=1),
        config,
    )
    pair = build_shock_pair(shock_1, shock_2, assessment, config)
    ae_1 = calculate_aggressor_effectiveness(
        shock_1,
        shock_impact(shock_1, 25, "0.02"),
        EPSILON,
    )
    ae_2 = calculate_aggressor_effectiveness(
        shock_2,
        shock_impact(shock_2, 25, "0.01"),
        EPSILON,
    )

    result = compare_aggressor_effectiveness(pair, ae_1, ae_2, Decimal("0.000001"))

    assert ae_1.effectiveness == Decimal("0.02") / (Decimal("0.5") + EPSILON)
    assert ae_2.effectiveness == Decimal("0.01") / (Decimal("0.5") + EPSILON)
    assert ae_1.effectiveness.quantize(Decimal("0.01")) == Decimal("0.04")
    assert ae_2.effectiveness.quantize(Decimal("0.01")) == Decimal("0.02")
    assert result.delta_ae == ae_2.effectiveness - ae_1.effectiveness
    assert result.delta_ae.quantize(Decimal("0.01")) == Decimal("-0.02")
    assert result.interpretation is EffectivenessInterpretation.WEAKENING
    assert result.relative_ae_change == result.delta_ae / (abs(ae_1.effectiveness) + EPSILON)


def test_negative_second_effectiveness_is_valid_reversal_evidence() -> None:
    """Movement against the second SELL aggressor should make AE negative, not invalid."""
    shock_1 = liquidity_shock(end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(seconds=1))
    config = ShockPairConfig(
        required_impact_horizons_events=(10,),
        required_resiliency_horizons_events=(10,),
        required_recovery_thresholds=(Decimal("0.75"),),
    )
    pair = build_shock_pair(
        shock_1,
        shock_2,
        assess_shock_pair(shock_1, shock_2, ShockPairSpan(event_distance=1), config),
        config,
    )
    ae_1 = calculate_aggressor_effectiveness(
        shock_1,
        shock_impact(shock_1, 10, "0.02"),
        config.epsilon,
    )
    ae_2 = calculate_aggressor_effectiveness(
        shock_2,
        shock_impact(shock_2, 10, "-0.01"),
        config.epsilon,
    )

    result = compare_aggressor_effectiveness(
        pair,
        ae_1,
        ae_2,
        config.effectiveness_stability_tolerance,
    )

    assert ae_2.effectiveness < 0
    assert result.delta_ae < 0
    assert result.interpretation is EffectivenessInterpretation.WEAKENING


@pytest.mark.parametrize(
    ("delta", "expected"),
    (
        ("-0.0011", EffectivenessInterpretation.WEAKENING),
        ("-0.001", EffectivenessInterpretation.STABLE),
        ("0", EffectivenessInterpretation.STABLE),
        ("0.001", EffectivenessInterpretation.STABLE),
        ("0.0011", EffectivenessInterpretation.STRENGTHENING),
    ),
)
def test_effectiveness_tolerance_defines_inclusive_stable_band(
    delta: str,
    expected: EffectivenessInterpretation,
) -> None:
    """Only changes strictly outside plus/minus tolerance should leave STABLE."""
    assert interpret_effectiveness_change(Decimal(delta), Decimal("0.001")) is expected
