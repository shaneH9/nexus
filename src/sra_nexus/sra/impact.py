"""Exact event-horizon price-impact research primitives."""

from __future__ import annotations

from collections.abc import Sequence
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
from sra_nexus.common.types import ShockId
from sra_nexus.sra.enums import ImpactUnavailableReason, ShockDirection
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.state import MarketStateObservation, elapsed_decimal_seconds

IMPACT_VERSION = "impact-v1"


def _default_impact_horizons() -> tuple[int, ...]:
    return (1, 5, 10, 25, 50, 100)


class ImpactConfig(ContractModel):
    """Central event-count horizons and formula version for impact research."""

    horizons_events: tuple[int, ...] = Field(default_factory=_default_impact_horizons)
    impact_version: NonBlankStr = IMPACT_VERSION

    @model_validator(mode="after")
    def validate_horizons(self) -> Self:
        """Require unique positive horizons in increasing order."""
        if not self.horizons_events:
            raise ValueError("at least one impact horizon is required")
        if any(horizon <= 0 for horizon in self.horizons_events):
            raise ValueError("impact horizons must be positive")
        if tuple(sorted(set(self.horizons_events))) != self.horizons_events:
            raise ValueError("impact horizons must be unique and increasing")
        return self


class ShockImpact(ContractModel):
    """Exact price impact and alternative normalizations at one event horizon.

    Raw and directional impacts use instrument price units. Volume-normalized
    fields use price per instrument-quantity unit. Normalized-aggression impact
    uses price units per dimensionless aggression unit.
    """

    shock_id: ShockId
    direction: ShockDirection
    horizon_events: int = Field(gt=0)
    baseline_midprice: PositiveDecimal | None
    future_midprice: PositiveDecimal | None
    raw_price_impact: ExactDecimal | None
    directional_price_impact: ExactDecimal | None
    volume_normalized_impact: NonNegativeDecimal | None
    directional_volume_normalized_impact: ExactDecimal | None
    normalized_aggression_impact: NonNegativeDecimal | None
    exchange_elapsed_seconds: NonNegativeDecimal | None
    process_elapsed_seconds: NonNegativeDecimal | None
    available: bool
    unavailable_reason: ImpactUnavailableReason | None = None
    impact_version: NonBlankStr

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        """Keep unavailable horizons distinct from genuine zero impact."""
        impact_values = (
            self.raw_price_impact,
            self.directional_price_impact,
            self.volume_normalized_impact,
            self.directional_volume_normalized_impact,
            self.normalized_aggression_impact,
        )
        if self.available:
            if self.baseline_midprice is None or self.future_midprice is None:
                raise ValueError("available impact requires baseline and future midprices")
            if any(value is None for value in impact_values):
                raise ValueError("available impact requires every impact representation")
            if self.unavailable_reason is not None:
                raise ValueError("available impact cannot have an unavailable reason")
        elif self.unavailable_reason is None or any(value is not None for value in impact_values):
            raise ValueError("unavailable impact requires a reason and no fabricated values")
        return self


def calculate_price_impact(baseline_midprice: Decimal, future_midprice: Decimal) -> Decimal:
    """Return exact ``PI_k(h) = M(t_k+h) - M(t_k^-)`` in price units."""
    if baseline_midprice <= 0 or future_midprice <= 0:
        raise ValueError("impact midprices must be positive")
    return future_midprice - baseline_midprice


def calculate_directional_price_impact(
    direction: ShockDirection,
    raw_price_impact: Decimal,
) -> Decimal:
    """Return ``DI=+PI`` for BUY and ``DI=-PI`` for SELL shocks."""
    sign = Decimal(1) if direction is ShockDirection.BUY else Decimal(-1)
    return sign * raw_price_impact


def calculate_shock_impacts(
    shock: LiquidityShock,
    baseline_midprice: Decimal | None,
    response_observations: Sequence[MarketStateObservation],
    config: ImpactConfig | None = None,
) -> tuple[ShockImpact, ...]:
    """Calculate configured event-indexed impacts without filling missing futures."""
    policy = ImpactConfig() if config is None else config
    responses = tuple(response_observations)
    if any(item.snapshot.instrument_id != shock.instrument_id for item in responses):
        raise ValueError("shock and response observations must share instrument_id")
    _validate_response_order(shock, responses)
    return tuple(
        _impact_at_horizon(shock, baseline_midprice, responses, horizon, policy)
        for horizon in policy.horizons_events
    )


