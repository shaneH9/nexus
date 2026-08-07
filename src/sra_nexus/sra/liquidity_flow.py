"""Exact accepted-MBO liquidity provision and withdrawal accounting."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonNegativeDecimal,
    PositiveDecimal,
)
from sra_nexus.market_data.enums import BookAction, BookSide
from sra_nexus.market_data.snapshots import BookSnapshot
from sra_nexus.sra.enums import ShockDirection
from sra_nexus.sra.lifecycle import OrderLifecycle, OrderLifecycleTransition
from sra_nexus.sra.state import MarketEventReference
from sra_nexus.sra.toxicity_math import (
    calculate_net_liquidity_provision,
    calculate_normalized_net_liquidity_provision,
    calculate_withdrawal_pressure,
)

SignedUnitDecimal = Annotated[
    ExactDecimal,
    Field(ge=-1, le=1, description="Exact dimensionless value in [-1, 1]."),
]
UnitIntervalDecimal = Annotated[
    ExactDecimal,
    Field(ge=0, le=1, description="Exact dimensionless value in [0, 1]."),
]


class SideLiquidityFlow(ContractModel):
    """Exact displayed-liquidity activity at fixed absolute prices on one side."""

    side: BookSide
    original_price_levels: tuple[PositiveDecimal, ...]
    added_quantity: NonNegativeDecimal
    withdrawn_quantity: NonNegativeDecimal
    executed_quantity: NonNegativeDecimal
    net_liquidity_provision: ExactDecimal
    normalized_net_liquidity_provision: SignedUnitDecimal

    @model_validator(mode="after")
    def validate_equations(self) -> Self:
        """Require unique prices and exact NLP/NNLP quantities."""
        if len(set(self.original_price_levels)) != len(self.original_price_levels):
            raise ValueError("liquidity-flow price levels must be unique")
        expected_nlp = calculate_net_liquidity_provision(
            self.added_quantity,
            self.withdrawn_quantity,
        )
        if self.net_liquidity_provision != expected_nlp:
            raise ValueError("net liquidity provision is inconsistent")
        gross = self.added_quantity + self.withdrawn_quantity
        expected_nnlp = Decimal(0) if gross == 0 else expected_nlp / gross
        if self.normalized_net_liquidity_provision != expected_nnlp:
            raise ValueError("normalized net liquidity provision is inconsistent")
        return self


class LiquidityFlowFeatures(ContractModel):
    """Attacked- and opposite-side provision over one exact event-index window."""

    attacked_side: BookSide
    opposite_side: BookSide
    depth_levels: int = Field(gt=0)
    window_start_event_index: int = Field(ge=0)
    window_end_event_index: int = Field(ge=0)
    window_start_reference: MarketEventReference
    window_end_reference: MarketEventReference
    attacked: SideLiquidityFlow
    opposite: SideLiquidityFlow
    withdrawal_pressure: UnitIntervalDecimal
    epsilon: PositiveDecimal

    @model_validator(mode="after")
    def validate_features(self) -> Self:
        """Keep side symmetry, event order, and withdrawal pressure exact."""
        if self.window_end_event_index < self.window_start_event_index:
            raise ValueError("liquidity-flow window cannot have negative event span")
        if self.attacked_side is self.opposite_side:
            raise ValueError("attacked and opposite liquidity sides must differ")
        if self.attacked.side is not self.attacked_side:
            raise ValueError("attacked side result has the wrong side")
        if self.opposite.side is not self.opposite_side:
            raise ValueError("opposite side result has the wrong side")
        if (
            self.window_start_reference.instrument_id != self.window_end_reference.instrument_id
            or self.window_start_reference.venue != self.window_end_reference.venue
        ):
            raise ValueError("liquidity-flow boundaries must share instrument and venue")
        if (
            self.window_start_reference.exchange_time > self.window_end_reference.exchange_time
            or self.window_start_reference.process_time > self.window_end_reference.process_time
        ):
            raise ValueError("liquidity-flow boundary clocks cannot regress")
        expected = calculate_withdrawal_pressure(
            self.attacked.added_quantity,
            self.attacked.withdrawn_quantity,
            self.epsilon,
        )
        if self.withdrawal_pressure != expected:
            raise ValueError("withdrawal pressure is inconsistent")
        return self


def calculate_liquidity_flow_features(
    *,
    direction: ShockDirection,
    pre_shock_snapshot: BookSnapshot,
    lifecycles: Sequence[OrderLifecycle],
    window_start_event_index: int,
    window_end_event_index: int,
    window_start_reference: MarketEventReference,
    window_end_reference: MarketEventReference,
    depth_levels: int,
    epsilon: Decimal,
) -> LiquidityFlowFeatures:
    """Aggregate additions, withdrawals, and executions at original K levels.

    Same-price MODIFY uses only its absolute quantity delta. A price-changing
    MODIFY withdraws its full pre-event remainder from an included old price and
    adds its full post-event remainder at an included new price. Executions are
    retained separately and never counted as withdrawals.
    """
    if depth_levels <= 0:
        raise ValueError("liquidity-flow depth levels must be positive")
    if epsilon <= 0 or not epsilon.is_finite():
        raise ValueError("liquidity-flow epsilon must be finite and positive")
    if window_start_event_index < 0 or window_end_event_index < window_start_event_index:
        raise ValueError("liquidity-flow event-index window is invalid")
    if (
        window_start_reference.instrument_id != pre_shock_snapshot.instrument_id
        or window_end_reference.instrument_id != pre_shock_snapshot.instrument_id
        or window_start_reference.venue != pre_shock_snapshot.venue
        or window_end_reference.venue != pre_shock_snapshot.venue
    ):
        raise ValueError("liquidity-flow boundaries must share snapshot instrument and venue")
    lifecycle_values = tuple(lifecycles)
    if any(
        lifecycle.instrument_id != pre_shock_snapshot.instrument_id
        or lifecycle.venue != pre_shock_snapshot.venue
        for lifecycle in lifecycle_values
    ):
        raise ValueError("liquidity-flow lifecycles must share snapshot instrument and venue")

    attacked_side = BookSide.ASK if direction is ShockDirection.BUY else BookSide.BID
    opposite_side = BookSide.BID if attacked_side is BookSide.ASK else BookSide.ASK
    prices = {
        BookSide.BID: tuple(level.price for level in pre_shock_snapshot.bid_levels[:depth_levels]),
        BookSide.ASK: tuple(level.price for level in pre_shock_snapshot.ask_levels[:depth_levels]),
    }
    attacked = _side_flow(
        lifecycle_values,
        attacked_side,
        prices[attacked_side],
        window_start_event_index,
        window_end_event_index,
        epsilon,
    )
    opposite = _side_flow(
        lifecycle_values,
        opposite_side,
        prices[opposite_side],
        window_start_event_index,
        window_end_event_index,
        epsilon,
    )
    return LiquidityFlowFeatures(
        attacked_side=attacked_side,
        opposite_side=opposite_side,
        depth_levels=depth_levels,
        window_start_event_index=window_start_event_index,
        window_end_event_index=window_end_event_index,
        window_start_reference=window_start_reference,
        window_end_reference=window_end_reference,
        attacked=attacked,
        opposite=opposite,
        withdrawal_pressure=calculate_withdrawal_pressure(
            attacked.added_quantity,
            attacked.withdrawn_quantity,
            epsilon,
        ),
        epsilon=epsilon,
    )


def _side_flow(
    lifecycles: tuple[OrderLifecycle, ...],
    side: BookSide,
    prices: tuple[Decimal, ...],
    start_index: int,
    end_index: int,
    epsilon: Decimal,
) -> SideLiquidityFlow:
    price_set = set(prices)
    added = Decimal(0)
    withdrawn = Decimal(0)
    executed = Decimal(0)
    for lifecycle in lifecycles:
        if lifecycle.side is not side:
            continue
        for transition in lifecycle.transitions:
            index = _required_event_index(transition)
            if not start_index <= index <= end_index:
                continue
            transition_added, transition_withdrawn, transition_executed = _region_quantity_effects(
                transition, price_set
            )
            added += transition_added
            withdrawn += transition_withdrawn
            executed += transition_executed
    return SideLiquidityFlow(
        side=side,
        original_price_levels=prices,
        added_quantity=added,
        withdrawn_quantity=withdrawn,
        executed_quantity=executed,
        net_liquidity_provision=calculate_net_liquidity_provision(added, withdrawn),
        normalized_net_liquidity_provision=(
            calculate_normalized_net_liquidity_provision(added, withdrawn, epsilon)
        ),
    )


def _region_quantity_effects(
    transition: OrderLifecycleTransition,
    price_set: set[Decimal],
) -> tuple[Decimal, Decimal, Decimal]:
    pre_in_region = transition.pre_price in price_set
    post_in_region = transition.post_price in price_set
    if transition.action is BookAction.ADD:
        return (
            transition.post_remaining_quantity if post_in_region else Decimal(0),
            Decimal(0),
            Decimal(0),
        )
    if transition.action is BookAction.MODIFY:
        if transition.pre_price == transition.post_price:
            return (
                transition.added_quantity if post_in_region else Decimal(0),
                transition.withdrawn_quantity if pre_in_region else Decimal(0),
                Decimal(0),
            )
        return (
            transition.post_remaining_quantity if post_in_region else Decimal(0),
            transition.pre_remaining_quantity if pre_in_region else Decimal(0),
            Decimal(0),
        )
    if transition.action is BookAction.EXECUTE:
        return (
            Decimal(0),
            Decimal(0),
            transition.executed_quantity if pre_in_region else Decimal(0),
        )
    if transition.action in {BookAction.CANCEL, BookAction.DELETE}:
        return (
            Decimal(0),
            transition.withdrawn_quantity if pre_in_region else Decimal(0),
            Decimal(0),
        )
    raise ValueError("unsupported lifecycle transition in liquidity-flow analysis")


def _required_event_index(transition: OrderLifecycleTransition) -> int:
    index = transition.event.event_index
    if index is None:
        raise ValueError("liquidity-flow analysis requires normalized event indices")
    return index
