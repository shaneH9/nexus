"""Tests for deterministic canonicalization decisions and event evolution."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sra_nexus.aggregator import (
    CanonicalEvent,
    EventState,
    EventSubtype,
    EventType,
    NewsSourceType,
    RawNewsItem,
)
from sra_nexus.aggregator.canonicalization import (
    CanonicalizationDecisionType,
    CanonicalizationService,
)
from sra_nexus.aggregator.classification import DeterministicEventClassifier
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.normalization import comparison_tokens
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.common.types import NewsId
from sra_nexus.storage import SQLiteCanonicalEventRepository

BASE_TIME = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def _item(
    headline: str,
    *,
    process_time: datetime = BASE_TIME,
    source: str = "Reporter A",
    source_type: NewsSourceType = NewsSourceType.WIRE,
    tickers: tuple[str, ...] = ("ACME", "BETA"),
    entities: tuple[str, ...] = (),
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "source": source,
            "source_type": source_type,
            "provider_item_id": f"{source}:{process_time.isoformat()}:{headline}",
            "headline": headline,
            "event_time": process_time - timedelta(seconds=2),
            "receive_time": process_time - timedelta(seconds=1),
            "process_time": process_time,
            "provider_tickers": tickers,
            "provider_entities": entities,
        }
    )


def _service(
    tmp_path: Path,
) -> tuple[CanonicalizationService, SQLiteCanonicalEventRepository]:
    repository = SQLiteCanonicalEventRepository(tmp_path / "canonicalization.sqlite3")
    repository.initialize_schema()
    service = CanonicalizationService(repository, DeterministicEventClassifier())
    return service, repository


def test_evolving_event_revisions_prevent_future_information_leakage(tmp_path: Path) -> None:
    """Historical queries must expose only A, then A+B, then A+B+C and their states."""
    service, repository = _service(tmp_path)
    raw_a = _item("Acme reportedly considering acquisition of Beta")
    raw_b = _item(
        "Acme agrees to acquire Beta",
        process_time=BASE_TIME + timedelta(minutes=5),
        source="Reporter B",
        source_type=NewsSourceType.FINANCIAL_NEWS,
    )
    raw_c = _item(
        "Acme announces definitive agreement to acquire Beta",
        process_time=BASE_TIME + timedelta(minutes=20),
        source="Acme Investor Relations",
        source_type=NewsSourceType.COMPANY_RELEASE,
    )

    results = service.canonicalize_many((raw_c, raw_a, raw_b))
    event_id = results[0].event_id

    assert [result.decision for result in results] == [
        CanonicalizationDecisionType.NEW_EVENT,
        CanonicalizationDecisionType.CLUSTERED,
        CanonicalizationDecisionType.CLUSTERED,
    ]
    assert event_id is not None
    assert all(result.event_id == event_id for result in results)
    assert repository.get_event_as_of(event_id, BASE_TIME - timedelta(minutes=1)) is None

    at_1002 = repository.get_event_as_of(event_id, BASE_TIME + timedelta(minutes=2))
    at_1010 = repository.get_event_as_of(event_id, BASE_TIME + timedelta(minutes=10))
    at_1030 = repository.get_event_as_of(event_id, BASE_TIME + timedelta(minutes=30))

    assert at_1002 is not None
    assert at_1002.source_news_ids == (raw_a.news_id,)
    assert at_1002.event_state is EventState.NEW
    assert raw_b.news_id not in at_1002.source_news_ids
    assert raw_c.news_id not in at_1002.source_news_ids

    assert at_1010 is not None
    assert at_1010.source_news_ids == (raw_a.news_id, raw_b.news_id)
    assert at_1010.event_state is EventState.DEVELOPING
    assert raw_c.news_id not in at_1010.source_news_ids

    assert at_1030 is not None
    assert at_1030.source_news_ids == (raw_a.news_id, raw_b.news_id, raw_c.news_id)
    assert at_1030.event_state is EventState.CONFIRMED
    assert len(repository.list_event_revisions(event_id)) == 3


def test_processing_same_news_twice_is_idempotent(tmp_path: Path) -> None:
    """A repeated NewsId should return ALREADY_PROCESSED without another revision."""
    service, repository = _service(tmp_path)
    item = _item("Acme agrees to acquire Beta")

    first = service.canonicalize(item)
    second = service.canonicalize(item)

    assert first.decision is CanonicalizationDecisionType.NEW_EVENT
    assert second.decision is CanonicalizationDecisionType.ALREADY_PROCESSED
    assert second.event_id == first.event_id
    assert first.event_id is not None
    assert len(repository.list_event_revisions(first.event_id)) == 1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("NVDA reports quarterly earnings", "NVDA posts quarterly results"),
        ("US CPI rises 3 percent in May", "May CPI rises 3 percent according to report"),
    ],
)
def test_positive_wording_variants_cluster(
    tmp_path: Path,
    first: str,
    second: str,
) -> None:
    """Same earnings and macro releases should merge across modest wording differences."""
    service, _ = _service(tmp_path)
    is_macro = "CPI" in first
    tickers = () if is_macro else ("NVDA",)
    first_item = _item(first, tickers=tickers)
    second_item = _item(
        second,
        process_time=BASE_TIME + timedelta(minutes=3),
        source="Reporter B",
        source_type=NewsSourceType.FINANCIAL_NEWS,
        tickers=tickers,
    )

    initial = service.canonicalize(first_item)
    followup = service.canonicalize(second_item)

    assert initial.decision is CanonicalizationDecisionType.NEW_EVENT
    assert followup.decision is CanonicalizationDecisionType.CLUSTERED
    assert followup.event_id == initial.event_id


@pytest.mark.parametrize(
    ("first", "second", "first_tickers", "second_tickers", "second_time"),
    [
        (
            "NVDA reports quarterly earnings",
            "NVDA agrees to acquire Alpha",
            ("NVDA",),
            ("NVDA",),
            BASE_TIME + timedelta(minutes=5),
        ),
        (
            "Apple agrees to acquire Alpha",
            "Microsoft agrees to acquire Alpha",
            ("AAPL",),
            ("MSFT",),
            BASE_TIME + timedelta(minutes=5),
        ),
        (
            "US CPI rises in June",
            "Acme reports quarterly earnings",
            (),
            ("ACME",),
            BASE_TIME + timedelta(minutes=5),
        ),
        (
            "Company announces strategic update",
            "Company announces strategic update",
            (),
            (),
            BASE_TIME + timedelta(minutes=5),
        ),
        (
            "NVDA reports quarterly earnings",
            "NVDA reports quarterly earnings",
            ("NVDA",),
            ("NVDA",),
            BASE_TIME + timedelta(days=3),
        ),
    ],
)
def test_negative_cases_split_conservatively(
    tmp_path: Path,
    first: str,
    second: str,
    first_tickers: tuple[str, ...],
    second_tickers: tuple[str, ...],
    second_time: datetime,
) -> None:
    """Subtype, anchor, type, generic wording, and horizon guards should prevent merges."""
    service, _ = _service(tmp_path)
    initial = service.canonicalize(_item(first, tickers=first_tickers))
    followup = service.canonicalize(
        _item(
            second,
            process_time=second_time,
            source="Reporter B",
            tickers=second_tickers,
        )
    )

    assert initial.decision is CanonicalizationDecisionType.NEW_EVENT
    assert followup.decision is CanonicalizationDecisionType.NEW_EVENT
    assert followup.event_id != initial.event_id


def test_same_source_material_update_creates_updated_revision(tmp_path: Path) -> None:
    """A matched new NewsId from the same provider should remain explicit as UPDATED."""
    service, repository = _service(tmp_path)
    first = _item("Acme agrees to acquire Beta")
    second = _item(
        "Acme announces additional acquisition terms for Beta",
        process_time=BASE_TIME + timedelta(minutes=2),
    )

    initial = service.canonicalize(first)
    followup = service.canonicalize(second)

    assert followup.decision is CanonicalizationDecisionType.CLUSTERED
    assert initial.event_id is not None
    current = repository.get_current_event(initial.event_id)
    assert current is not None
    assert current.event_state is EventState.UPDATED
    assert current.source_news_ids == (first.news_id, second.news_id)


def test_ambiguous_candidates_return_explicit_decision_without_persistence(
    tmp_path: Path,
) -> None:
    """Near-tied candidates should not be resolved using database ordering."""
    service, repository = _service(tmp_path)
    for headline in ("Acme acquires Alpha division", "Acme acquires Beta division"):
        event = CanonicalEvent(
            first_event_time=BASE_TIME - timedelta(seconds=2),
            first_receive_time=BASE_TIME - timedelta(seconds=1),
            last_update_time=BASE_TIME,
            event_type=EventType.COMPANY,
            event_subtype=EventSubtype.COMPANY_MERGER_ACQUISITION,
            headline_summary=headline,
            source_news_ids=(NewsId.new(),),
            event_state=EventState.NEW,
        )
        repository.create_event(
            CanonicalEventRevision(
                revision_number=1,
                available_at=BASE_TIME,
                event=event,
                headline_tokens=tuple(sorted(comparison_tokens(headline))),
                anchors=("acme",),
                ticker_anchors=("acme",),
                source_names=(headline,),
                source_types=(NewsSourceType.WIRE,),
            )
        )
    incoming = _item(
        "Acme acquires division",
        process_time=BASE_TIME + timedelta(minutes=1),
        source="Ambiguous Reporter",
        tickers=("ACME",),
    )

    result = service.canonicalize(incoming)

    assert result.decision is CanonicalizationDecisionType.AMBIGUOUS
    assert result.event_id is None
    assert len(result.candidate_scores) == 2
    assert repository.get_event_id_for_news(incoming.news_id) is None


def test_speculative_item_canonicalizes_through_ordinary_framework(tmp_path: Path) -> None:
    """SPECULATIVE source records should not require source-specific event logic."""
    service, repository = _service(tmp_path)
    item = _item(
        "Acme raises annual guidance",
        source="Alternative Observation",
        source_type=NewsSourceType.SPECULATIVE,
        tickers=("ACME",),
    )

    result = service.canonicalize(item)

    assert result.decision is CanonicalizationDecisionType.NEW_EVENT
    assert result.event_id is not None
    event = repository.get_current_event(result.event_id)
    assert event is not None
    assert event.event_type is EventType.COMPANY
    assert event.event_subtype is EventSubtype.COMPANY_GUIDANCE


def test_batch_processing_is_sorted_by_process_time_then_news_id(tmp_path: Path) -> None:
    """Batch input order must not alter chronological historical processing."""
    service, _ = _service(tmp_path)
    earlier = _item("EARLY reports quarterly earnings", tickers=("EARLY",))
    later = _item(
        "LATE reports quarterly earnings",
        process_time=BASE_TIME + timedelta(minutes=1),
        tickers=("LATE",),
    )

    results = service.canonicalize_many((later, earlier))

    assert [result.news_id for result in results] == [earlier.news_id, later.news_id]
