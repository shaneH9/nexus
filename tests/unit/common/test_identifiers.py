"""Tests for strongly typed UUID identifiers."""

from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from sra_nexus.common import (
    CanonicalEventId,
    CanonicalEventRevisionId,
    EntityId,
    InstrumentId,
    NewsId,
)
from sra_nexus.common.types import (
    new_canonical_event_id,
    new_canonical_event_revision_id,
    new_entity_id,
    new_instrument_id,
    new_news_id,
)

type Identifier = CanonicalEventId | CanonicalEventRevisionId | EntityId | InstrumentId | NewsId


def test_identifier_factories_generate_correct_uuid_types() -> None:
    """Every factory should return the intended UUID-backed value object."""
    identifiers: tuple[Identifier, ...] = (
        new_instrument_id(),
        new_entity_id(),
        new_news_id(),
        new_canonical_event_id(),
        new_canonical_event_revision_id(),
    )

    assert isinstance(identifiers[0], InstrumentId)
    assert isinstance(identifiers[1], EntityId)
    assert isinstance(identifiers[2], NewsId)
    assert isinstance(identifiers[3], CanonicalEventId)
    assert isinstance(identifiers[4], CanonicalEventRevisionId)
    assert all(isinstance(identifier.root, UUID) for identifier in identifiers)


def test_identifier_types_are_not_interchangeable_at_runtime() -> None:
    """Different identifier classes should remain unequal for the same UUID."""
    shared_uuid = uuid4()
    instrument_id = InstrumentId(shared_uuid)
    entity_id = EntityId(shared_uuid)

    assert not _objects_equal(instrument_id, entity_id)


def test_identifier_comparison_uses_uuid_value_within_same_type() -> None:
    """Two identifiers of the same type and UUID should compare equally."""
    shared_uuid = uuid4()

    assert InstrumentId(shared_uuid) == InstrumentId(shared_uuid)


def test_identifier_is_immutable() -> None:
    """An identifier's UUID cannot be reassigned after creation."""
    identifier = InstrumentId.new()

    with pytest.raises(ValidationError, match="frozen"):
        identifier.root = uuid4()


def test_identifier_serializes_as_uuid_string_when_nested() -> None:
    """Identifiers should remain convenient for JSON domain serialization."""

    class Envelope(BaseModel):
        instrument_id: InstrumentId

    identifier = InstrumentId.new()
    envelope = Envelope(instrument_id=identifier)

    assert envelope.model_dump(mode="json") == {"instrument_id": str(identifier)}


def _objects_equal(left: object, right: object) -> bool:
    return left == right
