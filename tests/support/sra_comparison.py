"""Exact immutable Milestone H inputs for focused comparison tests."""

from decimal import Decimal

from sra_nexus.sra import (
    IMPACT_VERSION,
    RESILIENCY_VERSION,
    RecoveryTime,
    ResiliencyObservation,
    ResiliencyVector,
    ShockDirection,
    ShockImpact,
)
from sra_nexus.sra.shock import LiquidityShock


def shock_impact(
    shock: LiquidityShock,
    horizon_events: int,
    directional_price_impact: str,
) -> ShockImpact:
    """Build one internally coherent available signed impact."""
    directional = Decimal(directional_price_impact)
    direction_sign = Decimal(1) if shock.direction is ShockDirection.BUY else Decimal(-1)
    raw = direction_sign * directional
    baseline = Decimal("100")
    future = baseline + raw
    return ShockImpact(
        shock_id=shock.shock_id,
        direction=shock.direction,
        horizon_events=horizon_events,
        baseline_midprice=baseline,
        future_midprice=future,
        raw_price_impact=raw,
        directional_price_impact=directional,
        volume_normalized_impact=abs(raw) / shock.aggressive_volume,
        directional_volume_normalized_impact=directional / shock.aggressive_volume,
        normalized_aggression_impact=abs(raw) / shock.normalized_aggression,
        exchange_elapsed_seconds=Decimal(horizon_events),
        process_elapsed_seconds=Decimal(horizon_events),
        available=True,
        impact_version=IMPACT_VERSION,
    )


def recovery_time(
    threshold: str,
    events: int | None,
    exchange_seconds: str | None = None,
    process_seconds: str | None = None,
) -> RecoveryTime:
    """Build a reached or explicitly unreached recovery threshold."""
    recovered = events is not None
    exchange = (
        None
        if exchange_seconds is None and not recovered
        else Decimal(str(events) if exchange_seconds is None else exchange_seconds)
    )
    process = (
        None
        if process_seconds is None and not recovered
        else Decimal(str(events) if process_seconds is None else process_seconds)
    )
    return RecoveryTime(
        threshold=Decimal(threshold),
        events_to_recovery=events,
        exchange_seconds_to_recovery=exchange,
        process_seconds_to_recovery=process,
        recovered=recovered,
    )


def resiliency_vector(
    shock: LiquidityShock,
    rr_by_horizon: tuple[tuple[int, str], ...],
    recovery_times: tuple[RecoveryTime, ...],
) -> ResiliencyVector:
    """Build a valid raw-depth vector with selected available RR values."""
    observations = tuple(
        ResiliencyObservation(
            horizon_events=horizon,
            attacked_depth=Decimal("50") + Decimal("50") * Decimal(ratio),
            replenished_depth=Decimal("50") * Decimal(ratio),
            replenishment_ratio=Decimal(ratio),
            level_recoveries=(),
            near_touch_weighted_sum=None,
            near_touch_available_weight=None,
            near_touch_strength=None,
            deep_support_numerator=None,
            touch_recovery=None,
            deep_support_ratio=None,
            exchange_elapsed_seconds=Decimal(horizon),
            process_elapsed_seconds=Decimal(horizon),
        )
        for horizon, ratio in rr_by_horizon
    )
    return ResiliencyVector(
        shock_id=shock.shock_id,
        depth_levels_k=3,
        baseline_depth=Decimal("100"),
        minimum_depth=Decimal("50"),
        consumed_depth=Decimal("50"),
        original_price_levels=(),
        rr_by_horizon=observations,
        recovery_times=recovery_times,
        observation_end=None,
        resiliency_version=RESILIENCY_VERSION,
    )
