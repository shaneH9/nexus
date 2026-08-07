"""Tests for exact depletion, replenishment, multi-level, and recovery features."""

from datetime import timedelta
from decimal import Decimal

from tests.support.sra import (
    SRA_BASE_TIME,
    liquidity_shock,
    response_observation,
    snapshot,
)

from sra_nexus.sra import (
    RecoveryPoint,
    ResiliencyConfig,
    ResiliencyUnavailableReason,
    ShockDirection,
    calculate_deep_support_ratio,
    calculate_level_recovery,
    calculate_near_touch_strength,
    calculate_recovery_times,
    calculate_replenishment_ratio,
    calculate_resiliency_vector,
)


def test_replenishment_ratio_exact_half_recovery() -> None:
    """D0=1000, D_min=400, and future=700 should yield RR=0.5."""
    assert calculate_replenishment_ratio(
        Decimal("1000"), Decimal("400"), Decimal("700")
    ) == Decimal("0.5")


def test_replenishment_ratio_preserves_over_recovery_above_one() -> None:
    """Future depth above baseline should not be clamped."""
    result = calculate_replenishment_ratio(
        Decimal("1000"),
        Decimal("400"),
        Decimal("1200"),
    )

    assert result == Decimal("800") / Decimal("600")
    assert result is not None and result > 1


def test_no_depletion_makes_replenishment_unavailable() -> None:
    """D0 equal to D_min should return unavailable rather than zero."""
    assert calculate_replenishment_ratio(Decimal("1000"), Decimal("1000"), Decimal("1100")) is None


def test_recovery_thresholds_use_first_event_from_shock_end() -> None:
    """First-passage event counts and exchange seconds should remain independent."""
    ratios = ("0.10", "0.30", "0.55", "0.80", "1.05")
    irregular_seconds = (1, 3, 8, 9, 15)
    points = tuple(
        RecoveryPoint(
            horizon_events=index,
            replenishment_ratio=Decimal(ratio),
            exchange_time=SRA_BASE_TIME + timedelta(seconds=seconds),
            process_time=SRA_BASE_TIME + timedelta(seconds=seconds, milliseconds=2),
        )
        for index, (ratio, seconds) in enumerate(
            zip(ratios, irregular_seconds, strict=True),
            start=1,
        )
    )

    results = calculate_recovery_times(
        points,
        tuple(Decimal(value) for value in ("0.25", "0.50", "0.75", "1.00")),
        origin_exchange_time=SRA_BASE_TIME,
        origin_process_time=SRA_BASE_TIME,
    )

    assert tuple(result.events_to_recovery for result in results) == (2, 3, 4, 5)
    assert tuple(result.exchange_seconds_to_recovery for result in results) == (
        Decimal("3"),
        Decimal("8"),
        Decimal("9"),
        Decimal("15"),
    )
    assert tuple(result.process_seconds_to_recovery for result in results) == (
        Decimal("3.002"),
        Decimal("8.002"),
        Decimal("9.002"),
        Decimal("15.002"),
    )


def test_unreached_recovery_uses_none_without_sentinel() -> None:
    """A threshold absent from the observation window should remain unrecovered."""
    result = calculate_recovery_times(
        (
            RecoveryPoint(
                horizon_events=1,
                replenishment_ratio=Decimal("0.5"),
                exchange_time=SRA_BASE_TIME + timedelta(seconds=1),
                process_time=SRA_BASE_TIME + timedelta(seconds=1),
            ),
        ),
        (Decimal("1"),),
        origin_exchange_time=SRA_BASE_TIME,
        origin_process_time=SRA_BASE_TIME,
    )[0]

    assert not result.recovered
    assert result.events_to_recovery is None
    assert result.exchange_seconds_to_recovery is None


def test_original_price_level_recovery_and_unavailable_denominator() -> None:
    """Each fixed pre-shock price should use its own exact depletion denominator."""
    recovered = calculate_level_recovery(
        level_rank=1,
        original_price=Decimal("100"),
        pre_depth=Decimal("200"),
        minimum_depth=Decimal("50"),
        future_depth=Decimal("125"),
    )
    unchanged = calculate_level_recovery(
        level_rank=2,
        original_price=Decimal("99"),
        pre_depth=Decimal("300"),
        minimum_depth=Decimal("300"),
        future_depth=Decimal("300"),
    )

    assert recovered.replenishment_ratio == Decimal("0.5")
    assert unchanged.replenishment_ratio is None


def test_near_touch_strength_renormalizes_only_available_weights() -> None:
    """Known RR values and weights should produce the exact documented weighted mean."""
    levels = (
        calculate_level_recovery(
            level_rank=1,
            original_price=Decimal("100"),
            pre_depth=Decimal("100"),
            minimum_depth=Decimal("0"),
            future_depth=Decimal("50"),
        ),
        calculate_level_recovery(
            level_rank=2,
            original_price=Decimal("99"),
            pre_depth=Decimal("100"),
            minimum_depth=Decimal("0"),
            future_depth=Decimal("100"),
        ),
        calculate_level_recovery(
            level_rank=3,
            original_price=Decimal("98"),
            pre_depth=Decimal("100"),
            minimum_depth=Decimal("100"),
            future_depth=Decimal("100"),
        ),
    )

    result = calculate_near_touch_strength(
        levels,
        (Decimal("0.5"), Decimal("0.3"), Decimal("0.2")),
    )

    assert result == Decimal("0.55") / Decimal("0.8")


