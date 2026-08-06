"""SQLite development storage for immutable normalized raw market events."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sra_nexus.common.models import normalize_utc_datetime
from sra_nexus.common.types import InstrumentId
from sra_nexus.market_data.enums import MarketEventKind
from sra_nexus.market_data.events import BookEvent, MarketEvent, QuoteEvent, TradeEvent
from sra_nexus.market_data.ordering import market_event_id, market_event_sort_key
from sra_nexus.storage.market_data import (
    MarketEventId,
    MarketEventInsertResult,
    MarketEventInsertStatus,
    MarketEventQuery,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_market_events (
    event_id TEXT PRIMARY KEY,
    event_kind TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
    exchange_time TEXT NOT NULL,
    receive_time TEXT NOT NULL,
    process_time TEXT NOT NULL,
    event_payload TEXT NOT NULL,
    UNIQUE (instrument_id, venue, event_kind, sequence_number)
);

CREATE INDEX IF NOT EXISTS ix_raw_market_events_instrument_process
ON raw_market_events (instrument_id, process_time, venue, event_kind, sequence_number);

CREATE INDEX IF NOT EXISTS ix_raw_market_events_stream_sequence
ON raw_market_events (instrument_id, venue, event_kind, sequence_number);
"""


class SQLiteRawMarketEventRepository:
    """Append-only SQLite backend with explicit event and sequence conflicts."""

    def __init__(self, database_path: str | Path) -> None:
        """Configure an explicitly initialized local development database."""
        self._database_path = Path(database_path)

    def initialize_schema(self) -> None:
        """Create immutable raw-event storage and indexed replay queries."""
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def insert(self, event: MarketEvent) -> MarketEventInsertResult:
        """Insert without update, returning explicit duplicate/conflict status."""
        incoming_id = market_event_id(event)
        with closing(self._connect()) as connection, connection:
            by_id = connection.execute(
                "SELECT * FROM raw_market_events WHERE event_id = ?",
                (str(incoming_id),),
            ).fetchone()
            if by_id is not None:
                existing = _row_to_event(by_id)
                return MarketEventInsertResult(
                    status=(
                        MarketEventInsertStatus.DUPLICATE_EVENT
                        if existing == event
                        else MarketEventInsertStatus.CONFLICTING_EVENT_ID
                    ),
                    incoming_event_id=incoming_id,
                    existing_event_id=market_event_id(existing),
                )

            by_sequence = connection.execute(
                """
                SELECT * FROM raw_market_events
                WHERE instrument_id = ? AND venue = ? AND event_kind = ?
                  AND sequence_number = ?
                """,
                (
                    str(event.instrument_id),
                    event.venue,
                    event.event_kind.value,
                    event.sequence_number,
                ),
            ).fetchone()
            if by_sequence is not None:
                existing = _row_to_event(by_sequence)
                return MarketEventInsertResult(
                    status=(
                        MarketEventInsertStatus.DUPLICATE_EVENT
                        if existing == event
                        else MarketEventInsertStatus.CONFLICTING_SEQUENCE
                    ),
                    incoming_event_id=incoming_id,
                    existing_event_id=market_event_id(existing),
                )

            connection.execute(
                """
                INSERT INTO raw_market_events (
                    event_id, event_kind, instrument_id, venue, sequence_number,
                    exchange_time, receive_time, process_time, event_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _event_row(event),
            )
        return MarketEventInsertResult(
            status=MarketEventInsertStatus.INSERTED,
            incoming_event_id=incoming_id,
        )

    def get(self, event_id: MarketEventId) -> MarketEvent | None:
        """Return an exact immutable market event by stable identity."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM raw_market_events WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()
        return None if row is None else _row_to_event(row)

    def list_stream(self, query: MarketEventQuery) -> tuple[MarketEvent, ...]:
        """Return an indexed stream range in deterministic sequence order."""
        clauses = ["instrument_id = ?", "venue = ?", "event_kind = ?"]
        parameters: list[object] = [
            str(query.instrument_id),
            query.venue,
            query.event_kind.value,
        ]
        if query.start_sequence is not None:
            clauses.append("sequence_number >= ?")
            parameters.append(query.start_sequence)
        if query.end_sequence is not None:
            clauses.append("sequence_number <= ?")
            parameters.append(query.end_sequence)
        if query.as_of is not None:
            clauses.append("process_time <= ?")
            parameters.append(_serialize_datetime(query.as_of))
        statement = f"""
            SELECT * FROM raw_market_events
            WHERE {" AND ".join(clauses)}
            ORDER BY sequence_number ASC, exchange_time ASC, receive_time ASC,
                     process_time ASC, event_id ASC
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return tuple(_row_to_event(row) for row in rows)

    def list_for_instrument(
        self,
        instrument_id: InstrumentId,
        as_of: datetime | None = None,
    ) -> tuple[MarketEvent, ...]:
        """Return only the requested instrument, process-time gated when supplied."""
        parameters: list[object] = [str(instrument_id)]
        availability = ""
        if as_of is not None:
            availability = "AND process_time <= ?"
            parameters.append(_serialize_datetime(normalize_utc_datetime(as_of)))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM raw_market_events
                WHERE instrument_id = ? {availability}
                """,
                parameters,
            ).fetchall()
        return tuple(sorted((_row_to_event(row) for row in rows), key=market_event_sort_key))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _event_row(event: MarketEvent) -> tuple[object, ...]:
    return (
        str(market_event_id(event)),
        event.event_kind.value,
        str(event.instrument_id),
        event.venue,
        event.sequence_number,
        _serialize_datetime(event.exchange_time),
        _serialize_datetime(event.receive_time),
        _serialize_datetime(event.process_time),
        event.model_dump_json(),
    )


def _row_to_event(row: sqlite3.Row) -> MarketEvent:
    kind = MarketEventKind(row["event_kind"])
    payload = row["event_payload"]
    if kind is MarketEventKind.BOOK:
        return BookEvent.model_validate_json(payload)
    if kind is MarketEventKind.TRADE:
        return TradeEvent.model_validate_json(payload)
    return QuoteEvent.model_validate_json(payload)


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
