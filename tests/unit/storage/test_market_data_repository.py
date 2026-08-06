"""Tests for immutable SQLite raw market-event storage and replay queries."""

import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.support.market_data import INSTRUMENT

from sra_nexus.common.types import BookEventId
from sra_nexus.market_data import BookEvent, MarketEventKind, TradeEvent
from sra_nexus.market_data.sources import MockMarketDataSource
from sra_nexus.storage import (
    MarketEventInsertStatus,
    MarketEventQuery,
    RawMarketEventRepository,
    SQLiteRawMarketEventRepository,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "market_data" / "full_replay.json"


def _repository(tmp_path: Path) -> SQLiteRawMarketEventRepository:
    repository = SQLiteRawMarketEventRepository(tmp_path / "raw-market.sqlite3")
    repository.initialize_schema()
    return repository


def test_sqlite_market_backend_satisfies_repository_protocol(tmp_path: Path) -> None:
    """Domain and replay code should depend on the explicit storage abstraction."""
    repository: RawMarketEventRepository = _repository(tmp_path)

    assert repository.list_for_instrument(INSTRUMENT.instrument_id) == ()


def test_book_trade_and_quote_events_round_trip_exactly(tmp_path: Path) -> None:
    """All normalized event variants and optional fields should retain semantic equality."""
    repository = _repository(tmp_path)
    events = MockMarketDataSource(FIXTURE).read()

    for event in events:
        result = repository.insert(event)
        assert result.status is MarketEventInsertStatus.INSERTED
        event_id = result.incoming_event_id
        assert repository.get(event_id) == event


def test_duplicate_and_conflicting_inserts_never_overwrite(tmp_path: Path) -> None:
    """Exact repeats and identity/sequence conflicts should have distinct outcomes."""
    repository = _repository(tmp_path)
    event = next(
        item for item in MockMarketDataSource(FIXTURE).read() if isinstance(item, BookEvent)
    )
    assert repository.insert(event).inserted

    duplicate = repository.insert(event)
    conflicting_id_event = event.model_copy(update={"quantity": Decimal("999")})
    conflicting_sequence_event = event.model_copy(
        update={
            "event_id": BookEventId.new(),
            "quantity": Decimal("998"),
        }
    )

    assert duplicate.status is MarketEventInsertStatus.DUPLICATE_EVENT
    assert (
        repository.insert(conflicting_id_event).status
        is MarketEventInsertStatus.CONFLICTING_EVENT_ID
    )
    assert (
        repository.insert(conflicting_sequence_event).status
        is MarketEventInsertStatus.CONFLICTING_SEQUENCE
    )
    assert repository.get(event.event_id) == event


def test_sequence_identity_is_independent_per_event_stream(tmp_path: Path) -> None:
    """Book and trade streams may legitimately use the same sequence number."""
    repository = _repository(tmp_path)
    events = MockMarketDataSource(FIXTURE).read()
    book = next(event for event in events if isinstance(event, BookEvent))
    trade = next(event for event in events if isinstance(event, TradeEvent))
    same_sequence_trade = trade.model_copy(update={"sequence_number": book.sequence_number})

    assert repository.insert(book).inserted
    assert repository.insert(same_sequence_trade).inserted


def test_stream_query_uses_inclusive_sequence_range_and_process_cutoff(tmp_path: Path) -> None:
    """Indexed replay queries should be deterministic and historical."""
    repository = _repository(tmp_path)
    events = MockMarketDataSource(FIXTURE).read()
    for event in events:
        repository.insert(event)
    book_events = tuple(event for event in events if isinstance(event, BookEvent))
    cutoff = book_events[1].process_time

    visible = repository.list_stream(
        MarketEventQuery(
            instrument_id=INSTRUMENT.instrument_id,
            venue=INSTRUMENT.exchange,
            event_kind=MarketEventKind.BOOK,
            start_sequence=100,
            end_sequence=103,
            as_of=cutoff,
        )
    )
    ranged = repository.list_stream(
        MarketEventQuery(
            instrument_id=INSTRUMENT.instrument_id,
            venue=INSTRUMENT.exchange,
            event_kind=MarketEventKind.BOOK,
            start_sequence=101,
            end_sequence=102,
        )
    )

    assert tuple(event.sequence_number for event in visible) == (100, 101)
    assert tuple(event.sequence_number for event in ranged) == (101, 102)
    assert all(event.process_time <= cutoff for event in visible)


def test_instrument_query_rejects_naive_historical_cutoff(tmp_path: Path) -> None:
    """Raw market history must not guess the timezone of replay availability."""
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.list_for_instrument(
            INSTRUMENT.instrument_id,
            datetime(2026, 8, 1, 14, 0),
        )


def test_market_schema_contains_immutability_and_replay_indexes(tmp_path: Path) -> None:
    """The raw table should expose explicit event and stream lookup structures."""
    path = tmp_path / "raw-market.sqlite3"
    SQLiteRawMarketEventRepository(path).initialize_schema()
    with closing(sqlite3.connect(path)) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }

    assert {
        "raw_market_events",
        "ix_raw_market_events_instrument_process",
        "ix_raw_market_events_stream_sequence",
    } <= names
