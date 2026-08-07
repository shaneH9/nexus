"""Exact depth-depletion, replenishment, and recovery-time primitives."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import ShockId
from sra_nexus.market_data.enums import BookSide
from sra_nexus.market_data.snapshots import BookSnapshot, PriceLevel
from sra_nexus.sra.enums import ResiliencyUnavailableReason, ShockDirection
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.state import (
    MarketEventReference,
    MarketStateObservation,
    elapsed_decimal_seconds,
)

RESILIENCY_VERSION = "resiliency-v1"


def _default_recovery_horizons() -> tuple[int, ...]:
    return (5, 10, 25, 50, 100)


def _default_recovery_thresholds() -> tuple[Decimal, ...]:
    return (Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))


def _default_multi_level_weights() -> tuple[Decimal, ...]:
    return (Decimal("0.5"), Decimal("0.3"), Decimal("0.2"))


class ResiliencyConfig(ContractModel):
    """Central event horizons, raw-depth K, level weights, and formula version."""

    depth_levels_k: int = Field(default=3, gt=0)
    recovery_horizons_events: tuple[int, ...] = Field(default_factory=_default_recovery_horizons)
    recovery_thresholds: tuple[PositiveDecimal, ...] = Field(
        default_factory=_default_recovery_thresholds
    )
    multi_level_weights: tuple[PositiveDecimal, ...] = Field(
        default_factory=_default_multi_level_weights
    )
    epsilon: PositiveDecimal = Decimal("0.000001")
    resiliency_version: NonBlankStr = RESILIENCY_VERSION

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        """Require deterministic increasing horizons/thresholds and one weight per rank."""
        if not self.recovery_horizons_events or any(
            horizon <= 0 for horizon in self.recovery_horizons_events
        ):
            raise ValueError("recovery horizons must contain positive values")
        if tuple(sorted(set(self.recovery_horizons_events))) != self.recovery_horizons_events:
            raise ValueError("recovery horizons must be unique and increasing")
        if not self.recovery_thresholds:
            raise ValueError("at least one recovery threshold is required")
        if tuple(sorted(set(self.recovery_thresholds))) != self.recovery_thresholds:
            raise ValueError("recovery thresholds must be unique and increasing")
        if len(self.multi_level_weights) != self.depth_levels_k:
            raise ValueError("multi_level_weights must contain depth_levels_k values")
        if sum(self.multi_level_weights, Decimal(0)) != 1:
            raise ValueError("multi_level_weights must sum exactly to one")
        return self


class LevelRecovery(ContractModel):
    """Recovery of one original attacked-side absolute price level."""

    level_rank: int = Field(gt=0)
    original_price: PositiveDecimal
    pre_depth: PositiveDecimal
    minimum_depth: NonNegativeDecimal
    future_depth: NonNegativeDecimal
    replenishment_ratio: ExactDecimal | None

    @model_validator(mode="after")
    def validate_formula(self) -> Self:
        """Use unavailable rather than zero when an original level did not deplete."""
        if self.minimum_depth > self.pre_depth:
            raise ValueError("level minimum depth cannot exceed pre-shock depth")
        denominator = self.pre_depth - self.minimum_depth
        if denominator == 0:
            if self.replenishment_ratio is not None:
                raise ValueError("non-depleted level recovery must be unavailable")
        elif self.replenishment_ratio != (self.future_depth - self.minimum_depth) / denominator:
            raise ValueError("level recovery ratio does not match original-price depths")
        return self


class RecoveryPoint(ContractModel):
    """One per-event total-depth recovery observation used to find first passage."""

    horizon_events: int = Field(gt=0)
    replenishment_ratio: ExactDecimal | None
    exchange_time: UtcDatetime
    process_time: UtcDatetime


class RecoveryTime(ContractModel):
    """First event at which a configured replenishment threshold is reached."""

    threshold: PositiveDecimal
    events_to_recovery: int | None
    exchange_seconds_to_recovery: NonNegativeDecimal | None
    process_seconds_to_recovery: NonNegativeDecimal | None
    recovered: bool

    @model_validator(mode="after")
    def validate_recovery_shape(self) -> Self:
        """Use ``None`` rather than a sentinel for an unreached threshold."""
        values = (
            self.events_to_recovery,
            self.exchange_seconds_to_recovery,
            self.process_seconds_to_recovery,
        )
        if self.recovered and any(value is None for value in values):
            raise ValueError("recovered threshold requires event and clock times")
        if not self.recovered and any(value is not None for value in values):
            raise ValueError("unreached threshold cannot contain recovery sentinels")
        return self


class ResiliencyObservation(ContractModel):
    """Total and original-price-level recovery at one configured event horizon."""

    horizon_events: int = Field(gt=0)
    attacked_depth: NonNegativeDecimal | None
    replenished_depth: ExactDecimal | None
    replenishment_ratio: ExactDecimal | None
    level_recoveries: tuple[LevelRecovery, ...]
    near_touch_weighted_sum: ExactDecimal | None
    near_touch_available_weight: NonNegativeDecimal | None
    near_touch_strength: ExactDecimal | None
    deep_support_numerator: ExactDecimal | None
    touch_recovery: ExactDecimal | None
    deep_support_ratio: ExactDecimal | None
    exchange_elapsed_seconds: NonNegativeDecimal | None
    process_elapsed_seconds: NonNegativeDecimal | None
    unavailable_reason: ResiliencyUnavailableReason | None = None


class ResiliencyVector(ContractModel):
    """Uncompressed configured-horizon depth response for one liquidity shock."""

    shock_id: ShockId
    depth_levels_k: int = Field(gt=0)
    baseline_depth: NonNegativeDecimal
    minimum_depth: NonNegativeDecimal
    consumed_depth: NonNegativeDecimal
    original_price_levels: tuple[PositiveDecimal, ...]
    rr_by_horizon: tuple[ResiliencyObservation, ...]
    recovery_times: tuple[RecoveryTime, ...]
    observation_end: MarketEventReference | None
    resiliency_version: NonBlankStr

    @model_validator(mode="after")
    def validate_depth_identity(self) -> Self:
        """Require exact ``ConsumedDepth = D0 - D_min`` without clamping."""
        if self.minimum_depth > self.baseline_depth:
            raise ValueError("minimum depth cannot exceed baseline depth")
        if self.consumed_depth != self.baseline_depth - self.minimum_depth:
            raise ValueError("consumed_depth must equal baseline_depth - minimum_depth")
        return self


def attacked_side(direction: ShockDirection) -> BookSide:
    """Return ASK for BUY shocks and BID for SELL shocks."""
    return BookSide.ASK if direction is ShockDirection.BUY else BookSide.BID


def raw_attacked_depth(
    snapshot: BookSnapshot,
    direction: ShockDirection,
    depth_levels_k: int,
) -> Decimal:
    """Return raw current-rank attacked-side depth across the first K levels."""
    if depth_levels_k <= 0:
        raise ValueError("depth_levels_k must be positive")
    return (
        snapshot.ask_depth_n(depth_levels_k)
        if direction is ShockDirection.BUY
        else snapshot.bid_depth_n(depth_levels_k)
    )


def calculate_replenishment_ratio(
    baseline_depth: Decimal,
    minimum_depth: Decimal,
    future_depth: Decimal,
) -> Decimal | None:
    """Return ``(future_depth-D_min)/(D0-D_min)`` without upper clamping."""
    if min(baseline_depth, minimum_depth, future_depth) < 0:
        raise ValueError("depth values must be non-negative")
    if minimum_depth > baseline_depth:
        raise ValueError("minimum depth cannot exceed baseline depth")
    consumed_depth = baseline_depth - minimum_depth
    if consumed_depth == 0:
        return None
    return (future_depth - minimum_depth) / consumed_depth


def calculate_level_recovery(
    *,
    level_rank: int,
    original_price: Decimal,
    pre_depth: Decimal,
    minimum_depth: Decimal,
    future_depth: Decimal,
) -> LevelRecovery:
    """Calculate recovery for one fixed original absolute price level."""
    if level_rank <= 0:
        raise ValueError("level_rank must be positive")
    if pre_depth <= 0 or min(minimum_depth, future_depth) < 0:
        raise ValueError("level depths must be positive pre-shock and non-negative later")
    if minimum_depth > pre_depth:
        raise ValueError("minimum level depth cannot exceed pre-shock depth")
    denominator = pre_depth - minimum_depth
    ratio = None if denominator == 0 else (future_depth - minimum_depth) / denominator
    return LevelRecovery(
        level_rank=level_rank,
        original_price=original_price,
        pre_depth=pre_depth,
        minimum_depth=minimum_depth,
        future_depth=future_depth,
        replenishment_ratio=ratio,
    )


def calculate_near_touch_strength(
    level_recoveries: Sequence[LevelRecovery],
    weights: Sequence[Decimal],
) -> Decimal | None:
    """Return weighted mean NTS over available original-price recoveries.

    Missing, non-depleted levels are excluded and the remaining weights are
    explicitly renormalized by their available-weight sum.
    """
    weighted_sum, available_weight = _near_touch_components(level_recoveries, weights)
    if available_weight == 0:
        return None
    return weighted_sum / available_weight


def calculate_deep_support_ratio(
    level_recoveries: Sequence[LevelRecovery],
    weights: Sequence[Decimal],
    epsilon: Decimal,
) -> Decimal | None:
    """Return ``sum(j>=2, w_j*RR_j)/(RR_1+epsilon)`` when components exist."""
    if epsilon <= 0:
        raise ValueError("epsilon must be positive")
    _validate_level_weights(weights, len(level_recoveries))
    if not level_recoveries or level_recoveries[0].replenishment_ratio is None:
        return None
    deep_values = tuple(
        (weights[index], recovery.replenishment_ratio)
        for index, recovery in enumerate(level_recoveries[1:], start=1)
        if recovery.replenishment_ratio is not None
    )
    if not deep_values:
        return None
    numerator = sum((weight * ratio for weight, ratio in deep_values), Decimal(0))
    denominator = level_recoveries[0].replenishment_ratio + epsilon
    if denominator == 0:
        return None
    return numerator / denominator


def calculate_recovery_times(
    points: Sequence[RecoveryPoint],
    thresholds: Sequence[Decimal],
    *,
    origin_exchange_time: datetime,
    origin_process_time: datetime,
) -> tuple[RecoveryTime, ...]:
    """Find first event passages from shock END in event and clock units."""
    ordered = tuple(points)
    if any(
        earlier.horizon_events >= later.horizon_events
        for earlier, later in zip(ordered, ordered[1:], strict=False)
    ):
        raise ValueError("recovery points must have increasing event horizons")
    results: list[RecoveryTime] = []
    for threshold in thresholds:
        if threshold <= 0:
            raise ValueError("recovery thresholds must be positive")
        recovered = next(
            (
                point
                for point in ordered
                if point.replenishment_ratio is not None and point.replenishment_ratio >= threshold
            ),
            None,
        )
        if recovered is None:
            results.append(
                RecoveryTime(
                    threshold=threshold,
                    events_to_recovery=None,
                    exchange_seconds_to_recovery=None,
                    process_seconds_to_recovery=None,
                    recovered=False,
                )
            )
        else:
            results.append(
                RecoveryTime(
                    threshold=threshold,
                    events_to_recovery=recovered.horizon_events,
                    exchange_seconds_to_recovery=elapsed_decimal_seconds(
                        origin_exchange_time,
                        recovered.exchange_time,
                    ),
                    process_seconds_to_recovery=elapsed_decimal_seconds(
                        origin_process_time,
                        recovered.process_time,
                    ),
                    recovered=True,
                )
            )
    return tuple(results)


def calculate_resiliency_vector(
    shock: LiquidityShock,
    pre_snapshot: BookSnapshot,
    depletion_snapshots: Sequence[BookSnapshot],
    response_observations: Sequence[MarketStateObservation],
    config: ResiliencyConfig | None = None,
) -> ResiliencyVector:
    """Calculate raw K-level and original-price-level event response."""
    policy = ResiliencyConfig() if config is None else config
    depletion = tuple(depletion_snapshots)
    responses = tuple(response_observations)
    if not depletion:
        raise ValueError("at least one within-episode depletion snapshot is required")
    all_snapshots = (pre_snapshot, *depletion, *(item.snapshot for item in responses))
    if any(snapshot.instrument_id != shock.instrument_id for snapshot in all_snapshots):
        raise ValueError("shock and resiliency snapshots must share instrument_id")
    if len({snapshot.venue for snapshot in all_snapshots}) > 1:
        raise ValueError("all resiliency snapshots must share venue")
    if (
        pre_snapshot.exchange_time > shock.start_exchange_time
        or pre_snapshot.process_time > shock.start_process_time
    ):
        raise ValueError("resiliency baseline must be observable before shock onset")
    if any(
        snapshot.exchange_time > shock.end_exchange_time
        or snapshot.process_time > shock.end_process_time
        for snapshot in depletion
    ):
        raise ValueError("depletion snapshots must not expose post-shock state")
    _validate_response_order(shock, responses)

    baseline = raw_attacked_depth(pre_snapshot, shock.direction, policy.depth_levels_k)
    within_depths = tuple(
        raw_attacked_depth(snapshot, shock.direction, policy.depth_levels_k)
        for snapshot in depletion
    )
    minimum = min((baseline, *within_depths))
    consumed = baseline - minimum
    original_levels = _attacked_levels(pre_snapshot, shock.direction)[: policy.depth_levels_k]
    level_minima = tuple(
        min(
            (level.aggregate_quantity,)
            + tuple(
                _quantity_at_price(snapshot, shock.direction, level.price) for snapshot in depletion
            )
        )
        for level in original_levels
    )

    points = tuple(
        RecoveryPoint(
            horizon_events=index,
            replenishment_ratio=calculate_replenishment_ratio(
                baseline,
                minimum,
                raw_attacked_depth(response.snapshot, shock.direction, policy.depth_levels_k),
            ),
            exchange_time=response.event_reference.exchange_time,
            process_time=response.event_reference.process_time,
        )
        for index, response in enumerate(responses, start=1)
    )
    horizon_results = tuple(
        _resiliency_at_horizon(
            shock,
            baseline,
            minimum,
            responses,
            original_levels,
            level_minima,
            horizon,
            policy,
        )
        for horizon in policy.recovery_horizons_events
    )
    recovery_times = calculate_recovery_times(
        points,
        policy.recovery_thresholds,
        origin_exchange_time=shock.end_exchange_time,
        origin_process_time=shock.end_process_time,
    )
    return ResiliencyVector(
        shock_id=shock.shock_id,
        depth_levels_k=policy.depth_levels_k,
        baseline_depth=baseline,
        minimum_depth=minimum,
        consumed_depth=consumed,
        original_price_levels=tuple(level.price for level in original_levels),
        rr_by_horizon=horizon_results,
        recovery_times=recovery_times,
        observation_end=(None if not responses else responses[-1].event_reference),
        resiliency_version=policy.resiliency_version,
    )


def _resiliency_at_horizon(
    shock: LiquidityShock,
    baseline: Decimal,
    minimum: Decimal,
    responses: tuple[MarketStateObservation, ...],
    original_levels: tuple[PriceLevel, ...],
    level_minima: tuple[Decimal, ...],
    horizon: int,
    config: ResiliencyConfig,
) -> ResiliencyObservation:
    if horizon > len(responses):
        return ResiliencyObservation(
            horizon_events=horizon,
            attacked_depth=None,
            replenished_depth=None,
            replenishment_ratio=None,
            level_recoveries=(),
            near_touch_weighted_sum=None,
            near_touch_available_weight=None,
            near_touch_strength=None,
            deep_support_numerator=None,
            touch_recovery=None,
            deep_support_ratio=None,
            exchange_elapsed_seconds=None,
            process_elapsed_seconds=None,
            unavailable_reason=ResiliencyUnavailableReason.FUTURE_OBSERVATION_UNAVAILABLE,
        )
    response = responses[horizon - 1]
    future_depth = raw_attacked_depth(response.snapshot, shock.direction, config.depth_levels_k)
    ratio = calculate_replenishment_ratio(baseline, minimum, future_depth)
    level_recoveries = tuple(
        calculate_level_recovery(
            level_rank=index,
            original_price=level.price,
            pre_depth=level.aggregate_quantity,
            minimum_depth=level_minima[index - 1],
            future_depth=_quantity_at_price(response.snapshot, shock.direction, level.price),
        )
        for index, level in enumerate(original_levels, start=1)
    )
    weighted_sum, available_weight = _near_touch_components(
        level_recoveries,
        config.multi_level_weights,
    )
    nts = None if available_weight == 0 else weighted_sum / available_weight
    touch_recovery = None if not level_recoveries else level_recoveries[0].replenishment_ratio
    deep_numerator = _deep_support_numerator(level_recoveries, config.multi_level_weights)
    dsr = calculate_deep_support_ratio(
        level_recoveries,
        config.multi_level_weights,
        config.epsilon,
    )
    return ResiliencyObservation(
        horizon_events=horizon,
        attacked_depth=future_depth,
        replenished_depth=future_depth - minimum,
        replenishment_ratio=ratio,
        level_recoveries=level_recoveries,
        near_touch_weighted_sum=(None if available_weight == 0 else weighted_sum),
        near_touch_available_weight=(None if available_weight == 0 else available_weight),
        near_touch_strength=nts,
        deep_support_numerator=deep_numerator,
        touch_recovery=touch_recovery,
        deep_support_ratio=dsr,
        exchange_elapsed_seconds=elapsed_decimal_seconds(
            shock.end_exchange_time,
            response.event_reference.exchange_time,
        ),
        process_elapsed_seconds=elapsed_decimal_seconds(
            shock.end_process_time,
            response.event_reference.process_time,
        ),
        unavailable_reason=(ResiliencyUnavailableReason.NO_DEPLETION if ratio is None else None),
    )


def _near_touch_components(
    level_recoveries: Sequence[LevelRecovery],
    weights: Sequence[Decimal],
) -> tuple[Decimal, Decimal]:
    _validate_level_weights(weights, len(level_recoveries))
    available = tuple(
        (weights[index], recovery.replenishment_ratio)
        for index, recovery in enumerate(level_recoveries)
        if recovery.replenishment_ratio is not None
    )
    return (
        sum((weight * ratio for weight, ratio in available), Decimal(0)),
        sum((weight for weight, _ in available), Decimal(0)),
    )


def _deep_support_numerator(
    level_recoveries: Sequence[LevelRecovery],
    weights: Sequence[Decimal],
) -> Decimal | None:
    available = tuple(
        (weights[index], recovery.replenishment_ratio)
        for index, recovery in enumerate(level_recoveries[1:], start=1)
        if recovery.replenishment_ratio is not None
    )
    if not available:
        return None
    return sum((weight * ratio for weight, ratio in available), Decimal(0))


def _attacked_levels(
    snapshot: BookSnapshot,
    direction: ShockDirection,
) -> tuple[PriceLevel, ...]:
    return snapshot.ask_levels if direction is ShockDirection.BUY else snapshot.bid_levels


def _quantity_at_price(
    snapshot: BookSnapshot,
    direction: ShockDirection,
    price: Decimal,
) -> Decimal:
    return next(
        (
            level.aggregate_quantity
            for level in _attacked_levels(snapshot, direction)
            if level.price == price
        ),
        Decimal(0),
    )


def _validate_level_weights(weights: Sequence[Decimal], level_count: int) -> None:
    if len(weights) < level_count:
        raise ValueError("weights must cover every level recovery")
    if any(weight <= 0 for weight in weights):
        raise ValueError("level-recovery weights must be positive")
    if sum(weights, Decimal(0)) != 1:
        raise ValueError("level-recovery weights must sum exactly to one")


def _validate_response_order(
    shock: LiquidityShock,
    responses: tuple[MarketStateObservation, ...],
) -> None:
    for response in responses:
        if response.event_reference.exchange_time < shock.end_exchange_time:
            raise ValueError("resiliency response exchange_time must not precede shock end")
        if response.event_reference.process_time < shock.end_process_time:
            raise ValueError("resiliency response process_time must not precede shock end")
    for earlier, later in zip(responses, responses[1:], strict=False):
        if earlier.event_reference.exchange_time > later.event_reference.exchange_time:
            raise ValueError("resiliency responses must use nondecreasing exchange time")
        if earlier.event_reference.process_time > later.event_reference.process_time:
            raise ValueError("resiliency responses must use nondecreasing process time")
