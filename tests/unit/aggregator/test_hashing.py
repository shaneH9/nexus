"""Tests for deterministic raw-news content hashing."""

import re
from datetime import UTC, datetime, timedelta

from sra_nexus.aggregator import NewsSourceType, RawNewsItem
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.hashing import compute_raw_news_content_hash


def _item(**overrides: object) -> RawNewsItem:
    data: dict[str, object] = {
        "source": "Example Wire",
        "source_type": NewsSourceType.WIRE,
        "provider_item_id": "wire-1",
        "headline": "Example headline",
        "body": "First line\nSecond line",
        "url": "https://example.test/1",
        "event_time": datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        "receive_time": datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        "process_time": datetime(2026, 6, 1, 10, 0, 2, tzinfo=UTC),
        "provider_tickers": ["EXM"],
        "provider_entities": [],
        "language": "en",
        "raw_metadata": {},
    }
    data.update(overrides)
    return build_raw_news_item(data)


def test_same_normalized_content_has_same_hash() -> None:
    """Line-ending and surrounding-whitespace normalization should be deterministic."""
    first = _item(body=" First line\r\nSecond line ", url=" https://example.test/1 ")
    second = _item(body="First line\nSecond line", url="https://example.test/1")

    assert compute_raw_news_content_hash(first) == compute_raw_news_content_hash(second)


def test_receive_time_does_not_change_content_hash() -> None:
    """Receipt latency is excluded from persistent content identity."""
    first = _item()
    second = _item(
        receive_time=first.receive_time + timedelta(milliseconds=500),
    )

    assert compute_raw_news_content_hash(first) == compute_raw_news_content_hash(second)


def test_process_time_does_not_change_content_hash() -> None:
    """Processing latency is excluded from persistent content identity."""
    first = _item()
    second = _item(process_time=first.process_time + timedelta(seconds=10))

    assert compute_raw_news_content_hash(first) == compute_raw_news_content_hash(second)


def test_provider_item_id_does_not_change_content_hash() -> None:
    """Provider identifiers use their own duplicate rule and are excluded from content."""
    first = _item(provider_item_id="wire-1")
    second = _item(provider_item_id="wire-renumbered")

    assert compute_raw_news_content_hash(first) == compute_raw_news_content_hash(second)


def test_meaningful_content_change_changes_hash() -> None:
    """Changing article content must produce a different raw identity."""
    first = _item(headline="Original headline")
    second = _item(headline="Corrected headline")

    assert compute_raw_news_content_hash(first) != compute_raw_news_content_hash(second)


def test_hash_is_stable_sha256_hex() -> None:
    """Repeated hashing should return one stable lowercase 64-character digest."""
    item = _item()

    first = compute_raw_news_content_hash(item)
    second = compute_raw_news_content_hash(item)

    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)
