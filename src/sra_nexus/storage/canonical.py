"""Provider-independent persistence contract for canonical-event revisions."""

from datetime import datetime
from typing import Protocol

from sra_nexus.aggregator.events import CanonicalEvent
from sra_nexus.aggregator.revisions import (
    CanonicalEventCandidateQuery,
    CanonicalEventRevision,
)
from sra_nexus.common.types import CanonicalEventId, NewsId


class CanonicalEventRepositoryError(RuntimeError):
    """Base failure for canonical-event persistence invariants."""


class CanonicalEventAlreadyExistsError(CanonicalEventRepositoryError):
    """Raised when an event identity has already been created."""


class CanonicalEventNotFoundError(CanonicalEventRepositoryError):
    """Raised when an append targets an unknown canonical event."""


class CanonicalRevisionConflictError(CanonicalEventRepositoryError):
    """Raised when revision history is non-monotonic or loses prior state."""


class NewsAlreadyCanonicalizedError(CanonicalEventRepositoryError):
    """Raised when a NewsId is already associated with a canonical event."""


class CanonicalEventRepository(Protocol):
    """Storage boundary for immutable, historically reconstructible event state."""

    def create_event(self, revision: CanonicalEventRevision) -> None:
        """Create an event identity and its immutable first revision."""
        ...

    def append_revision(self, revision: CanonicalEventRevision) -> None:
        """Append the next immutable revision without changing prior state."""
        ...

    def get_current_event(self, event_id: CanonicalEventId) -> CanonicalEvent | None:
        """Return the latest materialized event state."""
        ...

    def get_current_revision(
        self,
        event_id: CanonicalEventId,
    ) -> CanonicalEventRevision | None:
        """Return the latest immutable event revision and clustering metadata."""
        ...

    def get_event_as_of(
        self,
        event_id: CanonicalEventId,
        as_of: datetime,
    ) -> CanonicalEvent | None:
        """Return the latest event revision available no later than the cutoff."""
        ...

    def list_event_revisions(
        self,
        event_id: CanonicalEventId,
    ) -> tuple[CanonicalEventRevision, ...]:
        """Return every immutable revision in ascending revision order."""
        ...

    def find_candidates(
        self,
        query: CanonicalEventCandidateQuery,
    ) -> tuple[CanonicalEventRevision, ...]:
        """Return indexed historical candidates satisfying query constraints."""
        ...

    def get_event_id_for_news(self, news_id: NewsId) -> CanonicalEventId | None:
        """Return the one event already associated with a raw-news item."""
        ...

    def list_events_available_as_of(self, as_of: datetime) -> tuple[CanonicalEvent, ...]:
        """Return one latest available revision per event in deterministic order."""
        ...
