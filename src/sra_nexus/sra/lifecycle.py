"""Deterministic lifecycle accounting for already accepted MBO book events."""

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
    UtcDatetime,
)
from sra_nexus.common.types import (
    InstrumentId,
    MarketOrderId,
    OrderLifecycleId,
    SequenceStreamId,
)
from sra_nexus.market_data.enums import BookAction, BookDataMode, BookSide
from sra_nexus.market_data.events import BookEvent
from sra_nexus.market_data.exceptions import UnsupportedBookModeError
from sra_nexus.sra.enums import OrderLifecycleTerminalReason
from sra_nexus.sra.state import (
    MarketEventReference,
    elapsed_decimal_seconds,
    market_event_reference,
)

ORDER_LIFECYCLE_VERSION = "order-lifecycle-v1"
ORDER_LIFECYCLE_NAMESPACE = UUID("0c3151fb-cc4c-593c-bbe2-886cb221dfdd")


class IndexedMarketEventReference(ContractModel):
    """Market-event reference paired with an optional true normalized-event index."""

    reference: MarketEventReference
    event_index: int | None = Field(default=None, ge=0)


class OrderLifecycleTransition(ContractModel):
    """One accepted MBO transition with explicit quantity-accounting effects."""

    event: IndexedMarketEventReference
    action: BookAction
    pre_price: PositiveDecimal | None
    post_price: PositiveDecimal | None
    pre_remaining_quantity: NonNegativeDecimal
    post_remaining_quantity: NonNegativeDecimal
    added_quantity: NonNegativeDecimal
    executed_quantity: NonNegativeDecimal
    withdrawn_quantity: NonNegativeDecimal

    @model_validator(mode="after")
    def validate_transition_shape(self) -> Self:
        """Require exact conservation across the accepted transition."""
        expected = (
            self.pre_remaining_quantity
            + self.added_quantity
            - self.executed_quantity
            - self.withdrawn_quantity
        )
        if expected != self.post_remaining_quantity:
            raise ValueError("lifecycle transition violates exact quantity conservation")
        if self.action is BookAction.ADD:
            if (
                self.pre_remaining_quantity != 0
                or self.pre_price is not None
                or self.added_quantity <= 0
                or self.executed_quantity != 0
                or self.withdrawn_quantity != 0
                or self.post_price is None
            ):
                raise ValueError("ADD transition must only introduce displayed quantity")
        elif self.action is BookAction.MODIFY:
            if (
                self.pre_price is None
                or self.post_price is None
                or self.executed_quantity != 0
                or (self.added_quantity > 0 and self.withdrawn_quantity > 0)
            ):
                raise ValueError("MODIFY transition has incompatible quantity effects")
        elif self.action is BookAction.EXECUTE:
            if (
                self.pre_price is None
                or self.post_price != self.pre_price
                or self.executed_quantity <= 0
                or self.added_quantity != 0
                or self.withdrawn_quantity != 0
            ):
                raise ValueError("EXECUTE transition must only consume displayed quantity")
        elif self.action in {BookAction.CANCEL, BookAction.DELETE}:
            if (
                self.pre_price is None
                or self.post_price != self.pre_price
                or self.withdrawn_quantity <= 0
                or self.added_quantity != 0
                or self.executed_quantity != 0
            ):
                raise ValueError("withdrawal transition must only remove displayed quantity")
            if self.action is BookAction.DELETE and self.post_remaining_quantity != 0:
                raise ValueError("DELETE transition must withdraw all remaining quantity")
        else:
            raise ValueError("RESET cannot appear inside an order lifecycle transition")
        return self


