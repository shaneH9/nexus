"""Tests for immutable SQLite raw-news persistence."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sra_nexus.aggregator import NewsSourceType, RawNewsItem
from sra_nexus.aggregator.hashing import compute_raw_news_content_hash
from sra_nexus.common.models import thaw_json_object
from sra_nexus.storage import RawNewsInsertStatus, SQLiteRawNewsRepository


def _item(**overrides: object) -> RawNewsItem:
    data: dict[str, object] = {
        "source": "Example Wire",
        "source_type": NewsSourceType.WIRE,
        "provider_item_id": "wire-1",
        "headline": "Example headline",
        "body": "Example body",
        "url": "https://example.test/1",
        "event_time": datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        "receive_time": datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        "process_time": datetime(2026, 6, 1, 10, 0, 2, tzinfo=UTC),
        "provider_tickers": ["EXM"],
        "provider_entities": ["Example Corp"],
        "language": "en",
        "raw_metadata": {"nested": {"levels": [1, 2], "active": True}},
        "content_hash": "pending",
    }
    data.update(overrides)
    provisional = RawNewsItem.model_validate(data)
    return provisional.model_copy(
        update={"content_hash": compute_raw_news_content_hash(provisional)}
    )


def _repository(tmp_path: Path) -> SQLiteRawNewsRepository:
    repository = SQLiteRawNewsRepository(tmp_path / "raw-news.sqlite3")
    repository.initialize_schema()
    return repository


def test_schema_initialization_creates_required_constraints_and_index(
    tmp_path: Path,
) -> None:
    """Explicit initialization should create identity and availability indexes."""
    database_path = tmp_path / "raw-news.sqlite3"
    repository = SQLiteRawNewsRepository(database_path)

    repository.initialize_schema()

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    definitions = {name: sql for name, sql in rows if sql is not None}
    assert "ux_raw_news_source_provider_item" in definitions
    assert "WHERE provider_item_id IS NOT NULL" in definitions["ux_raw_news_source_provider_item"]
    assert "ix_raw_news_process_time_news_id" in definitions


def test_insert_and_get_round_trip_all_raw_fields(tmp_path: Path) -> None:
    """SQLite serialization should reconstruct the immutable raw contract exactly."""
    repository = _repository(tmp_path)
    item = _item(source_type=NewsSourceType.SPECULATIVE)

    result = repository.insert(item)
    restored = repository.get(item.news_id)

    assert result.status is RawNewsInsertStatus.INSERTED
    assert result.existing_news_id is None
    assert restored == item
    assert restored is not None
    assert restored.source_type is NewsSourceType.SPECULATIVE
    assert thaw_json_object(restored.raw_metadata) == {"nested": {"levels": [1, 2], "active": True}}
    assert restored.event_time == item.event_time
    assert restored.receive_time == item.receive_time
    assert restored.process_time == item.process_time


def test_duplicate_provider_identity_is_rejected_without_overwrite(tmp_path: Path) -> None:
    """The same source/provider pair should preserve the first immutable record."""
    repository = _repository(tmp_path)
    original = _item()
    redelivery = _item(
        headline="A corrected-looking redelivery",
        receive_time=original.receive_time + timedelta(seconds=5),
        process_time=original.process_time + timedelta(seconds=10),
    )

    first = repository.insert(original)
    duplicate = repository.insert(redelivery)

    assert first.status is RawNewsInsertStatus.INSERTED
    assert duplicate.status is RawNewsInsertStatus.DUPLICATE_PROVIDER_ITEM
    assert duplicate.existing_news_id == original.news_id
    assert repository.get(original.news_id) == original
    assert repository.get(redelivery.news_id) is None


def test_duplicate_content_hash_is_rejected_across_provider_ids(tmp_path: Path) -> None:
    """Identical normalized source content should trigger the content identity rule."""
    repository = _repository(tmp_path)
    original = _item(provider_item_id="wire-1")
    renumbered = _item(provider_item_id="wire-2")

    repository.insert(original)
    result = repository.insert(renumbered)

    assert result.status is RawNewsInsertStatus.DUPLICATE_CONTENT_HASH
    assert result.existing_news_id == original.news_id


def test_duplicate_internal_news_id_is_rejected_without_overwrite(tmp_path: Path) -> None:
    """A colliding internal identifier must never replace an existing record."""
    repository = _repository(tmp_path)
    original = _item()
    collision = _item(
        news_id=original.news_id,
        source="Different Provider",
        provider_item_id="different-2",
        headline="Different content",
    )

    repository.insert(original)
    result = repository.insert(collision)

    assert result.status is RawNewsInsertStatus.DUPLICATE_NEWS_ID
    assert result.existing_news_id == original.news_id
    assert repository.get(original.news_id) == original


def test_null_provider_ids_can_coexist_when_content_differs(tmp_path: Path) -> None:
    """The partial provider-identity constraint should ignore missing provider IDs."""
    repository = _repository(tmp_path)
    first = _item(provider_item_id=None, headline="First no-ID article")
    second = _item(provider_item_id=None, headline="Second no-ID article")

    assert repository.insert(first).inserted
    assert repository.insert(second).inserted


def test_existence_queries_follow_stored_identity_rules(tmp_path: Path) -> None:
    """Repository identity lookups should reflect a successfully stored item."""
    repository = _repository(tmp_path)
    item = _item()

    assert not repository.exists_provider_item(item.source, item.provider_item_id or "")
    assert not repository.exists_content_hash(item.content_hash)

    repository.insert(item)

    assert repository.exists_provider_item(item.source, item.provider_item_id or "")
    assert repository.exists_content_hash(item.content_hash)
