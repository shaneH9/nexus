"""Tests for the immutable raw-news contract."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone

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
        "url": "provider://items/123",
        "event_time": datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
        "receive_time": datetime(2026, 1, 2, 12, 0, 1, tzinfo=UTC),
        "process_time": datetime(2026, 1, 2, 12, 0, 2, tzinfo=UTC),
        "provider_tickers": [" exm ", "EXM", "oth"],
        "provider_entities": ["Example Corp"],
        "language": "en-US",
        "raw_metadata": {"priority": 1, "tags": ["breaking"]},
        "content_hash": "provider-digest-123",
    }
    data.update(overrides)
    return RawNewsItem.model_validate(data)


def test_raw_news_item_valid_creation_and_ticker_normalization() -> None:
    """Raw news should preserve provider data while normalizing ticker metadata."""
    item = _raw_news_item()

    assert item.provider_tickers == ("EXM", "OTH")
    assert item.source_type is NewsSourceType.WIRE
    assert item.provider_item_id == "wire-123"


def test_raw_news_item_allows_optional_provider_fields() -> None:
    """Provider ID, body, URL, and language may be unavailable."""
    item = _raw_news_item(provider_item_id=None, body=None, url=None, language=None)

    assert item.provider_item_id is None
    assert item.language is None


@pytest.mark.parametrize("field_name", ["event_time", "receive_time", "process_time"])
def test_raw_news_item_rejects_naive_datetime(field_name: str) -> None:
    """Every raw-news timestamp must be timezone-aware."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        _raw_news_item(**{field_name: datetime(2026, 1, 2, 12, 0)})


def test_raw_news_item_normalizes_aware_datetime_to_utc() -> None:
    """Offset-aware source timestamps should be retained as equivalent UTC instants."""
    eastern = timezone(timedelta(hours=-5))

    item = _raw_news_item(event_time=datetime(2026, 1, 2, 7, 0, tzinfo=eastern))

    assert item.event_time == datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    assert item.event_time.tzinfo is UTC


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
def test_raw_news_item_rejects_invalid_timestamp_order(
    event_time: datetime,
    receive_time: datetime,
    process_time: datetime,
) -> None:
    """Raw observations cannot become available before occurrence and receipt."""
    with pytest.raises(ValidationError, match="must not be after"):
        _raw_news_item(
            event_time=event_time,
            receive_time=receive_time,
            process_time=process_time,
        )


@pytest.mark.parametrize("field_name", ["source", "headline", "content_hash"])
def test_raw_news_item_rejects_blank_required_text(field_name: str) -> None:
    """Source, headline, and content digest must contain meaningful text."""
    with pytest.raises(ValidationError):
        _raw_news_item(**{field_name: "   "})


def test_raw_news_item_supports_speculative_source_category_only() -> None:
    """SPECULATIVE should classify raw data without adding provider-specific fields."""
    item = _raw_news_item(source_type=NewsSourceType.SPECULATIVE)

    assert item.source_type is NewsSourceType.SPECULATIVE
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _raw_news_item(transaction_date="2026-01-01")


def test_news_source_type_includes_speculative_and_other() -> None:
    """The source taxonomy should match Milestone A, including SPECULATIVE."""
    assert {member.value for member in NewsSourceType} == {
        "FINANCIAL_NEWS",
        "WIRE",
        "SEC",
        "COMPANY_RELEASE",
        "MACRO_CALENDAR",
        "CENTRAL_BANK",
        "GOVERNMENT",
        "GLOBAL_NEWS",
        "SOCIAL",
        "SPECULATIVE",
        "OTHER",
    }


def test_raw_news_item_is_frozen() -> None:
    """Raw source records should not be mutated after construction."""
    item = _raw_news_item()

    with pytest.raises(ValidationError, match="frozen"):
        item.headline = "Changed"


def test_raw_metadata_is_immutable_and_serializes_cleanly() -> None:
    """Opaque provider metadata should freeze in memory and emit ordinary JSON."""
    item = _raw_news_item(raw_metadata={"nested": {"tags": ["first"]}})
    nested = item.raw_metadata["nested"]

    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        nested["tags"] = []  # type: ignore[index]

    payload = json.loads(item.model_dump_json())
    assert payload["raw_metadata"] == {"nested": {"tags": ["first"]}}
    assert isinstance(payload["news_id"], str)
