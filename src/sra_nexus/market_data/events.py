"""Immutable provider-neutral book, trade, and quote event contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import (
    BookEventId,
    InstrumentId,
    MarketOrderId,
    MarketTradeId,
    QuoteEventId,
    TradeEventId,
    new_book_event_id,
    new_quote_event_id,
    new_trade_event_id,
)
from sra_nexus.market_data.enums import (
    AggressorSide,
    BookAction,
    BookDataMode,
    BookSide,
    MarketEventFlag,
    MarketEventKind,
)


class MarketTimedEvent(ContractModel):
    """Shared causal timeline and stream identity for external market data."""

    instrument_id: InstrumentId
    venue: NonBlankStr
    exchange_time: UtcDatetime = Field(description="UTC timestamp assigned by the venue.")
    receive_time: UtcDatetime = Field(description="UTC timestamp received by SRA-Nexus.")
    process_time: UtcDatetime = Field(description="UTC timestamp usable downstream.")
    sequence_number: int = Field(ge=0)
    flags: tuple[MarketEventFlag, ...] = ()

    @field_validator("flags", mode="after")
    @classmethod
    def deduplicate_flags(
        cls,
        value: tuple[MarketEventFlag, ...],
    ) -> tuple[MarketEventFlag, ...]:
        """Retain each normalized flag once in stable input order."""
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        """Apply the initial deterministic single-clock causal assumption."""
        if self.exchange_time > self.receive_time:
            raise ValueError("exchange_time must not be after receive_time")
        if self.receive_time > self.process_time:
            raise ValueError("receive_time must not be after process_time")
        return self


class BookEvent(MarketTimedEvent):
    """One immutable order-book transition in MBO or explicitly labeled MBP form."""

    event_kind: Literal[MarketEventKind.BOOK] = MarketEventKind.BOOK
    event_id: BookEventId = Field(default_factory=new_book_event_id)
    action: BookAction
    side: BookSide | None = None
    price: PositiveDecimal | None = None
    quantity: NonNegativeDecimal | None = None
    order_id: MarketOrderId | None = None
    trade_id: MarketTradeId | None = None
    book_mode: BookDataMode = BookDataMode.MARKET_BY_ORDER

    @model_validator(mode="after")
    def validate_action_shape(self) -> Self:
        """Require explicit non-ambiguous values for each transition semantic."""
        if self.action is BookAction.RESET:
            if any(
                value is not None
                for value in (self.side, self.price, self.quantity, self.order_id, self.trade_id)
            ):
                raise ValueError(
                    "RESET cannot contain side, price, quantity, order_id, or trade_id"
                )
            return self

        if self.side is None or self.price is None:
            raise ValueError("non-RESET book events require side and price")
        if self.book_mode is BookDataMode.MARKET_BY_ORDER and self.order_id is None:
            raise ValueError("MBO book events require order_id")
        if self.book_mode is BookDataMode.MARKET_BY_PRICE and self.order_id is not None:
            raise ValueError("MBP book events cannot contain order_id")

        if self.action in {
            BookAction.ADD,
            BookAction.MODIFY,
            BookAction.CANCEL,
            BookAction.EXECUTE,
        } and (self.quantity is None or self.quantity <= 0):
            raise ValueError(f"{self.action.value} requires positive quantity")
        if self.action is BookAction.DELETE and self.quantity is not None:
            raise ValueError("DELETE removes all remaining quantity and requires quantity=None")
        if self.action is not BookAction.EXECUTE and self.trade_id is not None:
            raise ValueError("trade_id is only valid on EXECUTE")
        return self


class TradeEvent(MarketTimedEvent):
    """One immutable executed-trade observation with non-inferred aggressor side."""

    event_kind: Literal[MarketEventKind.TRADE] = MarketEventKind.TRADE
    trade_event_id: TradeEventId = Field(default_factory=new_trade_event_id)
    trade_id: MarketTradeId
    price: PositiveDecimal
    quantity: PositiveDecimal
    aggressor_side: AggressorSide = AggressorSide.UNKNOWN


class QuoteEvent(MarketTimedEvent):
    """One immutable top-of-book observation; locked quotes are allowed, crossed are not."""

    event_kind: Literal[MarketEventKind.QUOTE] = MarketEventKind.QUOTE
    quote_event_id: QuoteEventId = Field(default_factory=new_quote_event_id)
    bid_price: PositiveDecimal
    bid_quantity: NonNegativeDecimal
    ask_price: PositiveDecimal
    ask_quantity: NonNegativeDecimal

    @model_validator(mode="after")
    def reject_crossed_quote(self) -> Self:
        """Allow locked markets but reject rather than repair crossed quotes."""
        if self.bid_price > self.ask_price:
            raise ValueError("crossed quote has bid_price greater than ask_price")
        return self


type MarketEvent = BookEvent | TradeEvent | QuoteEvent
