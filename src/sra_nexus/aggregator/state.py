"""Instrument-level news-state data contract."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from sra_nexus.aggregator.events import EventExposure
from sra_nexus.common.models import (
    ContractModel,
    NonNegativeFiniteFloat,
    UnitIntervalScore,
    UtcDatetime,
)
from sra_nexus.common.types import CanonicalEventId, InstrumentId


class NewsState(ContractModel):
    """Reproducible information state for one instrument at a UTC cutoff."""

    instrument_id: InstrumentId
    as_of: UtcDatetime = Field(description="UTC information cutoff represented by this state.")
    positive_event_intensity: NonNegativeFiniteFloat = 0.0
    negative_event_intensity: NonNegativeFiniteFloat = 0.0
    company_event_risk: UnitIntervalScore = 0.0
    sector_event_risk: UnitIntervalScore = 0.0
    macro_event_risk: UnitIntervalScore = 0.0
    geopolitical_event_risk: UnitIntervalScore = 0.0
    regulatory_event_risk: UnitIntervalScore = 0.0
    systemic_event_risk: UnitIntervalScore = 0.0
    news_volume: int = Field(
        default=0,
        ge=0,
        description="Count of source news items in the state's configured lookback window.",
    )
    news_acceleration: float = Field(
        default=0.0,
        description="Change in news arrival rate, measured in items per minute squared.",
    )
    novelty_intensity: UnitIntervalScore = 0.0
    uncertainty: UnitIntervalScore = Field(
        default=0.0,
        description="Conflicting or unresolved information score in [0, 1].",
    )
    confidence: UnitIntervalScore = Field(
        default=0.0,
        description="Aggregate state confidence in [0, 1].",
    )
    active_event_ids: tuple[CanonicalEventId, ...] = ()
    direct_event_exposures: tuple[EventExposure, ...] = ()
    indirect_event_exposures: tuple[EventExposure, ...] = ()

    @field_validator("active_event_ids", mode="after")
    @classmethod
    def deduplicate_active_events(
        cls, value: tuple[CanonicalEventId, ...]
    ) -> tuple[CanonicalEventId, ...]:
        """Remove duplicate active events while preserving their order."""
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_exposure_groups(self) -> NewsState:
        """Reject cross-instrument or incorrectly classified exposures."""
        for exposure in self.direct_event_exposures:
            self._validate_exposure(exposure, expected_direct=True)
        for exposure in self.indirect_event_exposures:
            self._validate_exposure(exposure, expected_direct=False)
        return self

    def _validate_exposure(self, exposure: EventExposure, *, expected_direct: bool) -> None:
        if exposure.instrument_id != self.instrument_id:
            raise ValueError("event exposure instrument_id must match NewsState")
        if exposure.is_direct is not expected_direct:
            group = "direct" if expected_direct else "indirect"
            raise ValueError(f"{group} exposures have the wrong is_direct value")
