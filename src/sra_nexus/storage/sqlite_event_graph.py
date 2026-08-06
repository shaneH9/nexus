"""SQLite persistence for immutable revision-aware entity links and exposures."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sra_nexus.aggregator.entity_links import EventEntityLink
from sra_nexus.aggregator.exposures import ExposurePath, RevisionEventExposure
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.common.models import normalize_utc_datetime
from sra_nexus.common.types import CanonicalEventId, CanonicalEventRevisionId, InstrumentId
from sra_nexus.storage.event_graph import (
    EventGraphRepositoryError,
    EventGraphRevisionConflictError,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_entity_link_runs (
    revision_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    available_at TEXT NOT NULL,
    UNIQUE (event_id, revision_number),
    FOREIGN KEY (revision_id) REFERENCES canonical_event_revisions(revision_id),
    FOREIGN KEY (event_id) REFERENCES canonical_events(event_id)
);

CREATE TABLE IF NOT EXISTS event_entity_links (
    revision_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    entity_id TEXT NOT NULL,
    role TEXT NOT NULL,
    relevance REAL NOT NULL CHECK (relevance >= 0 AND relevance <= 1),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    is_direct INTEGER NOT NULL CHECK (is_direct IN (0, 1)),
    matched_text TEXT NOT NULL,
    match_method TEXT NOT NULL,
    explanation TEXT NOT NULL,
    available_at TEXT NOT NULL,
    PRIMARY KEY (revision_id, entity_id),
    FOREIGN KEY (revision_id) REFERENCES canonical_event_revisions(revision_id),
    FOREIGN KEY (event_id) REFERENCES canonical_events(event_id),
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id)
);

CREATE TABLE IF NOT EXISTS event_exposure_runs (
    revision_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    available_at TEXT NOT NULL,
    UNIQUE (event_id, revision_number),
    FOREIGN KEY (revision_id) REFERENCES canonical_event_revisions(revision_id),
    FOREIGN KEY (event_id) REFERENCES canonical_events(event_id)
);

CREATE TABLE IF NOT EXISTS exposure_paths (
    path_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    available_at TEXT NOT NULL,
    starting_entity_id TEXT NOT NULL,
    relationship_ids TEXT NOT NULL,
    entity_ids TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    target_instrument_id TEXT NOT NULL,
    depth INTEGER NOT NULL CHECK (depth >= 0),
    direction REAL NOT NULL CHECK (direction >= -1 AND direction <= 1),
    magnitude REAL NOT NULL CHECK (magnitude >= 0 AND magnitude <= 1),
    relevance REAL NOT NULL CHECK (relevance >= 0 AND relevance <= 1),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    FOREIGN KEY (revision_id) REFERENCES canonical_event_revisions(revision_id),
    FOREIGN KEY (event_id) REFERENCES canonical_events(event_id),
    FOREIGN KEY (starting_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY (target_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY (target_instrument_id) REFERENCES instruments(instrument_id)
);

CREATE TABLE IF NOT EXISTS event_exposures (
    revision_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    available_at TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    direction REAL NOT NULL CHECK (direction >= -1 AND direction <= 1),
    magnitude REAL NOT NULL CHECK (magnitude >= 0 AND magnitude <= 1),
    relevance REAL NOT NULL CHECK (relevance >= 0 AND relevance <= 1),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    is_direct INTEGER NOT NULL CHECK (is_direct IN (0, 1)),
    direction_conflict INTEGER NOT NULL CHECK (direction_conflict IN (0, 1)),
    path_ids TEXT NOT NULL,
    PRIMARY KEY (revision_id, instrument_id, is_direct),
    FOREIGN KEY (revision_id) REFERENCES canonical_event_revisions(revision_id),
    FOREIGN KEY (event_id) REFERENCES canonical_events(event_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE INDEX IF NOT EXISTS ix_event_entity_link_runs_as_of
ON event_entity_link_runs (event_id, available_at, revision_number);

CREATE INDEX IF NOT EXISTS ix_event_entity_links_entity_as_of
ON event_entity_links (entity_id, available_at, event_id, revision_number);

CREATE INDEX IF NOT EXISTS ix_event_exposure_runs_as_of
ON event_exposure_runs (event_id, available_at, revision_number);

CREATE INDEX IF NOT EXISTS ix_event_exposures_instrument_as_of
ON event_exposures (instrument_id, available_at, event_id, revision_number);

CREATE INDEX IF NOT EXISTS ix_exposure_paths_revision_target
ON exposure_paths (revision_id, target_instrument_id, depth, path_id);
"""


