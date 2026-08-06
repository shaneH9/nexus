"""Tests for structural entity and entity-instrument relationship contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from sra_nexus.common import EntityId, InstrumentId
from sra_nexus.reference import (
    EntityInstrumentLink,
    EntityInstrumentRelationType,
    EntityRelationship,
    EntityRelationshipType,
    RelationshipDirection,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_entity_relationship_preserves_structural_not_event_direction() -> None:
    """A directed edge should retain bounded structural strength and confidence."""
    relationship = EntityRelationship(
        source_entity_id=EntityId.new(),
        target_entity_id=EntityId.new(),
        relation_type=EntityRelationshipType.SUPPLIER_TO,
        direction=RelationshipDirection.DIRECTED,
        magnitude=0.8,
        confidence=0.9,
    )

    assert relationship.direction is RelationshipDirection.DIRECTED
    assert relationship.magnitude == 0.8


@pytest.mark.parametrize(("field_name", "value"), [("magnitude", -0.1), ("confidence", 1.1)])
def test_relationship_rejects_impossible_ranges(field_name: str, value: float) -> None:
    """Structural normalized values must remain in the closed unit interval."""
    data: dict[str, object] = {
        "source_entity_id": EntityId.new(),
        "target_entity_id": EntityId.new(),
        "relation_type": EntityRelationshipType.SUPPLIER_TO,
        "magnitude": 0.8,
        "confidence": 0.9,
    }
    data[field_name] = value

    with pytest.raises(ValidationError):
        EntityRelationship.model_validate(data)


def test_relationship_rejects_self_edge_and_invalid_validity() -> None:
    """Graph edges must connect entities over a valid half-open interval."""
    entity_id = EntityId.new()
    with pytest.raises(ValidationError, match="distinct entities"):
        EntityRelationship(
            source_entity_id=entity_id,
            target_entity_id=entity_id,
            relation_type=EntityRelationshipType.OTHER,
            magnitude=0.5,
            confidence=0.5,
        )
    with pytest.raises(ValidationError, match="valid_from must be before valid_to"):
        EntityRelationship(
            source_entity_id=EntityId.new(),
            target_entity_id=EntityId.new(),
            relation_type=EntityRelationshipType.OTHER,
            magnitude=0.5,
            confidence=0.5,
            valid_from=NOW,
            valid_to=NOW,
        )


def test_competitor_requires_explicit_symmetric_semantics() -> None:
    """The only initially symmetric relationship must declare that behavior."""
    with pytest.raises(ValidationError, match="explicitly SYMMETRIC"):
        EntityRelationship(
            source_entity_id=EntityId.new(),
            target_entity_id=EntityId.new(),
            relation_type=EntityRelationshipType.COMPETITOR,
            direction=RelationshipDirection.DIRECTED,
            magnitude=0.5,
            confidence=0.5,
        )


def test_entity_instrument_link_uses_half_open_validity() -> None:
    """Explicit mappings should be available from valid_from until before valid_to."""
    link = EntityInstrumentLink(
        entity_id=EntityId.new(),
        instrument_id=InstrumentId.new(),
        relationship_type=EntityInstrumentRelationType.PRIMARY_EQUITY,
        confidence=1.0,
        valid_from=NOW,
        valid_to=NOW + timedelta(days=1),
    )

    assert not link.is_valid_at(NOW - timedelta(microseconds=1))
    assert link.is_valid_at(NOW)
    assert not link.is_valid_at(NOW + timedelta(days=1))
