"""Shared strongly typed identifiers for SRA-Nexus domain objects."""

from typing import NewType
from uuid import UUID, uuid4

NewsId = NewType("NewsId", UUID)
EventId = NewType("EventId", UUID)
ExposureId = NewType("ExposureId", UUID)
InstrumentId = NewType("InstrumentId", UUID)
EntityId = NewType("EntityId", UUID)


def new_news_id() -> NewsId:
    """Return a new internal news identifier."""
    return NewsId(uuid4())


def new_event_id() -> EventId:
    """Return a new internal canonical-event identifier."""
    return EventId(uuid4())


def new_exposure_id() -> ExposureId:
    """Return a new internal event-exposure identifier."""
    return ExposureId(uuid4())


def new_instrument_id() -> InstrumentId:
    """Return a new internal instrument identifier."""
    return InstrumentId(uuid4())


def new_entity_id() -> EntityId:
    """Return a new internal entity identifier."""
    return EntityId(uuid4())