class SQLiteEventGraphRepository:
    """SQLite implementation for immutable event entity/exposure snapshots."""

    def __init__(self, database_path: str | Path) -> None:
        """Configure a repository sharing canonical and reference schemas."""
        self._database_path = Path(database_path)

    def initialize_schema(self) -> None:
        """Create revision-run, entity-link, exposure, path, and history indexes."""
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def save_revision_links(
        self,
        revision: CanonicalEventRevision,
        links: tuple[EventEntityLink, ...],
    ) -> None:
        """Persist one complete entity-link snapshot idempotently."""
        _validate_entity_links(revision, links)
        if self._link_run_exists(revision.revision_id):
            if self.list_entity_links_for_revision(revision.revision_id) != links:
                raise EventGraphRevisionConflictError("stored event entity links differ")
            return
        try:
            with closing(self._connect()) as connection, connection:
                connection.executemany(
                    """
                    INSERT INTO event_entity_links (
                        revision_id, event_id, revision_number, entity_id, role,
                        relevance, confidence, is_direct, matched_text, match_method,
                        explanation, available_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_entity_link_row(link) for link in links),
                )
                connection.execute(
                    """
                    INSERT INTO event_entity_link_runs (
                        revision_id, event_id, revision_number, available_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    _revision_row(revision),
                )
        except sqlite3.IntegrityError as error:
            raise EventGraphRepositoryError("cannot save event entity links") from error

    def list_entity_links_for_revision(
        self,
        revision_id: CanonicalEventRevisionId,
    ) -> tuple[EventEntityLink, ...]:
        """Return the exact immutable entity links for one revision."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM event_entity_links
                WHERE revision_id = ?
                ORDER BY entity_id ASC
                """,
                (str(revision_id),),
            ).fetchall()
        return tuple(EventEntityLink.model_validate(dict(row)) for row in rows)

    def get_event_entity_links_as_of(
        self,
        event_id: CanonicalEventId,
        as_of: datetime,
    ) -> tuple[EventEntityLink, ...]:
        """Return links from the latest processed revision visible at the cutoff."""
        revision_id = self._latest_run_revision("event_entity_link_runs", event_id, as_of)
        return () if revision_id is None else self.list_entity_links_for_revision(revision_id)

    def is_revision_processed(self, revision_id: CanonicalEventRevisionId) -> bool:
        """Return whether a complete exposure snapshot marker exists."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM event_exposure_runs WHERE revision_id = ?",
                (str(revision_id),),
            ).fetchone()
        return row is not None

    def save_revision_exposures(
        self,
        revision: CanonicalEventRevision,
        exposures: tuple[RevisionEventExposure, ...],
        paths: tuple[ExposurePath, ...],
    ) -> None:
        """Persist one complete exposure/path snapshot idempotently."""
        _validate_exposure_snapshot(revision, exposures, paths)
        if self.is_revision_processed(revision.revision_id):
            if self.list_exposures_for_revision(revision.revision_id) != exposures:
                raise EventGraphRevisionConflictError("stored event exposures differ")
            if self.list_paths_for_revision(revision.revision_id) != paths:
                raise EventGraphRevisionConflictError("stored exposure paths differ")
            return
        try:
            with closing(self._connect()) as connection, connection:
                connection.executemany(
                    """
                    INSERT INTO exposure_paths (
                        path_id, revision_id, event_id, revision_number, available_at,
                        starting_entity_id, relationship_ids, entity_ids, target_entity_id,
                        target_instrument_id, depth, direction, magnitude, relevance, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_exposure_path_row(path) for path in paths),
                )
                connection.executemany(
                    """
                    INSERT INTO event_exposures (
                        revision_id, event_id, revision_number, available_at, instrument_id,
                        relation_type, direction, magnitude, relevance, confidence, is_direct,
                        direction_conflict, path_ids
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_event_exposure_row(exposure) for exposure in exposures),
                )
                connection.execute(
                    """
                    INSERT INTO event_exposure_runs (
                        revision_id, event_id, revision_number, available_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    _revision_row(revision),
                )
        except sqlite3.IntegrityError as error:
            raise EventGraphRepositoryError("cannot save event exposure snapshot") from error

    def list_exposures_for_revision(
        self,
        revision_id: CanonicalEventRevisionId,
    ) -> tuple[RevisionEventExposure, ...]:
        """Return materialized exposures in deterministic instrument/direct order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM event_exposures
                WHERE revision_id = ?
                ORDER BY instrument_id ASC, is_direct DESC
                """,
                (str(revision_id),),
            ).fetchall()
        return tuple(_row_to_revision_exposure(row) for row in rows)

    def list_paths_for_revision(
        self,
        revision_id: CanonicalEventRevisionId,
    ) -> tuple[ExposurePath, ...]:
        """Return auditable paths in deterministic depth and identity order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM exposure_paths
                WHERE revision_id = ?
                ORDER BY depth ASC, path_id ASC
                """,
                (str(revision_id),),
            ).fetchall()
        return tuple(_row_to_exposure_path(row) for row in rows)

    def get_event_exposures_as_of(
        self,
        event_id: CanonicalEventId,
        as_of: datetime,
    ) -> tuple[RevisionEventExposure, ...]:
        """Return latest processed event-revision exposures visible at the cutoff."""
        revision_id = self._latest_run_revision("event_exposure_runs", event_id, as_of)
        return () if revision_id is None else self.list_exposures_for_revision(revision_id)

    def list_instrument_exposures_as_of(
        self,
        instrument_id: InstrumentId,
        as_of: datetime,
    ) -> tuple[RevisionEventExposure, ...]:
        """Return latest visible per-event exposures for one instrument."""
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                WITH ranked_runs AS (
                    SELECT
                        run.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY run.event_id
                            ORDER BY run.available_at DESC, run.revision_number DESC
                        ) AS historical_rank
                    FROM event_exposure_runs AS run
                    WHERE run.available_at <= ?
                )
                SELECT exposure.*
                FROM ranked_runs AS run
                JOIN event_exposures AS exposure ON exposure.revision_id = run.revision_id
                WHERE run.historical_rank = 1 AND exposure.instrument_id = ?
                ORDER BY exposure.available_at ASC, exposure.event_id ASC,
                         exposure.is_direct DESC
                """,
                (cutoff, str(instrument_id)),
            ).fetchall()
        return tuple(_row_to_revision_exposure(row) for row in rows)

    def _link_run_exists(self, revision_id: CanonicalEventRevisionId) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM event_entity_link_runs WHERE revision_id = ?",
                (str(revision_id),),
            ).fetchone()
        return row is not None

    def _latest_run_revision(
        self,
        table: str,
        event_id: CanonicalEventId,
        as_of: datetime,
    ) -> CanonicalEventRevisionId | None:
        if table not in {"event_entity_link_runs", "event_exposure_runs"}:
            raise ValueError("unsupported revision run table")
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT revision_id FROM {table}
                WHERE event_id = ? AND available_at <= ?
                ORDER BY available_at DESC, revision_number DESC
                LIMIT 1
                """,
                (str(event_id), cutoff),
            ).fetchone()
        return None if row is None else CanonicalEventRevisionId.model_validate(row["revision_id"])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _validate_entity_links(
    revision: CanonicalEventRevision,
    links: tuple[EventEntityLink, ...],
) -> None:
    expected = (revision.event.event_id, revision.revision_id, revision.revision_number)
    identities: set[object] = set()
    for link in links:
        if (link.event_id, link.revision_id, link.revision_number) != expected:
            raise EventGraphRevisionConflictError("entity link revision identity mismatch")
        if link.available_at != revision.available_at:
            raise EventGraphRevisionConflictError("entity link available_at mismatch")
        if link.entity_id in identities:
            raise EventGraphRevisionConflictError("duplicate entity link in revision")
        identities.add(link.entity_id)


