"""Persistence protocols for immutable revision-aware event exposure graphs."""

from datetime import datetime
from typing import Protocol

from sra_nexus.aggregator.entity_links import EventEntityLink
from sra_nexus.aggregator.exposures import ExposurePath, RevisionEventExposure
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.common.types import CanonicalEventId, CanonicalEventRevisionId, InstrumentId


class EventGraphRepositoryError(RuntimeError):
    """Base error for immutable event graph persistence."""


class EventGraphRevisionConflictError(EventGraphRepositoryError):
    """Raised when a saved revision graph differs from immutable stored state."""


class EventEntityLinkRepository(Protocol):
    """Storage boundary for revision-specific canonical event entity links."""

    def save_revision_links(
        self,
        revision: CanonicalEventRevision,
        links: tuple[EventEntityLink, ...],
    ) -> None:
        """Persist entity links idempotently without changing prior revisions."""
        ...

    def list_entity_links_for_revision(
        self,
        revision_id: CanonicalEventRevisionId,
    ) -> tuple[EventEntityLink, ...]:
        """Return links for one exact canonical revision."""
        ...

    def get_event_entity_links_as_of(
        self,
        event_id: CanonicalEventId,
        as_of: datetime,
    ) -> tuple[EventEntityLink, ...]:
        """Return links from the latest processed revision visible by ``as_of``."""
        ...


class EventExposureRepository(Protocol):
    """Storage boundary for revision exposure records and their auditable paths."""

    def is_revision_processed(self, revision_id: CanonicalEventRevisionId) -> bool:
        """Return whether an immutable exposure snapshot exists for the revision."""
        ...

    def save_revision_exposures(
        self,
        revision: CanonicalEventRevision,
        exposures: tuple[RevisionEventExposure, ...],
        paths: tuple[ExposurePath, ...],
    ) -> None:
        """Persist one complete immutable exposure snapshot idempotently."""
        ...

    def list_exposures_for_revision(
        self,
        revision_id: CanonicalEventRevisionId,
    ) -> tuple[RevisionEventExposure, ...]:
        """Return materialized exposures for one exact canonical revision."""
        ...

    def list_paths_for_revision(
        self,
        revision_id: CanonicalEventRevisionId,
    ) -> tuple[ExposurePath, ...]:
        """Return every auditable path for one exact canonical revision."""
        ...

    def get_event_exposures_as_of(
        self,
        event_id: CanonicalEventId,
        as_of: datetime,
    ) -> tuple[RevisionEventExposure, ...]:
        """Return latest processed event-revision exposures visible by ``as_of``."""
        ...

    def list_instrument_exposures_as_of(
        self,
        instrument_id: InstrumentId,
        as_of: datetime,
    ) -> tuple[RevisionEventExposure, ...]:
        """Return one latest visible exposure revision per event for an instrument."""
        ...
