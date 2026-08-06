"""SQLite development persistence for deterministic reference data."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sra_nexus.aggregator.normalization import normalize_comparison_text
from sra_nexus.common.models import normalize_utc_datetime, thaw_json_object
from sra_nexus.common.types import EntityId, EntityRelationshipId, InstrumentId
from sra_nexus.reference.enums import AssetType, ReferenceResolutionStatus
from sra_nexus.reference.models import (
    Entity,
    EntityInstrumentLink,
    EntityRelationship,
    Instrument,
)
from sra_nexus.reference.repositories import (
    EntityResolution,
    InstrumentResolution,
    ReferenceRecordAlreadyExistsError,
    ReferenceRepositoryError,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    canonical_name_key TEXT NOT NULL,
    metadata TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    alias TEXT NOT NULL,
    alias_key TEXT NOT NULL,
    PRIMARY KEY (entity_id, alias),
    UNIQUE (entity_id, ordinal),
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id)
);

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    ticker_key TEXT NOT NULL,
    exchange TEXT NOT NULL,
    exchange_key TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    sector TEXT,
    industry TEXT,
    country TEXT,
    tick_size TEXT,
    lot_size TEXT
);

CREATE TABLE IF NOT EXISTS entity_instrument_links (
    link_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    valid_from TEXT,
    valid_to TEXT,
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE TABLE IF NOT EXISTS entity_relationships (
    relationship_id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    magnitude REAL NOT NULL CHECK (magnitude >= 0 AND magnitude <= 1),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    valid_from TEXT,
    valid_to TEXT,
    FOREIGN KEY (source_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY (target_entity_id) REFERENCES entities(entity_id)
);

CREATE INDEX IF NOT EXISTS ix_entities_canonical_name
ON entities (canonical_name_key, entity_id);

CREATE INDEX IF NOT EXISTS ix_entity_aliases_lookup
ON entity_aliases (alias_key, entity_id);

CREATE INDEX IF NOT EXISTS ix_instruments_ticker
ON instruments (ticker_key, exchange_key, asset_type, instrument_id);

CREATE INDEX IF NOT EXISTS ix_entity_instrument_links_entity_validity
ON entity_instrument_links (entity_id, valid_from, valid_to, instrument_id);

CREATE INDEX IF NOT EXISTS ix_entity_instrument_links_instrument_validity
ON entity_instrument_links (instrument_id, valid_from, valid_to, entity_id);

CREATE INDEX IF NOT EXISTS ix_entity_relationships_source_validity
ON entity_relationships (source_entity_id, valid_from, valid_to, target_entity_id);

CREATE INDEX IF NOT EXISTS ix_entity_relationships_target_validity
ON entity_relationships (target_entity_id, valid_from, valid_to, source_entity_id);
"""