def _validate_exposure_snapshot(
    revision: CanonicalEventRevision,
    exposures: tuple[RevisionEventExposure, ...],
    paths: tuple[ExposurePath, ...],
) -> None:
    expected = (revision.event.event_id, revision.revision_id, revision.revision_number)
    path_by_id = {path.path_id: path for path in paths}
    if len(path_by_id) != len(paths):
        raise EventGraphRevisionConflictError("duplicate path identity in revision")
    for path in paths:
        if (path.event_id, path.revision_id, path.revision_number) != expected:
            raise EventGraphRevisionConflictError("exposure path revision identity mismatch")
        if path.available_at != revision.available_at:
            raise EventGraphRevisionConflictError("exposure path available_at mismatch")
    exposure_keys: set[tuple[InstrumentId, bool]] = set()
    referenced_paths: set[object] = set()
    for record in exposures:
        exposure = record.exposure
        if (
            exposure.event_id,
            record.revision_id,
            record.revision_number,
        ) != expected:
            raise EventGraphRevisionConflictError("event exposure revision identity mismatch")
        if record.available_at != revision.available_at:
            raise EventGraphRevisionConflictError("event exposure available_at mismatch")
        key = (exposure.instrument_id, exposure.is_direct)
        if key in exposure_keys:
            raise EventGraphRevisionConflictError("duplicate materialized exposure key")
        exposure_keys.add(key)
        for path_id in record.path_ids:
            supporting_path = path_by_id.get(path_id)
            if (
                supporting_path is None
                or supporting_path.target_instrument_id != exposure.instrument_id
            ):
                raise EventGraphRevisionConflictError("exposure references incompatible path")
            if (supporting_path.depth == 0) != exposure.is_direct:
                raise EventGraphRevisionConflictError("path directness does not match exposure")
            referenced_paths.add(path_id)
    if referenced_paths != set(path_by_id):
        raise EventGraphRevisionConflictError("every stored path must support an exposure")