class OrderLifecycle(ContractModel):
    """Immutable complete or right-censored lifecycle for one MBO order identity."""

    lifecycle_id: OrderLifecycleId
    order_id: MarketOrderId
    instrument_id: InstrumentId
    venue: NonBlankStr
    sequence_stream_id: SequenceStreamId
    side: BookSide
    initial_price: PositiveDecimal
    final_price: PositiveDecimal
    first_seen_exchange_time: UtcDatetime
    last_seen_exchange_time: UtcDatetime
    first_seen_process_time: UtcDatetime
    last_seen_process_time: UtcDatetime
    first_event_index: int | None = Field(default=None, ge=0)
    last_event_index: int | None = Field(default=None, ge=0)
    event_lifetime: int | None = Field(default=None, ge=0)
    initial_quantity: PositiveDecimal
    observed_added_quantity: PositiveDecimal
    maximum_observed_quantity: PositiveDecimal
    total_executed_quantity: NonNegativeDecimal
    total_withdrawn_quantity: NonNegativeDecimal
    unresolved_remaining_quantity: NonNegativeDecimal
    modify_count: int = Field(ge=0)
    price_change_count: int = Field(ge=0)
    execution_count: int = Field(ge=0)
    cancel_count: int = Field(ge=0)
    terminal_reason: OrderLifecycleTerminalReason
    transitions: tuple[OrderLifecycleTransition, ...]
    feature_version: NonBlankStr = ORDER_LIFECYCLE_VERSION

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        """Keep added, executed, withdrawn, and unresolved quantity exact."""
        if not self.transitions or self.transitions[0].action is not BookAction.ADD:
            raise ValueError("lifecycle transitions must begin with ADD")
        if self.last_seen_exchange_time < self.first_seen_exchange_time:
            raise ValueError("exchange lifetime cannot be negative")
        if self.last_seen_process_time < self.first_seen_process_time:
            raise ValueError("process lifetime cannot be negative")
        if self.observed_added_quantity < self.initial_quantity:
            raise ValueError("observed added quantity cannot be below initial quantity")
        if self.maximum_observed_quantity < self.initial_quantity:
            raise ValueError("maximum observed quantity cannot be below initial quantity")
        first_transition = self.transitions[0]
        last_transition = self.transitions[-1]
        if (
            self.initial_price != first_transition.post_price
            or self.final_price != last_transition.post_price
            or self.initial_quantity != first_transition.added_quantity
        ):
            raise ValueError("lifecycle boundary values must match its transitions")
        for previous, current in zip(
            self.transitions,
            self.transitions[1:],
            strict=False,
        ):
            if (
                previous.post_remaining_quantity != current.pre_remaining_quantity
                or previous.post_price != current.pre_price
            ):
                raise ValueError("lifecycle transitions must form one continuous state")
        expected_added = sum(
            (transition.added_quantity for transition in self.transitions),
            Decimal(0),
        )
        expected_executed = sum(
            (transition.executed_quantity for transition in self.transitions),
            Decimal(0),
        )
        expected_withdrawn = sum(
            (transition.withdrawn_quantity for transition in self.transitions),
            Decimal(0),
        )
        if self.observed_added_quantity != expected_added:
            raise ValueError("observed additions must sum lifecycle transitions")
        if self.total_executed_quantity != expected_executed:
            raise ValueError("executed quantity must sum lifecycle transitions")
        if self.total_withdrawn_quantity != expected_withdrawn:
            raise ValueError("withdrawn quantity must sum lifecycle transitions")
        if self.unresolved_remaining_quantity != last_transition.post_remaining_quantity:
            raise ValueError("unresolved quantity must equal final transition remainder")
        if self.maximum_observed_quantity != max(
            transition.post_remaining_quantity for transition in self.transitions
        ):
            raise ValueError("maximum observed quantity must match transition history")
        if self.modify_count != sum(
            transition.action is BookAction.MODIFY for transition in self.transitions
        ):
            raise ValueError("modify count must match lifecycle transitions")
        if self.price_change_count != sum(
            transition.action is BookAction.MODIFY and transition.pre_price != transition.post_price
            for transition in self.transitions
        ):
            raise ValueError("price-change count must match lifecycle transitions")
        if self.execution_count != sum(
            transition.action is BookAction.EXECUTE for transition in self.transitions
        ):
            raise ValueError("execution count must match lifecycle transitions")
        if self.cancel_count != sum(
            transition.action is BookAction.CANCEL for transition in self.transitions
        ):
            raise ValueError("cancel count must match lifecycle transitions")
        accounted = (
            self.total_executed_quantity
            + self.total_withdrawn_quantity
            + self.unresolved_remaining_quantity
        )
        if accounted != self.observed_added_quantity:
            raise ValueError("lifecycle quantities must exactly account for observed additions")
        if (
            self.terminal_reason
            in {
                OrderLifecycleTerminalReason.EXECUTED,
                OrderLifecycleTerminalReason.CANCELLED,
                OrderLifecycleTerminalReason.DELETED,
            }
            and self.unresolved_remaining_quantity != 0
        ):
            raise ValueError("normally terminated lifecycle cannot retain unresolved quantity")
        if (self.first_event_index is None) != (self.last_event_index is None):
            raise ValueError("lifecycle event indices must be both present or both unavailable")
        transition_indices = tuple(transition.event.event_index for transition in self.transitions)
        if self.first_event_index is None:
            if self.event_lifetime is not None or any(
                index is not None for index in transition_indices
            ):
                raise ValueError("event lifetime requires explicit normalized event indices")
        else:
            last_index = self.last_event_index
            if last_index is None:
                raise AssertionError("validated lifecycle unexpectedly lacks last event index")
            if any(index is None for index in transition_indices):
                raise ValueError("indexed lifecycle requires every transition index")
            if transition_indices[0] != self.first_event_index:
                raise ValueError("first event index must match the ADD transition")
            final_transition_index = transition_indices[-1]
            if final_transition_index is None:
                raise AssertionError("validated transition unexpectedly lacks event index")
            if last_index < final_transition_index:
                raise ValueError("lifecycle end cannot precede its final transition")
            if self.event_lifetime != last_index - self.first_event_index:
                raise ValueError("event lifetime must equal last event index minus first")
        first_reference = first_transition.event.reference
        last_reference = last_transition.event.reference
        if (
            self.first_seen_exchange_time != first_reference.exchange_time
            or self.first_seen_process_time != first_reference.process_time
        ):
            raise ValueError("first-seen clocks must match the ADD transition")
        if (
            self.last_seen_exchange_time < last_reference.exchange_time
            or self.last_seen_process_time < last_reference.process_time
        ):
            raise ValueError("lifecycle end clocks cannot precede its final transition")
        normal_terminal_action = {
            OrderLifecycleTerminalReason.EXECUTED: BookAction.EXECUTE,
            OrderLifecycleTerminalReason.CANCELLED: BookAction.CANCEL,
            OrderLifecycleTerminalReason.DELETED: BookAction.DELETE,
        }.get(self.terminal_reason)
        if normal_terminal_action is not None:
            if last_transition.action is not normal_terminal_action:
                raise ValueError("normal terminal reason must match final transition")
            if (
                self.last_seen_exchange_time != last_reference.exchange_time
                or self.last_seen_process_time != last_reference.process_time
            ):
                raise ValueError("normal terminal clocks must match final transition")
        return self

    @property
    def exchange_lifetime_seconds(self) -> Decimal:
        """Return exact market-time lifetime in seconds."""
        return elapsed_decimal_seconds(
            self.first_seen_exchange_time,
            self.last_seen_exchange_time,
        )

    @property
    def process_lifetime_seconds(self) -> Decimal:
        """Return exact system-observable lifetime in seconds."""
        return elapsed_decimal_seconds(
            self.first_seen_process_time,
            self.last_seen_process_time,
        )

    @property
    def executed_fraction(self) -> Decimal:
        """Return executed quantity divided by all observed additions."""
        return self.total_executed_quantity / self.observed_added_quantity

    @property
    def withdrawn_fraction(self) -> Decimal:
        """Return withdrawn quantity divided by all observed additions."""
        return self.total_withdrawn_quantity / self.observed_added_quantity


