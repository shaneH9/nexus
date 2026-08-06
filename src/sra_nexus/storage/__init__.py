"""Raw and derived research-data persistence interfaces."""

from sra_nexus.storage.canonical import (
    CanonicalEventAlreadyExistsError,
    CanonicalEventNotFoundError,
    CanonicalEventRepository,
    CanonicalEventRepositoryError,
    CanonicalRevisionConflictError,
    NewsAlreadyCanonicalizedError,
)
from sra_nexus.storage.event_graph import (
    EventEntityLinkRepository,
    EventExposureRepository,
    EventGraphRepositoryError,
    EventGraphRevisionConflictError,
)
from sra_nexus.storage.raw import (
    RawNewsInsertResult,
    RawNewsInsertStatus,
    RawNewsRepository,
)
from sra_nexus.storage.sqlite import SQLiteRawNewsRepository
from sra_nexus.storage.sqlite_canonical import SQLiteCanonicalEventRepository
from sra_nexus.storage.sqlite_event_graph import SQLiteEventGraphRepository
from sra_nexus.storage.sqlite_reference import SQLiteReferenceRepository

__all__ = [
    "CanonicalEventAlreadyExistsError",
    "CanonicalEventNotFoundError",
    "CanonicalEventRepository",
    "CanonicalEventRepositoryError",
    "CanonicalRevisionConflictError",
    "NewsAlreadyCanonicalizedError",
    "EventEntityLinkRepository",
    "EventExposureRepository",
    "EventGraphRepositoryError",
    "EventGraphRevisionConflictError",
    "RawNewsInsertResult",
    "RawNewsInsertStatus",
    "RawNewsRepository",
    "SQLiteCanonicalEventRepository",
    "SQLiteEventGraphRepository",
    "SQLiteRawNewsRepository",
    "SQLiteReferenceRepository",
]
