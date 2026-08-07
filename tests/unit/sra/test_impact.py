"""Tests for raw, directional, and normalized event-horizon price impact."""

from decimal import Decimal

from tests.support.sra import liquidity_shock, response_observation, snapshot

from sra_nexus.sra import (
    ImpactConfig,
    ImpactUnavailableReason,
    ShockDirection,
    calculate_directional_price_impact,
    calculate_price_impact,
    calculate_shock_impacts,
)


def test_buy_shock_impact_preserves_all_exact_representations() -> None:
    """BUY impact should be positive in the aggressor direction with exact normalizations."""
    shock = liquidity_shock(
        direction=ShockDirection.BUY,
        aggressive_volume="100",
        normalized_aggression="0.5",
    )
    future = snapshot(1, bids=(("100.99", "100"),), asks=(("101.01", "100"),))
    response = response_observation(1, future)

    result = calculate_shock_impacts(
        shock,
        Decimal("100"),
        (response,),
        ImpactConfig(horizons_events=(1,)),
    )[0]

    assert result.raw_price_impact == Decimal("1")
    assert result.directional_price_impact == Decimal("1")
    assert result.volume_normalized_impact == Decimal("0.01")
    assert result.directional_volume_normalized_impact == Decimal("0.01")
    assert result.normalized_aggression_impact == Decimal("2")
    assert result.available


def test_sell_shock_directional_sign_and_reversal_are_exact() -> None:
    """A rising midprice after SELL aggression should produce negative DI."""
    raw = calculate_price_impact(Decimal("100"), Decimal("101"))

    directional = calculate_directional_price_impact(ShockDirection.SELL, raw)

    assert raw == Decimal("1")
    assert directional == Decimal("-1")


def test_sell_price_decline_is_positive_directional_impact() -> None:
    """A falling midprice after SELL aggression should be positive aggressor-direction impact."""
    shock = liquidity_shock(direction=ShockDirection.SELL)
    future = snapshot(1, bids=(("98.99", "100"),), asks=(("99.01", "100"),))

    result = calculate_shock_impacts(
        shock,
        Decimal("100"),
        (response_observation(1, future),),
        ImpactConfig(horizons_events=(1,)),
    )[0]

    assert result.raw_price_impact == Decimal("-1")
    assert result.directional_price_impact == Decimal("1")


def test_unavailable_future_horizon_is_not_fabricated_as_zero() -> None:
    """A missing h=2 market state should retain an explicit unavailable result."""
    shock = liquidity_shock()
    future = snapshot(1)

    results = calculate_shock_impacts(
        shock,
        Decimal("100.005"),
        (response_observation(1, future),),
        ImpactConfig(horizons_events=(1, 2)),
    )

    assert results[0].available
    assert results[0].raw_price_impact == 0
    assert not results[1].available
    assert results[1].raw_price_impact is None
    assert results[1].unavailable_reason is ImpactUnavailableReason.FUTURE_OBSERVATION_UNAVAILABLE


def test_missing_baseline_midprice_is_explicitly_unavailable() -> None:
    """One-sided pre-shock state must not invent a price-impact baseline."""
    result = calculate_shock_impacts(
        liquidity_shock(),
        None,
        (),
        ImpactConfig(horizons_events=(1,)),
    )[0]

    assert not result.available
    assert result.unavailable_reason is ImpactUnavailableReason.BASELINE_MIDPRICE_UNAVAILABLE
