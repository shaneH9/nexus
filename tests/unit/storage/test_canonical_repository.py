"""Tests for SQLite immutable canonical-event revision persistence."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sra_nexus.aggregator import (
    CanonicalEvent,
    EventState,
    EventSubtype,
    EventType,
    NewsSourceType,
)
from sra_nexus.aggregator.normalization import comparison_tokens
from sra_nexus.aggregator.revisions import (
    CanonicalEventCandidateQuery,
    CanonicalEventRevision,
)
from sra_nexus.common.types import CanonicalEventId, NewsId
from sra_nexus.storage import (
    CanonicalEventRepository,
    CanonicalRevisionConflictError,
    NewsAlreadyCanonicalizedError,
    SQLiteCanonicalEventRepository,
)

BASE_TIME = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def _revision(
    *,
    event_id: CanonicalEventId | None = None,
    news_ids: tuple[NewsId, ...] | None = None,
    revision_number: int = 1,
    available_at: datetime = BASE_TIME,
    headline: str = "Acme agrees to acquire Beta",
    event_state: EventState = EventState.NEW,
    event_type: EventType = EventType.COMPANY,
    event_subtype: EventSubtype = EventSubtype.COMPANY_MERGER_ACQUISITION,
    anchors: tuple[str, ...] = ("acme", "beta"),
    ticker_anchors: tuple[str, ...] = ("acme", "beta"),
    source_names: tuple[str, ...] = ("Example Wire",),
    source_types: tuple[NewsSourceType, ...] = (NewsSourceType.WIRE,),
) -> CanonicalEventRevision:
    resolved_event_id = CanonicalEventId.new() if event_id is None else event_id
    resolved_news_ids = (NewsId.new(),) if news_ids is None else news_ids
    event = CanonicalEvent(
        event_id=resolved_event_id,
        first_event_time=BASE_TIME - timedelta(seconds=2),
        first_receive_time=BASE_TIME - timedelta(seconds=1),
        last_update_time=available_at,
        event_type=event_type,
        event_subtype=event_subtype,
        headline_summary=headline,
        source_news_ids=resolved_news_ids,
        event_state=event_state,
    )
    return CanonicalEventRevision(
        revision_number=revision_number,
        available_at=available_at,
        event=event,
        headline_tokens=tuple(sorted(comparison_tokens(headline))),
        anchors=anchors,
        ticker_anchors=ticker_anchors,
        source_names=source_names,
        source_types=source_types,
    )


def _repository(tmp_path: Path) -> SQLiteCanonicalEventRepository:
    repository = SQLiteCanonicalEventRepository(tmp_path / "canonical.sqlite3")
    repository.initialize_schema()
    return repository


def test_repository_satisfies_provider_independent_protocol(tmp_path: Path) -> None:
    """The SQLite backend should be usable solely through the canonical protocol."""
    repository: CanonicalEventRepository = _repository(tmp_path)

    assert repository.list_events_available_as_of(BASE_TIME) == ()


def test_schema_initialization_creates_history_and_candidate_indexes(tmp_path: Path) -> None:
    """Explicit initialization should create immutable-history support structures."""
    database_path = tmp_path / "canonical.sqlite3"
    SQLiteCanonicalEventRepository(database_path).initialize_schema()

    with closing(sqlite3.connect(database_path)) as connection:
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name LIKE 'canonical%' OR name LIKE 'ix_canonical%'"
        ).fetchall()
    names = {name for _, name in objects}

    assert "canonical_events" in names
    assert "canonical_event_revisions" in names
    assert "canonical_event_revision_sources" in names
    assert "canonical_event_revision_anchors" in names
    assert "canonicalized_news" in names
    assert "ix_canonical_revisions_candidates" in names
    assert "ix_canonical_revisions_event_available" in names


def test_create_and_current_event_round_trip(tmp_path: Path) -> None:
    """Creation should losslessly reconstruct event state and revision metadata."""
    repository = _repository(tmp_path)
    revision = _revision()

    repository.create_event(revision)

    assert repository.get_current_event(revision.event.event_id) == revision.event
    assert repository.get_current_revision(revision.event.event_id) == revision
    assert repository.get_event_revision(revision.event.event_id, 1) == revision
    assert repository.get_event_id_for_news(revision.event.source_news_ids[0]) == (
        revision.event.event_id
    )


def test_append_preserves_prior_revision_and_monotonic_numbers(tmp_path: Path) -> None:
    """Appending should retain revision one and add exactly the next revision."""
    repository = _repository(tmp_path)
    first = _revision()
    second_news = NewsId.new()
    second = _revision(
        event_id=first.event.event_id,
        news_ids=(*first.event.source_news_ids, second_news),
        revision_number=2,
        available_at=BASE_TIME + timedelta(minutes=5),
        headline="Acme announces agreement to acquire Beta",
        event_state=EventState.DEVELOPING,
        source_names=("Example Wire", "Second Provider"),
        source_types=(NewsSourceType.WIRE, NewsSourceType.FINANCIAL_NEWS),
    )
    repository.create_event(first)

    repository.append_revision(second)

    assert repository.list_event_revisions(first.event.event_id) == (first, second)
    assert repository.get_current_event(first.event.event_id) == second.event
    assert repository.get_event_id_for_news(second_news) == first.event.event_id


def test_append_rejects_non_monotonic_revision_number(tmp_path: Path) -> None:
    """The repository should reject gaps and competing revision numbers."""
    repository = _repository(tmp_path)
    first = _revision()
    repository.create_event(first)
    invalid = _revision(
        event_id=first.event.event_id,
        news_ids=(*first.event.source_news_ids, NewsId.new()),
        revision_number=3,
        available_at=BASE_TIME + timedelta(minutes=5),
    )

    with pytest.raises(CanonicalRevisionConflictError, match="revision_number must be 2"):
        repository.append_revision(invalid)

    assert repository.list_event_revisions(first.event.event_id) == (first,)


def test_duplicate_news_id_cannot_belong_to_two_events(tmp_path: Path) -> None:
    """Global NewsId assignment should prevent accidental double canonicalization."""
    repository = _repository(tmp_path)
    shared_news_id = NewsId.new()
    first = _revision(news_ids=(shared_news_id,))
    competing = _revision(news_ids=(shared_news_id,), headline="Different event")
    repository.create_event(first)

    with pytest.raises(NewsAlreadyCanonicalizedError, match="already belongs"):
        repository.create_event(competing)

    assert repository.get_current_event(competing.event.event_id) is None


def test_historical_query_never_exposes_future_revision(tmp_path: Path) -> None:
    """get_event_as_of should materialize only the latest available revision."""
    repository = _repository(tmp_path)
    first = _revision(available_at=BASE_TIME)
    second = _revision(
        event_id=first.event.event_id,
        news_ids=(*first.event.source_news_ids, NewsId.new()),
        revision_number=2,
        available_at=BASE_TIME + timedelta(minutes=5),
        headline="Confirmed acquisition agreement",
        event_state=EventState.DEVELOPING,
        source_names=("Example Wire", "Second Provider"),
        source_types=(NewsSourceType.WIRE, NewsSourceType.FINANCIAL_NEWS),
    )
    repository.create_event(first)
    repository.append_revision(second)

    assert (
        repository.get_event_as_of(first.event.event_id, BASE_TIME - timedelta(microseconds=1))
        is None
    )
    assert repository.get_event_as_of(first.event.event_id, BASE_TIME + timedelta(minutes=2)) == (
        first.event
    )
    assert repository.get_event_as_of(first.event.event_id, BASE_TIME + timedelta(minutes=10)) == (
        second.event
    )


def test_candidate_retrieval_uses_historical_latest_revision_and_anchor(tmp_path: Path) -> None:
    """Indexed retrieval should return the latest eligible state without future data."""
    repository = _repository(tmp_path)
    first = _revision(available_at=BASE_TIME)
    second = _revision(
        event_id=first.event.event_id,
        news_ids=(*first.event.source_news_ids, NewsId.new()),
        revision_number=2,
        available_at=BASE_TIME + timedelta(minutes=5),
        headline="Acme confirms acquisition of Beta",
        event_state=EventState.DEVELOPING,
        source_names=("Example Wire", "Second Provider"),
        source_types=(NewsSourceType.WIRE, NewsSourceType.FINANCIAL_NEWS),
    )
    unrelated = _revision(
        available_at=BASE_TIME,
        anchors=("msft",),
        ticker_anchors=("msft",),
        headline="Microsoft agrees to acquire Delta",
    )
    repository.create_event(first)
    repository.append_revision(second)
    repository.create_event(unrelated)

    early = repository.find_candidates(
        CanonicalEventCandidateQuery(
            event_type=EventType.COMPANY,
            event_subtype=EventSubtype.COMPANY_MERGER_ACQUISITION,
            not_before=BASE_TIME - timedelta(hours=1),
            as_of=BASE_TIME + timedelta(minutes=2),
            anchors=("acme",),
        )
    )
    later = repository.find_candidates(
        CanonicalEventCandidateQuery(
            event_type=EventType.COMPANY,
            event_subtype=EventSubtype.COMPANY_MERGER_ACQUISITION,
            not_before=BASE_TIME - timedelta(hours=1),
            as_of=BASE_TIME + timedelta(minutes=10),
            anchors=("acme",),
        )
    )

    assert early == (first,)
    assert later == (second,)


def test_list_events_as_of_is_stably_ordered_by_availability_then_id(tmp_path: Path) -> None:
    """Historical multi-event results should have deterministic availability ordering."""
    repository = _repository(tmp_path)
    later = _revision(available_at=BASE_TIME + timedelta(minutes=1))
    earlier = _revision(available_at=BASE_TIME)
    repository.create_event(later)
    repository.create_event(earlier)

    events = repository.list_events_available_as_of(BASE_TIME + timedelta(minutes=2))

    assert events == (earlier.event, later.event)


def test_historical_queries_reject_naive_cutoffs_and_normalize_offsets(tmp_path: Path) -> None:
    """Canonical history should reuse strict aware-UTC cutoff semantics."""
    repository = _repository(tmp_path)
    revision = _revision()
    repository.create_event(revision)

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.get_event_as_of(revision.event.event_id, datetime(2026, 7, 1, 10, 0))

    eastern = timezone(timedelta(hours=-4))
    assert (
        repository.get_event_as_of(
            revision.event.event_id,
            datetime(2026, 7, 1, 6, 0, tzinfo=eastern),
        )
        == revision.event
    )
