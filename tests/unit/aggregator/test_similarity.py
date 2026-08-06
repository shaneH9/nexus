"""Tests for deterministic event-similarity components and hard guards."""

from datetime import UTC, datetime, timedelta

import pytest

from sra_nexus.aggregator import (
    CanonicalEvent,
    EventState,
    EventSubtype,
    EventType,
    NewsSourceType,
    RawNewsItem,
)
from sra_nexus.aggregator.anchors import EventAnchors
from sra_nexus.aggregator.classification import EventClassification
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.normalization import comparison_tokens
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.aggregator.similarity import (
    ClusteringConfig,
    jaccard_similarity,
    score_event_similarity,
    temporal_proximity_score,
)
from sra_nexus.common.types import NewsId

BASE_TIME = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def _item(
    *,
    headline: str = "Acme agrees to acquire Beta",
    process_time: datetime = BASE_TIME,
    tickers: tuple[str, ...] = ("ACME", "BETA"),
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "source": "Similarity Incoming",
            "source_type": NewsSourceType.WIRE,
            "provider_item_id": f"incoming-{process_time.isoformat()}-{headline}",
            "headline": headline,
            "event_time": BASE_TIME - timedelta(seconds=2),
            "receive_time": BASE_TIME - timedelta(seconds=1),
            "process_time": process_time,
            "provider_tickers": tickers,
        }
    )


def _classification(
    event_type: EventType = EventType.COMPANY,
    event_subtype: EventSubtype = EventSubtype.COMPANY_MERGER_ACQUISITION,
) -> EventClassification:
    return EventClassification(
        event_type=event_type,
        event_subtype=event_subtype,
        confidence=0.9,
        matched_rules=("test.rule",),
        explanation="Test classification.",
    )


def _candidate(
    *,
    headline: str = "Acme reportedly considering acquisition of Beta",
    available_at: datetime = BASE_TIME - timedelta(minutes=5),
    event_type: EventType = EventType.COMPANY,
    event_subtype: EventSubtype = EventSubtype.COMPANY_MERGER_ACQUISITION,
    anchors: tuple[str, ...] = ("acme", "beta"),
    ticker_anchors: tuple[str, ...] = ("acme", "beta"),
) -> CanonicalEventRevision:
    event = CanonicalEvent(
        first_event_time=available_at - timedelta(seconds=2),
        first_receive_time=available_at - timedelta(seconds=1),
        last_update_time=available_at,
        event_type=event_type,
        event_subtype=event_subtype,
        headline_summary=headline,
        source_news_ids=(NewsId.new(),),
        event_state=EventState.NEW,
    )
    return CanonicalEventRevision(
        revision_number=1,
        available_at=available_at,
        event=event,
        headline_tokens=tuple(sorted(comparison_tokens(headline))),
        anchors=anchors,
        ticker_anchors=ticker_anchors,
        source_names=("Candidate Source",),
        source_types=(NewsSourceType.WIRE,),
    )


def test_jaccard_similarity_calculates_intersection_over_union() -> None:
    """Headline and anchor Jaccard use the documented dimensionless equation."""
    assert jaccard_similarity(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)
    assert jaccard_similarity(frozenset(), frozenset()) == 0.0


def test_temporal_score_decays_linearly_to_zero_at_horizon() -> None:
    """Temporal proximity should have exact values at now, midpoint, and horizon."""
    maximum = timedelta(hours=10)

    assert temporal_proximity_score(timedelta(0), maximum) == 1.0
    assert temporal_proximity_score(timedelta(hours=5), maximum) == 0.5
    assert temporal_proximity_score(timedelta(hours=10), maximum) == 0.0
    assert temporal_proximity_score(timedelta(hours=20), maximum) == 0.0


def test_final_score_is_exact_configured_weighted_sum() -> None:
    """Total similarity should equal the centrally configured component equation."""
    item = _item()
    config = ClusteringConfig()
    result = score_event_similarity(
        item,
        _classification(),
        EventAnchors(tickers=frozenset({"acme", "beta"}), terms=frozenset()),
        _candidate(),
        config,
    )
    expected = (
        config.weights.headline * result.headline_similarity
        + config.weights.anchor * result.anchor_similarity
        + config.weights.temporal * result.temporal_score
        + config.weights.event_type * result.type_score
    )

    assert result.total_score == pytest.approx(expected)
    assert result.anchor_similarity == 1.0
    assert result.type_score == 1.0
    assert not result.guard_failures


def test_incompatible_event_type_is_a_hard_guard() -> None:
    """A macro candidate cannot cluster with a company acquisition."""
    candidate = _candidate(
        event_type=EventType.MACRO,
        event_subtype=EventSubtype.MACRO_CPI,
        anchors=(),
        ticker_anchors=(),
    )
    result = score_event_similarity(
        _item(),
        _classification(),
        EventAnchors(tickers=frozenset({"acme", "beta"}), terms=frozenset()),
        candidate,
        ClusteringConfig(),
    )

    assert result.total_score == 0.0
    assert "incompatible_event_type" in result.guard_failures


def test_incompatible_subtype_is_a_hard_guard() -> None:
    """Same-ticker earnings and acquisitions must remain distinct events."""
    candidate = _candidate(
        event_subtype=EventSubtype.COMPANY_EARNINGS,
    )
    result = score_event_similarity(
        _item(),
        _classification(),
        EventAnchors(tickers=frozenset({"acme", "beta"}), terms=frozenset()),
        candidate,
        ClusteringConfig(),
    )

    assert result.total_score == 0.0
    assert "incompatible_event_subtype" in result.guard_failures


def test_conflicting_company_tickers_are_a_hard_guard() -> None:
    """Apple and Microsoft reports cannot merge solely on generic acquisition wording."""
    result = score_event_similarity(
        _item(tickers=("AAPL",)),
        _classification(),
        EventAnchors(tickers=frozenset({"aapl"}), terms=frozenset()),
        _candidate(anchors=("msft",), ticker_anchors=("msft",)),
        ClusteringConfig(),
    )

    assert result.total_score == 0.0
    assert "conflicting_ticker_anchors" in result.guard_failures
    assert "missing_shared_company_anchor" in result.guard_failures


def test_company_candidates_require_a_shared_anchor() -> None:
    """Generic unanchored company wording should split conservatively."""
    result = score_event_similarity(
        _item(tickers=()),
        _classification(),
        EventAnchors(tickers=frozenset(), terms=frozenset()),
        _candidate(anchors=(), ticker_anchors=()),
        ClusteringConfig(),
    )

    assert result.total_score == 0.0
    assert "missing_shared_company_anchor" in result.guard_failures


def test_candidate_outside_maximum_age_is_a_hard_guard() -> None:
    """Even otherwise identical events beyond the horizon must not cluster."""
    config = ClusteringConfig(maximum_candidate_age=timedelta(hours=36))
    result = score_event_similarity(
        _item(process_time=BASE_TIME + timedelta(days=3)),
        _classification(),
        EventAnchors(tickers=frozenset({"acme", "beta"}), terms=frozenset()),
        _candidate(),
        config,
    )

    assert result.total_score == 0.0
    assert "outside_candidate_horizon" in result.guard_failures
