"""Tests for SQLite event-graph schema and repository boundaries."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from sra_nexus.common import CanonicalEventId, CanonicalEventRevisionId
from sra_nexus.storage import (
    EventEntityLinkRepository,
    EventExposureRepository,
    SQLiteCanonicalEventRepository,
    SQLiteEventGraphRepository,
    SQLiteReferenceRepository,
)


def _repository(tmp_path: Path) -> SQLiteEventGraphRepository:
    path = tmp_path / "event_graph.sqlite3"
    SQLiteCanonicalEventRepository(path).initialize_schema()
    SQLiteReferenceRepository(path).initialize_schema()
    repository = SQLiteEventGraphRepository(path)
    repository.initialize_schema()
    return repository


def test_sqlite_graph_backend_satisfies_separate_repository_protocols(tmp_path: Path) -> None:
    """Entity links and exposure snapshots should depend on explicit protocols."""
    repository = _repository(tmp_path)
    entity_links: EventEntityLinkRepository = repository
    exposures: EventExposureRepository = repository

    assert entity_links.list_entity_links_for_revision(CanonicalEventRevisionId.new()) == ()
    assert exposures.list_exposures_for_revision(CanonicalEventRevisionId.new()) == ()


def test_schema_contains_revision_graph_tables_and_indexes(tmp_path: Path) -> None:
    """Historical and instrument queries should have explicit storage structures."""
    path = tmp_path / "event_graph.sqlite3"
    _repository(tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }

    assert {
        "event_entity_link_runs",
        "event_entity_links",
        "event_exposure_runs",
        "event_exposures",
        "exposure_paths",
        "ix_event_entity_link_runs_as_of",
        "ix_event_entity_links_entity_as_of",
        "ix_event_exposure_runs_as_of",
        "ix_event_exposures_instrument_as_of",
        "ix_exposure_paths_revision_target",
    } <= names


def test_event_graph_as_of_queries_reject_naive_cutoffs(tmp_path: Path) -> None:
    """Historical graph visibility should require timezone-aware timestamps."""
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.get_event_exposures_as_of(
            CanonicalEventId.new(),
            datetime(2026, 1, 1),
        )
