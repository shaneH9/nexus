"""SQLite development implementation of immutable raw-news persistence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common.models import normalize_utc_datetime, thaw_json_object
from sra_nexus.common.types import NewsId
from sra_nexus.storage.raw import (
    RawNewsInsertResult,
    RawNewsInsertStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_news (
    news_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    provider_item_id TEXT,
    headline TEXT NOT NULL,
    body TEXT,
    url TEXT,
    event_time TEXT NOT NULL,
    receive_time TEXT NOT NULL,
    process_time TEXT NOT NULL,
    provider_tickers TEXT NOT NULL,
    provider_entities TEXT NOT NULL,
    language TEXT,
    raw_metadata TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_news_source_provider_item
ON raw_news (source, provider_item_id)
WHERE provider_item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_raw_news_process_time_news_id
ON raw_news (process_time, news_id);
"""

_INSERT = """
INSERT INTO raw_news (
    news_id,
    source,
    source_type,
    provider_item_id,
    headline,
    body,
    url,
    event_time,
    receive_time,
    process_time,
    provider_tickers,
    provider_entities,
    language,
    raw_metadata,
    content_hash
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SQLiteRawNewsRepository:
    """SQLite-backed raw repository with no update or overwrite operation."""

    def __init__(self, database_path: str | Path) -> None:
        """Configure a repository for an explicitly initialized SQLite database."""
        self._database_path = Path(database_path)

    def initialize_schema(self) -> None:
        """Explicitly create the development raw-news schema and indexes."""
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def insert(self, item: RawNewsItem) -> RawNewsInsertResult:
        """Insert a raw item or return the first matching duplicate rule."""
        with closing(self._connect()) as connection, connection:
            duplicate = self._find_duplicate(connection, item)
            if duplicate is not None:
                return duplicate

            try:
                connection.execute(_INSERT, _item_to_row(item))
            except sqlite3.IntegrityError:
                duplicate = self._find_duplicate(connection, item)
                if duplicate is None:
                    raise
                return duplicate

        return RawNewsInsertResult(
            status=RawNewsInsertStatus.INSERTED,
            incoming_news_id=item.news_id,
        )

    def get(self, news_id: NewsId) -> RawNewsItem | None:
        """Return an immutable raw record by its internal ID."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM raw_news WHERE news_id = ?",
                (str(news_id),),
            ).fetchone()
        return None if row is None else _row_to_item(row)

    def get_many_available_as_of(
        self,
        news_ids: tuple[NewsId, ...],
        as_of: datetime,
    ) -> tuple[RawNewsItem, ...]:
        """Return an indexed subset gated by process-time availability."""
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        unique_ids = tuple(dict.fromkeys(news_ids))
        if not unique_ids:
            return ()
        placeholders = ", ".join("?" for _ in unique_ids)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM raw_news
                WHERE news_id IN ({placeholders}) AND process_time <= ?
                ORDER BY process_time ASC, news_id ASC
                """,
                (*tuple(str(news_id) for news_id in unique_ids), cutoff),
            ).fetchall()
        return tuple(_row_to_item(row) for row in rows)

    def exists_provider_item(self, source: str, provider_item_id: str) -> bool:
        """Return whether a non-null provider identity already exists."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM raw_news WHERE source = ? AND provider_item_id = ? LIMIT 1",
                (source, provider_item_id),
            ).fetchone()
        return row is not None

    def exists_content_hash(self, content_hash: str) -> bool:
        """Return whether the content identity already exists."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM raw_news WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
        return row is not None

    def list_available_as_of(self, as_of: datetime) -> tuple[RawNewsItem, ...]:
        """Return process-time-available records in deterministic order."""
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM raw_news
                WHERE process_time <= ?
                ORDER BY process_time ASC, news_id ASC
                """,
                (cutoff,),
            ).fetchall()
        return tuple(_row_to_item(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _find_duplicate(
        connection: sqlite3.Connection,
        item: RawNewsItem,
    ) -> RawNewsInsertResult | None:
        if item.provider_item_id is not None:
            row = connection.execute(
                """
                SELECT news_id
                FROM raw_news
                WHERE source = ? AND provider_item_id = ?
                LIMIT 1
                """,
                (item.source, item.provider_item_id),
            ).fetchone()
            if row is not None:
                return _duplicate_result(
                    RawNewsInsertStatus.DUPLICATE_PROVIDER_ITEM,
                    item.news_id,
                    row,
                )

        row = connection.execute(
            "SELECT news_id FROM raw_news WHERE content_hash = ? LIMIT 1",
            (item.content_hash,),
        ).fetchone()
        if row is not None:
            return _duplicate_result(
                RawNewsInsertStatus.DUPLICATE_CONTENT_HASH,
                item.news_id,
                row,
            )

        row = connection.execute(
            "SELECT news_id FROM raw_news WHERE news_id = ? LIMIT 1",
            (str(item.news_id),),
        ).fetchone()
        if row is not None:
            return _duplicate_result(
                RawNewsInsertStatus.DUPLICATE_NEWS_ID,
                item.news_id,
                row,
            )
        return None


def _duplicate_result(
    status: RawNewsInsertStatus,
    incoming_news_id: NewsId,
    row: sqlite3.Row,
) -> RawNewsInsertResult:
    return RawNewsInsertResult(
        status=status,
        incoming_news_id=incoming_news_id,
        existing_news_id=NewsId.model_validate(row["news_id"]),
    )


def _item_to_row(item: RawNewsItem) -> tuple[object, ...]:
    return (
        str(item.news_id),
        item.source,
        item.source_type.value,
        item.provider_item_id,
        item.headline,
        item.body,
        item.url,
        _serialize_datetime(item.event_time),
        _serialize_datetime(item.receive_time),
        _serialize_datetime(item.process_time),
        _json_dumps(list(item.provider_tickers)),
        _json_dumps(list(item.provider_entities)),
        item.language,
        _json_dumps(thaw_json_object(item.raw_metadata)),
        item.content_hash,
    )


def _row_to_item(row: sqlite3.Row) -> RawNewsItem:
    return RawNewsItem.model_validate(
        {
            "news_id": row["news_id"],
            "source": row["source"],
            "source_type": row["source_type"],
            "provider_item_id": row["provider_item_id"],
            "headline": row["headline"],
            "body": row["body"],
            "url": row["url"],
            "event_time": row["event_time"],
            "receive_time": row["receive_time"],
            "process_time": row["process_time"],
            "provider_tickers": json.loads(row["provider_tickers"]),
            "provider_entities": json.loads(row["provider_entities"]),
            "language": row["language"],
            "raw_metadata": json.loads(row["raw_metadata"]),
            "content_hash": row["content_hash"],
        }
    )


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
