"""Canonical event and instrument-exposure contracts."""

from __future__ import annotations

from datetime import timedelta

from pydantic import Field, model_validator

from sra_nexus.aggregator.enums import EventState, EventType, ExposurePath, ExposureRelationship
from sra_nexus.common.models import (
    CountryCode,
    EventSubtype,
    NonBlankStr,
    SignedUnitScore,
    TimedEventModel,
    UnitIntervalScore,
)
from sra_nexus.common.types import (
    EntityId,
    EventId,
    ExposureId,
    InstrumentId,
    NewsId,
    new_event_id,
    new_exposure_id,
)


class CanonicalEvent(TimedEventModel):
    """Provider-neutral representation of one real-world event."""

    event_id: EventId = Field(default_factory=new_event_id)
    event_type: EventType
    event_subtype: EventSubtype | None = None
    headline_summary: NonBlankStr
    event_summary: NonBlankStr
    source_news_ids: tuple[NewsId, ...] = Field(min_length=1)
    entity_ids: tuple[EntityId, ...] = ()
    instrument_ids: tuple[InstrumentId, ...] = ()
    sectors: tuple[NonBlankStr, ...] = ()
    industries: tuple[NonBlankStr, ...] = ()
    countries: tuple[CountryCode, ...] = ()
    commodities: tuple[NonBlankStr, ...] = ()
    macro_factors: tuple[NonBlankStr, ...] = ()
    sentiment: SignedUnitScore = Field(
        description="Directional sentiment from -1 (negative) to +1 (positive)."
    )
    surprise: float | None = Field(
        default=None,
        description="Optional finite standardized surprise in standard-deviation units.",
    )
    novelty: UnitIntervalScore = Field(description="Novelty score in [0, 1].")
    severity: UnitIntervalScore = Field(description="Expected economic significance in [0, 1].")
    relevance: UnitIntervalScore = Field(description="Broad event relevance in [0, 1].")
    confidence: UnitIntervalScore = Field(description="Event construction confidence in [0, 1].")
    credibility: UnitIntervalScore = Field(
        description="Underlying information reliability in [0, 1]."
    )
    expected_duration: timedelta | None = Field(
        default=None,
        gt=timedelta(0),
        description="Expected active duration; must be positive when supplied.",
    )
    event_state: EventState

    @model_validator(mode="after")
    def validate_canonical_references(self) -> CanonicalEvent:
        """Reject inconsistent subtype and duplicate canonical references."""
        if self.event_subtype is not None:
            subtype_category = self.event_subtype.partition(".")[0]
            if subtype_category != self.event_type.value:
                raise ValueError("event_subtype prefix must match event_type")

        collections = {
            "source_news_ids": self.source_news_ids,
            "entity_ids": self.entity_ids,
            "instrument_ids": self.instrument_ids,
            "sectors": self.sectors,
            "industries": self.industries,
            "countries": self.countries,
            "commodities": self.commodities,
            "macro_factors": self.macro_factors,
        }
        for field_name, values in collections.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique values")
        return self


class EventExposure(TimedEventModel):
    """Directional relationship between one canonical event and instrument."""

    exposure_id: ExposureId = Field(default_factory=new_exposure_id)
    event_id: EventId
    instrument_id: InstrumentId
    entity_id: EntityId | None = None
    relationship: ExposureRelationship
    exposure_path: ExposurePath
    directional_exposure: SignedUnitScore = Field(
        description="Dimensionless direction from -1 (negative) to +1 (positive)."
    )
    relationship_magnitude: UnitIntervalScore = Field(
        description="Dimensionless relationship strength in [0, 1]."
    )
    relevance: UnitIntervalScore = Field(
        description="Instrument-specific event relevance in [0, 1]."
    )
    confidence: UnitIntervalScore = Field(description="Exposure-mapping confidence in [0, 1].")

    @property
    def net_exposure(self) -> float:
        """Return dimensionless signed exposure in the closed interval [-1, 1]."""
        return self.directional_exposure * self.relationship_magnitude
