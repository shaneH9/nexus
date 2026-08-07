"""Tests for complete and explicitly unavailable failed-aggression comparisons."""

from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple

import pytest
from tests.support.sra import SRA_BASE_TIME, liquidity_shock
from tests.support.sra_comparison import recovery_time, resiliency_vector, shock_impact

from sra_nexus.sra import (
    EffectivenessInterpretation,
    LiquidityShock,
    RecoveryComparisonUnavailableReason,
    RecoveryTimeInterpretation,
    ResiliencyVector,
    ShockDirection,
    ShockImpact,
    ShockPairConfig,
    ShockPairIncomparabilityReason,
    ShockPairService,
    ShockPairSpan,
    StructuralBreakKind,
)


class _PairInputs(NamedTuple):
    shock_1: LiquidityShock
    shock_2: LiquidityShock
    impacts_1: tuple[ShockImpact, ...]
    impacts_2: tuple[ShockImpact, ...]
    vector_1: ResiliencyVector
    vector_2: ResiliencyVector


def _config() -> ShockPairConfig:
    return ShockPairConfig(
        max_event_distance=100,
        max_exchange_seconds=Decimal("30"),
        required_impact_horizons_events=(5, 10, 25),
        required_resiliency_horizons_events=(5, 10, 25),
        required_recovery_thresholds=(Decimal("0.75"), Decimal("1.00")),
        epsilon=Decimal("0.000001"),
        effectiveness_stability_tolerance=Decimal("0.000001"),
    )


def _inputs(
    *,
    direction: ShockDirection = ShockDirection.SELL,
    shock_2_aggression: str = "0.5",
    shock_2_reaches_full: bool = True,
) -> _PairInputs:
    shock_1 = liquidity_shock(
        direction=direction,
        normalized_aggression="0.5",
        end_time=SRA_BASE_TIME,
    )
    shock_2 = liquidity_shock(
        direction=direction,
        normalized_aggression=shock_2_aggression,
        end_time=SRA_BASE_TIME + timedelta(seconds=10),
    )
    impacts_1 = tuple(
        shock_impact(shock_1, horizon, directional)
        for horizon, directional in ((5, "0.02"), (10, "0.03"), (25, "0.04"))
    )
    impacts_2 = tuple(
        shock_impact(shock_2, horizon, directional)
        for horizon, directional in ((5, "0.01"), (10, "0.015"), (25, "0.01"))
    )
    recovery_1 = (
        recovery_time("0.75", 10, "20", "21"),
        recovery_time("1.00", 15, "30", "31"),
    )
    recovery_2 = (
        recovery_time("0.75", 6, "12", "13"),
        (
            recovery_time("1.00", 12, "24", "25")
            if shock_2_reaches_full
            else recovery_time("1.00", None)
        ),
    )
    vector_1 = resiliency_vector(
        shock_1,
        ((5, "0.30"), (10, "0.40"), (25, "0.50")),
        recovery_1,
    )
    vector_2 = resiliency_vector(
        shock_2,
        ((5, "0.45"), (10, "0.60"), (25, "0.80")),
        recovery_2,
    )
    return _PairInputs(shock_1, shock_2, impacts_1, impacts_2, vector_1, vector_2)


def test_service_builds_multi_horizon_failed_aggression_features() -> None:
    """One comparable pair should retain independent AE, RR, recovery, and absorption."""
    shock_1, shock_2, impacts_1, impacts_2, vector_1, vector_2 = _inputs()

    result = ShockPairService(_config()).compare(
        shock_1=shock_1,
        shock_2=shock_2,
        span=ShockPairSpan(event_distance=40),
        impacts_1=impacts_1,
        impacts_2=impacts_2,
        resiliency_1=vector_1,
        resiliency_2=vector_2,
    )

    assert result.comparison_available
    assert result.pair is not None
    assert result.pair_id == result.pair.pair_id
    assert result.event_distance == 40
    assert result.exchange_seconds_distance == Decimal("10")
    assert result.aggression_ratio == Decimal("1")
    assert tuple(item.horizon_events for item in result.effectiveness_by_horizon) == (5, 10, 25)
    assert tuple(item.horizon_events for item in result.resiliency_by_horizon) == (5, 10, 25)
    assert tuple(item.horizon_events for item in result.absorption_efficiency_by_horizon) == (
        5,
        10,
        25,
    )
    expected_deltas = tuple(
        second / (Decimal("0.5") + _config().epsilon) - first / (Decimal("0.5") + _config().epsilon)
        for first, second in (
            (Decimal("0.02"), Decimal("0.01")),
            (Decimal("0.03"), Decimal("0.015")),
            (Decimal("0.04"), Decimal("0.01")),
        )
    )
    assert tuple(item.delta_ae for item in result.effectiveness_by_horizon) == expected_deltas
    assert all(
        item.interpretation is EffectivenessInterpretation.WEAKENING
        for item in result.effectiveness_by_horizon
    )
    assert result.resiliency_by_horizon[2].delta_rr == Decimal("0.30")
    recovery_75 = result.recovery_comparisons[0]
    assert recovery_75.delta_events == -4
    assert recovery_75.delta_exchange_seconds == Decimal("-8")
    assert recovery_75.delta_process_seconds == Decimal("-8")
    assert recovery_75.events_interpretation is RecoveryTimeInterpretation.FASTER
    assert recovery_75.exchange_interpretation is RecoveryTimeInterpretation.FASTER
    assert recovery_75.process_interpretation is RecoveryTimeInterpretation.FASTER
    assert result.absorption_efficiency_by_horizon[2].delta_absorption_efficiency > 0
    assert result.reasons_unavailable == ()
    assert not hasattr(result, "signal")
    assert not hasattr(result, "order")


