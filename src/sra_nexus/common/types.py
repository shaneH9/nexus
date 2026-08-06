"""Shared strongly typed identifiers for SRA-Nexus domain objects."""

from typing import Self
from uuid import UUID, uuid4

from pydantic import ConfigDict, RootModel


class _UuidIdentifier(RootModel[UUID]):
    """Immutable UUID value object with type-specific equality."""

    model_config = ConfigDict(frozen=True)

    @classmethod
    def new(cls) -> Self:
        """Generate a new identifier of the concrete subtype."""
        return cls(uuid4())

    def __str__(self) -> str:
        """Return the canonical UUID string."""
        return str(self.root)


class InstrumentId(_UuidIdentifier):
    """Stable internal identifier for an instrument."""


class EntityId(_UuidIdentifier):
    """Stable internal identifier for a canonical entity."""


class NewsId(_UuidIdentifier):
    """Stable internal identifier for a raw news item."""


class CanonicalEventId(_UuidIdentifier):
    """Stable internal identifier for a canonical event."""


def new_news_id() -> NewsId:
    """Return a new internal news identifier."""
    return NewsId.new()


def new_canonical_event_id() -> CanonicalEventId:
    """Return a new internal canonical-event identifier."""
    return CanonicalEventId.new()


def new_instrument_id() -> InstrumentId:
    """Return a new internal instrument identifier."""
    return InstrumentId.new()


def new_entity_id() -> EntityId:
    """Return a new internal entity identifier."""
    return EntityId.new()
