"""Deterministic in-memory market-by-order reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import TypeAdapter

from sra_nexus.common.models import NonBlankStr
from sra_nexus.common.types import InstrumentId, MarketOrderId, SequenceStreamId
from sra_nexus.market_data.enums import BookAction, BookDataMode, BookSide
from sra_nexus.market_data.events import BookEvent, MarketEvent, QuoteEvent, TradeEvent
from sra_nexus.market_data.exceptions import (
    BookNotInitializedError,
    BookStreamMismatchError,
    CrossedBookError,
    DuplicateOrderError,
    DuplicateSequenceError,
    NegativeDepthError,
    OrderAttributeMismatchError,
    QuantityExceedsRemainingError,
    SequenceGapError,
    SequenceRegressionError,
    TickAlignmentError,
    UnknownOrderError,
    UnsupportedBookModeError,
)
from sra_nexus.market_data.features import (
    calculate_microprice,
    calculate_midprice,
    calculate_spread,
    is_tick_aligned,
)
from sra_nexus.market_data.snapshots import BookSnapshot, PriceLevel
from sra_nexus.reference.models import Instrument

_VENUE_ADAPTER = TypeAdapter(NonBlankStr)
_SEQUENCE_STREAM_ID_ADAPTER = TypeAdapter(SequenceStreamId)


@dataclass(frozen=True, slots=True)
class OrderState:
    """Immutable remaining state for one active provider order identity."""

    order_id: MarketOrderId
    side: BookSide
    price: Decimal
    remaining_quantity: Decimal


class SequenceTracker:
    """Validate one monotonic sequence stream without silently repairing gaps."""

    def __init__(self) -> None:
        """Start before the first observed sequence number."""
        self._last_sequence: int | None = None

    @property
    def last_sequence(self) -> int | None:
        """Return the last committed sequence, if the stream has started."""
        return self._last_sequence

    def validate(self, sequence_number: int, *, is_reset: bool) -> None:
        """Validate without committing so failed stream events remain atomic."""
        previous = self._last_sequence
        if previous is None:
            return
        if sequence_number == previous:
            raise DuplicateSequenceError(f"duplicate sequence_number {sequence_number}")
        if sequence_number < previous:
            raise SequenceRegressionError(
                f"sequence regressed from {previous} to {sequence_number}"
            )
        expected = previous + 1
        if sequence_number != expected and not is_reset:
            raise SequenceGapError(
                f"expected sequence_number {expected}, received {sequence_number}"
            )

    def commit(self, sequence_number: int) -> None:
        """Commit a sequence only after its state transition succeeds."""
        self._last_sequence = sequence_number


class OrderBook:
    """MBO-first exact-arithmetic book with transactional state transitions."""

    def __init__(
        self,
        instrument: Instrument,
        venue: NonBlankStr | None = None,
        sequence_stream_id: SequenceStreamId | str | None = None,
    ) -> None:
        """Configure one instrument/venue and optionally bind its sequence domain."""
        self._instrument = instrument
        self._venue = (
            instrument.exchange if venue is None else _VENUE_ADAPTER.validate_python(venue)
        )
        self._sequence_stream_id = (
            None
            if sequence_stream_id is None
            else _SEQUENCE_STREAM_ID_ADAPTER.validate_python(sequence_stream_id)
        )
        self._orders: dict[MarketOrderId, OrderState] = {}
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._sequence = SequenceTracker()
        self._last_book_sequence: int | None = None
        self._last_exchange_time: datetime | None = None
        self._last_receive_time: datetime | None = None
        self._last_process_time: datetime | None = None

    @property
    def instrument_id(self) -> InstrumentId:
        """Return the stable instrument identity reconstructed by this book."""
        return self._instrument.instrument_id

    @property
    def venue(self) -> str:
        """Return the normalized venue stream identifier."""
        return self._venue

    @property
    def sequence_stream_id(self) -> SequenceStreamId | None:
        """Return the normalized sequence domain once configured or observed."""
        return self._sequence_stream_id

    @property
    def last_sequence(self) -> int | None:
        """Return the last sequence committed from the normalized event stream."""
        return self._sequence.last_sequence

    def get_order(self, order_id: MarketOrderId) -> OrderState | None:
        """Return immutable current state for an active order identity."""
        return self._orders.get(order_id)

    def apply(self, event: BookEvent) -> None:
        """Apply one valid MBO transition atomically or raise an explicit error."""
        self._validate_stream(event)
        if event.book_mode is not BookDataMode.MARKET_BY_ORDER:
            raise UnsupportedBookModeError(
                "MARKET_BY_PRICE reconstruction is intentionally deferred"
            )
        self._sequence.validate(
            event.sequence_number,
            is_reset=event.action is BookAction.RESET,
        )

        if event.action is BookAction.RESET:
            self._orders = {}
            self._bids = {}
            self._asks = {}
            self._commit_book_event(event)
            return

        side, price, order_id = _required_order_fields(event)
        if not is_tick_aligned(price, self._instrument.tick_size):
            raise TickAlignmentError(
                f"price {price} is not aligned to tick_size {self._instrument.tick_size}"
            )
        orders = dict(self._orders)
        bids = dict(self._bids)
        asks = dict(self._asks)
        if event.action is BookAction.ADD:
            quantity = _required_quantity(event)
            self._apply_add(orders, bids, asks, order_id, side, price, quantity)
        elif event.action is BookAction.MODIFY:
            quantity = _required_quantity(event)
            self._apply_modify(orders, bids, asks, order_id, side, price, quantity)
        elif event.action is BookAction.CANCEL:
            quantity = _required_quantity(event)
            self._apply_reduction(
                orders,
                bids,
                asks,
                order_id,
                side,
                price,
                quantity,
                transition="CANCEL",
            )
        elif event.action is BookAction.EXECUTE:
            quantity = _required_quantity(event)
            self._apply_reduction(
                orders,
                bids,
                asks,
                order_id,
                side,
                price,
                quantity,
                transition="EXECUTE",
            )
        else:
            self._apply_delete(orders, bids, asks, order_id, side, price)

        _validate_uncrossed(bids, asks)
        _validate_aggregate_consistency(orders, bids, asks)
        self._orders = orders
        self._bids = bids
        self._asks = asks
        self._commit_book_event(event)

    def observe_non_book_event(self, event: TradeEvent | QuoteEvent) -> None:
        """Advance a shared sequence domain without changing reconstructed book state."""
        self._validate_stream(event)
        self._sequence.validate(event.sequence_number, is_reset=False)
        self._commit_sequence(event)

    def snapshot(self) -> BookSnapshot:
        """Return state and clocks from the most recent accepted BookEvent."""
        sequence_number = self._last_book_sequence
        if (
            sequence_number is None
            or self._last_exchange_time is None
            or self._last_receive_time is None
            or self._last_process_time is None
        ):
            raise BookNotInitializedError("cannot snapshot before an accepted book event")
        bid_levels = _price_levels(self._bids, self._orders, BookSide.BID)
        ask_levels = _price_levels(self._asks, self._orders, BookSide.ASK)
        best_bid = None if not bid_levels else bid_levels[0].price
        best_ask = None if not ask_levels else ask_levels[0].price
        bid_quantity = None if not bid_levels else bid_levels[0].aggregate_quantity
        ask_quantity = None if not ask_levels else ask_levels[0].aggregate_quantity
        return BookSnapshot(
            instrument_id=self.instrument_id,
            venue=self.venue,
            exchange_time=self._last_exchange_time,
            receive_time=self._last_receive_time,
            process_time=self._last_process_time,
            sequence_number=sequence_number,
            bid_levels=bid_levels,
            ask_levels=ask_levels,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=calculate_spread(best_bid, best_ask),
            midprice=calculate_midprice(best_bid, best_ask),
            microprice=calculate_microprice(
                best_bid,
                bid_quantity,
                best_ask,
                ask_quantity,
            ),
        )

    def _validate_stream(self, event: MarketEvent) -> None:
        if event.instrument_id != self.instrument_id or event.venue != self.venue:
            raise BookStreamMismatchError("market event belongs to another instrument/venue")
        if (
            self._sequence_stream_id is not None
            and event.sequence_stream_id != self._sequence_stream_id
        ):
            raise BookStreamMismatchError("market event belongs to another sequence stream")

    def _commit_sequence(self, event: MarketEvent) -> None:
        if self._sequence_stream_id is None:
            self._sequence_stream_id = event.sequence_stream_id
        self._sequence.commit(event.sequence_number)

    def _commit_book_event(self, event: BookEvent) -> None:
        self._commit_sequence(event)
        self._last_book_sequence = event.sequence_number
        self._last_exchange_time = event.exchange_time
        self._last_receive_time = event.receive_time
        self._last_process_time = event.process_time

    @staticmethod
    def _apply_add(
        orders: dict[MarketOrderId, OrderState],
        bids: dict[Decimal, Decimal],
        asks: dict[Decimal, Decimal],
        order_id: MarketOrderId,
        side: BookSide,
        price: Decimal,
        quantity: Decimal,
    ) -> None:
        if order_id in orders:
            raise DuplicateOrderError(f"active order_id {order_id} already exists")
        orders[order_id] = OrderState(order_id, side, price, quantity)
        _adjust_level(bids if side is BookSide.BID else asks, price, quantity)

    @staticmethod
    def _apply_modify(
        orders: dict[MarketOrderId, OrderState],
        bids: dict[Decimal, Decimal],
        asks: dict[Decimal, Decimal],
        order_id: MarketOrderId,
        side: BookSide,
        price: Decimal,
        quantity: Decimal,
    ) -> None:
        current = _known_order(orders, order_id)
        if current.side is not side:
            raise OrderAttributeMismatchError("MODIFY cannot change an order's side")
        old_levels = bids if current.side is BookSide.BID else asks
        new_levels = bids if side is BookSide.BID else asks
        _adjust_level(old_levels, current.price, -current.remaining_quantity)
        _adjust_level(new_levels, price, quantity)
        orders[order_id] = OrderState(order_id, side, price, quantity)

    @staticmethod
    def _apply_reduction(
        orders: dict[MarketOrderId, OrderState],
        bids: dict[Decimal, Decimal],
        asks: dict[Decimal, Decimal],
        order_id: MarketOrderId,
        side: BookSide,
        price: Decimal,
        quantity: Decimal,
        *,
        transition: str,
    ) -> None:
        current = _known_order(orders, order_id)
        _require_matching_order(current, side, price, transition)
        if quantity > current.remaining_quantity:
            raise QuantityExceedsRemainingError(
                f"{transition} quantity {quantity} exceeds remaining {current.remaining_quantity}"
            )
        levels = bids if side is BookSide.BID else asks
        _adjust_level(levels, price, -quantity)
        remaining = current.remaining_quantity - quantity
        if remaining == 0:
            del orders[order_id]
        else:
            orders[order_id] = OrderState(order_id, side, price, remaining)

    @staticmethod
    def _apply_delete(
        orders: dict[MarketOrderId, OrderState],
        bids: dict[Decimal, Decimal],
        asks: dict[Decimal, Decimal],
        order_id: MarketOrderId,
        side: BookSide,
        price: Decimal,
    ) -> None:
        current = _known_order(orders, order_id)
        _require_matching_order(current, side, price, "DELETE")
        levels = bids if side is BookSide.BID else asks
        _adjust_level(levels, price, -current.remaining_quantity)
        del orders[order_id]


def _required_order_fields(
    event: BookEvent,
) -> tuple[BookSide, Decimal, MarketOrderId]:
    if event.side is None or event.price is None or event.order_id is None:
        raise ValueError("validated non-RESET MBO event is missing required fields")
    return event.side, event.price, event.order_id


def _required_quantity(event: BookEvent) -> Decimal:
    if event.quantity is None:
        raise ValueError("validated quantity-bearing event is missing quantity")
    return event.quantity


def _known_order(
    orders: dict[MarketOrderId, OrderState],
    order_id: MarketOrderId,
) -> OrderState:
    current = orders.get(order_id)
    if current is None:
        raise UnknownOrderError(f"unknown active order_id {order_id}")
    return current


def _require_matching_order(
    current: OrderState,
    side: BookSide,
    price: Decimal,
    transition: str,
) -> None:
    if current.side is not side or current.price != price:
        raise OrderAttributeMismatchError(
            f"{transition} side/price does not match active order state"
        )


def _adjust_level(
    levels: dict[Decimal, Decimal],
    price: Decimal,
    quantity_delta: Decimal,
) -> None:
    updated = levels.get(price, Decimal(0)) + quantity_delta
    if updated < 0:
        raise NegativeDepthError(f"price level {price} would become negative")
    if updated == 0:
        levels.pop(price, None)
    else:
        levels[price] = updated


def _validate_uncrossed(
    bids: dict[Decimal, Decimal],
    asks: dict[Decimal, Decimal],
) -> None:
    if bids and asks and max(bids) > min(asks):
        raise CrossedBookError("transition would create a crossed order book")


def _validate_aggregate_consistency(
    orders: dict[MarketOrderId, OrderState],
    bids: dict[Decimal, Decimal],
    asks: dict[Decimal, Decimal],
) -> None:
    expected_bids: dict[Decimal, Decimal] = {}
    expected_asks: dict[Decimal, Decimal] = {}
    for order in orders.values():
        _adjust_level(
            expected_bids if order.side is BookSide.BID else expected_asks,
            order.price,
            order.remaining_quantity,
        )
    if bids != expected_bids or asks != expected_asks:
        raise NegativeDepthError("aggregate levels disagree with active order state")


def _price_levels(
    quantities: dict[Decimal, Decimal],
    orders: dict[MarketOrderId, OrderState],
    side: BookSide,
) -> tuple[PriceLevel, ...]:
    prices = sorted(quantities, reverse=side is BookSide.BID)
    return tuple(
        PriceLevel(
            price=price,
            aggregate_quantity=quantities[price],
            order_count=sum(
                order.side is side and order.price == price for order in orders.values()
            ),
        )
        for price in prices
    )