def _revision_row(revision: CanonicalEventRevision) -> tuple[object, ...]:
    return (
        str(revision.revision_id),
        str(revision.event.event_id),
        revision.revision_number,
        _serialize_datetime(revision.available_at),
    )


def _entity_link_row(link: EventEntityLink) -> tuple[object, ...]:
    return (
        str(link.revision_id),
        str(link.event_id),
        link.revision_number,
        str(link.entity_id),
        link.role.value,
        link.relevance,
        link.confidence,
        int(link.is_direct),
        link.matched_text,
        link.match_method.value,
        link.explanation,
        _serialize_datetime(link.available_at),
    )


def _exposure_path_row(path: ExposurePath) -> tuple[object, ...]:
    return (
        str(path.path_id),
        str(path.revision_id),
        str(path.event_id),
        path.revision_number,
        _serialize_datetime(path.available_at),
        str(path.starting_entity_id),
        _json_dumps([str(value) for value in path.relationship_ids]),
        _json_dumps([str(value) for value in path.entity_ids]),
        str(path.target_entity_id),
        str(path.target_instrument_id),
        path.depth,
        path.direction,
        path.magnitude,
        path.relevance,
        path.confidence,
    )


def _event_exposure_row(record: RevisionEventExposure) -> tuple[object, ...]:
    exposure = record.exposure
    return (
        str(record.revision_id),
        str(exposure.event_id),
        record.revision_number,
        _serialize_datetime(record.available_at),
        str(exposure.instrument_id),
        exposure.relation_type.value,
        exposure.direction,
        exposure.magnitude,
        exposure.relevance,
        exposure.confidence,
        int(exposure.is_direct),
        int(record.direction_conflict),
        _json_dumps([str(path_id) for path_id in record.path_ids]),
    )


def _row_to_revision_exposure(row: sqlite3.Row) -> RevisionEventExposure:
    return RevisionEventExposure.model_validate(
        {
            "revision_id": row["revision_id"],
            "revision_number": row["revision_number"],
            "available_at": row["available_at"],
            "exposure": {
                "event_id": row["event_id"],
                "instrument_id": row["instrument_id"],
                "relation_type": row["relation_type"],
                "direction": row["direction"],
                "magnitude": row["magnitude"],
                "relevance": row["relevance"],
                "confidence": row["confidence"],
                "is_direct": bool(row["is_direct"]),
            },
            "direction_conflict": bool(row["direction_conflict"]),
            "path_ids": json.loads(row["path_ids"]),
        }
    )


def _row_to_exposure_path(row: sqlite3.Row) -> ExposurePath:
    return ExposurePath.model_validate(
        {
            **dict(row),
            "relationship_ids": json.loads(row["relationship_ids"]),
            "entity_ids": json.loads(row["entity_ids"]),
        }
    )


def _serialize_datetime(value: datetime) -> str:
    normalized = normalize_utc_datetime(value)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
