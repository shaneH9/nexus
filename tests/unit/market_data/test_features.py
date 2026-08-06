"""Exact mathematical tests for basic supporting order-book features."""

from decimal import Decimal

import pytest

from sra_nexus.market_data import (
    WeightedDepthConfig,
    calculate_microprice,
    calculate_midprice,
    calculate_order_book_imbalance,
    calculate_spread,
    calculate_weighted_depth,
    is_tick_aligned,
)


def test_spread_and_midprice_use_exact_price_units() -> None:
    """The basic-book values should preserve Decimal arithmetic exactly."""
    bid = Decimal("100.00")
    ask = Decimal("100.01")

    assert calculate_spread(bid, ask) == Decimal("0.01")
    assert calculate_midprice(bid, ask) == Decimal("100.005")
    assert calculate_spread(None, ask) is None
    assert calculate_midprice(bid, None) is None


def test_microprice_matches_exact_documented_equation() -> None:
    """Top-level quantities must weight the opposite-side price exactly."""
    result = calculate_microprice(
        Decimal("100.00"),
        Decimal("200"),
        Decimal("100.01"),
        Decimal("100"),
    )
    expected = (Decimal("100.01") * Decimal("200") + Decimal("100.00") * Decimal("100")) / Decimal(
        "300"
    )

    assert result == expected


def test_microprice_returns_none_for_missing_side_or_zero_denominator() -> None:
    """No top-level value may be fabricated from absent or empty depth."""
    assert calculate_microprice(None, None, Decimal("100.01"), Decimal("10")) is None
    assert (
        calculate_microprice(
            Decimal("100.00"),
            Decimal(0),
            Decimal("100.01"),
            Decimal(0),
        )
        is None
    )


@pytest.mark.parametrize(
    ("bids", "asks", "expected"),
    [
        (("100",), ("100",), "0"),
        (("300",), ("100",), "0.5"),
        (("100",), ("300",), "-0.5"),
        ((), (), "0"),
        (("100", "50"), ("50", "25"), str(Decimal("75") / Decimal("225"))),
    ],
)
def test_order_book_imbalance_exact_cases(
    bids: tuple[str, ...],
    asks: tuple[str, ...],
    expected: str,
) -> None:
    """Balanced, heavy-sided, multi-level, and empty OBI cases are deterministic."""
    result = calculate_order_book_imbalance(
        tuple(Decimal(value) for value in bids),
        tuple(Decimal(value) for value in asks),
        2,
    )

    assert result == Decimal(expected)


def test_weighted_depth_uses_explicit_decreasing_decimal_weights() -> None:
    """Default weighted depth retains raw quantity units without float conversion."""
    quantities = (Decimal("100"), Decimal("200"), Decimal("400"))

    assert calculate_weighted_depth(quantities) == Decimal("300")
    custom = WeightedDepthConfig(weights=(Decimal("1"), Decimal("0.25")))
    assert calculate_weighted_depth(quantities, custom) == Decimal("150")


def test_weighted_depth_rejects_non_decreasing_weights() -> None:
    """Near-level priority must remain explicit in configuration."""
    with pytest.raises(ValueError, match="strictly decreasing"):
        WeightedDepthConfig(weights=(Decimal("1"), Decimal("1")))


def test_tick_alignment_uses_exact_decimal_modulus() -> None:
    """Known ticks validate exactly while unknown tick size is an explicit skip."""
    assert is_tick_aligned(Decimal("100.01"), Decimal("0.01"))
    assert not is_tick_aligned(Decimal("100.005"), Decimal("0.01"))
    assert is_tick_aligned(Decimal("100.005"), None)
