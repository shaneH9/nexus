"""Pure exact-arithmetic supporting features for reconstructed order books."""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel, PositiveDecimal


def _default_depth_weights() -> tuple[Decimal, ...]:
    return (
        Decimal("1"),
        Decimal("0.5"),
        Decimal("0.25"),
        Decimal("0.125"),
        Decimal("0.0625"),
    )


class WeightedDepthConfig(ContractModel):
    """Initial explicit dimensionless weights for near-to-deep book levels."""

    weights: tuple[PositiveDecimal, ...] = Field(
        default_factory=_default_depth_weights,
        min_length=1,
    )

    @model_validator(mode="after")
    def require_strictly_decreasing_weights(self) -> Self:
        """Require nearer levels to have strictly greater configured weight."""
        if any(left <= right for left, right in zip(self.weights, self.weights[1:], strict=False)):
            raise ValueError("weighted-depth weights must be strictly decreasing")
        return self


def calculate_spread(
    best_bid: Decimal | None,
    best_ask: Decimal | None,
) -> Decimal | None:
    """Return ``best_ask - best_bid`` in price units, or ``None`` if one side is absent."""
    if best_bid is None or best_ask is None:
        return None
    if best_bid > best_ask:
        raise ValueError("spread is undefined for a crossed book")
    return best_ask - best_bid


def calculate_midprice(
    best_bid: Decimal | None,
    best_ask: Decimal | None,
) -> Decimal | None:
    """Return ``(best_bid + best_ask) / 2`` in price units when both sides exist."""
    if best_bid is None or best_ask is None:
        return None
    if best_bid > best_ask:
        raise ValueError("midprice is undefined for a crossed book")
    return (best_bid + best_ask) / Decimal(2)


def calculate_microprice(
    bid_price: Decimal | None,
    bid_quantity: Decimal | None,
    ask_price: Decimal | None,
    ask_quantity: Decimal | None,
) -> Decimal | None:
    """Return exact top-level size-weighted microprice in price units.

    Definition: ``(ask_price * bid_quantity + bid_price * ask_quantity) /
    (bid_quantity + ask_quantity)``. Missing sides or zero combined quantity
    return ``None``.
    """
    if any(value is None for value in (bid_price, bid_quantity, ask_price, ask_quantity)):
        return None
    if bid_price is None or bid_quantity is None or ask_price is None or ask_quantity is None:
        return None
    _require_nonnegative_depths((bid_quantity, ask_quantity))
    denominator = bid_quantity + ask_quantity
    if denominator == 0:
        return None
    return (ask_price * bid_quantity + bid_price * ask_quantity) / denominator


def calculate_order_book_imbalance(
    bid_quantities: tuple[Decimal, ...],
    ask_quantities: tuple[Decimal, ...],
    level_count: int,
) -> Decimal:
    """Return exact ``(bid_depth - ask_depth) / (bid_depth + ask_depth)``.

    Quantities use instrument units, the output is dimensionless in ``[-1, 1]``,
    and an empty/zero denominator returns ``Decimal(0)``.
    """
    if level_count <= 0:
        raise ValueError("level_count must be positive")
    _require_nonnegative_depths((*bid_quantities, *ask_quantities))
    bid_depth = sum(bid_quantities[:level_count], Decimal(0))
    ask_depth = sum(ask_quantities[:level_count], Decimal(0))
    denominator = bid_depth + ask_depth
    if denominator == 0:
        return Decimal(0)
    return (bid_depth - ask_depth) / denominator


def calculate_weighted_depth(
    quantities: tuple[Decimal, ...],
    config: WeightedDepthConfig | None = None,
) -> Decimal:
    """Return ``sum(weight_k * quantity_k)`` in weighted quantity units."""
    _require_nonnegative_depths(quantities)
    policy = WeightedDepthConfig() if config is None else config
    return sum(
        (weight * quantity for weight, quantity in zip(policy.weights, quantities, strict=False)),
        Decimal(0),
    )


def is_tick_aligned(price: Decimal, tick_size: Decimal | None) -> bool:
    """Return exact Decimal tick alignment; unknown tick size skips validation."""
    if price <= 0:
        raise ValueError("price must be positive")
    if tick_size is None:
        return True
    if tick_size <= 0:
        raise ValueError("tick_size must be positive when supplied")
    return price % tick_size == 0


def _require_nonnegative_depths(values: tuple[Decimal, ...]) -> None:
    if any(value < 0 for value in values):
        raise ValueError("depth quantities must be non-negative")
