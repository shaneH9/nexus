"""Provider-independent and fixture-backed news source adapters."""

from sra_nexus.aggregator.sources.base import (
    NewsSource,
    NewsSourceBatch,
    SourceRecordFailure,
    SourceRecordFailureType,
)
from sra_nexus.aggregator.sources.mock import MockNewsSource, MockNewsSourceFormatError

__all__ = [
    "MockNewsSource",
    "MockNewsSourceFormatError",
    "NewsSource",
    "NewsSourceBatch",
    "SourceRecordFailure",
    "SourceRecordFailureType",
]