def _impact_at_horizon(
    shock: LiquidityShock,
    baseline_midprice: Decimal | None,
    responses: tuple[MarketStateObservation, ...],
    horizon: int,
    config: ImpactConfig,
) -> ShockImpact:
    if baseline_midprice is None:
        return _unavailable_impact(
            shock,
            horizon,
            baseline_midprice,
            None,
            ImpactUnavailableReason.BASELINE_MIDPRICE_UNAVAILABLE,
            config,
        )
    if horizon > len(responses):
        return _unavailable_impact(
            shock,
            horizon,
            baseline_midprice,
            None,
            ImpactUnavailableReason.FUTURE_OBSERVATION_UNAVAILABLE,
            config,
        )
    response = responses[horizon - 1]
    future_midprice = response.snapshot.midprice
    exchange_elapsed = elapsed_decimal_seconds(
        shock.end_exchange_time,
        response.event_reference.exchange_time,
    )
    process_elapsed = elapsed_decimal_seconds(
        shock.end_process_time,
        response.event_reference.process_time,
    )
    if future_midprice is None:
        return ShockImpact(
            shock_id=shock.shock_id,
            direction=shock.direction,
            horizon_events=horizon,
            baseline_midprice=baseline_midprice,
            future_midprice=None,
            raw_price_impact=None,
            directional_price_impact=None,
            volume_normalized_impact=None,
            directional_volume_normalized_impact=None,
            normalized_aggression_impact=None,
            exchange_elapsed_seconds=exchange_elapsed,
            process_elapsed_seconds=process_elapsed,
            available=False,
            unavailable_reason=ImpactUnavailableReason.FUTURE_MIDPRICE_UNAVAILABLE,
            impact_version=config.impact_version,
        )
    raw = calculate_price_impact(baseline_midprice, future_midprice)
    directional = calculate_directional_price_impact(shock.direction, raw)
    return ShockImpact(
        shock_id=shock.shock_id,
        direction=shock.direction,
        horizon_events=horizon,
        baseline_midprice=baseline_midprice,
        future_midprice=future_midprice,
        raw_price_impact=raw,
        directional_price_impact=directional,
        volume_normalized_impact=abs(raw) / shock.aggressive_volume,
        directional_volume_normalized_impact=directional / shock.aggressive_volume,
        normalized_aggression_impact=abs(raw) / shock.normalized_aggression,
        exchange_elapsed_seconds=exchange_elapsed,
        process_elapsed_seconds=process_elapsed,
        available=True,
        impact_version=config.impact_version,
    )


def _unavailable_impact(
    shock: LiquidityShock,
    horizon: int,
    baseline_midprice: Decimal | None,
    future_midprice: Decimal | None,
    reason: ImpactUnavailableReason,
    config: ImpactConfig,
) -> ShockImpact:
    return ShockImpact(
        shock_id=shock.shock_id,
        direction=shock.direction,
        horizon_events=horizon,
        baseline_midprice=baseline_midprice,
        future_midprice=future_midprice,
        raw_price_impact=None,
        directional_price_impact=None,
        volume_normalized_impact=None,
        directional_volume_normalized_impact=None,
        normalized_aggression_impact=None,
        exchange_elapsed_seconds=None,
        process_elapsed_seconds=None,
        available=False,
        unavailable_reason=reason,
        impact_version=config.impact_version,
    )


def _validate_response_order(
    shock: LiquidityShock,
    responses: tuple[MarketStateObservation, ...],
) -> None:
    for response in responses:
        if response.event_reference.exchange_time < shock.end_exchange_time:
            raise ValueError("impact response exchange_time must not precede shock end")
        if response.event_reference.process_time < shock.end_process_time:
            raise ValueError("impact response process_time must not precede shock end")
    for earlier, later in zip(responses, responses[1:], strict=False):
        if earlier.event_reference.exchange_time > later.event_reference.exchange_time:
            raise ValueError("impact responses must use nondecreasing exchange time")
        if earlier.event_reference.process_time > later.event_reference.process_time:
            raise ValueError("impact responses must use nondecreasing process time")
