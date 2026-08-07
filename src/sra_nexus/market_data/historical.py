"""Provider-independent contracts for strict historical market-data normalization."""

from __future__ import annotations

from datetime import date, time
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    NonNegativeFiniteFloat,
    UtcDatetime,
)
from sra_nexus.common.types import InstrumentId
from sra_nexus.market_data.events import MarketEvent
from sra_nexus.reference.models import Instrument

HISTORICAL_MANIFEST_VERSION = "historical-data-manifest-v1"
HISTORICAL_QUALITY_VERSION = "historical-data-quality-v1"


class HistoricalSessionSegment(StrEnum):
    """Configured exchange-local segment, never inferred from machine timezone."""

    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"


class HistoricalBoundaryKind(StrEnum):
    """Structural boundaries that prevent continuous SRA windows."""

    SESSION = "SESSION"
    RESET = "RESET"
    HALT = "HALT"
    CORPORATE_ACTION = "CORPORATE_ACTION"


class HistoricalProcessTimePolicy(StrEnum):
    """Deterministic policy used when files have no original process clock."""

    RECEIVE_TIME = "RECEIVE_TIME"
    EXCHANGE_TIME_PLUS_OFFSET = "EXCHANGE_TIME_PLUS_OFFSET"


class HistoricalFileIdentity(ContractModel):
    """Immutable cryptographic identity for one source file."""

    source_filename: NonBlankStr
    sha256: NonBlankStr = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)


class HistoricalInstrumentMapping(ContractModel):
    """Explicit provider identity to canonical Instrument mapping."""

    provider_instrument_id: int = Field(ge=0)
    publisher_id: int = Field(ge=0)
    provider_symbol: NonBlankStr
    venue: NonBlankStr
    instrument: Instrument
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        """Require venue agreement and a valid half-open effective interval."""
        if self.instrument.exchange != self.venue:
            raise ValueError("instrument exchange must match historical venue mapping")
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from >= self.valid_to:
                raise ValueError("mapping valid_from must precede valid_to")
        return self

    def is_valid_at(self, timestamp: UtcDatetime) -> bool:
        """Return whether this mapping applies under half-open validity semantics."""
        if self.valid_from is not None and timestamp < self.valid_from:
            return False
        return self.valid_to is None or timestamp < self.valid_to


class HistoricalSessionPolicy(ContractModel):
    """Explicit U.S.-equities-style segment policy in a named exchange timezone."""

    exchange_timezone: NonBlankStr = "America/New_York"
    premarket_start: time = time(4, 0)
    regular_start: time = time(9, 30)
    regular_end: time = time(16, 0)
    after_hours_end: time = time(20, 0)

    @model_validator(mode="after")
    def validate_boundaries(self) -> Self:
        """Require strictly increasing same-day local segment boundaries."""
        if not (
            self.premarket_start < self.regular_start < self.regular_end < self.after_hours_end
        ):
            raise ValueError("historical session boundaries must be strictly increasing")
        return self


class HistoricalNormalizedEvent(ContractModel):
    """One canonical market event plus noncanonical historical provenance."""

    event: MarketEvent
    source_sha256: NonBlankStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_line_numbers: tuple[int, ...]
    provider_sequence: int = Field(ge=0)
    session_date: date
    session_segment: HistoricalSessionSegment
    session_id: NonBlankStr
    boundary_before: HistoricalBoundaryKind | None = None
    process_time_synthetic: bool
    is_recovery_snapshot: bool = False

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        """Require stable positive source lines and matching session identity."""
        if not self.source_line_numbers or any(value <= 1 for value in self.source_line_numbers):
            raise ValueError("historical source lines must identify CSV data rows")
        if self.source_line_numbers != tuple(sorted(set(self.source_line_numbers))):
            raise ValueError("historical source line numbers must be unique and sorted")
        return self


