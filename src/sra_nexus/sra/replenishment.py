"""Price-level replenishment episodes without participant-identity inference."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
)
from sra_nexus.common.types import (
    MarketOrderId,
    OrderLifecycleId,
    ReplenishmentEpisodeId,
    ShockId,
)
from sra_nexus.market_data.enums import BookAction, BookSide
from sra_nexus.sra.lifecycle import OrderLifecycle, OrderLifecycleTransition
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.state import MarketEventReference, elapsed_decimal_seconds

REPLENISHMENT_EPISODE_VERSION = "replenishment-episode-v1"
REPLENISHMENT_EPISODE_NAMESPACE = UUID("cbcd04cb-0f92-5e63-b0f5-155c041581af")


class ReplenishmentEpisode(ContractModel):
    """One same-price replenishment burst after observed attacked-side execution."""

    episode_id: ReplenishmentEpisodeId
    shock_id: ShockId
    side: BookSide
    price: PositiveDecimal
    depletion_event_reference: MarketEventReference
    first_replenishment_event_reference: MarketEventReference
    depletion_event_index: int = Field(ge=0)
    first_replenishment_event_index: int = Field(ge=0)
    quantity_added: PositiveDecimal
    contributing_order_ids: tuple[MarketOrderId, ...]
    exchange_delay_seconds: NonNegativeDecimal
    process_delay_seconds: NonNegativeDecimal
    subsequent_executed_quantity: NonNegativeDecimal
    subsequent_withdrawn_quantity: NonNegativeDecimal
    executed_fraction: NonNegativeDecimal | None
    withdrawn_fraction: NonNegativeDecimal | None
    attribution_complete: bool
    feature_version: NonBlankStr = REPLENISHMENT_EPISODE_VERSION

    @model_validator(mode="after")
    def validate_episode(self) -> Self:
        """Keep event order, attribution, and exact fractions internally coherent."""
        if self.first_replenishment_event_index <= self.depletion_event_index:
            raise ValueError("replenishment must follow its depletion execution")
        if not self.contributing_order_ids:
            raise ValueError("replenishment requires at least one contributing order")
        if len(set(self.contributing_order_ids)) != len(self.contributing_order_ids):
            raise ValueError("replenishment contributing order IDs must be unique")
        attributed = self.subsequent_executed_quantity + self.subsequent_withdrawn_quantity
        if attributed > self.quantity_added:
            raise ValueError("attributed replenishment outcomes exceed quantity added")
        if self.attribution_complete:
            expected_execution = self.subsequent_executed_quantity / self.quantity_added
            expected_withdrawal = self.subsequent_withdrawn_quantity / self.quantity_added
            if self.executed_fraction != expected_execution:
                raise ValueError("replenishment executed fraction is inconsistent")
            if self.withdrawn_fraction != expected_withdrawal:
                raise ValueError("replenishment withdrawal fraction is inconsistent")
        elif self.executed_fraction is not None or self.withdrawn_fraction is not None:
            raise ValueError("incomplete replenishment attribution cannot expose fractions")
        return self


@dataclass(frozen=True, slots=True)
class _ObservedTransition:
    lifecycle_id: OrderLifecycleId
    order_id: MarketOrderId
    transition: OrderLifecycleTransition


@dataclass(slots=True)
class _EpisodeBuilder:
    shock_id: ShockId
    side: BookSide
    price: Decimal
    depletion: _ObservedTransition
    first_replenishment: _ObservedTransition
    quantity_added: Decimal = Decimal(0)
    contributing_order_ids: list[MarketOrderId] = field(default_factory=list)
    attributable_remaining: dict[OrderLifecycleId, Decimal] = field(default_factory=dict)
    subsequent_executed_quantity: Decimal = Decimal(0)
    subsequent_withdrawn_quantity: Decimal = Decimal(0)
    attribution_complete: bool = True
    last_add_event_index: int = 0
    received_execution: bool = False


def identify_replenishment_episodes(
    *,
    shock: LiquidityShock,
    attacked_side: BookSide,
    original_prices: tuple[Decimal, ...],
    lifecycles: tuple[OrderLifecycle, ...],
    shock_start_event_index: int,
    observation_end_event_index: int,
    episode_event_gap: int,
) -> tuple[ReplenishmentEpisode, ...]:
    """Identify deterministic same-price bursts and exact MBO-attributable outcomes.

    A price becomes eligible after an execution at that original attacked price.
    Positive ADD/MODIFY quantity starts or extends a burst. Adds no more than
    ``episode_event_gap`` normalized events apart join the open burst unless that
    burst has already received execution; otherwise a new episode begins.
    """
    if episode_event_gap < 0:
        raise ValueError("replenishment episode event gap must be non-negative")
    observed = _flatten_transitions(
        lifecycles,
        shock_start_event_index,
        observation_end_event_index,
    )
    original_price_set = set(original_prices)
    last_execution: dict[Decimal, _ObservedTransition] = {}
    open_episode: dict[Decimal, _EpisodeBuilder] = {}
    episodes: list[_EpisodeBuilder] = []

    for item in observed:
        transition = item.transition
        pre_price = transition.pre_price
        post_price = transition.post_price
        _attribute_price_departure(item, episodes)
        if transition.action is BookAction.EXECUTE and pre_price in original_price_set:
            current = open_episode.get(pre_price)
            if current is not None:
                current.received_execution = True
            _attribute_execution(item, episodes, pre_price)
            last_execution[pre_price] = item
            continue
        if pre_price in original_price_set:
            _attribute_withdrawal(item, episodes, pre_price)
        if transition.added_quantity <= 0 or post_price not in original_price_set:
            continue
        depletion = last_execution.get(post_price)
        if depletion is None:
            continue
        event_index = _required_event_index(transition)
        current = open_episode.get(post_price)
        should_extend = (
            current is not None
            and not current.received_execution
            and event_index - current.last_add_event_index <= episode_event_gap
        )
        if not should_extend:
            current = _EpisodeBuilder(
                shock_id=shock.shock_id,
                side=attacked_side,
                price=post_price,
                depletion=depletion,
                first_replenishment=item,
                last_add_event_index=event_index,
            )
            episodes.append(current)
            open_episode[post_price] = current
        if current is None:
            raise AssertionError("replenishment episode was not initialized")
        _add_contribution(current, item)

    return tuple(_materialize_episode(item) for item in episodes)


def absorption_cycle_count(episodes: tuple[ReplenishmentEpisode, ...]) -> int:
    """Count replenish-then-execute cycles without assuming participant identity."""
    return sum(episode.subsequent_executed_quantity > 0 for episode in episodes)


def _flatten_transitions(
    lifecycles: tuple[OrderLifecycle, ...],
    start_index: int,
    end_index: int,
) -> tuple[_ObservedTransition, ...]:
    items: list[_ObservedTransition] = []
    for lifecycle in lifecycles:
        for transition in lifecycle.transitions:
            index = transition.event.event_index
            if index is None:
                raise ValueError("replenishment analysis requires normalized event indices")
            if start_index <= index <= end_index:
                items.append(
                    _ObservedTransition(
                        lifecycle_id=lifecycle.lifecycle_id,
                        order_id=lifecycle.order_id,
                        transition=transition,
                    )
                )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                _required_event_index(item.transition),
                str(item.transition.event.reference.event_id),
                str(item.lifecycle_id),
            ),
        )
    )


def _add_contribution(builder: _EpisodeBuilder, item: _ObservedTransition) -> None:
    transition = item.transition
    quantity = transition.added_quantity
    builder.quantity_added += quantity
    builder.last_add_event_index = _required_event_index(transition)
    if item.order_id not in builder.contributing_order_ids:
        builder.contributing_order_ids.append(item.order_id)
    builder.attributable_remaining[item.lifecycle_id] = (
        builder.attributable_remaining.get(item.lifecycle_id, Decimal(0)) + quantity
    )
    if transition.action is not BookAction.ADD:
        builder.attribution_complete = False


def _attribute_execution(
    item: _ObservedTransition,
    episodes: list[_EpisodeBuilder],
    price: Decimal,
) -> None:
    remaining_effect = item.transition.executed_quantity
    for builder in episodes:
        if builder.price != price or remaining_effect == 0:
            continue
        available = builder.attributable_remaining.get(item.lifecycle_id, Decimal(0))
        attributed = min(available, remaining_effect)
        if attributed > 0:
            builder.subsequent_executed_quantity += attributed
            builder.attributable_remaining[item.lifecycle_id] = available - attributed
            remaining_effect -= attributed


def _attribute_withdrawal(
    item: _ObservedTransition,
    episodes: list[_EpisodeBuilder],
    price: Decimal,
) -> None:
    remaining_effect = item.transition.withdrawn_quantity
    for builder in episodes:
        if builder.price != price or remaining_effect == 0:
            continue
        available = builder.attributable_remaining.get(item.lifecycle_id, Decimal(0))
        attributed = min(available, remaining_effect)
        if attributed > 0:
            builder.subsequent_withdrawn_quantity += attributed
            builder.attributable_remaining[item.lifecycle_id] = available - attributed
            remaining_effect -= attributed


def _attribute_price_departure(
    item: _ObservedTransition,
    episodes: list[_EpisodeBuilder],
) -> None:
    transition = item.transition
    if transition.pre_price is None or transition.pre_price == transition.post_price:
        return
    for builder in episodes:
        if builder.price != transition.pre_price:
            continue
        available = builder.attributable_remaining.get(item.lifecycle_id, Decimal(0))
        if available > 0:
            builder.subsequent_withdrawn_quantity += available
            builder.attributable_remaining[item.lifecycle_id] = Decimal(0)


def _materialize_episode(builder: _EpisodeBuilder) -> ReplenishmentEpisode:
    depletion_reference = builder.depletion.transition.event.reference
    first_reference = builder.first_replenishment.transition.event.reference
    depletion_index = _required_event_index(builder.depletion.transition)
    first_index = _required_event_index(builder.first_replenishment.transition)
    episode_id = _derive_episode_id(
        builder.shock_id,
        builder.price,
        depletion_reference,
        first_reference,
    )
    return ReplenishmentEpisode(
        episode_id=episode_id,
        shock_id=builder.shock_id,
        side=builder.side,
        price=builder.price,
        depletion_event_reference=depletion_reference,
        first_replenishment_event_reference=first_reference,
        depletion_event_index=depletion_index,
        first_replenishment_event_index=first_index,
        quantity_added=builder.quantity_added,
        contributing_order_ids=tuple(builder.contributing_order_ids),
        exchange_delay_seconds=elapsed_decimal_seconds(
            depletion_reference.exchange_time,
            first_reference.exchange_time,
        ),
        process_delay_seconds=elapsed_decimal_seconds(
            depletion_reference.process_time,
            first_reference.process_time,
        ),
        subsequent_executed_quantity=builder.subsequent_executed_quantity,
        subsequent_withdrawn_quantity=builder.subsequent_withdrawn_quantity,
        executed_fraction=(
            builder.subsequent_executed_quantity / builder.quantity_added
            if builder.attribution_complete
            else None
        ),
        withdrawn_fraction=(
            builder.subsequent_withdrawn_quantity / builder.quantity_added
            if builder.attribution_complete
            else None
        ),
        attribution_complete=builder.attribution_complete,
    )


def _derive_episode_id(
    shock_id: ShockId,
    price: Decimal,
    depletion: MarketEventReference,
    first_replenishment: MarketEventReference,
) -> ReplenishmentEpisodeId:
    identity = (
        f"{shock_id}|{price}|{depletion.event_id}|{first_replenishment.event_id}|"
        f"{REPLENISHMENT_EPISODE_VERSION}"
    )
    return ReplenishmentEpisodeId(uuid5(REPLENISHMENT_EPISODE_NAMESPACE, identity))


def _required_event_index(transition: OrderLifecycleTransition) -> int:
    index = transition.event.event_index
    if index is None:
        raise ValueError("replenishment transition requires normalized event index")
    return index