@dataclass(slots=True)
class _ActiveOrderLifecycle:
    lifecycle_id: OrderLifecycleId
    order_id: MarketOrderId
    instrument_id: InstrumentId
    venue: str
    sequence_stream_id: SequenceStreamId
    side: BookSide
    initial_price: Decimal
    current_price: Decimal
    first_event: IndexedMarketEventReference
    last_event: IndexedMarketEventReference
    initial_quantity: Decimal
    observed_added_quantity: Decimal
    maximum_observed_quantity: Decimal
    remaining_quantity: Decimal
    total_executed_quantity: Decimal = Decimal(0)
    total_withdrawn_quantity: Decimal = Decimal(0)
    modify_count: int = 0
    price_change_count: int = 0
    execution_count: int = 0
    cancel_count: int = 0
    transitions: list[OrderLifecycleTransition] = field(default_factory=list)


class OrderLifecycleTracker:
    """Observe accepted MBO events without duplicating order-book reconstruction.

    Callers must invoke ``observe_accepted`` only after the same event has been
    successfully committed by ``OrderBook`` or canonical market replay.
    """

    def __init__(self) -> None:
        """Start an unbound, open accepted-event observation stream."""
        self._active: dict[MarketOrderId, _ActiveOrderLifecycle] = {}
        self._completed: list[OrderLifecycle] = []
        self._reset_events: list[IndexedMarketEventReference] = []
        self._instrument_id: InstrumentId | None = None
        self._venue: str | None = None
        self._sequence_stream_id: SequenceStreamId | None = None
        self._last_sequence: int | None = None
        self._last_event_index: int | None = None
        self._uses_event_indices: bool | None = None
        self._last_reference: MarketEventReference | None = None
        self._closed = False

    @property
    def completed_lifecycles(self) -> tuple[OrderLifecycle, ...]:
        """Return terminal lifecycles in deterministic completion order."""
        return tuple(self._completed)

    @property
    def reset_events(self) -> tuple[IndexedMarketEventReference, ...]:
        """Return accepted RESET boundaries for downstream invalidation policy."""
        return tuple(self._reset_events)

    def observe_accepted(self, event: BookEvent, *, event_index: int | None = None) -> None:
        """Account for one BookEvent only after OrderBook accepted it."""
        if self._closed:
            raise ValueError("cannot observe events after lifecycle observation closure")
        if event.book_mode is not BookDataMode.MARKET_BY_ORDER:
            raise UnsupportedBookModeError("order lifecycle tracking requires MARKET_BY_ORDER")
        indexed = IndexedMarketEventReference(
            reference=market_event_reference(event),
            event_index=event_index,
        )
        self._validate_ordering(event, indexed)
        if event.action is BookAction.RESET:
            self._reset_events.append(indexed)
            for active in tuple(self._active.values()):
                self._completed.append(
                    _materialize_lifecycle(
                        active,
                        OrderLifecycleTerminalReason.RESET,
                        indexed,
                    )
                )
            self._active.clear()
        elif event.action is BookAction.ADD:
            self._observe_add(event, indexed)
        else:
            self._observe_existing(event, indexed)
        self._commit_boundary(event, indexed)

    def close_observation(
        self,
        observation_end: MarketEventReference,
        *,
        event_index: int | None = None,
    ) -> tuple[OrderLifecycle, ...]:
        """Right-censor every active order at an explicit observation boundary."""
        if self._closed:
            raise ValueError("lifecycle observation is already closed")
        self._validate_close_boundary(observation_end, event_index)
        indexed = IndexedMarketEventReference(
            reference=observation_end,
            event_index=event_index,
        )
        for active in tuple(self._active.values()):
            self._completed.append(
                _materialize_lifecycle(
                    active,
                    OrderLifecycleTerminalReason.OBSERVATION_END,
                    indexed,
                )
            )
        self._active.clear()
        self._closed = True
        return tuple(self._completed)

    def _observe_add(
        self,
        event: BookEvent,
        indexed: IndexedMarketEventReference,
    ) -> None:
        side, price, order_id = _required_fields(event)
        quantity = _required_quantity(event)
        if order_id in self._active:
            raise ValueError("accepted ADD reused an active lifecycle order_id")
        transition = OrderLifecycleTransition(
            event=indexed,
            action=BookAction.ADD,
            pre_price=None,
            post_price=price,
            pre_remaining_quantity=Decimal(0),
            post_remaining_quantity=quantity,
            added_quantity=quantity,
            executed_quantity=Decimal(0),
            withdrawn_quantity=Decimal(0),
        )
        self._active[order_id] = _ActiveOrderLifecycle(
            lifecycle_id=_derive_lifecycle_id(event),
            order_id=order_id,
            instrument_id=event.instrument_id,
            venue=event.venue,
            sequence_stream_id=event.sequence_stream_id,
            side=side,
            initial_price=price,
            current_price=price,
            first_event=indexed,
            last_event=indexed,
            initial_quantity=quantity,
            observed_added_quantity=quantity,
            maximum_observed_quantity=quantity,
            remaining_quantity=quantity,
            transitions=[transition],
        )

    def _observe_existing(
        self,
        event: BookEvent,
        indexed: IndexedMarketEventReference,
    ) -> None:
        side, price, order_id = _required_fields(event)
        active = self._active.get(order_id)
        if active is None:
            raise ValueError("accepted lifecycle event references an unknown active order")
        if active.side is not side:
            raise ValueError("accepted lifecycle event changed immutable order side")
        pre_quantity = active.remaining_quantity
        pre_price = active.current_price
        added = Decimal(0)
        executed = Decimal(0)
        withdrawn = Decimal(0)
        post_quantity = pre_quantity
        post_price = pre_price
        terminal_reason: OrderLifecycleTerminalReason | None = None

        if event.action is BookAction.MODIFY:
            post_quantity = _required_quantity(event)
            post_price = price
            if post_quantity > pre_quantity:
                added = post_quantity - pre_quantity
                active.observed_added_quantity += added
            elif post_quantity < pre_quantity:
                withdrawn = pre_quantity - post_quantity
                active.total_withdrawn_quantity += withdrawn
            active.modify_count += 1
            if post_price != pre_price:
                active.price_change_count += 1
        elif event.action is BookAction.EXECUTE:
            _require_matching_price(pre_price, price, "EXECUTE")
            executed = _required_quantity(event)
            post_quantity -= executed
            active.total_executed_quantity += executed
            active.execution_count += 1
            if post_quantity == 0:
                terminal_reason = OrderLifecycleTerminalReason.EXECUTED
        elif event.action is BookAction.CANCEL:
            _require_matching_price(pre_price, price, "CANCEL")
            withdrawn = _required_quantity(event)
            post_quantity -= withdrawn
            active.total_withdrawn_quantity += withdrawn
            active.cancel_count += 1
            if post_quantity == 0:
                terminal_reason = OrderLifecycleTerminalReason.CANCELLED
        elif event.action is BookAction.DELETE:
            _require_matching_price(pre_price, price, "DELETE")
            withdrawn = pre_quantity
            post_quantity = Decimal(0)
            active.total_withdrawn_quantity += withdrawn
            terminal_reason = OrderLifecycleTerminalReason.DELETED
        else:
            raise ValueError("unsupported accepted lifecycle action")

        if post_quantity < 0:
            raise ValueError("accepted lifecycle event produced negative order quantity")
        active.current_price = post_price
        active.remaining_quantity = post_quantity
        active.maximum_observed_quantity = max(
            active.maximum_observed_quantity,
            post_quantity,
        )
        active.last_event = indexed
        active.transitions.append(
            OrderLifecycleTransition(
                event=indexed,
                action=event.action,
                pre_price=pre_price,
                post_price=post_price,
                pre_remaining_quantity=pre_quantity,
                post_remaining_quantity=post_quantity,
                added_quantity=added,
                executed_quantity=executed,
                withdrawn_quantity=withdrawn,
            )
        )
        if terminal_reason is not None:
            self._completed.append(_materialize_lifecycle(active, terminal_reason, indexed))
            del self._active[order_id]

    def _validate_ordering(
        self,
        event: BookEvent,
        indexed: IndexedMarketEventReference,
    ) -> None:
        if self._instrument_id is not None and (
            event.instrument_id != self._instrument_id
            or event.venue != self._venue
            or event.sequence_stream_id != self._sequence_stream_id
        ):
            raise ValueError("lifecycle tracker cannot combine different market streams")
        if self._last_sequence is not None and event.sequence_number <= self._last_sequence:
            raise ValueError("accepted lifecycle events must use increasing sequence numbers")
        has_index = indexed.event_index is not None
        if self._uses_event_indices is not None and has_index is not self._uses_event_indices:
            raise ValueError("normalized event indices must be supplied for every event or none")
        if (
            indexed.event_index is not None
            and self._last_event_index is not None
            and indexed.event_index <= self._last_event_index
        ):
            raise ValueError("normalized event indices must be strictly increasing")
        if self._last_reference is not None and (
            event.exchange_time < self._last_reference.exchange_time
            or event.process_time < self._last_reference.process_time
        ):
            raise ValueError("lifecycle event clocks must not regress")

    def _commit_boundary(
        self,
        event: BookEvent,
        indexed: IndexedMarketEventReference,
    ) -> None:
        if self._instrument_id is None:
            self._instrument_id = event.instrument_id
            self._venue = event.venue
            self._sequence_stream_id = event.sequence_stream_id
            self._uses_event_indices = indexed.event_index is not None
        self._last_sequence = event.sequence_number
        self._last_event_index = indexed.event_index
        self._last_reference = indexed.reference

    def _validate_close_boundary(
        self,
        reference: MarketEventReference,
        event_index: int | None,
    ) -> None:
        if self._instrument_id is not None and (
            reference.instrument_id != self._instrument_id
            or reference.venue != self._venue
            or reference.sequence_stream_id != self._sequence_stream_id
        ):
            raise ValueError("observation boundary belongs to another market stream")
        if self._uses_event_indices is not None and (event_index is not None) is not (
            self._uses_event_indices
        ):
            raise ValueError("observation boundary must preserve event-index availability")
        if self._last_event_index is not None and event_index is not None:
            if event_index < self._last_event_index:
                raise ValueError("observation boundary cannot precede accepted events")
        if self._last_reference is not None and (
            reference.exchange_time < self._last_reference.exchange_time
            or reference.process_time < self._last_reference.process_time
        ):
            raise ValueError("observation boundary clocks cannot regress")


