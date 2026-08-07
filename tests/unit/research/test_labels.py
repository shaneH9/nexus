"""Tests for isolated gross forward market-response labels."""

from decimal import Decimal

from tests.support.research import indexed_state

from sra_nexus.research import (
    ForwardLabelConfig,
    ForwardMarketResponseLabel,
    LabelBuilder,
    LabelUnavailableReason,
    UnavailableForwardLabel,
)
from sra_nexus.sra import ShockDirection


def test_forward_return_and_reversal_signs_are_exact() -> None:
    """SELL reversal is up and BUY reversal is down while raw return is retained."""
    states = (indexed_state(0, "100"), indexed_state(1, "100"), indexed_state(2, "101"))
    anchor = states[0].observation.event_reference
    builder = LabelBuilder(ForwardLabelConfig(horizons_events=(2,)))

    sell = builder.build(
        direction=ShockDirection.SELL,
        prediction_anchor_event_index=0,
        prediction_anchor_event_reference=anchor,
        market_states=states,
    )[0]
    buy = builder.build(
        direction=ShockDirection.BUY,
        prediction_anchor_event_index=0,
        prediction_anchor_event_reference=anchor,
        market_states=states,
    )[0]

    assert isinstance(sell, ForwardMarketResponseLabel)
    assert isinstance(buy, ForwardMarketResponseLabel)
    assert sell.forward_return == Decimal("0.01")
    assert buy.forward_return == Decimal("0.01")
    assert sell.reversal_adjusted_return == Decimal("0.01")
    assert buy.reversal_adjusted_return == Decimal("-0.01")
    assert sell.reversal_success is True
    assert buy.reversal_success is False


def test_mfe_and_maximum_adverse_excursion_for_sell_reversal() -> None:
    """Upward reversal labels retain exact favorable and adverse path magnitudes."""
    states = tuple(
        indexed_state(index, price) for index, price in enumerate(("100", "99", "102", "101"))
    )
    label = LabelBuilder(ForwardLabelConfig(horizons_events=(3,))).build(
        direction=ShockDirection.SELL,
        prediction_anchor_event_index=0,
        prediction_anchor_event_reference=states[0].observation.event_reference,
        market_states=states,
    )[0]

    assert isinstance(label, ForwardMarketResponseLabel)
    assert label.maximum_favorable_excursion.magnitude == Decimal("0.02")
    assert label.maximum_adverse_excursion.magnitude == Decimal("0.01")
    assert label.events_to_max_favorable_excursion == 2
    assert label.exchange_seconds_to_max_favorable_excursion == Decimal(2)


def test_mfe_and_maximum_adverse_excursion_for_buy_reversal() -> None:
    """Downward reversal labels invert the same future path exactly once."""
    states = tuple(
        indexed_state(index, price) for index, price in enumerate(("100", "99", "102", "101"))
    )
    label = LabelBuilder(ForwardLabelConfig(horizons_events=(3,))).build(
        direction=ShockDirection.BUY,
        prediction_anchor_event_index=0,
        prediction_anchor_event_reference=states[0].observation.event_reference,
        market_states=states,
    )[0]

    assert isinstance(label, ForwardMarketResponseLabel)
    assert label.maximum_favorable_excursion.magnitude == Decimal("0.01")
    assert label.maximum_adverse_excursion.magnitude == Decimal("0.02")
    assert label.events_to_max_favorable_excursion == 1


def test_incomplete_horizon_is_unavailable_without_truncation() -> None:
    """Missing target state yields no shorter-horizon substitute return."""
    states = (indexed_state(0), indexed_state(1, "101"))
    label = LabelBuilder(ForwardLabelConfig(horizons_events=(2,))).build(
        direction=ShockDirection.SELL,
        prediction_anchor_event_index=0,
        prediction_anchor_event_reference=states[0].observation.event_reference,
        market_states=states,
    )[0]

    assert isinstance(label, UnavailableForwardLabel)
    assert label.unavailable_reason is LabelUnavailableReason.MISSING_FUTURE_EVENT


def test_internal_event_gap_is_explicitly_unavailable() -> None:
    """A present endpoint cannot hide a missing normalized event inside the label path."""
    states = (indexed_state(0), indexed_state(2, "101"))
    label = LabelBuilder(ForwardLabelConfig(horizons_events=(2,))).build(
        direction=ShockDirection.SELL,
        prediction_anchor_event_index=0,
        prediction_anchor_event_reference=states[0].observation.event_reference,
        market_states=states,
    )[0]

    assert isinstance(label, UnavailableForwardLabel)
    assert label.unavailable_reason is LabelUnavailableReason.STRUCTURAL_GAP


def test_zero_return_is_not_reversal_success() -> None:
    """The binary descriptive label uses a strict greater-than-zero rule."""
    states = (indexed_state(0), indexed_state(1), indexed_state(2))
    label = LabelBuilder(ForwardLabelConfig(horizons_events=(2,))).build(
        direction=ShockDirection.SELL,
        prediction_anchor_event_index=0,
        prediction_anchor_event_reference=states[0].observation.event_reference,
        market_states=states,
    )[0]

    assert isinstance(label, ForwardMarketResponseLabel)
    assert label.reversal_adjusted_return == 0
    assert label.reversal_success is False