def test_deep_support_ratio_preserves_exact_components() -> None:
    """DSR should use raw deep weighted recovery over touch recovery plus epsilon."""
    levels = (
        calculate_level_recovery(
            level_rank=1,
            original_price=Decimal("100"),
            pre_depth=Decimal("100"),
            minimum_depth=Decimal("0"),
            future_depth=Decimal("50"),
        ),
        calculate_level_recovery(
            level_rank=2,
            original_price=Decimal("99"),
            pre_depth=Decimal("100"),
            minimum_depth=Decimal("0"),
            future_depth=Decimal("100"),
        ),
    )

    result = calculate_deep_support_ratio(
        levels,
        (Decimal("0.6"), Decimal("0.4")),
        Decimal("0.1"),
    )

    assert result == Decimal("0.4") / Decimal("0.6")


def test_resiliency_vector_uses_raw_k_depth_and_original_prices() -> None:
    """Vector should retain D0, D_min, consumed depth, RR, and fixed price levels."""
    shock = liquidity_shock(
        direction=ShockDirection.SELL,
        end_time=SRA_BASE_TIME + timedelta(milliseconds=2),
    )
    pre = snapshot(
        0,
        bids=(("100.00", "300"), ("99.99", "300"), ("99.98", "400")),
    )
    depleted = snapshot(1, bids=(("99.99", "200"), ("99.98", "200")))
    future = snapshot(
        2,
        bids=(("100.00", "200"), ("99.99", "250"), ("99.98", "250")),
    )
    response = response_observation(
        1,
        future,
        exchange_time=SRA_BASE_TIME + timedelta(seconds=2),
        process_time=SRA_BASE_TIME + timedelta(seconds=2, milliseconds=1),
    )
    config = ResiliencyConfig(
        depth_levels_k=3,
        recovery_horizons_events=(1,),
        multi_level_weights=(Decimal("0.5"), Decimal("0.3"), Decimal("0.2")),
    )

    vector = calculate_resiliency_vector(shock, pre, (depleted,), (response,), config)
    observation = vector.rr_by_horizon[0]

    assert vector.baseline_depth == Decimal("1000")
    assert vector.minimum_depth == Decimal("400")
    assert vector.consumed_depth == Decimal("600")
    assert observation.attacked_depth == Decimal("700")
    assert observation.replenished_depth == Decimal("300")
    assert observation.replenishment_ratio == Decimal("0.5")
    assert vector.original_price_levels == (
        Decimal("100.00"),
        Decimal("99.99"),
        Decimal("99.98"),
    )
    assert observation.level_recoveries[0].original_price == Decimal("100.00")


def test_buy_shock_depth_baseline_is_symmetric_on_ask_side() -> None:
    """BUY shocks should calculate raw K-level depletion from asks."""
    shock = liquidity_shock(
        direction=ShockDirection.BUY,
        end_time=SRA_BASE_TIME + timedelta(milliseconds=2),
    )
    pre = snapshot(
        0,
        asks=(("100.01", "300"), ("100.02", "300"), ("100.03", "400")),
    )
    depleted = snapshot(1, asks=(("100.02", "200"), ("100.03", "200")))
    response = response_observation(1, depleted, exchange_time=SRA_BASE_TIME + timedelta(seconds=1))

    vector = calculate_resiliency_vector(
        shock,
        pre,
        (depleted,),
        (response,),
        ResiliencyConfig(
            depth_levels_k=3,
            recovery_horizons_events=(1,),
            multi_level_weights=(Decimal("0.5"), Decimal("0.3"), Decimal("0.2")),
        ),
    )

    assert vector.baseline_depth == Decimal("1000")
    assert vector.minimum_depth == Decimal("400")


def test_vector_marks_no_depletion_ratio_unavailable() -> None:
    """A flat depth path should expose NO_DEPLETION rather than RR=0."""
    shock = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(milliseconds=2))
    pre = snapshot(0, bids=(("100.00", "1000"),))
    future = snapshot(1, bids=(("100.00", "1000"),))
    response = response_observation(1, future, exchange_time=SRA_BASE_TIME + timedelta(seconds=1))

    vector = calculate_resiliency_vector(
        shock,
        pre,
        (future,),
        (response,),
        ResiliencyConfig(
            depth_levels_k=1,
            recovery_horizons_events=(1,),
            multi_level_weights=(Decimal("1"),),
        ),
    )

    assert vector.consumed_depth == 0
    assert vector.rr_by_horizon[0].replenishment_ratio is None
    assert vector.rr_by_horizon[0].unavailable_reason is ResiliencyUnavailableReason.NO_DEPLETION