def _materialize_lifecycle(
    active: _ActiveOrderLifecycle,
    terminal_reason: OrderLifecycleTerminalReason,
    terminal_event: IndexedMarketEventReference,
) -> OrderLifecycle:
    first_index = active.first_event.event_index
    last_index = terminal_event.event_index
    event_lifetime = None if first_index is None or last_index is None else last_index - first_index
    return OrderLifecycle(
        lifecycle_id=active.lifecycle_id,
        order_id=active.order_id,
        instrument_id=active.instrument_id,
        venue=active.venue,
        sequence_stream_id=active.sequence_stream_id,
        side=active.side,
        initial_price=active.initial_price,
        final_price=active.current_price,
        first_seen_exchange_time=active.first_event.reference.exchange_time,
        last_seen_exchange_time=terminal_event.reference.exchange_time,
        first_seen_process_time=active.first_event.reference.process_time,
        last_seen_process_time=terminal_event.reference.process_time,
        first_event_index=first_index,
        last_event_index=last_index,
        event_lifetime=event_lifetime,
        initial_quantity=active.initial_quantity,
        observed_added_quantity=active.observed_added_quantity,
        maximum_observed_quantity=active.maximum_observed_quantity,
        total_executed_quantity=active.total_executed_quantity,
        total_withdrawn_quantity=active.total_withdrawn_quantity,
        unresolved_remaining_quantity=active.remaining_quantity,
        modify_count=active.modify_count,
        price_change_count=active.price_change_count,
        execution_count=active.execution_count,
        cancel_count=active.cancel_count,
        terminal_reason=terminal_reason,
        transitions=tuple(active.transitions),
    )


def _derive_lifecycle_id(event: BookEvent) -> OrderLifecycleId:
    if event.order_id is None:
        raise ValueError("MBO ADD requires order_id")
    identity = (
        f"{event.instrument_id}|{event.venue}|{event.sequence_stream_id}|"
        f"{event.order_id}|{event.event_id}"
    )
    return OrderLifecycleId(uuid5(ORDER_LIFECYCLE_NAMESPACE, identity))


def _required_fields(event: BookEvent) -> tuple[BookSide, Decimal, MarketOrderId]:
    if event.side is None or event.price is None or event.order_id is None:
        raise ValueError("validated non-RESET MBO event lacks order fields")
    return event.side, event.price, event.order_id


def _required_quantity(event: BookEvent) -> Decimal:
    if event.quantity is None:
        raise ValueError("validated quantity-bearing MBO event lacks quantity")
    return event.quantity


def _require_matching_price(current: Decimal, observed: Decimal, action: str) -> None:
    if current != observed:
        raise ValueError(f"accepted {action} price conflicts with lifecycle state")
