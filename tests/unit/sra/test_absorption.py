"""Tests for finite magnitude-normalized absorption efficiency."""

from datetime import timedelta
from decimal import Decimal

from tests.support.sra import SRA_BASE_TIME, liquidity_shock
from tests.support.sra_comparison import shock_impact

from sra_nexus.sra import (
    ShockPairConfig,
    ShockPairSpan,
    assess_shock_pair,
    build_shock_pair,
    calculate_absorption_efficiency,
    calculate_aggressor_effectiveness,
    compare_absorption_efficiency,
)


def test_absorption_delta_matches_magnitude_formula_without_clamping() -> None:
    """The epsilon-regularized values should approach 12.5, 40, and +27.5 exactly."""
    epsilon = Decimal("0.000000000001")
    shock_1 = liquidity_shock(end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(seconds=1))
    config = ShockPairConfig(
        required_impact_horizons_events=(25,),
        required_resiliency_horizons_events=(25,),
        required_recovery_thresholds=(Decimal("0.75"),),
        epsilon=epsilon,
    )
    pair = build_shock_pair(
        shock_1,
        shock_2,
        assess_shock_pair(shock_1, shock_2, ShockPairSpan(event_distance=1), config),
        config,
    )
    ae_1 = calculate_aggressor_effectiveness(
        shock_1,
        shock_impact(shock_1, 25, "0.02"),
        epsilon,
    )
    ae_2 = calculate_aggressor_effectiveness(
        shock_2,
        shock_impact(shock_2, 25, "0.01"),
        epsilon,
    )
    abs_eff_1 = calculate_absorption_efficiency(ae_1, Decimal("0.5"), epsilon)
    abs_eff_2 = calculate_absorption_efficiency(ae_2, Decimal("0.8"), epsilon)

    result = compare_absorption_efficiency(pair, abs_eff_1, abs_eff_2)

    assert abs_eff_1.magnitude_efficiency == Decimal("0.5") / (abs(ae_1.effectiveness) + epsilon)
    assert abs_eff_2.magnitude_efficiency == Decimal("0.8") / (abs(ae_2.effectiveness) + epsilon)
    assert abs(abs_eff_1.magnitude_efficiency - Decimal("12.5")) < Decimal("0.00000001")
    assert abs(abs_eff_2.magnitude_efficiency - Decimal("40")) < Decimal("0.00000001")
    assert result.delta_absorption_efficiency == (
        abs_eff_2.magnitude_efficiency - abs_eff_1.magnitude_efficiency
    )
    assert abs(result.delta_absorption_efficiency - Decimal("27.5")) < Decimal("0.00000001")


def test_zero_effectiveness_uses_epsilon_and_remains_finite() -> None:
    """AE=0 should yield a large exact value rather than infinity, NaN, or a clamp."""
    epsilon = Decimal("0.000001")
    shock = liquidity_shock(end_time=SRA_BASE_TIME)
    ae = calculate_aggressor_effectiveness(
        shock,
        shock_impact(shock, 5, "0"),
        epsilon,
    )

    result = calculate_absorption_efficiency(ae, Decimal("0.5"), epsilon)

    assert ae.effectiveness == 0
    assert result.magnitude_efficiency == Decimal("500000")
    assert result.magnitude_efficiency.is_finite()


def test_negative_effectiveness_uses_magnitude_without_directional_instability() -> None:
    """Equal positive and negative AE magnitudes should have equal absorption values."""
    epsilon = Decimal("0.000001")
    shock = liquidity_shock(end_time=SRA_BASE_TIME)
    positive = calculate_aggressor_effectiveness(
        shock,
        shock_impact(shock, 5, "0.01"),
        epsilon,
    )
    negative = calculate_aggressor_effectiveness(
        shock,
        shock_impact(shock, 5, "-0.01"),
        epsilon,
    )

    positive_absorption = calculate_absorption_efficiency(
        positive,
        Decimal("0.5"),
        epsilon,
    )
    negative_absorption = calculate_absorption_efficiency(
        negative,
        Decimal("0.5"),
        epsilon,
    )

    assert positive_absorption.magnitude_efficiency == negative_absorption.magnitude_efficiency
