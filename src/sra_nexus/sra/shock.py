"""Transparent deterministic liquidity-shock feature primitives."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import InstrumentId, ShockId, new_shock_id
from sra_nexus.market_data.enums import BookAction, BookSide
from sra_nexus.market_data.events import BookEvent
from sra_nexus.market_data.features import WeightedDepthConfig
from sra_nexus.market_data.snapshots import BookSnapshot, PriceLevel
from sra_nexus.sra.enums import (
    AggressionUnavailableReason,
    ShockDetectionMethod,
    ShockDetectionRule,
    ShockDirection,
)
from sra_nexus.sra.state import (
    MarketEventReference,
    SnapshotReference,
    elapsed_decimal_seconds,
    snapshot_reference,
)
from sra_nexus.sra.windows import (
    AggressiveFlowWindow,
    directional_aggressive_volume,
    directional_trade_count,
)

SHOCK_DETECTION_VERSION = "shock-detection-v1"
_SHOCK_ID_NAMESPACE = UUID("4381b0a6-4aa6-5ad5-908f-7c7c4b7767f1")


class ShockDetectionConfig(ContractModel):
    """Central initial engineering values for event-window shock classification."""

    aggression_window_event_count: int = Field(default=20, gt=0)
    weighted_depth_config: WeightedDepthConfig = Field(default_factory=WeightedDepthConfig)
    minimum_normalized_aggression: NonNegativeDecimal | None = Decimal("0.5")
    minimum_aggressive_volume: NonNegativeDecimal | None = Decimal("100")
    minimum_levels_consumed: int | None = 1
    minimum_average_aggressive_trade_size: NonNegativeDecimal | None = None
    detection_version: NonBlankStr = SHOCK_DETECTION_VERSION

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        """Require at least one configured inclusive threshold and valid level count."""
        if self.minimum_levels_consumed is not None and self.minimum_levels_consumed < 0:
            raise ValueError("minimum_levels_consumed must be non-negative")
        if all(
            threshold is None
            for threshold in (
                self.minimum_normalized_aggression,
                self.minimum_aggressive_volume,
                self.minimum_levels_consumed,
                self.minimum_average_aggressive_trade_size,
            )
        ):
            raise ValueError("at least one shock threshold must be configured")
        return self


class NormalizedAggression(ContractModel):
    """Directional aggressive volume divided by pre-window opposing weighted depth."""

    direction: ShockDirection
    aggressive_volume: PositiveDecimal
    weighted_opposite_depth: NonNegativeDecimal
    normalized_aggression: NonNegativeDecimal | None
    unknown_volume: NonNegativeDecimal
    start_snapshot_reference: SnapshotReference
    end_snapshot_reference: SnapshotReference
    unavailable_reason: AggressionUnavailableReason | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        """Represent a zero denominator explicitly instead of fabricating infinity."""
        if self.weighted_opposite_depth == 0:
            if self.normalized_aggression is not None:
                raise ValueError("zero opposite depth cannot have normalized aggression")
            if self.unavailable_reason is not AggressionUnavailableReason.ZERO_OPPOSITE_DEPTH:
                raise ValueError("zero opposite depth requires an unavailable reason")
        else:
            expected = self.aggressive_volume / self.weighted_opposite_depth
            if self.normalized_aggression != expected or self.unavailable_reason is not None:
                raise ValueError("normalized aggression must equal volume / opposite depth")
        return self


class BookExecutionState(ContractModel):
    """One book execution paired with exact reconstructed state before and after it."""

    event: BookEvent
    pre_snapshot: BookSnapshot
    post_snapshot: BookSnapshot

    @model_validator(mode="after")
    def validate_execution_boundary(self) -> Self:
        """Require an atomic, same-stream EXECUTE transition boundary."""
        event = self.event
        if event.action is not BookAction.EXECUTE:
            raise ValueError("book execution state requires action=EXECUTE")
        if event.side is None or event.price is None or event.quantity is None:
            raise ValueError("validated EXECUTE event is missing side, price, or quantity")
        for snapshot in (self.pre_snapshot, self.post_snapshot):
            if snapshot.instrument_id != event.instrument_id or snapshot.venue != event.venue:
                raise ValueError("execution snapshots must share event instrument and venue")
        if self.pre_snapshot.sequence_number >= event.sequence_number:
            raise ValueError("pre_snapshot must precede the execution sequence")
        if self.pre_snapshot.exchange_time > event.exchange_time:
            raise ValueError("pre_snapshot exchange_time must not follow the execution")
        if self.pre_snapshot.process_time > event.process_time:
            raise ValueError("pre_snapshot process_time must not follow the execution")
        if self.post_snapshot.sequence_number != event.sequence_number:
            raise ValueError("post_snapshot must be produced by the execution event")
        if (
            self.post_snapshot.exchange_time != event.exchange_time
            or self.post_snapshot.receive_time != event.receive_time
            or self.post_snapshot.process_time != event.process_time
        ):
            raise ValueError("post_snapshot clocks must equal the execution clocks")
        if _level_quantity(self.pre_snapshot, event.side, event.price) < event.quantity:
            raise ValueError("execution quantity exceeds displayed pre-event level depth")
        return self


class LevelPenetration(ContractModel):
    """Distinct attacked price levels touched and fully exhausted by execution."""

    direction: ShockDirection
    levels_touched: int
    levels_consumed: int
    touched_prices: tuple[PositiveDecimal, ...]
    consumed_prices: tuple[PositiveDecimal, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        """Keep counts equal to unique price identities and consumed as a subset."""
        if self.levels_touched != len(self.touched_prices):
            raise ValueError("levels_touched must equal unique touched prices")
        if self.levels_consumed != len(self.consumed_prices):
            raise ValueError("levels_consumed must equal unique consumed prices")
        if len(set(self.touched_prices)) != len(self.touched_prices):
            raise ValueError("touched prices must be unique")
        if len(set(self.consumed_prices)) != len(self.consumed_prices):
            raise ValueError("consumed prices must be unique")
        if not set(self.consumed_prices).issubset(self.touched_prices):
            raise ValueError("consumed prices must also be touched")
        return self


class ShockFeatures(ContractModel):
    """Raw, non-calibrated features for one directional aggressive episode."""

    instrument_id: InstrumentId
    direction: ShockDirection
    start_reference: MarketEventReference
    end_reference: MarketEventReference
    start_exchange_time: UtcDatetime
    end_exchange_time: UtcDatetime
    start_process_time: UtcDatetime
    end_process_time: UtcDatetime
    aggressive_volume: PositiveDecimal
    unknown_volume: NonNegativeDecimal
    normalized_aggression: NonNegativeDecimal | None
    normalization_unavailable_reason: AggressionUnavailableReason | None = None
    levels_touched: int = Field(ge=0)
    levels_consumed: int = Field(ge=0)
    average_aggressive_trade_size: PositiveDecimal
    clock_aggressive_flow_rate: PositiveDecimal | None
    pre_spread: NonNegativeDecimal | None
    pre_midprice: PositiveDecimal | None
    pre_weighted_opposite_depth: NonNegativeDecimal
    immediate_midprice_change: ExactDecimal | None


class ShockRuleResult(ContractModel):
    """One configured or disabled threshold and its explainable outcome."""

    rule: ShockDetectionRule
    configured: bool
    threshold: NonNegativeDecimal | None
    observed: NonNegativeDecimal | None
    passed: bool | None
    explanation: NonBlankStr

    @model_validator(mode="after")
    def validate_rule_shape(self) -> Self:
        """Keep disabled, unavailable, and evaluated rule states distinguishable."""
        if not self.configured:
            if self.threshold is not None or self.passed is not None:
                raise ValueError("disabled rule cannot have a threshold or pass result")
        elif self.threshold is None or self.passed is None:
            raise ValueError("configured rule requires threshold and pass result")
        return self


class ShockClassification(ContractModel):
    """Deterministic inclusive-threshold decision without statistical probability."""

    is_candidate: bool
    method: ShockDetectionMethod = ShockDetectionMethod.DETERMINISTIC_THRESHOLDS
    detection_version: NonBlankStr
    rule_results: tuple[ShockRuleResult, ...]
    explanation: NonBlankStr


class LiquidityShock(ContractModel):
    """Immutable materialized shock candidate with market and observable clocks."""

    shock_id: ShockId = Field(default_factory=new_shock_id)
    instrument_id: InstrumentId
    direction: ShockDirection
    start_exchange_time: UtcDatetime
    end_exchange_time: UtcDatetime
    start_process_time: UtcDatetime
    end_process_time: UtcDatetime
    start_reference: MarketEventReference
    end_reference: MarketEventReference
    aggressive_volume: PositiveDecimal
    normalized_aggression: PositiveDecimal
    levels_touched: int = Field(ge=0)
    levels_consumed: int = Field(ge=0)
    pre_spread: NonNegativeDecimal | None
    pre_depth: PositiveDecimal
    immediate_price_change: ExactDecimal | None
    detection_method: ShockDetectionMethod
    detection_version: NonBlankStr

    @model_validator(mode="after")
    def validate_durations(self) -> Self:
        """Keep market-time and process-time durations non-negative and separate."""
        if self.start_exchange_time > self.end_exchange_time:
            raise ValueError("shock market-time duration cannot be negative")
        if self.start_process_time > self.end_process_time:
            raise ValueError("shock observable-time duration cannot be negative")
        return self

    @property
    def market_duration_seconds(self) -> Decimal:
        """Return shock duration in exchange-time seconds."""
        return elapsed_decimal_seconds(self.start_exchange_time, self.end_exchange_time)

    @property
    def observable_duration_seconds(self) -> Decimal:
        """Return shock duration in downstream process-time seconds."""
        return elapsed_decimal_seconds(self.start_process_time, self.end_process_time)


def calculate_normalized_aggression(
    direction: ShockDirection,
    window: AggressiveFlowWindow,
    pre_snapshot: BookSnapshot,
    end_snapshot: BookSnapshot,
    weighted_depth_config: WeightedDepthConfig | None = None,
) -> NormalizedAggression | None:
    """Return ``V_buy/WD_A`` or ``V_sell/WD_B`` using the pre-window snapshot.

    No directional result is created when the requested direction has no
    observed volume. A zero weighted-depth denominator returns a typed result
    with ``normalized_aggression=None``.
    """
    _require_snapshot_instrument(window.instrument_id, pre_snapshot, end_snapshot)
    venue = window.start_reference.venue
    if pre_snapshot.venue != venue or end_snapshot.venue != venue:
        raise ValueError("flow window and snapshots must share venue")
    if (
        pre_snapshot.exchange_time > window.start_exchange_time
        or pre_snapshot.process_time > window.start_process_time
    ):
        raise ValueError("pre_snapshot must be observable before the aggression window")
    if (
        end_snapshot.exchange_time > window.end_exchange_time
        or end_snapshot.process_time > window.end_process_time
    ):
        raise ValueError("end_snapshot cannot expose post-window book state")
    if pre_snapshot.sequence_number > end_snapshot.sequence_number:
        raise ValueError("end_snapshot must not precede pre_snapshot")
    aggressive_volume = directional_aggressive_volume(window, direction)
    if aggressive_volume == 0:
        return None
    opposite_depth = (
        pre_snapshot.weighted_ask_depth(weighted_depth_config)
        if direction is ShockDirection.BUY
        else pre_snapshot.weighted_bid_depth(weighted_depth_config)
    )
    normalized = None if opposite_depth == 0 else aggressive_volume / opposite_depth
    return NormalizedAggression(
        direction=direction,
        aggressive_volume=aggressive_volume,
        weighted_opposite_depth=opposite_depth,
        normalized_aggression=normalized,
        unknown_volume=window.unknown_volume,
        start_snapshot_reference=snapshot_reference(pre_snapshot),
        end_snapshot_reference=snapshot_reference(end_snapshot),
        unavailable_reason=(
            AggressionUnavailableReason.ZERO_OPPOSITE_DEPTH if opposite_depth == 0 else None
        ),
    )


def calculate_level_penetration(
    direction: ShockDirection,
    executions: Sequence[BookExecutionState],
) -> LevelPenetration:
    """Count distinct attacked prices executed and fully exhausted during an episode."""
    attacked_side = BookSide.ASK if direction is ShockDirection.BUY else BookSide.BID
    touched: list[Decimal] = []
    consumed: list[Decimal] = []
    for state in executions:
        event = state.event
        if event.side is not attacked_side or event.price is None:
            raise ValueError("book execution side conflicts with shock direction")
        if event.price not in touched:
            touched.append(event.price)
        post_quantity = _level_quantity(state.post_snapshot, attacked_side, event.price)
        if post_quantity == 0 and event.price not in consumed:
            consumed.append(event.price)
    return LevelPenetration(
        direction=direction,
        levels_touched=len(touched),
        levels_consumed=len(consumed),
        touched_prices=tuple(touched),
        consumed_prices=tuple(consumed),
    )


def build_shock_features(
    window: AggressiveFlowWindow,
    normalized: NormalizedAggression,
    penetration: LevelPenetration,
    pre_snapshot: BookSnapshot,
    end_snapshot: BookSnapshot,
) -> ShockFeatures:
    """Combine exact window, penetration, and book-state primitives transparently."""
    _require_snapshot_instrument(window.instrument_id, pre_snapshot, end_snapshot)
    if normalized.direction is not penetration.direction:
        raise ValueError("normalized aggression and penetration directions must match")
    directional_count = directional_trade_count(window, normalized.direction)
    if directional_count <= 0:
        raise ValueError("directional features require at least one directional trade")
    elapsed = elapsed_decimal_seconds(window.start_exchange_time, window.end_exchange_time)
    immediate_change = (
        None
        if pre_snapshot.midprice is None or end_snapshot.midprice is None
        else end_snapshot.midprice - pre_snapshot.midprice
    )
    return ShockFeatures(
        instrument_id=window.instrument_id,
        direction=normalized.direction,
        start_reference=window.start_reference,
        end_reference=window.end_reference,
        start_exchange_time=window.start_exchange_time,
        end_exchange_time=window.end_exchange_time,
        start_process_time=window.start_process_time,
        end_process_time=window.end_process_time,
        aggressive_volume=normalized.aggressive_volume,
        unknown_volume=window.unknown_volume,
        normalized_aggression=normalized.normalized_aggression,
        normalization_unavailable_reason=normalized.unavailable_reason,
        levels_touched=penetration.levels_touched,
        levels_consumed=penetration.levels_consumed,
        average_aggressive_trade_size=(normalized.aggressive_volume / Decimal(directional_count)),
        clock_aggressive_flow_rate=(
            None if elapsed == 0 else normalized.aggressive_volume / elapsed
        ),
        pre_spread=pre_snapshot.spread,
        pre_midprice=pre_snapshot.midprice,
        pre_weighted_opposite_depth=normalized.weighted_opposite_depth,
        immediate_midprice_change=immediate_change,
    )


def classify_shock(
    features: ShockFeatures,
    config: ShockDetectionConfig | None = None,
) -> ShockClassification:
    """Apply configured inclusive ``observed >= threshold`` engineering rules."""
    policy = ShockDetectionConfig() if config is None else config
    rule_results = (
        _evaluate_rule(
            ShockDetectionRule.NORMALIZED_AGGRESSION,
            features.normalized_aggression,
            policy.minimum_normalized_aggression,
        ),
        _evaluate_rule(
            ShockDetectionRule.AGGRESSIVE_VOLUME,
            features.aggressive_volume,
            policy.minimum_aggressive_volume,
        ),
        _evaluate_rule(
            ShockDetectionRule.LEVELS_CONSUMED,
            Decimal(features.levels_consumed),
            (
                None
                if policy.minimum_levels_consumed is None
                else Decimal(policy.minimum_levels_consumed)
            ),
        ),
        _evaluate_rule(
            ShockDetectionRule.AVERAGE_AGGRESSIVE_TRADE_SIZE,
            features.average_aggressive_trade_size,
            policy.minimum_average_aggressive_trade_size,
        ),
    )
    normalized_available = features.normalized_aggression is not None
    configured_results = tuple(result for result in rule_results if result.configured)
    candidate = normalized_available and all(result.passed is True for result in configured_results)
    if not normalized_available:
        explanation = "normalized aggression unavailable; no shock candidate materialized"
    elif candidate:
        explanation = "all configured inclusive engineering thresholds passed"
    else:
        explanation = "one or more configured inclusive engineering thresholds failed"
    return ShockClassification(
        is_candidate=candidate,
        detection_version=policy.detection_version,
        rule_results=rule_results,
        explanation=explanation,
    )


def materialize_liquidity_shock(
    features: ShockFeatures,
    classification: ShockClassification,
) -> LiquidityShock:
    """Create an immutable shock only from a passing deterministic classification."""
    if not classification.is_candidate:
        raise ValueError("cannot materialize a liquidity shock below configured thresholds")
    if features.normalized_aggression is None or features.pre_weighted_opposite_depth == 0:
        raise ValueError("liquidity shock requires available positive normalized aggression")
    return LiquidityShock(
        shock_id=derive_liquidity_shock_id(features, classification.detection_version),
        instrument_id=features.instrument_id,
        direction=features.direction,
        start_exchange_time=features.start_exchange_time,
        end_exchange_time=features.end_exchange_time,
        start_process_time=features.start_process_time,
        end_process_time=features.end_process_time,
        start_reference=features.start_reference,
        end_reference=features.end_reference,
        aggressive_volume=features.aggressive_volume,
        normalized_aggression=features.normalized_aggression,
        levels_touched=features.levels_touched,
        levels_consumed=features.levels_consumed,
        pre_spread=features.pre_spread,
        pre_depth=features.pre_weighted_opposite_depth,
        immediate_price_change=features.immediate_midprice_change,
        detection_method=classification.method,
        detection_version=classification.detection_version,
    )


def derive_liquidity_shock_id(features: ShockFeatures, detection_version: str) -> ShockId:
    """Derive stable shock identity from immutable event boundaries and version."""
    identity = "|".join(
        (
            str(features.instrument_id),
            str(features.start_reference.event_id),
            str(features.end_reference.event_id),
            features.direction.value,
            detection_version,
        )
    )
    return ShockId(uuid5(_SHOCK_ID_NAMESPACE, identity))


def _evaluate_rule(
    rule: ShockDetectionRule,
    observed: Decimal | None,
    threshold: Decimal | None,
) -> ShockRuleResult:
    if threshold is None:
        return ShockRuleResult(
            rule=rule,
            configured=False,
            threshold=None,
            observed=observed,
            passed=None,
            explanation="rule disabled by configuration",
        )
    passed = observed is not None and observed >= threshold
    explanation = (
        "observed value is unavailable"
        if observed is None
        else f"observed {observed} {'>=' if passed else '<'} threshold {threshold}"
    )
    return ShockRuleResult(
        rule=rule,
        configured=True,
        threshold=threshold,
        observed=observed,
        passed=passed,
        explanation=explanation,
    )


def _level_quantity(snapshot: BookSnapshot, side: BookSide, price: Decimal) -> Decimal:
    levels: tuple[PriceLevel, ...] = (
        snapshot.bid_levels if side is BookSide.BID else snapshot.ask_levels
    )
    return next(
        (level.aggregate_quantity for level in levels if level.price == price),
        Decimal(0),
    )


def _require_snapshot_instrument(
    instrument_id: InstrumentId,
    *snapshots: BookSnapshot,
) -> None:
    if any(snapshot.instrument_id != instrument_id for snapshot in snapshots):
        raise ValueError("flow window and snapshots must share instrument_id")
    if len({snapshot.venue for snapshot in snapshots}) > 1:
        raise ValueError("all snapshots must share venue")