def test_unreached_recovery_makes_only_that_delta_explicitly_unavailable() -> None:
    """A recorded unreached threshold should not invalidate otherwise comparable features."""
    shock_1, shock_2, impacts_1, impacts_2, vector_1, vector_2 = _inputs(shock_2_reaches_full=False)

    result = ShockPairService(_config()).compare(
        shock_1=shock_1,
        shock_2=shock_2,
        span=ShockPairSpan(event_distance=40),
        impacts_1=impacts_1,
        impacts_2=impacts_2,
        resiliency_1=vector_1,
        resiliency_2=vector_2,
    )

    full_recovery = result.recovery_comparisons[1]
    assert result.comparison_available
    assert not full_recovery.available
    assert full_recovery.delta_events is None
    assert full_recovery.delta_exchange_seconds is None
    assert full_recovery.events_interpretation is RecoveryTimeInterpretation.UNAVAILABLE
    assert full_recovery.exchange_interpretation is RecoveryTimeInterpretation.UNAVAILABLE
    assert full_recovery.process_interpretation is RecoveryTimeInterpretation.UNAVAILABLE
    assert full_recovery.unavailable_reason is RecoveryComparisonUnavailableReason.SHOCK_2_UNREACHED


def test_missing_required_impact_returns_incomparable_without_partial_features() -> None:
    """Ordinary feature absence should be typed and atomic rather than an exception."""
    shock_1, shock_2, impacts_1, impacts_2, vector_1, vector_2 = _inputs()
    missing_horizon = tuple(item for item in impacts_2 if item.horizon_events != 25)

    result = ShockPairService(_config()).compare(
        shock_1=shock_1,
        shock_2=shock_2,
        span=ShockPairSpan(event_distance=40),
        impacts_1=impacts_1,
        impacts_2=missing_horizon,
        resiliency_1=vector_1,
        resiliency_2=vector_2,
    )

    assert not result.comparison_available
    assert result.pair is None
    assert result.effectiveness_by_horizon == ()
    assert result.resiliency_by_horizon == ()
    assert result.recovery_comparisons == ()
    assert result.absorption_efficiency_by_horizon == ()
    assert any(
        reason.reason is ShockPairIncomparabilityReason.REQUIRED_IMPACT_UNAVAILABLE_SHOCK_2
        and reason.horizon_events == 25
        for reason in result.reasons_unavailable
    )


def test_ratio_direction_and_structural_break_failures_return_reasons() -> None:
    """Comparability policy failures should return no pair-derived feature fragments."""
    shock_1, shock_2, impacts_1, impacts_2, vector_1, vector_2 = _inputs(shock_2_aggression="2.0")

    result = ShockPairService(_config()).compare(
        shock_1=shock_1,
        shock_2=shock_2,
        span=ShockPairSpan(
            event_distance=40,
            structural_break=StructuralBreakKind.SEQUENCE_CORRUPTION,
        ),
        impacts_1=impacts_1,
        impacts_2=impacts_2,
        resiliency_1=vector_1,
        resiliency_2=vector_2,
    )
    codes = {reason.reason for reason in result.reasons_unavailable}

    assert not result.comparison_available
    assert ShockPairIncomparabilityReason.STRUCTURAL_BREAK in codes
    assert ShockPairIncomparabilityReason.AGGRESSION_RATIO_OUTSIDE_BOUNDS in codes


def test_malformed_feature_ownership_remains_a_data_integrity_error() -> None:
    """An impact attached to the wrong shock is malformed input, not ordinary absence."""
    shock_1, shock_2, impacts_1, impacts_2, vector_1, vector_2 = _inputs()
    wrong_owner = (impacts_2[0], *impacts_1[1:])

    with pytest.raises(ValueError, match="every impact must belong"):
        ShockPairService(_config()).compare(
            shock_1=shock_1,
            shock_2=shock_2,
            span=ShockPairSpan(event_distance=40),
            impacts_1=wrong_owner,
            impacts_2=impacts_2,
            resiliency_1=vector_1,
            resiliency_2=vector_2,
        )
