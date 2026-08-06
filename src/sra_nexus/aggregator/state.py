"""Instrument-level news-state data contract."""

from __future__ import annotations

from pydantic import Field, model_validator

from sra_nexus.aggregator.enums import ExposurePath
from sra_nexus.aggregator.events import EventExposure
from sra_nexus.common.models import (
    ContractModel,
    NonNegativeFiniteFloat,
    UnitIntervalScore,
    UtcDatetime,
)
from sra_nexus.common.types import EventId, InstrumentId


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
    active_event_ids: tuple[EventId, ...] = ()
    direct_event_exposures: tuple[EventExposure, ...] = ()
    indirect_event_exposures: tuple[EventExposure, ...] = ()

    @model_validator(mode="after")
    def validate_state_references(self) -> NewsState:
        """Reject future, cross-instrument, or misclassified exposures."""
        if len(self.active_event_ids) != len(set(self.active_event_ids)):
            raise ValueError("active_event_ids must be unique")

        all_exposures = self.direct_event_exposures + self.indirect_event_exposures
        exposure_ids = [exposure.exposure_id for exposure in all_exposures]
        if len(exposure_ids) != len(set(exposure_ids)):
            raise ValueError("event exposures must be unique")

        active_event_ids = set(self.active_event_ids)
        for exposure in self.direct_event_exposures:
            self._validate_exposure(exposure, ExposurePath.DIRECT, active_event_ids)
        for exposure in self.indirect_event_exposures:
            self._validate_exposure(exposure, ExposurePath.INDIRECT, active_event_ids)
        return self

    def _validate_exposure(
        self,
        exposure: EventExposure,
        expected_path: ExposurePath,
        active_event_ids: set[EventId],
    ) -> None:
        if exposure.instrument_id != self.instrument_id:
            raise ValueError("event exposure instrument_id must match NewsState")
        if exposure.exposure_path is not expected_path:
            raise ValueError(
                f"{expected_path.value.lower()} exposures have the wrong exposure_path"
            )
        if exposure.event_id not in active_event_ids:
            raise ValueError("event exposure must reference an active event")
        if exposure.process_time > self.as_of:
            raise ValueError("NewsState cannot contain exposure unavailable at as_of")
