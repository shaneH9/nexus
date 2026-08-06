"""Canonical event and instrument-exposure contracts."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from sra_nexus.aggregator.enums import EventState, EventType, ExposureRelationType
from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    SignedUnitScore,
    UnitIntervalScore,
    UtcDatetime,
)
from sra_nexus.common.types import (
    CanonicalEventId,
    EntityId,
    InstrumentId,
    NewsId,
    new_canonical_event_id,
)


class CanonicalEvent(ContractModel):
    """Provider-neutral representation of one real-world event."""

    event_id: CanonicalEventId = Field(default_factory=new_canonical_event_id)
    first_event_time: UtcDatetime = Field(
        description="Earliest UTC source occurrence or publication time."
    )
    first_receive_time: UtcDatetime = Field(
        description="Earliest UTC time SRA-Nexus received a supporting item."
    )
    last_update_time: UtcDatetime = Field(
        description="UTC time this canonical representation was last updated."
    )
    event_type: EventType
    event_subtype: NonBlankStr | None = None
    headline_summary: NonBlankStr
    event_summary: str | None = None
    source_news_ids: tuple[NewsId, ...] = Field(min_length=1)
    entity_ids: tuple[EntityId, ...] = ()
    instrument_ids: tuple[InstrumentId, ...] = ()
    sectors: tuple[NonBlankStr, ...] = ()
    industries: tuple[NonBlankStr, ...] = ()
    countries: tuple[NonBlankStr, ...] = ()
    commodities: tuple[NonBlankStr, ...] = ()
    macro_factors: tuple[NonBlankStr, ...] = ()
    sentiment: SignedUnitScore | None = Field(
        default=None, description="Directional sentiment from -1 (negative) to +1 (positive)."
    )
    surprise: float | None = Field(
        default=None,
        description="Optional finite standardized surprise in standard-deviation units.",
    )
    novelty: UnitIntervalScore | None = Field(default=None, description="Novelty score in [0, 1].")
    severity: UnitIntervalScore | None = Field(
        default=None,
        description="Expected economic significance in [0, 1].",
    )
    relevance: UnitIntervalScore | None = Field(
        default=None,
        description="Broad event relevance in [0, 1].",
    )
    confidence: UnitIntervalScore | None = Field(
        default=None,
        description="Event construction confidence in [0, 1].",
    )
    credibility: UnitIntervalScore | None = Field(
        default=None, description="Underlying information reliability in [0, 1]."
    )
    expected_duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Expected active duration in seconds; must be non-negative.",
    )
    event_state: EventState

    @field_validator(
        "source_news_ids",
        "entity_ids",
        "instrument_ids",
        "sectors",
        "industries",
        "countries",
        "commodities",
        "macro_factors",
        mode="after",
    )
    @classmethod
    def deduplicate_collections(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        """Remove repeated values while retaining deterministic input order."""
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_timeline(self) -> CanonicalEvent:
        """Reject canonical timelines that violate causal availability order."""
        if self.first_event_time > self.first_receive_time:
            raise ValueError("first_event_time must not be after first_receive_time")
        if self.first_receive_time > self.last_update_time:
            raise ValueError("first_receive_time must not be after last_update_time")
        return self


class EventExposure(ContractModel):
    """Directional relationship between one canonical event and instrument."""

    event_id: CanonicalEventId
    instrument_id: InstrumentId
    relation_type: ExposureRelationType
    direction: SignedUnitScore = Field(
        description="Dimensionless direction from -1 (negative) to +1 (positive)."
    )
    magnitude: UnitIntervalScore = Field(
        description="Dimensionless relationship strength in [0, 1]."
    )
    relevance: UnitIntervalScore = Field(
        description="Instrument-specific event relevance in [0, 1]."
    )
    confidence: UnitIntervalScore = Field(description="Exposure-mapping confidence in [0, 1].")
    is_direct: bool

    @property
    def exposure(self) -> float:
        """Return dimensionless signed exposure in the closed interval [-1, 1]."""
        return self.direction * self.magnitude
