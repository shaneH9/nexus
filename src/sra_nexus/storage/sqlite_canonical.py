"""SQLite development persistence for immutable canonical-event revisions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sra_nexus.aggregator.enums import NewsSourceType
from sra_nexus.aggregator.events import CanonicalEvent
from sra_nexus.aggregator.revisions import (
    CanonicalEventCandidateQuery,
    CanonicalEventRevision,
)
from sra_nexus.common.models import normalize_utc_datetime
from sra_nexus.common.types import (
    CanonicalEventId,
    CanonicalEventRevisionId,
    NewsId,
)
from sra_nexus.storage.canonical import (
    CanonicalEventAlreadyExistsError,
    CanonicalEventNotFoundError,
    CanonicalEventRepositoryError,
    CanonicalRevisionConflictError,
    NewsAlreadyCanonicalizedError,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_events (
    event_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_event_revisions (
    revision_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    available_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_subtype TEXT NOT NULL,
    last_update_time TEXT NOT NULL,
    event_payload TEXT NOT NULL,
    headline_tokens TEXT NOT NULL,
    source_names TEXT NOT NULL,
    source_types TEXT NOT NULL,
    UNIQUE (event_id, revision_number),
    FOREIGN KEY (event_id) REFERENCES canonical_events(event_id)
);

CREATE TABLE IF NOT EXISTS canonical_event_revision_sources (
    revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    news_id TEXT NOT NULL,
    PRIMARY KEY (revision_id, news_id),
    UNIQUE (revision_id, ordinal),
    FOREIGN KEY (revision_id) REFERENCES canonical_event_revisions(revision_id)
);

CREATE TABLE IF NOT EXISTS canonical_event_revision_anchors (
    revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    anchor TEXT NOT NULL,
    is_ticker INTEGER NOT NULL CHECK (is_ticker IN (0, 1)),
    PRIMARY KEY (revision_id, anchor),
    UNIQUE (revision_id, ordinal),
    FOREIGN KEY (revision_id) REFERENCES canonical_event_revisions(revision_id)
);

CREATE TABLE IF NOT EXISTS canonicalized_news (
    news_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    first_revision_id TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES canonical_events(event_id),
    FOREIGN KEY (first_revision_id) REFERENCES canonical_event_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS ix_canonical_revisions_event_available
ON canonical_event_revisions (event_id, available_at, revision_number);

CREATE INDEX IF NOT EXISTS ix_canonical_revisions_candidates
ON canonical_event_revisions (
    event_type,
    event_subtype,
    available_at,
    event_id,
    revision_number
);

CREATE INDEX IF NOT EXISTS ix_canonical_revision_anchors_lookup
ON canonical_event_revision_anchors (anchor, revision_id);

CREATE INDEX IF NOT EXISTS ix_canonicalized_news_event
ON canonicalized_news (event_id, news_id);
"""