class HistoricalFileInspection(ContractModel):
    """Read-only pre-flight findings for one provider file."""

    file_identity: HistoricalFileIdentity
    provider: NonBlankStr
    detected_schema: NonBlankStr | None
    raw_record_count: int = Field(ge=0)
    normalized_event_estimate: int = Field(ge=0)
    provider_instrument_ids: tuple[int, ...]
    raw_symbols: tuple[NonBlankStr, ...]
    venues: tuple[NonBlankStr, ...]
    start_exchange_time: UtcDatetime | None
    end_exchange_time: UtcDatetime | None
    minimum_provider_sequence: int | None = Field(default=None, ge=0)
    maximum_provider_sequence: int | None = Field(default=None, ge=0)
    event_actions: tuple[NonBlankStr, ...]
    book_mode: NonBlankStr
    missing_fields: tuple[NonBlankStr, ...]
    malformed_records: tuple[NonBlankStr, ...]
    timestamp_ordering_problems: tuple[NonBlankStr, ...]
    sequence_gaps: tuple[NonBlankStr, ...]
    sequence_regressions: tuple[NonBlankStr, ...]
    duplicate_records: tuple[NonBlankStr, ...]
    unsupported_records: tuple[NonBlankStr, ...]

    @property
    def has_fatal_issues(self) -> bool:
        """Return whether strict normalization must refuse this file."""
        return any(
            (
                self.missing_fields,
                self.malformed_records,
                self.timestamp_ordering_problems,
                self.sequence_regressions,
                self.duplicate_records,
                self.unsupported_records,
            )
        )


class HistoricalDataManifest(ContractModel):
    """Exact normalized source identity and coverage for one research run."""

    provider: NonBlankStr
    provider_schema_version: NonBlankStr
    source_files: tuple[HistoricalFileIdentity, ...]
    instruments: tuple[InstrumentId, ...]
    venues: tuple[NonBlankStr, ...]
    start_exchange_time: UtcDatetime
    end_exchange_time: UtcDatetime
    event_count: int = Field(ge=0)
    book_event_count: int = Field(ge=0)
    trade_event_count: int = Field(ge=0)
    quote_event_count: int = Field(ge=0)
    normalization_version: NonBlankStr
    process_time_policy: HistoricalProcessTimePolicy
    synthetic_process_time_used: bool
    instrument_mappings: tuple[HistoricalInstrumentMapping, ...]
    created_at: UtcDatetime
    manifest_version: NonBlankStr = HISTORICAL_MANIFEST_VERSION

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        """Require exact variant totals, canonical collections, and coverage."""
        if self.event_count != (
            self.book_event_count + self.trade_event_count + self.quote_event_count
        ):
            raise ValueError("historical event count must equal variant counts")
        if self.end_exchange_time < self.start_exchange_time:
            raise ValueError("historical manifest time coverage cannot regress")
        if self.venues != tuple(sorted(set(self.venues))):
            raise ValueError("historical manifest venues must be sorted and unique")
        if self.instruments != tuple(sorted(set(self.instruments), key=str)):
            raise ValueError("historical manifest instruments must be sorted and unique")
        return self


class HistoricalDataQualityReport(ContractModel):
    """Visible data-quality and downstream-availability diagnostics."""

    source_files: tuple[HistoricalFileIdentity, ...]
    sequence_gaps: tuple[NonBlankStr, ...] = ()
    sequence_regressions: tuple[NonBlankStr, ...] = ()
    duplicate_records: tuple[NonBlankStr, ...] = ()
    reset_count: int = Field(default=0, ge=0)
    structural_break_count: int = Field(default=0, ge=0)
    invalid_records: tuple[NonBlankStr, ...] = ()
    one_sided_book_periods: int = Field(default=0, ge=0)
    missing_aggressor_side_count: int = Field(default=0, ge=0)
    reconciled_trade_observation_count: int = Field(default=0, ge=0)
    aggression_episode_count: int = Field(default=0, ge=0)
    mean_observations_per_aggression_episode: NonNegativeFiniteFloat = 0.0
    maximum_observations_per_aggression_episode: int = Field(default=0, ge=0)
    directional_flow_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    unknown_flow_share: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_feature_count: int = Field(default=0, ge=0)
    missing_feature_share: float = Field(default=0.0, ge=0.0, le=1.0)
    unavailable_label_count: int = Field(default=0, ge=0)
    total_label_count: int = Field(default=0, ge=0)
    unavailable_label_share: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: tuple[NonBlankStr, ...] = ()
    quality_version: NonBlankStr = HISTORICAL_QUALITY_VERSION


def historical_source_paths(paths: tuple[str, ...]) -> tuple[Path, ...]:
    """Resolve explicit source paths without discovering or scraping remote data."""
    return tuple(Path(value).expanduser().resolve() for value in paths)
