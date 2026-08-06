"""Immutable reconstructed price-level and book-snapshot contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import InstrumentId
from sra_nexus.market_data.features import (
    WeightedDepthConfig,
    calculate_microprice,
    calculate_midprice,
    calculate_order_book_imbalance,
    calculate_spread,
    calculate_weighted_depth,
)


class PriceLevel(ContractModel):
    """One immutable aggregate price level in price and instrument-quantity units."""

    price: PositiveDecimal
    aggregate_quantity: PositiveDecimal
    order_count: int | None = Field(default=None, ge=1)


class BookSnapshot(ContractModel):
    """Immutable on-demand state after one successfully applied book event."""

    instrument_id: InstrumentId
    venue: NonBlankStr
    exchange_time: UtcDatetime
    receive_time: UtcDatetime
    process_time: UtcDatetime
    sequence_number: int = Field(ge=0)
    bid_levels: tuple[PriceLevel, ...] = ()
    ask_levels: tuple[PriceLevel, ...] = ()
    best_bid: PositiveDecimal | None = None
    best_ask: PositiveDecimal | None = None
    spread: NonNegativeDecimal | None = None
    midprice: PositiveDecimal | None = None
    microprice: PositiveDecimal | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """Require causal clocks, ordered levels, and exact derived fields."""
        if self.exchange_time > self.receive_time:
            raise ValueError("exchange_time must not be after receive_time")
        if self.receive_time > self.process_time:
            raise ValueError("receive_time must not be after process_time")
        bid_prices = tuple(level.price for level in self.bid_levels)
        ask_prices = tuple(level.price for level in self.ask_levels)
        if bid_prices != tuple(sorted(bid_prices, reverse=True)) or len(set(bid_prices)) != len(
            bid_prices
        ):
            raise ValueError("bid levels must be unique and ordered highest to lowest")
        if ask_prices != tuple(sorted(ask_prices)) or len(set(ask_prices)) != len(ask_prices):
            raise ValueError("ask levels must be unique and ordered lowest to highest")
        expected_bid = None if not self.bid_levels else self.bid_levels[0].price
        expected_ask = None if not self.ask_levels else self.ask_levels[0].price
        if self.best_bid != expected_bid or self.best_ask != expected_ask:
            raise ValueError("best prices must equal the first ordered levels")
        expected_spread = calculate_spread(expected_bid, expected_ask)
        expected_midprice = calculate_midprice(expected_bid, expected_ask)
        expected_microprice = calculate_microprice(
            expected_bid,
            None if not self.bid_levels else self.bid_levels[0].aggregate_quantity,
            expected_ask,
            None if not self.ask_levels else self.ask_levels[0].aggregate_quantity,
        )
        if self.spread != expected_spread:
            raise ValueError("spread does not match best prices")
        if self.midprice != expected_midprice:
            raise ValueError("midprice does not match best prices")
        if self.microprice != expected_microprice:
            raise ValueError("microprice does not match top-level prices and quantities")
        return self

    def bid_depth_n(self, level_count: int) -> Decimal:
        """Return raw bid depth across the first ``level_count`` levels."""
        return _depth_n(self.bid_levels, level_count)

    def ask_depth_n(self, level_count: int) -> Decimal:
        """Return raw ask depth across the first ``level_count`` levels."""
        return _depth_n(self.ask_levels, level_count)

    def order_book_imbalance(self, level_count: int) -> Decimal:
        """Return dimensionless OBI across the requested number of levels."""
        return calculate_order_book_imbalance(
            tuple(level.aggregate_quantity for level in self.bid_levels),
            tuple(level.aggregate_quantity for level in self.ask_levels),
            level_count,
        )

    def weighted_bid_depth(self, config: WeightedDepthConfig | None = None) -> Decimal:
        """Return configured weighted bid depth separately from raw depth."""
        return calculate_weighted_depth(
            tuple(level.aggregate_quantity for level in self.bid_levels),
            config,
        )

    def weighted_ask_depth(self, config: WeightedDepthConfig | None = None) -> Decimal:
        """Return configured weighted ask depth separately from raw depth."""
        return calculate_weighted_depth(
            tuple(level.aggregate_quantity for level in self.ask_levels),
            config,
        )


def _depth_n(levels: tuple[PriceLevel, ...], level_count: int) -> Decimal:
    if level_count <= 0:
        raise ValueError("level_count must be positive")
    return sum((level.aggregate_quantity for level in levels[:level_count]), Decimal(0))
