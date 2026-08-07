"""Future-only gross market-response labels, isolated from feature construction."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
)
from sra_nexus.research.enums import LabelUnavailableReason
from sra_nexus.research.models import LABEL_VERSION
from sra_nexus.sra.enums import ShockDirection
from sra_nexus.sra.state import MarketEventReference, elapsed_decimal_seconds
from sra_nexus.sra.toxicity import IndexedMarketStateObservation

DEFAULT_FORWARD_HORIZONS_EVENTS = (10, 25, 50, 100, 250)


class ForwardLabelConfig(ContractModel):
    """Explicit normalized-event horizons for gross forward labels."""

    horizons_events: tuple[int, ...] = DEFAULT_FORWARD_HORIZONS_EVENTS
    label_version: NonBlankStr = LABEL_VERSION

    @model_validator(mode="after")
    def validate_horizons(self) -> Self:
        """Require positive unique sorted horizons for deterministic labels."""
        if self.horizons_events != tuple(sorted(set(self.horizons_events))) or any(
            horizon <= 0 for horizon in self.horizons_events
        ):
            raise ValueError("forward horizons must be positive, unique, and sorted")
        if not self.horizons_events:
            raise ValueError("at least one forward horizon is required")
        return self


class MaximumFavorableExcursion(ContractModel):
    """Nonnegative best reversal-adjusted return reached within a label horizon."""

    magnitude: NonNegativeDecimal


class MaximumAdverseExcursion(ContractModel):
    """Nonnegative magnitude against the reversal direction, not mean absolute error."""

    magnitude: NonNegativeDecimal


class ForwardMarketResponseLabel(ContractModel):
    """Complete gross future market response from one explicit prediction anchor."""

    horizon_events: int = Field(gt=0)
    direction: ShockDirection
    reversal_direction_multiplier: Literal[-1, 1]
    prediction_anchor_event_index: int = Field(ge=0)
    prediction_anchor_event_reference: MarketEventReference
    label_end_event_index: int = Field(ge=0)
    label_end_event_reference: MarketEventReference
    anchor_midprice: PositiveDecimal
    future_midprice: PositiveDecimal
    forward_return: ExactDecimal
    reversal_adjusted_return: ExactDecimal
    maximum_favorable_excursion: MaximumFavorableExcursion
    maximum_adverse_excursion: MaximumAdverseExcursion
    events_to_max_favorable_excursion: int = Field(ge=0)
    exchange_seconds_to_max_favorable_excursion: NonNegativeDecimal
    reversal_success: bool
    available: Literal[True] = True
    gross_market_response: Literal[True] = True
    label_version: NonBlankStr = LABEL_VERSION

    @model_validator(mode="after")
    def validate_label(self) -> Self:
        """Require exact return signs, event span, and descriptive success state."""
        expected_multiplier = -1 if self.direction is ShockDirection.BUY else 1
        if self.reversal_direction_multiplier != expected_multiplier:
            raise ValueError("reversal multiplier must equal negative shock direction")
        if self.label_end_event_index != self.prediction_anchor_event_index + self.horizon_events:
            raise ValueError("label end index must equal prediction anchor plus horizon")
        expected_return = (self.future_midprice - self.anchor_midprice) / self.anchor_midprice
        if self.forward_return != expected_return:
            raise ValueError("forward return does not match anchor and future midprices")
        if self.reversal_adjusted_return != Decimal(expected_multiplier) * expected_return:
            raise ValueError("reversal-adjusted return has the wrong direction")
        if self.reversal_success != (self.reversal_adjusted_return > 0):
            raise ValueError("zero return is not reversal success")
        if self.events_to_max_favorable_excursion > self.horizon_events:
            raise ValueError("time to favorable excursion cannot exceed label horizon")
        if (
            self.prediction_anchor_event_reference.instrument_id
            != self.label_end_event_reference.instrument_id
            or self.prediction_anchor_event_reference.venue != self.label_end_event_reference.venue
        ):
            raise ValueError("label boundaries must share instrument and venue")
        return self


class UnavailableForwardLabel(ContractModel):
    """Explicitly incomplete event-horizon label with no truncated return."""

    horizon_events: int = Field(gt=0)
    direction: ShockDirection
    prediction_anchor_event_index: int = Field(ge=0)
    prediction_anchor_event_reference: MarketEventReference
    unavailable_reason: LabelUnavailableReason
    available: Literal[False] = False
    label_version: NonBlankStr = LABEL_VERSION


type ForwardLabel = ForwardMarketResponseLabel | UnavailableForwardLabel


class LabelBuilder:
    """Generate gross future labels from states unavailable to feature code."""

    def __init__(self, config: ForwardLabelConfig | None = None) -> None:
        """Configure exact event horizons without hard-coding them in label models."""
        self._config = ForwardLabelConfig() if config is None else config

    @property
    def config(self) -> ForwardLabelConfig:
        """Return the immutable label policy."""
        return self._config

    def build(
        self,
        *,
        direction: ShockDirection,
        prediction_anchor_event_index: int,
        prediction_anchor_event_reference: MarketEventReference,
        market_states: Sequence[IndexedMarketStateObservation],
    ) -> tuple[ForwardLabel, ...]:
        """Build each full event-horizon label or an explicit unavailable value."""
        state_index = _state_index(market_states, prediction_anchor_event_reference)
        anchor = state_index.get(prediction_anchor_event_index)
        if (
            anchor is None
            or anchor.observation.event_reference != prediction_anchor_event_reference
        ):
            raise ValueError("prediction anchor must be present exactly in market states")
        return tuple(
            _build_horizon_label(
                direction,
                prediction_anchor_event_index,
                prediction_anchor_event_reference,
                state_index,
                horizon,
                self._config.label_version,
            )
            for horizon in self._config.horizons_events
        )


def _build_horizon_label(
    direction: ShockDirection,
    anchor_index: int,
    anchor_reference: MarketEventReference,
    state_index: dict[int, IndexedMarketStateObservation],
    horizon: int,
    label_version: str,
) -> ForwardLabel:
    end_index = anchor_index + horizon
    end = state_index.get(end_index)
    if end is None:
        return _unavailable(
            direction,
            anchor_index,
            anchor_reference,
            horizon,
            LabelUnavailableReason.MISSING_FUTURE_EVENT,
            label_version,
        )
    path = tuple(state_index.get(index) for index in range(anchor_index, end_index + 1))
    if any(item is None for item in path):
        return _unavailable(
            direction,
            anchor_index,
            anchor_reference,
            horizon,
            LabelUnavailableReason.STRUCTURAL_GAP,
            label_version,
        )
    complete_path = tuple(_required(item) for item in path)
    midprices = tuple(item.observation.snapshot.midprice for item in complete_path)
    if any(midprice is None for midprice in midprices):
        return _unavailable(
            direction,
            anchor_index,
            anchor_reference,
            horizon,
            LabelUnavailableReason.MISSING_MIDPRICE,
            label_version,
        )
    prices = tuple(_required(midprice) for midprice in midprices)
    anchor_midprice = prices[0]
    forward_return = (prices[-1] - anchor_midprice) / anchor_midprice
    multiplier_value: Literal[-1, 1] = -1 if direction is ShockDirection.BUY else 1
    multiplier = Decimal(multiplier_value)
    adjusted_path = tuple(
        multiplier * (price - anchor_midprice) / anchor_midprice for price in prices
    )
    maximum_favorable = max(adjusted_path)
    maximum_adverse = max((-value for value in adjusted_path), default=Decimal(0))
    maximum_adverse = max(maximum_adverse, Decimal(0))
    events_to_favorable = adjusted_path.index(maximum_favorable)
    favorable_reference = complete_path[events_to_favorable].observation.event_reference
    return ForwardMarketResponseLabel(
        horizon_events=horizon,
        direction=direction,
        reversal_direction_multiplier=multiplier_value,
        prediction_anchor_event_index=anchor_index,
        prediction_anchor_event_reference=anchor_reference,
        label_end_event_index=end_index,
        label_end_event_reference=end.observation.event_reference,
        anchor_midprice=anchor_midprice,
        future_midprice=prices[-1],
        forward_return=forward_return,
        reversal_adjusted_return=multiplier * forward_return,
        maximum_favorable_excursion=MaximumFavorableExcursion(magnitude=maximum_favorable),
        maximum_adverse_excursion=MaximumAdverseExcursion(magnitude=maximum_adverse),
        events_to_max_favorable_excursion=events_to_favorable,
        exchange_seconds_to_max_favorable_excursion=elapsed_decimal_seconds(
            anchor_reference.exchange_time,
            favorable_reference.exchange_time,
        ),
        reversal_success=multiplier * forward_return > 0,
        label_version=label_version,
    )


def _unavailable(
    direction: ShockDirection,
    anchor_index: int,
    anchor_reference: MarketEventReference,
    horizon: int,
    reason: LabelUnavailableReason,
    label_version: str,
) -> UnavailableForwardLabel:
    return UnavailableForwardLabel(
        horizon_events=horizon,
        direction=direction,
        prediction_anchor_event_index=anchor_index,
        prediction_anchor_event_reference=anchor_reference,
        unavailable_reason=reason,
        label_version=label_version,
    )


def _state_index(
    states: Sequence[IndexedMarketStateObservation],
    anchor_reference: MarketEventReference,
) -> dict[int, IndexedMarketStateObservation]:
    index: dict[int, IndexedMarketStateObservation] = {}
    for item in states:
        reference = item.observation.event_reference
        if (
            reference.instrument_id != anchor_reference.instrument_id
            or reference.venue != anchor_reference.venue
        ):
            continue
        if item.event_index is None:
            raise ValueError("label states require true normalized-event indices")
        if item.event_index in index:
            raise ValueError("label market-state event indices must be unique")
        index[item.event_index] = item
    return index


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("required forward-label value is unavailable")
    return value