_INSERT_REVISION = """
INSERT INTO canonical_event_revisions (
    revision_id,
    event_id,
    revision_number,
    available_at,
    event_type,
    event_subtype,
    last_update_time,
    event_payload,
    headline_tokens,
    source_names,
    source_types
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SQLiteCanonicalEventRepository:
    """SQLite-backed canonical repository that only appends immutable revisions."""

    def __init__(self, database_path: str | Path) -> None:
        """Configure a repository for an explicitly initialized SQLite database."""
        self._database_path = Path(database_path)

    def initialize_schema(self) -> None:
        """Explicitly create canonical identity, history, source, and anchor tables."""
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def create_event(self, revision: CanonicalEventRevision) -> None:
        """Create revision one while assigning every source NewsId exactly once."""
        if revision.revision_number != 1:
            raise CanonicalRevisionConflictError("first revision_number must be 1")

        with closing(self._connect()) as connection, connection:
            if self._event_exists(connection, revision.event.event_id):
                raise CanonicalEventAlreadyExistsError(str(revision.event.event_id))
            self._ensure_news_unassigned(connection, revision.event.source_news_ids)
            connection.execute(
                "INSERT INTO canonical_events (event_id, created_at) VALUES (?, ?)",
                (str(revision.event.event_id), _serialize_datetime(revision.available_at)),
            )
            self._insert_revision(connection, revision)
            self._assign_news(
                connection,
                revision.event.event_id,
                revision.revision_id,
                revision.event.source_news_ids,
            )

    def append_revision(self, revision: CanonicalEventRevision) -> None:
        """Append a monotonic revision that retains all prior source membership."""
        with closing(self._connect()) as connection, connection:
            current = self._get_current_revision(connection, revision.event.event_id)
            if current is None:
                raise CanonicalEventNotFoundError(str(revision.event.event_id))
            new_news_ids = self._validate_append(current, revision)
            self._ensure_news_unassigned(connection, new_news_ids)
            self._insert_revision(connection, revision)
            self._assign_news(
                connection,
                revision.event.event_id,
                revision.revision_id,
                new_news_ids,
            )

    def get_current_event(self, event_id: CanonicalEventId) -> CanonicalEvent | None:
        """Return the event state from the highest immutable revision number."""
        revision = self.get_current_revision(event_id)
        return None if revision is None else revision.event

    def get_current_revision(
        self,
        event_id: CanonicalEventId,
    ) -> CanonicalEventRevision | None:
        """Return the highest immutable revision for one stable event identity."""
        with closing(self._connect()) as connection:
            return self._get_current_revision(connection, event_id)

    def get_event_as_of(
        self,
        event_id: CanonicalEventId,
        as_of: datetime,
    ) -> CanonicalEvent | None:
        """Return only the latest revision whose available_at permits visibility."""
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM canonical_event_revisions
                WHERE event_id = ? AND available_at <= ?
                ORDER BY available_at DESC, revision_number DESC
                LIMIT 1
                """,
                (str(event_id), cutoff),
            ).fetchone()
            return None if row is None else self._row_to_revision(connection, row).event

    def list_event_revisions(
        self,
        event_id: CanonicalEventId,
    ) -> tuple[CanonicalEventRevision, ...]:
        """Return all immutable states in ascending revision order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM canonical_event_revisions
                WHERE event_id = ?
                ORDER BY revision_number ASC
                """,
                (str(event_id),),
            ).fetchall()
            return tuple(self._row_to_revision(connection, row) for row in rows)

    def get_event_revision(
        self,
        event_id: CanonicalEventId,
        revision_number: int,
    ) -> CanonicalEventRevision | None:
        """Return one immutable revision by event identity and positive number."""
        if revision_number < 1:
            raise ValueError("revision_number must be positive")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM canonical_event_revisions
                WHERE event_id = ? AND revision_number = ?
                """,
                (str(event_id), revision_number),
            ).fetchone()
            return None if row is None else self._row_to_revision(connection, row)

    def find_candidates(
        self,
        query: CanonicalEventCandidateQuery,
    ) -> tuple[CanonicalEventRevision, ...]:
        """Retrieve latest historical revisions by indexed type, subtype, time, and anchor."""
        parameters: list[object] = [
            _serialize_datetime(query.not_before),
            _serialize_datetime(query.as_of),
            query.event_type.value,
            query.event_subtype.value,
        ]
        anchor_clause = ""
        if query.anchors:
            placeholders = ", ".join("?" for _ in query.anchors)
            anchor_clause = f"""
                AND EXISTS (
                    SELECT 1
                    FROM canonical_event_revision_anchors AS anchor
                    WHERE anchor.revision_id = ranked.revision_id
                      AND anchor.anchor IN ({placeholders})
                )
            """
            parameters.extend(query.anchors)

        statement = f"""
            WITH ranked AS (
                SELECT
                    revision.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY revision.event_id
                        ORDER BY revision.available_at DESC, revision.revision_number DESC
                    ) AS candidate_rank
                FROM canonical_event_revisions AS revision
                WHERE revision.available_at >= ? AND revision.available_at <= ?
            )
            SELECT *
            FROM ranked
            WHERE candidate_rank = 1
              AND event_type = ?
              AND event_subtype = ?
              {anchor_clause}
            ORDER BY available_at DESC, event_id ASC
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(statement, parameters).fetchall()
            return tuple(self._row_to_revision(connection, row) for row in rows)

    def get_event_id_for_news(self, news_id: NewsId) -> CanonicalEventId | None:
        """Return the stable event identity owning one raw-news observation."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT event_id FROM canonicalized_news WHERE news_id = ?",
                (str(news_id),),
            ).fetchone()
        return None if row is None else CanonicalEventId.model_validate(row["event_id"])

    def list_events_available_as_of(self, as_of: datetime) -> tuple[CanonicalEvent, ...]:
        """Return one latest visible state per event in deterministic availability order."""
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT
                        revision.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY revision.event_id
                            ORDER BY revision.available_at DESC, revision.revision_number DESC
                        ) AS historical_rank
                    FROM canonical_event_revisions AS revision
                    WHERE revision.available_at <= ?
                )
                SELECT *
                FROM ranked
                WHERE historical_rank = 1
                ORDER BY available_at ASC, event_id ASC
                """,
                (cutoff,),
            ).fetchall()
            return tuple(self._row_to_revision(connection, row).event for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _event_exists(connection: sqlite3.Connection, event_id: CanonicalEventId) -> bool:
        row = connection.execute(
            "SELECT 1 FROM canonical_events WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        return row is not None

    def _get_current_revision(
        self,
        connection: sqlite3.Connection,
        event_id: CanonicalEventId,
    ) -> CanonicalEventRevision | None:
        row = connection.execute(
            """
            SELECT *
            FROM canonical_event_revisions
            WHERE event_id = ?
            ORDER BY revision_number DESC
            LIMIT 1
            """,
            (str(event_id),),
        ).fetchone()
        return None if row is None else self._row_to_revision(connection, row)

    @staticmethod
    def _validate_append(
        current: CanonicalEventRevision,
        incoming: CanonicalEventRevision,
    ) -> tuple[NewsId, ...]:
        expected_number = current.revision_number + 1
        if incoming.revision_number != expected_number:
            raise CanonicalRevisionConflictError(
                f"revision_number must be {expected_number} for this event"
            )
        if incoming.available_at < current.available_at:
            raise CanonicalRevisionConflictError("available_at must be monotonic")
        if incoming.event.last_update_time < current.event.last_update_time:
            raise CanonicalRevisionConflictError("last_update_time must be monotonic")
        if incoming.event.event_type is not current.event.event_type or (
            incoming.event.event_subtype is not current.event.event_subtype
        ):
            raise CanonicalRevisionConflictError("event type and subtype cannot change")
        if incoming.event.first_event_time != current.event.first_event_time or (
            incoming.event.first_receive_time != current.event.first_receive_time
        ):
            raise CanonicalRevisionConflictError("first event and receive times must be preserved")
        previous_sources = current.event.source_news_ids
        incoming_sources = incoming.event.source_news_ids
        if incoming_sources[: len(previous_sources)] != previous_sources:
            raise CanonicalRevisionConflictError("new revision must retain prior source membership")
        new_sources = incoming_sources[len(previous_sources) :]
        if not new_sources:
            raise CanonicalRevisionConflictError("new revision must add at least one source NewsId")
        return new_sources

    @staticmethod
    def _ensure_news_unassigned(
        connection: sqlite3.Connection,
        news_ids: tuple[NewsId, ...],
    ) -> None:
        for news_id in news_ids:
            row = connection.execute(
                "SELECT event_id FROM canonicalized_news WHERE news_id = ?",
                (str(news_id),),
            ).fetchone()
            if row is not None:
                raise NewsAlreadyCanonicalizedError(
                    f"news_id {news_id} already belongs to event {row['event_id']}"
                )

    @staticmethod
    def _assign_news(
        connection: sqlite3.Connection,
        event_id: CanonicalEventId,
        revision_id: CanonicalEventRevisionId,
        news_ids: tuple[NewsId, ...],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO canonicalized_news (news_id, event_id, first_revision_id)
            VALUES (?, ?, ?)
            """,
            ((str(news_id), str(event_id), str(revision_id)) for news_id in news_ids),
        )

    @staticmethod
    def _insert_revision(
        connection: sqlite3.Connection,
        revision: CanonicalEventRevision,
    ) -> None:
        event_subtype = revision.event.event_subtype
        if event_subtype is None:
            raise CanonicalEventRepositoryError("revision event_subtype cannot be null")
        connection.execute(
            _INSERT_REVISION,
            (
                str(revision.revision_id),
                str(revision.event.event_id),
                revision.revision_number,
                _serialize_datetime(revision.available_at),
                revision.event.event_type.value,
                event_subtype.value,
                _serialize_datetime(revision.event.last_update_time),
                _json_dumps(revision.event.model_dump(mode="json")),
                _json_dumps(list(revision.headline_tokens)),
                _json_dumps(list(revision.source_names)),
                _json_dumps([source_type.value for source_type in revision.source_types]),
            ),
        )
        connection.executemany(
            """
            INSERT INTO canonical_event_revision_sources (revision_id, ordinal, news_id)
            VALUES (?, ?, ?)
            """,
            (
                (str(revision.revision_id), ordinal, str(news_id))
                for ordinal, news_id in enumerate(revision.event.source_news_ids)
            ),
        )
        ticker_anchors = set(revision.ticker_anchors)
        connection.executemany(
            """
            INSERT INTO canonical_event_revision_anchors
                (revision_id, ordinal, anchor, is_ticker)
            VALUES (?, ?, ?, ?)
            """,
            (
                (
                    str(revision.revision_id),
                    ordinal,
                    anchor,
                    int(anchor in ticker_anchors),
                )
                for ordinal, anchor in enumerate(revision.anchors)
            ),
        )

    @staticmethod
    def _row_to_revision(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> CanonicalEventRevision:
        source_rows = connection.execute(
            """
            SELECT news_id
            FROM canonical_event_revision_sources
            WHERE revision_id = ?
            ORDER BY ordinal ASC
            """,
            (row["revision_id"],),
        ).fetchall()
        anchor_rows = connection.execute(
            """
            SELECT anchor, is_ticker
            FROM canonical_event_revision_anchors
            WHERE revision_id = ?
            ORDER BY ordinal ASC
            """,
            (row["revision_id"],),
        ).fetchall()
        event = CanonicalEvent.model_validate(json.loads(row["event_payload"]))
        stored_source_ids = tuple(NewsId.model_validate(item["news_id"]) for item in source_rows)
        if stored_source_ids != event.source_news_ids:
            raise CanonicalEventRepositoryError("revision source membership does not match payload")
        anchors = tuple(item["anchor"] for item in anchor_rows)
        ticker_anchors = tuple(item["anchor"] for item in anchor_rows if item["is_ticker"])
        return CanonicalEventRevision.model_validate(
            {
                "revision_id": row["revision_id"],
                "revision_number": row["revision_number"],
                "available_at": row["available_at"],
                "event": event,
                "headline_tokens": json.loads(row["headline_tokens"]),
                "anchors": anchors,
                "ticker_anchors": ticker_anchors,
                "source_names": json.loads(row["source_names"]),
                "source_types": [
                    NewsSourceType(value) for value in json.loads(row["source_types"])
                ],
            }
        )


def _serialize_datetime(value: datetime) -> str:
    normalized = normalize_utc_datetime(value)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
