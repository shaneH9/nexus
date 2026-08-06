"""Load deterministic Milestone D reference fixtures into repository protocols."""

import json
from pathlib import Path

from sra_nexus.reference import Entity, EntityInstrumentLink, EntityRelationship, Instrument
from sra_nexus.reference.repositories import (
    EntityRepository,
    InstrumentRepository,
    RelationshipRepository,
)

REFERENCE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "reference" / "milestone_d.json"
)


def load_reference_fixture(
    entity_repository: EntityRepository,
    instrument_repository: InstrumentRepository,
    relationship_repository: RelationshipRepository,
    fixture_path: Path = REFERENCE_FIXTURE,
) -> None:
    """Validate and insert the offline reference graph in dependency order."""
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reference fixture must be a JSON object")
    for item in _records(payload, "entities"):
        entity_repository.insert_entity(Entity.model_validate(item))
    for item in _records(payload, "instruments"):
        instrument_repository.insert_instrument(Instrument.model_validate(item))
    for item in _records(payload, "entity_instrument_links"):
        instrument_repository.insert_entity_instrument_link(
            EntityInstrumentLink.model_validate(item)
        )
    for item in _records(payload, "relationships"):
        relationship_repository.insert_relationship(EntityRelationship.model_validate(item))


def _records(payload: dict[str, object], key: str) -> list[object]:
    records = payload.get(key)
    if not isinstance(records, list):
        raise ValueError(f"reference fixture field {key} must be a list")
    return records
