"""Raw and derived research-data persistence interfaces."""

from sra_nexus.storage.canonical import (
    CanonicalEventAlreadyExistsError,
    CanonicalEventNotFoundError,
    CanonicalEventRepository,
    CanonicalEventRepositoryError,
    CanonicalRevisionConflictError,
    NewsAlreadyCanonicalizedError,
)
from sra_nexus.storage.raw import (
    RawNewsInsertResult,
    RawNewsInsertStatus,
    RawNewsRepository,
)
from sra_nexus.storage.sqlite import SQLiteRawNewsRepository
from sra_nexus.storage.sqlite_canonical import SQLiteCanonicalEventRepository

__all__ = [
    "CanonicalEventAlreadyExistsError",
    "CanonicalEventNotFoundError",
    "CanonicalEventRepository",
    "CanonicalEventRepositoryError",
    "CanonicalRevisionConflictError",
    "NewsAlreadyCanonicalizedError",
    "RawNewsInsertResult",
    "RawNewsInsertStatus",
    "RawNewsRepository",
    "SQLiteCanonicalEventRepository",
    "SQLiteRawNewsRepository",
]
