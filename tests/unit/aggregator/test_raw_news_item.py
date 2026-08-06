"""Tests for the immutable raw-news contract."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import NewsSourceType, RawNewsItem


def _raw_news_item(**overrides: object) -> RawNewsItem:
    data: dict[str, object] = {
        "source": "Example Wire",
        "source_type": NewsSourceType.WIRE,
        "provider_item_id": "wire-123",
        "headline": "Example headline",
        "body": "Unmodified provider body.",
        "url": "https://example.com/news/123",
        "event_time": datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
        "receive_time": datetime(2026, 1, 2, 12, 0, 1, tzinfo=UTC),
        "process_time": datetime(2026, 1, 2, 12, 0, 2, tzinfo=UTC),
        "provider_tickers": ("EXM",),
        "provider_entities": ("Example Corp",),
        "language": "en-US",
        "raw_metadata": {"priority": 1, "tags": ["breaking"]},
        "content_hash": "a" * 64,
    }
    data.update(overrides)
    return RawNewsItem.model_validate(data)


def test_raw_news_item_creation_preserves_source_data() -> None:
    """A complete raw provider record should validate without normalization."""
    item = _raw_news_item()

    assert isinstance(item.news_id, UUID)
    assert item.source_type is NewsSourceType.WIRE
    assert item.provider_tickers == ("EXM",)
    assert item.raw_metadata["priority"] == 1
    assert item.event_time.tzinfo is UTC


def test_raw_news_item_is_frozen_and_defensively_copies_metadata() -> None:
    """Neither model attributes nor nested raw metadata should be mutable."""
    supplied_metadata: dict[str, object] = {"nested": {"priority": 1}, "tags": ["first"]}
    item = _raw_news_item(raw_metadata=supplied_metadata)
    supplied_metadata["nested"] = {"priority": 99}

    nested = item.raw_metadata["nested"]
    assert isinstance(nested, Mapping)
    assert nested["priority"] == 1
    assert item.raw_metadata["tags"] == ("first",)

    with pytest.raises(TypeError):
        nested["priority"] = 2  # type: ignore[index]
    with pytest.raises(ValidationError, match="frozen"):
        item.headline = "Changed"


def test_raw_news_item_serializes_immutable_metadata_as_json() -> None:
    """Persistence serializers should receive ordinary JSON objects and arrays."""
    item = _raw_news_item(raw_metadata={"nested": {"tags": ["first"]}})

    payload = json.loads(item.model_dump_json())

    assert payload["raw_metadata"] == {"nested": {"tags": ["first"]}}


@pytest.mark.parametrize("field_name", ["event_time", "receive_time", "process_time"])
def test_raw_news_item_rejects_naive_datetimes(field_name: str) -> None:
    """Every raw-news timestamp must be timezone-aware."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        _raw_news_item(**{field_name: datetime(2026, 1, 2, 12, 0)})


def test_raw_news_item_rejects_non_utc_datetime() -> None:
    """Offset-aware timestamps outside UTC should not enter internal models."""
    eastern = timezone(timedelta(hours=-5))

    with pytest.raises(ValidationError, match="must use UTC"):
        _raw_news_item(event_time=datetime(2026, 1, 2, 7, 0, tzinfo=eastern))


@pytest.mark.parametrize(
    ("event_time", "receive_time", "process_time"),
    [
        (
            datetime(2026, 1, 2, 12, 0, 2, tzinfo=UTC),
            datetime(2026, 1, 2, 12, 0, 1, tzinfo=UTC),
            datetime(2026, 1, 2, 12, 0, 3, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
            datetime(2026, 1, 2, 12, 0, 3, tzinfo=UTC),
            datetime(2026, 1, 2, 12, 0, 2, tzinfo=UTC),
        ),
    ],
)
def test_raw_news_item_rejects_impossible_timeline(
    event_time: datetime,
    receive_time: datetime,
    process_time: datetime,
) -> None:
    """A raw item cannot be available before it occurs or is received."""
    with pytest.raises(ValidationError, match="must not be after"):
        _raw_news_item(
            event_time=event_time,
            receive_time=receive_time,
            process_time=process_time,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("content_hash", "not-a-sha256"),
        ("language", "english"),
        ("provider_tickers", ("EXM", "EXM")),
        ("provider_entities", ("Example Corp", "Example Corp")),
        ("raw_metadata", {"invalid": object()}),
    ],
)
def test_raw_news_item_rejects_invalid_source_fields(field_name: str, value: object) -> None:
    """Malformed raw identifiers and non-JSON metadata should be rejected."""
    with pytest.raises(ValidationError):
        _raw_news_item(**{field_name: value})
