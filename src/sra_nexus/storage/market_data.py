"""Provider-independent immutable raw market-event persistence contract."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel, NonBlankStr, UtcDatetime
from sra_nexus.common.types import (
    BookEventId,
    InstrumentId,
    QuoteEventId,
    SequenceStreamId,
    TradeEventId,
)
from sra_nexus.market_data.events import MarketEvent

type MarketEventId = BookEventId | TradeEventId | QuoteEventId


class MarketEventInsertStatus(StrEnum):
    """Explicit outcome for append-only raw market-event insertion."""

    INSERTED = "INSERTED"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    CONFLICTING_EVENT_ID = "CONFLICTING_EVENT_ID"
    CONFLICTING_SEQUENCE = "CONFLICTING_SEQUENCE"


@dataclass(frozen=True, slots=True)
class MarketEventInsertResult:
    """Typed immutable insert result without overwriting existing observations."""

    status: MarketEventInsertStatus
    incoming_event_id: MarketEventId
    existing_event_id: MarketEventId | None = None

    @property
    def inserted(self) -> bool:
        """Return whether the raw event was newly stored."""
        return self.status is MarketEventInsertStatus.INSERTED


class MarketEventQuery(ContractModel):
    """Indexed range and historical cutoff for one explicit sequence domain."""

    instrument_id: InstrumentId
    venue: NonBlankStr
    sequence_stream_id: SequenceStreamId
    start_sequence: int | None = Field(default=None, ge=0)
    end_sequence: int | None = Field(default=None, ge=0)
    as_of: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        """Require an inclusive range whose lower bound does not exceed its upper."""
        if (
            self.start_sequence is not None
            and self.end_sequence is not None
            and self.start_sequence > self.end_sequence
        ):
            raise ValueError("start_sequence must not exceed end_sequence")
        return self


class RawMarketEventRepository(Protocol):
    """Storage boundary for immutable normalized raw market observations."""

    def insert(self, event: MarketEvent) -> MarketEventInsertResult:
        """Insert once or return an explicit duplicate/conflict result."""
        ...

    def get(self, event_id: MarketEventId) -> MarketEvent | None:
        """Return one event by stable internal identity."""
        ...

    def list_stream(self, query: MarketEventQuery) -> tuple[MarketEvent, ...]:
        """Return one stream in deterministic sequence order."""
        ...

    def list_for_instrument(
        self,
        instrument_id: InstrumentId,
        as_of: UtcDatetime | None = None,
    ) -> tuple[MarketEvent, ...]:
        """Return all indexed instrument streams in canonical deterministic order."""
        ...
