"""Provider-independent contracts for raw news sources."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sra_nexus.aggregator.raw import RawNewsItem


class SourceRecordFailureType(StrEnum):
    """Stable classifications for provider-record conversion failures."""

    RECORD_TYPE = "RECORD_TYPE"
    VALIDATION = "VALIDATION"


@dataclass(frozen=True, slots=True)
class SourceRecordFailure:
    """Validation failure for one provider record within a fetched batch."""

    record_index: int
    provider_reference: str | None
    error_type: SourceRecordFailureType
    message: str


@dataclass(frozen=True, slots=True)
class NewsSourceBatch:
    """Validated source items and independent per-record failures."""

    items: tuple[RawNewsItem, ...]
    failures: tuple[SourceRecordFailure, ...] = ()

    @property
    def received_count(self) -> int:
        """Return the number of provider records represented by this batch."""
        return len(self.items) + len(self.failures)


class NewsSource(Protocol):
    """Provider boundary that emits provider-neutral raw-news observations."""

    def fetch(self) -> NewsSourceBatch:
        """Fetch and validate one deterministic batch of source records."""
        ...
