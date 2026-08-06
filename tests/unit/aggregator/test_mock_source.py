"""Tests for the deterministic fixture-backed news source."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sra_nexus.aggregator import NewsSourceType
from sra_nexus.aggregator.hashing import compute_raw_news_content_hash
from sra_nexus.aggregator.sources import MockNewsSource, MockNewsSourceFormatError, NewsSource

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "news"


def test_mock_source_satisfies_provider_independent_interface() -> None:
    """The fixture adapter should be usable through the NewsSource protocol."""
    source: NewsSource = MockNewsSource(FIXTURE_DIR / "representative.json")

    assert source.fetch().received_count == 5


def test_mock_source_loads_multiple_valid_records() -> None:
    """Representative provider records should become validated raw items."""
    batch = MockNewsSource(FIXTURE_DIR / "representative.json").fetch()

    assert len(batch.items) == 5
    assert batch.failures == ()
    assert batch.items[0].headline == "Example Corp raises annual guidance"


def test_mock_source_emits_computed_sha256_content_hashes() -> None:
    """Every emitted item should contain its real canonical content digest."""
    items = MockNewsSource(FIXTURE_DIR / "representative.json").fetch().items

    assert items
    for item in items:
        assert re.fullmatch(r"[0-9a-f]{64}", item.content_hash)
        assert item.content_hash == compute_raw_news_content_hash(item)


def test_mock_source_normalizes_tickers_and_utc_offsets() -> None:
    """RawNewsItem validation should normalize provider symbols and timestamps."""
    first = MockNewsSource(FIXTURE_DIR / "representative.json").fetch().items[0]

    assert first.provider_tickers == ("EXM", "OTH")
    assert first.event_time == datetime(2026, 6, 1, 13, 0, tzinfo=UTC)
    assert first.event_time.tzinfo is UTC


def test_mock_source_preserves_optional_and_nested_provider_fields() -> None:
    """Null fields and arbitrary nested metadata should survive the source boundary."""
    items = MockNewsSource(FIXTURE_DIR / "representative.json").fetch().items
    government_item = items[1]
    first_metadata = items[0].raw_metadata

    assert government_item.body is None
    assert government_item.url is None
    assert government_item.language is None
    routing = first_metadata["routing"]
    assert isinstance(routing, Mapping)
    assert routing["regions"] == ("US", "CA")


def test_mock_source_loads_speculative_record_without_special_handling() -> None:
    """Generic speculative data should use the ordinary RawNewsItem path."""
    items = MockNewsSource(FIXTURE_DIR / "representative.json").fetch().items
    speculative = items[2]

    assert speculative.source_type is NewsSourceType.SPECULATIVE
    assert speculative.provider_tickers == ("SPEC",)


def test_similar_articles_from_different_providers_remain_distinct() -> None:
    """The source adapter must not perform semantic event deduplication."""
    items = MockNewsSource(FIXTURE_DIR / "representative.json").fetch().items
    alpha, beta = items[3], items[4]

    assert alpha.source != beta.source
    assert alpha.content_hash != beta.content_hash


def test_duplicate_fixture_records_are_both_emitted_for_repository_policy() -> None:
    """Raw duplicate classification belongs to repository insertion, not the source."""
    batch = MockNewsSource(FIXTURE_DIR / "duplicates.json").fetch()

    assert len(batch.items) == 2
    assert batch.items[0].provider_item_id == batch.items[1].provider_item_id
    assert batch.items[0].content_hash == batch.items[1].content_hash


def test_naive_fixture_timestamp_becomes_record_failure() -> None:
    """A malformed timestamp should fail its record without being silently fixed."""
    batch = MockNewsSource(FIXTURE_DIR / "naive_timestamp.json").fetch()

    assert batch.received_count == 1
    assert batch.items == ()
    assert len(batch.failures) == 1
    assert "timezone-aware" in batch.failures[0].message


def test_malformed_record_does_not_discard_valid_peers() -> None:
    """Per-record validation failure should preserve other records in the batch."""
    batch = MockNewsSource(FIXTURE_DIR / "mixed.json").fetch()

    assert batch.received_count == 3
    assert len(batch.items) == 2
    assert len(batch.failures) == 1
    assert batch.failures[0].provider_reference == "bad-700"


def test_invalid_fixture_shape_raises_explicit_format_error(tmp_path: Path) -> None:
    """Batch-level fixture corruption should propagate instead of becoming an item failure."""
    fixture = tmp_path / "invalid.json"
    fixture.write_text('{"not_records": []}', encoding="utf-8")

    with pytest.raises(MockNewsSourceFormatError, match="records list"):
        MockNewsSource(fixture).fetch()
