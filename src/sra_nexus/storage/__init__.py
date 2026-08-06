"""Raw and derived research-data persistence interfaces.

Exports are loaded lazily so importing one storage submodule does not initialize
unrelated aggregator and reference repositories.  This keeps the market-data
storage boundary independent while preserving the package's public API.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

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
    "MarketEventInsertResult",
    "MarketEventInsertStatus",
    "MarketEventQuery",
    "RawMarketEventRepository",
    "RawNewsInsertResult",
    "RawNewsInsertStatus",
    "RawNewsRepository",
    "SQLiteCanonicalEventRepository",
    "SQLiteEventGraphRepository",
    "SQLiteRawNewsRepository",
    "SQLiteRawMarketEventRepository",
    "SQLiteReferenceRepository",
]

if TYPE_CHECKING:
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
    from sra_nexus.storage.market_data import (
        MarketEventInsertResult,
        MarketEventInsertStatus,
        MarketEventQuery,
        RawMarketEventRepository,
    )
    from sra_nexus.storage.raw import (
        RawNewsInsertResult,
        RawNewsInsertStatus,
        RawNewsRepository,
    )
    from sra_nexus.storage.sqlite import SQLiteRawNewsRepository
    from sra_nexus.storage.sqlite_canonical import SQLiteCanonicalEventRepository
    from sra_nexus.storage.sqlite_event_graph import SQLiteEventGraphRepository
    from sra_nexus.storage.sqlite_market_data import SQLiteRawMarketEventRepository
    from sra_nexus.storage.sqlite_reference import SQLiteReferenceRepository

_EXPORT_MODULES = {
    "CanonicalEventAlreadyExistsError": "sra_nexus.storage.canonical",
    "CanonicalEventNotFoundError": "sra_nexus.storage.canonical",
    "CanonicalEventRepository": "sra_nexus.storage.canonical",
    "CanonicalEventRepositoryError": "sra_nexus.storage.canonical",
    "CanonicalRevisionConflictError": "sra_nexus.storage.canonical",
    "NewsAlreadyCanonicalizedError": "sra_nexus.storage.canonical",
    "EventEntityLinkRepository": "sra_nexus.storage.event_graph",
    "EventExposureRepository": "sra_nexus.storage.event_graph",
    "EventGraphRepositoryError": "sra_nexus.storage.event_graph",
    "EventGraphRevisionConflictError": "sra_nexus.storage.event_graph",
    "MarketEventInsertResult": "sra_nexus.storage.market_data",
    "MarketEventInsertStatus": "sra_nexus.storage.market_data",
    "MarketEventQuery": "sra_nexus.storage.market_data",
    "RawMarketEventRepository": "sra_nexus.storage.market_data",
    "RawNewsInsertResult": "sra_nexus.storage.raw",
    "RawNewsInsertStatus": "sra_nexus.storage.raw",
    "RawNewsRepository": "sra_nexus.storage.raw",
    "SQLiteCanonicalEventRepository": "sra_nexus.storage.sqlite_canonical",
    "SQLiteEventGraphRepository": "sra_nexus.storage.sqlite_event_graph",
    "SQLiteRawNewsRepository": "sra_nexus.storage.sqlite",
    "SQLiteRawMarketEventRepository": "sra_nexus.storage.sqlite_market_data",
    "SQLiteReferenceRepository": "sra_nexus.storage.sqlite_reference",
}


def __getattr__(name: str) -> Any:
    """Load a public storage symbol only when it is requested."""
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
