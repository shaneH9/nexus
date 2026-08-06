"""Tests for process-time-gated historical raw-news availability."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from sra_nexus.aggregator import NewsSourceType, RawNewsItem
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.common.types import NewsId
from sra_nexus.storage import SQLiteRawNewsRepository


def _timed_item(
    *,
    news_id: str,
    headline: str,
    event_time: datetime,
    receive_time: datetime,
    process_time: datetime,
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "news_id": NewsId(UUID(news_id)),
            "source": "Replay Wire",
            "source_type": NewsSourceType.WIRE,
            "provider_item_id": news_id,
            "headline": headline,
            "event_time": event_time,
            "receive_time": receive_time,
            "process_time": process_time,
        }
    )


def _repository(tmp_path: Path) -> SQLiteRawNewsRepository:
    repository = SQLiteRawNewsRepository(tmp_path / "availability.sqlite3")
    repository.initialize_schema()
    return repository


def test_as_of_query_uses_process_time_not_event_or_receive_time(tmp_path: Path) -> None:
    """Replay availability should expose records only once processing completes."""
    repository = _repository(tmp_path)
    first = _timed_item(
        news_id="00000000-0000-0000-0000-000000000001",
        headline="First",
        event_time=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        receive_time=datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        process_time=datetime(2026, 6, 1, 10, 0, 2, tzinfo=UTC),
    )
    second = _timed_item(
        news_id="00000000-0000-0000-0000-000000000002",
        headline="Second",
        event_time=datetime(2026, 6, 1, 9, 59, tzinfo=UTC),
        receive_time=datetime(2026, 6, 1, 10, 0, 3, tzinfo=UTC),
        process_time=datetime(2026, 6, 1, 10, 0, 4, tzinfo=UTC),
    )
    repository.insert(second)
    repository.insert(first)

    assert repository.list_available_as_of(datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC)) == ()
    assert repository.list_available_as_of(datetime(2026, 6, 1, 10, 0, 2, tzinfo=UTC)) == (first,)
    assert repository.list_available_as_of(datetime(2026, 6, 1, 10, 0, 3, tzinfo=UTC)) == (first,)
    assert repository.list_available_as_of(datetime(2026, 6, 1, 10, 0, 4, tzinfo=UTC)) == (
        first,
        second,
    )


def test_as_of_query_normalizes_aware_cutoff_to_utc(tmp_path: Path) -> None:
    """An aware non-UTC cutoff should have the same instant semantics as UTC."""
    repository = _repository(tmp_path)
    item = _timed_item(
        news_id="00000000-0000-0000-0000-000000000003",
        headline="Timezone",
        event_time=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        receive_time=datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        process_time=datetime(2026, 6, 1, 10, 0, 2, tzinfo=UTC),
    )
    repository.insert(item)

    eastern = timezone(timedelta(hours=-4))
    cutoff = datetime(2026, 6, 1, 6, 0, 2, tzinfo=eastern)

    assert repository.list_available_as_of(cutoff) == (item,)


def test_as_of_query_rejects_naive_cutoff(tmp_path: Path) -> None:
    """Historical queries must not silently guess the timezone of a cutoff."""
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.list_available_as_of(datetime(2026, 6, 1, 10, 0))


def test_equal_process_times_are_ordered_by_news_id(tmp_path: Path) -> None:
    """Stable internal-ID ordering should break ties at the availability boundary."""
    repository = _repository(tmp_path)
    process_time = datetime(2026, 6, 1, 10, 0, 2, tzinfo=UTC)
    later_id = _timed_item(
        news_id="00000000-0000-0000-0000-000000000020",
        headline="Later ID",
        event_time=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        receive_time=datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        process_time=process_time,
    )
    earlier_id = _timed_item(
        news_id="00000000-0000-0000-0000-000000000010",
        headline="Earlier ID",
        event_time=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        receive_time=datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        process_time=process_time,
    )
    repository.insert(later_id)
    repository.insert(earlier_id)

    assert repository.list_available_as_of(process_time) == (earlier_id, later_id)


def test_requested_raw_subset_remains_process_time_gated(tmp_path: Path) -> None:
    """Indexed source-ID lookup must not expose a requested future observation."""
    repository = _repository(tmp_path)
    first = _timed_item(
        news_id="00000000-0000-0000-0000-000000000031",
        headline="Visible",
        event_time=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        receive_time=datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        process_time=datetime(2026, 6, 1, 10, 0, 2, tzinfo=UTC),
    )
    second = _timed_item(
        news_id="00000000-0000-0000-0000-000000000032",
        headline="Future",
        event_time=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        receive_time=datetime(2026, 6, 1, 10, 0, 3, tzinfo=UTC),
        process_time=datetime(2026, 6, 1, 10, 0, 4, tzinfo=UTC),
    )
    repository.insert(first)
    repository.insert(second)

    assert repository.get_many_available_as_of(
        (second.news_id, first.news_id, first.news_id),
        datetime(2026, 6, 1, 10, 0, 3, tzinfo=UTC),
    ) == (first,)
    assert repository.get_many_available_as_of((), datetime(2026, 6, 1, tzinfo=UTC)) == ()
