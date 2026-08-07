"""Tests for normalized aggression, penetration, features, and classification."""

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.support.market_data import book_event, trade_event
from tests.support.sra import snapshot

from sra_nexus.market_data import AggressorSide, BookAction, BookSide
from sra_nexus.sra import (
    AggressionUnavailableReason,
    AggressiveTradeObservation,
    BookExecutionState,
    ShockDetectionConfig,
    ShockDetectionRule,
    ShockDirection,
    build_aggressive_flow_window,
    build_shock_features,
    calculate_level_penetration,
    calculate_normalized_aggression,
    classify_shock,
    materialize_liquidity_shock,
    reconcile_aggressive_trade_observations,
)


def _observed_trade(
    sequence: int,
    quantity: str,
    side: AggressorSide,
) -> AggressiveTradeObservation:
    return reconcile_aggressive_trade_observations(
        None,
        trade_event(sequence, quantity=quantity, aggressor_side=side),
    ).observations[0]


def test_normalized_sell_aggression_uses_pre_shock_weighted_bid_depth() -> None:
    """Sell volume 150 over weighted bid depth 300 should equal exactly 0.5."""
    pre = snapshot(0, bids=(("100.00", "300"),))
    end = snapshot(1, bids=(("100.00", "150"),))
    window = build_aggressive_flow_window((_observed_trade(1, "150", AggressorSide.SELL),))

    result = calculate_normalized_aggression(ShockDirection.SELL, window, pre, end)

    assert result is not None
    assert result.aggressive_volume == Decimal("150")
    assert result.weighted_opposite_depth == Decimal("300")
    assert result.normalized_aggression == Decimal("0.5")


def test_normalized_buy_aggression_is_symmetric_on_ask_depth() -> None:
    """BUY normalization should use pre-window weighted ask rather than bid depth."""
    pre = snapshot(0, bids=(("100.00", "900"),), asks=(("100.01", "300"),))
    end = snapshot(1, bids=(("100.00", "900"),), asks=(("100.01", "150"),))
    window = build_aggressive_flow_window((_observed_trade(1, "150", AggressorSide.BUY),))

    result = calculate_normalized_aggression(ShockDirection.BUY, window, pre, end)

    assert result is not None
    assert result.weighted_opposite_depth == Decimal("300")
    assert result.normalized_aggression == Decimal("0.5")


def test_zero_opposing_depth_makes_normalization_explicitly_unavailable() -> None:
    """Zero opposing depth should return no quotient rather than infinity."""
    pre = snapshot(0, bids=(), asks=(("100.01", "100"),))
    window = build_aggressive_flow_window((_observed_trade(1, "10", AggressorSide.SELL),))

    result = calculate_normalized_aggression(ShockDirection.SELL, window, pre, pre)

    assert result is not None
    assert result.normalized_aggression is None
    assert result.unavailable_reason is AggressionUnavailableReason.ZERO_OPPOSITE_DEPTH


def test_unknown_only_window_creates_no_directional_normalized_aggression() -> None:
    """UNKNOWN volume must not create false BUY or SELL directional aggression."""
    state = snapshot(0)
    window = build_aggressive_flow_window((_observed_trade(1, "10", AggressorSide.UNKNOWN),))

    assert calculate_normalized_aggression(ShockDirection.BUY, window, state, state) is None
    assert calculate_normalized_aggression(ShockDirection.SELL, window, state, state) is None


