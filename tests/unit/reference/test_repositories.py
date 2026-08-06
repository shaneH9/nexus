"""Tests for deterministic SQLite entity, instrument, and graph reference lookups."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support.reference_data import load_reference_fixture

from sra_nexus.reference import (
    AssetType,
    Entity,
    EntityInstrumentLink,
    EntityInstrumentRelationType,
    EntityRelationship,
    EntityRelationshipType,
    EntityRepository,
    EntityType,
    Instrument,
    InstrumentRepository,
    ReferenceResolutionStatus,
    RelationshipRepository,
)
from sra_nexus.storage import SQLiteReferenceRepository

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def _repository(tmp_path: Path) -> SQLiteReferenceRepository:
    repository = SQLiteReferenceRepository(tmp_path / "reference.sqlite3")
    repository.initialize_schema()
    load_reference_fixture(repository, repository, repository)
    return repository


def test_sqlite_repository_satisfies_all_reference_protocols(tmp_path: Path) -> None:
    """One backend may implement separate provider-independent boundaries."""
    repository = _repository(tmp_path)
    entity_repository: EntityRepository = repository
    instrument_repository: InstrumentRepository = repository
    relationship_repository: RelationshipRepository = repository

    assert entity_repository.list_entities()
    assert instrument_repository.resolve_ticker("NVDA").instrument is not None
    assert relationship_repository.list_outgoing_relationships(
        entity_repository.resolve_alias("TSMC").candidates[0].entity_id,
        NOW,
    )


def test_entity_exact_name_alias_and_alias_order_round_trip(tmp_path: Path) -> None:
    """Canonical and alias lookup should normalize text while retaining display aliases."""
    repository = _repository(tmp_path)

    canonical = repository.resolve_canonical_name("  nvidia corporation ")
    alias = repository.resolve_alias(" NVIDIA ")

    assert canonical.status is ReferenceResolutionStatus.RESOLVED
    assert alias.status is ReferenceResolutionStatus.RESOLVED
    assert alias.entity == canonical.entity
    assert canonical.entity is not None
    assert repository.list_aliases(canonical.entity.entity_id) == ("NVIDIA", "Nvidia")


def test_ambiguous_alias_never_selects_by_insertion_order(tmp_path: Path) -> None:
    """Alias collisions must remain explicit ambiguities."""
    repository = _repository(tmp_path)
    fruit = Entity(
        entity_type=EntityType.COMMODITY, canonical_name="Apple Fruit", aliases=("Apple",)
    )
    repository.insert_entity(fruit)

    result = repository.resolve_alias("apple")

    assert result.status is ReferenceResolutionStatus.AMBIGUOUS
    assert len(result.candidates) == 2
    assert {entity.entity_type for entity in result.candidates} == {
        EntityType.COMPANY,
        EntityType.COMMODITY,
    }


def test_ticker_resolution_uses_exchange_and_returns_ambiguity(tmp_path: Path) -> None:
    """A colliding ticker should require venue metadata instead of an arbitrary choice."""
    repository = _repository(tmp_path)
    alternate = Instrument(
        ticker="NVDA",
        exchange="SYNTH",
        asset_type=AssetType.EQUITY,
        currency="USD",
    )
    repository.insert_instrument(alternate)

    ambiguous = repository.resolve_ticker("nvda")
    resolved = repository.resolve_ticker("nvda", exchange="NASDAQ")

    assert ambiguous.status is ReferenceResolutionStatus.AMBIGUOUS
    assert resolved.status is ReferenceResolutionStatus.RESOLVED
    assert resolved.instrument is not None
    assert resolved.instrument.exchange == "NASDAQ"


def test_entity_instrument_queries_respect_validity(tmp_path: Path) -> None:
    """Future entity-instrument knowledge must not appear before valid_from."""
    repository = _repository(tmp_path)
    entity = Entity(entity_type=EntityType.COMPANY, canonical_name="Future Issuer")
    instrument = Instrument(
        ticker="FUTR",
        exchange="NASDAQ",
        asset_type=AssetType.EQUITY,
        currency="USD",
    )
    repository.insert_entity(entity)
    repository.insert_instrument(instrument)
    repository.insert_entity_instrument_link(
        EntityInstrumentLink(
            entity_id=entity.entity_id,
            instrument_id=instrument.instrument_id,
            relationship_type=EntityInstrumentRelationType.PRIMARY_EQUITY,
            confidence=1.0,
            valid_from=NOW,
            valid_to=NOW + timedelta(days=2),
        )
    )

    assert repository.list_instruments_for_entity(entity.entity_id, NOW - timedelta(days=1)) == ()
    assert repository.list_instruments_for_entity(entity.entity_id, NOW) == (instrument,)
    assert repository.list_instruments_for_entity(entity.entity_id, NOW + timedelta(days=2)) == ()


def test_relationship_queries_respect_valid_from_and_valid_to(tmp_path: Path) -> None:
    """Historical graph lookup should use half-open relationship validity."""
    repository = _repository(tmp_path)
    source = repository.resolve_alias("Microsoft").candidates[0]
    target = repository.resolve_alias("Apple").candidates[0]
    relationship = EntityRelationship(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        relation_type=EntityRelationshipType.SUPPLIER_TO,
        magnitude=0.6,
        confidence=0.8,
        valid_from=NOW,
        valid_to=NOW + timedelta(days=2),
    )
    repository.insert_relationship(relationship)

    assert relationship not in repository.list_outgoing_relationships(
        source.entity_id, NOW - timedelta(microseconds=1)
    )
    assert relationship in repository.list_outgoing_relationships(source.entity_id, NOW)
    assert relationship not in repository.list_outgoing_relationships(
        source.entity_id, NOW + timedelta(days=2)
    )


def test_historical_reference_queries_require_aware_cutoff(tmp_path: Path) -> None:
    """Validity-aware lookups should reject naive timestamps."""
    repository = _repository(tmp_path)
    entity = repository.resolve_alias("TSMC").candidates[0]

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.list_outgoing_relationships(entity.entity_id, datetime(2026, 1, 1))


def test_schema_contains_lookup_and_validity_indexes(tmp_path: Path) -> None:
    """The development schema should index deterministic reference and graph queries."""
    path = tmp_path / "reference.sqlite3"
    SQLiteReferenceRepository(path).initialize_schema()
    with closing(sqlite3.connect(path)) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }

    assert {
        "entities",
        "entity_aliases",
        "instruments",
        "entity_instrument_links",
        "entity_relationships",
        "ix_entities_canonical_name",
        "ix_entity_aliases_lookup",
        "ix_instruments_ticker",
        "ix_entity_relationships_source_validity",
        "ix_entity_relationships_target_validity",
    } <= names