class SQLiteReferenceRepository:
    """SQLite implementation of entity, instrument, and relationship repositories."""

    def __init__(self, database_path: str | Path) -> None:
        """Configure an explicitly initialized reference-data repository."""
        self._database_path = Path(database_path)

    def initialize_schema(self) -> None:
        """Create reference tables, foreign keys, and deterministic lookup indexes."""
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def insert_entity(self, entity: Entity) -> None:
        """Insert an entity and its ordered aliases without an update path."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO entities (
                        entity_id, entity_type, canonical_name, canonical_name_key, metadata
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(entity.entity_id),
                        entity.entity_type.value,
                        entity.canonical_name,
                        _reference_key(entity.canonical_name),
                        _json_dumps(thaw_json_object(entity.metadata)),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO entity_aliases (entity_id, ordinal, alias, alias_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        (str(entity.entity_id), ordinal, alias, _reference_key(alias))
                        for ordinal, alias in enumerate(entity.aliases)
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ReferenceRecordAlreadyExistsError(str(entity.entity_id)) from error

    def get_entity(self, entity_id: EntityId) -> Entity | None:
        """Return an entity and aliases by stable internal identity."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM entities WHERE entity_id = ?",
                (str(entity_id),),
            ).fetchone()
            return None if row is None else self._row_to_entity(connection, row)

    def list_entities(self) -> tuple[Entity, ...]:
        """Return all canonical entities in deterministic identity order."""
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM entities ORDER BY entity_id ASC").fetchall()
            return tuple(self._row_to_entity(connection, row) for row in rows)

    def resolve_canonical_name(self, name: str) -> EntityResolution:
        """Resolve an exact normalized canonical name with explicit ambiguity."""
        return self._resolve_entities(
            query=name,
            statement=(
                "SELECT * FROM entities WHERE canonical_name_key = ? ORDER BY entity_id ASC"
            ),
        )

    def resolve_alias(self, alias: str) -> EntityResolution:
        """Resolve an exact normalized alias with explicit ambiguity."""
        return self._resolve_entities(
            query=alias,
            statement="""
                SELECT DISTINCT entity.*
                FROM entity_aliases AS alias
                JOIN entities AS entity ON entity.entity_id = alias.entity_id
                WHERE alias.alias_key = ?
                ORDER BY entity.entity_id ASC
            """,
        )

    def list_aliases(self, entity_id: EntityId) -> tuple[str, ...]:
        """Return aliases in stored canonical order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT alias
                FROM entity_aliases
                WHERE entity_id = ?
                ORDER BY ordinal ASC
                """,
                (str(entity_id),),
            ).fetchall()
        return tuple(row["alias"] for row in rows)

    def insert_instrument(self, instrument: Instrument) -> None:
        """Insert an instrument without selecting identity by ticker."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO instruments (
                        instrument_id, ticker, ticker_key, exchange, exchange_key,
                        asset_type, currency, sector, industry, country, tick_size, lot_size
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(instrument.instrument_id),
                        instrument.ticker,
                        _ticker_key(instrument.ticker),
                        instrument.exchange,
                        _reference_key(instrument.exchange),
                        instrument.asset_type.value,
                        instrument.currency,
                        instrument.sector,
                        instrument.industry,
                        instrument.country,
                        _serialize_decimal(instrument.tick_size),
                        _serialize_decimal(instrument.lot_size),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ReferenceRecordAlreadyExistsError(str(instrument.instrument_id)) from error

    def get_instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        """Return an instrument by stable internal identity."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM instruments WHERE instrument_id = ?",
                (str(instrument_id),),
            ).fetchone()
        return None if row is None else _row_to_instrument(row)

    def resolve_ticker(
        self,
        ticker: str,
        *,
        exchange: str | None = None,
        asset_type: AssetType | None = None,
    ) -> InstrumentResolution:
        """Resolve ticker plus optional venue/class constraints without guessing."""
        ticker_key = _ticker_key(ticker)
        clauses = ["ticker_key = ?"]
        parameters: list[object] = [ticker_key]
        normalized_exchange: str | None = None
        if exchange is not None:
            normalized_exchange = exchange.strip()
            if not normalized_exchange:
                raise ValueError("exchange must not be blank")
            clauses.append("exchange_key = ?")
            parameters.append(_reference_key(normalized_exchange))
        if asset_type is not None:
            clauses.append("asset_type = ?")
            parameters.append(asset_type.value)
        statement = (
            "SELECT * FROM instruments WHERE "
            + " AND ".join(clauses)
            + " ORDER BY instrument_id ASC"
        )
        with closing(self._connect()) as connection:
            rows = connection.execute(statement, parameters).fetchall()
        candidates = tuple(_row_to_instrument(row) for row in rows)
        return InstrumentResolution(
            status=_resolution_status(len(candidates)),
            ticker=ticker.strip().upper(),
            exchange=normalized_exchange,
            asset_type=asset_type,
            candidates=candidates,
        )

    def insert_entity_instrument_link(self, link: EntityInstrumentLink) -> None:
        """Insert an explicit time-bounded entity-to-instrument mapping."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO entity_instrument_links (
                        link_id, entity_id, instrument_id, relationship_type,
                        confidence, valid_from, valid_to
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(link.link_id),
                        str(link.entity_id),
                        str(link.instrument_id),
                        link.relationship_type.value,
                        link.confidence,
                        _serialize_optional_datetime(link.valid_from),
                        _serialize_optional_datetime(link.valid_to),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ReferenceRepositoryError(
                f"cannot insert entity-instrument link {link.link_id}"
            ) from error

    def list_instrument_links_for_entity(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[EntityInstrumentLink, ...]:
        """Return entity-to-instrument links valid at the historical cutoff."""
        return self._list_entity_instrument_links("entity_id", str(entity_id), as_of)

    def list_entity_links_for_instrument(
        self,
        instrument_id: InstrumentId,
        as_of: datetime,
    ) -> tuple[EntityInstrumentLink, ...]:
        """Return instrument-to-entity links valid at the historical cutoff."""
        return self._list_entity_instrument_links("instrument_id", str(instrument_id), as_of)

    def list_instruments_for_entity(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[Instrument, ...]:
        """Return explicitly associated instruments in stable identity order."""
        links = self.list_instrument_links_for_entity(entity_id, as_of)
        instruments = (self.get_instrument(link.instrument_id) for link in links)
        return tuple(instrument for instrument in instruments if instrument is not None)

    def insert_relationship(self, relationship: EntityRelationship) -> None:
        """Insert one immutable validity-aware entity edge."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO entity_relationships (
                        relationship_id, source_entity_id, target_entity_id,
                        relation_type, direction, magnitude, confidence, valid_from, valid_to
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(relationship.relationship_id),
                        str(relationship.source_entity_id),
                        str(relationship.target_entity_id),
                        relationship.relation_type.value,
                        relationship.direction.value,
                        relationship.magnitude,
                        relationship.confidence,
                        _serialize_optional_datetime(relationship.valid_from),
                        _serialize_optional_datetime(relationship.valid_to),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ReferenceRepositoryError(
                f"cannot insert entity relationship {relationship.relationship_id}"
            ) from error

    def get_relationship(
        self,
        relationship_id: EntityRelationshipId,
    ) -> EntityRelationship | None:
        """Return one structural relationship by internal identity."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM entity_relationships WHERE relationship_id = ?",
                (str(relationship_id),),
            ).fetchone()
        return None if row is None else _row_to_relationship(row)

    def list_outgoing_relationships(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[EntityRelationship, ...]:
        """Return valid edges whose source is ``entity_id``."""
        return self._list_relationships("source_entity_id", entity_id, as_of)

    def list_incoming_relationships(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[EntityRelationship, ...]:
        """Return valid edges whose target is ``entity_id``."""
        return self._list_relationships("target_entity_id", entity_id, as_of)

    def _resolve_entities(self, *, query: str, statement: str) -> EntityResolution:
        query_key = _reference_key(query)
        with closing(self._connect()) as connection:
            rows = connection.execute(statement, (query_key,)).fetchall()
            candidates = tuple(self._row_to_entity(connection, row) for row in rows)
        return EntityResolution(
            status=_resolution_status(len(candidates)),
            query=query,
            candidates=candidates,
        )

    def _list_entity_instrument_links(
        self,
        column: str,
        identifier: str,
        as_of: datetime,
    ) -> tuple[EntityInstrumentLink, ...]:
        if column not in {"entity_id", "instrument_id"}:
            raise ValueError("unsupported entity-instrument lookup column")
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM entity_instrument_links
                WHERE {column} = ?
                  AND (valid_from IS NULL OR valid_from <= ?)
                  AND (valid_to IS NULL OR valid_to > ?)
                ORDER BY instrument_id ASC, entity_id ASC, link_id ASC
                """,
                (identifier, cutoff, cutoff),
            ).fetchall()
        return tuple(EntityInstrumentLink.model_validate(dict(row)) for row in rows)

    def _list_relationships(
        self,
        column: str,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[EntityRelationship, ...]:
        if column not in {"source_entity_id", "target_entity_id"}:
            raise ValueError("unsupported relationship lookup column")
        cutoff = _serialize_datetime(normalize_utc_datetime(as_of))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM entity_relationships
                WHERE {column} = ?
                  AND (valid_from IS NULL OR valid_from <= ?)
                  AND (valid_to IS NULL OR valid_to > ?)
                ORDER BY relationship_id ASC
                """,
                (str(entity_id), cutoff, cutoff),
            ).fetchall()
        return tuple(_row_to_relationship(row) for row in rows)

    @staticmethod
    def _row_to_entity(connection: sqlite3.Connection, row: sqlite3.Row) -> Entity:
        aliases = connection.execute(
            """
            SELECT alias FROM entity_aliases WHERE entity_id = ? ORDER BY ordinal ASC
            """,
            (row["entity_id"],),
        ).fetchall()
        return Entity.model_validate(
            {
                "entity_id": row["entity_id"],
                "entity_type": row["entity_type"],
                "canonical_name": row["canonical_name"],
                "aliases": [alias["alias"] for alias in aliases],
                "metadata": json.loads(row["metadata"]),
            }
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _row_to_instrument(row: sqlite3.Row) -> Instrument:
    return Instrument.model_validate(
        {
            "instrument_id": row["instrument_id"],
            "ticker": row["ticker"],
            "exchange": row["exchange"],
            "asset_type": row["asset_type"],
            "currency": row["currency"],
            "sector": row["sector"],
            "industry": row["industry"],
            "country": row["country"],
            "tick_size": row["tick_size"],
            "lot_size": row["lot_size"],
        }
    )


def _row_to_relationship(row: sqlite3.Row) -> EntityRelationship:
    return EntityRelationship.model_validate(dict(row))


def _resolution_status(candidate_count: int) -> ReferenceResolutionStatus:
    if candidate_count == 0:
        return ReferenceResolutionStatus.NOT_FOUND
    if candidate_count == 1:
        return ReferenceResolutionStatus.RESOLVED
    return ReferenceResolutionStatus.AMBIGUOUS


def _reference_key(value: str) -> str:
    normalized = normalize_comparison_text(value)
    if not normalized:
        raise ValueError("reference lookup value must not be blank")
    return normalized


def _ticker_key(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("ticker must not be blank")
    return normalized


def _serialize_datetime(value: datetime) -> str:
    normalized = normalize_utc_datetime(value)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    return None if value is None else _serialize_datetime(value)


def _serialize_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