def test_level_penetration_distinguishes_touched_from_fully_consumed() -> None:
    """Full best-ask and partial second-ask execution should be 2 touched, 1 consumed."""
    first_event = book_event(
        1,
        BookAction.EXECUTE,
        side=BookSide.ASK,
        price="100.01",
        quantity="200",
        order_id="ask-1",
    )
    second_event = book_event(
        2,
        BookAction.EXECUTE,
        side=BookSide.ASK,
        price="100.02",
        quantity="100",
        order_id="ask-2",
    )
    pre_first = snapshot(
        0,
        asks=(("100.01", "200"), ("100.02", "300")),
        exchange_time=first_event.exchange_time - timedelta(milliseconds=1),
        receive_time=first_event.exchange_time - timedelta(microseconds=999),
        process_time=first_event.exchange_time - timedelta(microseconds=998),
    )
    post_first = snapshot(
        1,
        asks=(("100.02", "300"),),
        exchange_time=first_event.exchange_time,
        receive_time=first_event.receive_time,
        process_time=first_event.process_time,
    )
    post_second = snapshot(
        2,
        asks=(("100.02", "200"),),
        exchange_time=second_event.exchange_time,
        receive_time=second_event.receive_time,
        process_time=second_event.process_time,
    )
    first = BookExecutionState(
        event=first_event,
        pre_snapshot=pre_first,
        post_snapshot=post_first,
    )
    second = BookExecutionState(
        event=second_event,
        pre_snapshot=post_first,
        post_snapshot=post_second,
    )

    penetration = calculate_level_penetration(ShockDirection.BUY, (first, second))

    assert penetration.levels_touched == 2
    assert penetration.levels_consumed == 1
    assert penetration.touched_prices == (Decimal("100.01"), Decimal("100.02"))
    assert penetration.consumed_prices == (Decimal("100.01"),)


def test_event_and_exchange_clock_velocity_remain_separate() -> None:
    """Directional event count and irregular exchange duration should yield distinct units."""
    pre = snapshot(0, bids=(("100.00", "300"),))
    end = snapshot(3, bids=(("100.00", "150"),))
    window = build_aggressive_flow_window(
        (
            _observed_trade(1, "50", AggressorSide.SELL),
            _observed_trade(3, "100", AggressorSide.SELL),
        )
    )
    normalized = calculate_normalized_aggression(ShockDirection.SELL, window, pre, end)
    assert normalized is not None
    penetration = calculate_level_penetration(ShockDirection.SELL, ())

    features = build_shock_features(window, normalized, penetration, pre, end)

    assert features.event_velocity == Decimal("75")
    assert features.clock_velocity == Decimal("75000")


@pytest.mark.parametrize(
    ("normalized_value", "expected"),
    (("0.49", False), ("0.50", True), ("0.51", True)),
)
def test_shock_threshold_is_inclusive(
    normalized_value: str,
    expected: bool,
) -> None:
    """Below, exactly-at, and above cases should document an inclusive >= policy."""
    pre = snapshot(0, bids=(("100.00", "100"),))
    end = snapshot(1, bids=(("100.00", "50"),))
    window = build_aggressive_flow_window(
        (_observed_trade(1, normalized_value, AggressorSide.SELL),)
    )
    normalized = calculate_normalized_aggression(ShockDirection.SELL, window, pre, end)
    assert normalized is not None
    features = build_shock_features(
        window,
        normalized,
        calculate_level_penetration(ShockDirection.SELL, ()),
        pre,
        end,
    )
    config = ShockDetectionConfig(
        minimum_normalized_aggression=Decimal("0.005"),
        minimum_aggressive_volume=Decimal("0.50"),
        minimum_levels_consumed=None,
    )

    classification = classify_shock(features, config)

    assert classification.is_candidate is expected
    volume_rule = next(
        item
        for item in classification.rule_results
        if item.rule is ShockDetectionRule.AGGRESSIVE_VOLUME
    )
    assert volume_rule.passed is expected


def test_passing_features_materialize_versioned_immutable_shock() -> None:
    """A passing decision should retain both market and observable durations."""
    pre = snapshot(0, bids=(("100.00", "100"),))
    end = snapshot(2, bids=(("100.00", "50"),))
    window = build_aggressive_flow_window(
        (
            _observed_trade(1, "25", AggressorSide.SELL),
            _observed_trade(2, "25", AggressorSide.SELL),
        )
    )
    normalized = calculate_normalized_aggression(ShockDirection.SELL, window, pre, end)
    assert normalized is not None
    features = build_shock_features(
        window,
        normalized,
        calculate_level_penetration(ShockDirection.SELL, ()),
        pre,
        end,
    )
    classification = classify_shock(
        features,
        ShockDetectionConfig(
            minimum_normalized_aggression=Decimal("0.5"),
            minimum_aggressive_volume=Decimal("50"),
            minimum_levels_consumed=None,
        ),
    )

    shock = materialize_liquidity_shock(features, classification)

    assert shock.normalized_aggression == Decimal("0.5")
    assert shock.market_duration_seconds == Decimal("0.001")
    assert shock.observable_duration_seconds == Decimal("0.001")
    assert shock.detection_version == "shock-detection-v1"
