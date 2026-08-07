"""Shared immutable event and snapshot references for SRA research."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import model_validator

from sra_nexus.common.models import ContractModel, NonBlankStr, UtcDatetime
from sra_nexus.common.types import InstrumentId, SequenceStreamId
from sra_nexus.market_data.enums import MarketEventKind
from sra_nexus.market_data.events import BookEvent, MarketEvent, QuoteEvent, TradeEvent
from sra_nexus.market_data.snapshots import BookSnapshot


class MarketEventReference(ContractModel):
    """Stable reference to one normalized market event and its three clocks."""

    instrument_id: InstrumentId
    venue: NonBlankStr
    sequence_stream_id: SequenceStreamId
    sequence_number: int
    event_kind: MarketEventKind
    event_id: UUID
    exchange_time: UtcDatetime
    receive_time: UtcDatetime
    process_time: UtcDatetime

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        """Preserve causal clock ordering on copied event references."""
        if self.sequence_number < 0:
            raise ValueError("sequence_number must be non-negative")
        if self.exchange_time > self.receive_time:
            raise ValueError("exchange_time must not be after receive_time")
        if self.receive_time > self.process_time:
            raise ValueError("receive_time must not be after process_time")
        return self


class SnapshotReference(ContractModel):
    """Stable reference to a reconstructed state after one book mutation."""

    instrument_id: InstrumentId
    venue: NonBlankStr
    sequence_number: int
    exchange_time: UtcDatetime
    receive_time: UtcDatetime
    process_time: UtcDatetime


class MarketStateObservation(ContractModel):
    """Current reconstructed book state observed after one normalized market event."""

    event_reference: MarketEventReference
    snapshot: BookSnapshot

    @model_validator(mode="after")
    def validate_stream_identity(self) -> Self:
        """Prevent response calculations from combining different instruments or venues."""
        reference = self.event_reference
        if reference.instrument_id != self.snapshot.instrument_id:
            raise ValueError("event reference and snapshot instrument_id must match")
        if reference.venue != self.snapshot.venue:
            raise ValueError("event reference and snapshot venue must match")
        return self


def market_event_reference(event: MarketEvent) -> MarketEventReference:
    """Copy provider-neutral identity and clocks from a normalized market event."""
    if isinstance(event, BookEvent):
        event_id = event.event_id.root
    elif isinstance(event, TradeEvent):
        event_id = event.trade_event_id.root
    elif isinstance(event, QuoteEvent):
        event_id = event.quote_event_id.root
    else:
        raise TypeError("unsupported normalized market event")
    return MarketEventReference(
        instrument_id=event.instrument_id,
        venue=event.venue,
        sequence_stream_id=event.sequence_stream_id,
        sequence_number=event.sequence_number,
        event_kind=event.event_kind,
        event_id=event_id,
        exchange_time=event.exchange_time,
        receive_time=event.receive_time,
        process_time=event.process_time,
    )


def snapshot_reference(snapshot: BookSnapshot) -> SnapshotReference:
    """Copy immutable identity and clocks from a reconstructed snapshot."""
    return SnapshotReference(
        instrument_id=snapshot.instrument_id,
        venue=snapshot.venue,
        sequence_number=snapshot.sequence_number,
        exchange_time=snapshot.exchange_time,
        receive_time=snapshot.receive_time,
        process_time=snapshot.process_time,
    )


def elapsed_decimal_seconds(start: datetime, end: datetime) -> Decimal:
    """Return exact elapsed seconds, including microseconds, and reject regression."""
    if end < start:
        raise ValueError("elapsed-time endpoint must not precede its origin")
    delta = end - start
    return Decimal(delta.days * 86_400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(
        1_000_000
    )
