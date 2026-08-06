"""Tests for auditable deterministic canonical-revision event scoring."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import (
    CanonicalEvent,
    CanonicalEventRevision,
    EventEntityLink,
    EventEntityRole,
    EventExposure,
    EventScoreComponent,
    EventScoringInput,
    EventScoringService,
    EventState,
    EventSubtype,
    EventType,
    ExposureRelationType,
    NewsSourceType,
    RevisionEventExposure,
)
from sra_nexus.aggregator.classification import DeterministicEventClassifier
from sra_nexus.aggregator.enums import EntityMatchMethod
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.normalization import comparison_tokens
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common import CanonicalEventId, EntityId, ExposurePathId, InstrumentId
from sra_nexus.reference import ReferenceDataPolicy

START = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
ENTITY_ID = EntityId.new()
INSTRUMENT_ID = InstrumentId.new()


def _raw(
    *,
    at: datetime,
    source: str,
    source_type: NewsSourceType,
    headline: str = "Issuer authorizes share buyback",
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "source": source,
            "source_type": source_type,
            "provider_item_id": f"{source}:{at.isoformat()}",
            "headline": headline,
            "event_time": at - timedelta(seconds=2),
            "receive_time": at - timedelta(seconds=1),
            "process_time": at,
            "provider_tickers": ["TEST"],
        }
    )


def _revision(
    items: tuple[RawNewsItem, ...],
    *,
    event_id: CanonicalEventId | None = None,
    revision_number: int = 1,
    event_state: EventState = EventState.NEW,
    event_type: EventType = EventType.COMPANY,
    event_subtype: EventSubtype = EventSubtype.COMPANY_BUYBACK,
    surprise: float | None = None,
    severity: float | None = None,
) -> CanonicalEventRevision:
    event = CanonicalEvent(
        event_id=CanonicalEventId.new() if event_id is None else event_id,
        first_event_time=items[0].event_time,
        first_receive_time=items[0].receive_time,
        last_update_time=items[-1].process_time,
        event_type=event_type,
        event_subtype=event_subtype,
        headline_summary=items[-1].headline,
        source_news_ids=tuple(item.news_id for item in items),
        surprise=surprise,
        severity=severity,
        event_state=event_state,
    )
    return CanonicalEventRevision(
        revision_number=revision_number,
        available_at=items[-1].process_time,
        event=event,
        headline_tokens=tuple(sorted(comparison_tokens(items[-1].headline))),
        source_names=tuple(dict.fromkeys(item.source for item in items)),
        source_types=tuple(dict.fromkeys(item.source_type for item in items)),
    )


def _link(revision: CanonicalEventRevision, *, confidence: float = 0.9) -> EventEntityLink:
    return EventEntityLink(
        event_id=revision.event.event_id,
        revision_id=revision.revision_id,
        revision_number=revision.revision_number,
        entity_id=ENTITY_ID,
        role=EventEntityRole.PRIMARY_SUBJECT,
        relevance=1.0,
        confidence=confidence,
        is_direct=True,
        matched_text="TEST",
        match_method=EntityMatchMethod.PROVIDER_TICKER,
        explanation="Deterministic fixture ticker match.",
        available_at=revision.available_at,
    )


def _exposure(
    revision: CanonicalEventRevision,
    *,
    confidence: float = 0.8,
    direction: float = 1.0,
) -> RevisionEventExposure:
    return RevisionEventExposure(
        revision_id=revision.revision_id,
        revision_number=revision.revision_number,
        available_at=revision.available_at,
        exposure=EventExposure(
            event_id=revision.event.event_id,
            instrument_id=INSTRUMENT_ID,
            relation_type=ExposureRelationType.DIRECT_COMPANY,
            direction=direction,
            magnitude=1.0,
            relevance=1.0,
            confidence=confidence,
            is_direct=True,
        ),
        path_ids=(ExposurePathId.new(),),
    )


def _input(
    revision: CanonicalEventRevision,
    items: tuple[RawNewsItem, ...],
    *,
    previous: CanonicalEventRevision | None = None,
) -> EventScoringInput:
    return EventScoringInput(
        revision=revision,
        previous_revision=previous,
        raw_items=items,
        entity_links=(_link(revision),),
        previous_entity_links=() if previous is None else (_link(previous),),
        exposures=(_exposure(revision),),
        previous_exposures=() if previous is None else (_exposure(previous),),
    )


def test_event_score_is_versioned_and_auditable_for_every_component() -> None:
    """Every scalar must have a stable method and retained contributing evidence."""
    item = _raw(at=START, source="Wire One", source_type=NewsSourceType.WIRE)
    revision = _revision((item,))

    score = EventScoringService(DeterministicEventClassifier()).score_revision(
        _input(revision, (item,))
    )

    assert {detail.component for detail in score.scoring_methods} == set(EventScoreComponent)
    assert score.contributing_factors
    assert score.event_scoring_version == "event-scoring-v1"
    assert score.reference_data_policy is ReferenceDataPolicy.CURRENT_REFERENCE_DATA
    assert score.source_news_ids == (item.news_id,)
    assert score.source_types == (NewsSourceType.WIRE,)


def test_source_credibility_uses_bounded_independent_corroboration() -> None:
    """Two independent source names combine with the exact configured union formula."""
    first = _raw(at=START, source="Alt Desk", source_type=NewsSourceType.SPECULATIVE)
    second = _raw(
        at=START + timedelta(minutes=5),
        source="Wire Two",
        source_type=NewsSourceType.WIRE,
    )
    initial = _revision((first,))
    updated = _revision(
        (first, second),
        event_id=initial.event.event_id,
        revision_number=2,
        event_state=EventState.DEVELOPING,
    )

    score = EventScoringService(DeterministicEventClassifier()).score_revision(
        _input(updated, (first, second), previous=initial)
    )

    assert score.credibility == pytest.approx(1.0 - (1.0 - 0.35) * (1.0 - 0.82))


def test_confidence_uses_documented_structured_interpretation_formula() -> None:
    """A single speculative source remains usable without implying return probability."""
    item = _raw(at=START, source="Alt Desk", source_type=NewsSourceType.SPECULATIVE)
    revision = _revision((item,))

    score = EventScoringService(DeterministicEventClassifier()).score_revision(
        _input(revision, (item,))
    )
    expected = 0.30 * 0.35 + 0.15 * 0.0 + 0.20 * 0.90 + 0.15 * 0.90 + 0.15 * 0.80

    assert score.credibility == pytest.approx(0.35)
    assert score.confidence == pytest.approx(expected)
    assert score.uncertainty != pytest.approx(1.0 - score.confidence)


def test_sentiment_is_conservative_and_surprise_requires_explicit_data() -> None:
    """Generic earnings remain directionless while an explicit surprise is preserved."""
    item = _raw(
        at=START,
        source="Wire One",
        source_type=NewsSourceType.WIRE,
        headline="Issuer reports quarterly results",
    )
    revision = _revision(
        (item,),
        event_subtype=EventSubtype.COMPANY_EARNINGS,
        surprise=1.5,
    )

    score = EventScoringService(DeterministicEventClassifier()).score_revision(
        _input(revision, (item,))
    )

    assert score.sentiment == 0.0
    assert score.surprise == 1.5
    assert score.severity == pytest.approx(0.70)


def test_missing_surprise_remains_none() -> None:
    """Scoring must not invent a missing expectation or consensus value."""
    item = _raw(at=START, source="Wire One", source_type=NewsSourceType.WIRE)
    revision = _revision((item,))

    score = EventScoringService(DeterministicEventClassifier()).score_revision(
        _input(revision, (item,))
    )

    assert score.surprise is None


def test_revision_novelty_distinguishes_source_only_from_official_confirmation() -> None:
    """A source-only repeat must score below a later official confirmation."""
    first = _raw(at=START, source="Desk One", source_type=NewsSourceType.FINANCIAL_NEWS)
    second = _raw(
        at=START + timedelta(minutes=5),
        source="Wire Two",
        source_type=NewsSourceType.WIRE,
    )
    official = _raw(
        at=START + timedelta(minutes=20),
        source="Issuer IR",
        source_type=NewsSourceType.COMPANY_RELEASE,
    )
    revision_one = _revision((first,))
    revision_two = _revision(
        (first, second),
        event_id=revision_one.event.event_id,
        revision_number=2,
        event_state=EventState.DEVELOPING,
    )
    revision_three = _revision(
        (first, second, official),
        event_id=revision_one.event.event_id,
        revision_number=3,
        event_state=EventState.CONFIRMED,
    )
    service = EventScoringService(DeterministicEventClassifier())

    first_score = service.score_revision(_input(revision_one, (first,)))
    source_only = service.score_revision(
        _input(revision_two, (first, second), previous=revision_one)
    )
    confirmation = service.score_revision(
        _input(revision_three, (first, second, official), previous=revision_two)
    )

    assert first_score.novelty == 1.0
    assert source_only.novelty == pytest.approx(0.10 * 0.5)
    assert confirmation.novelty == pytest.approx(0.10 / 3.0 + 0.20)
    assert source_only.novelty < confirmation.novelty < first_score.novelty


def test_scoring_input_rejects_raw_evidence_available_after_revision() -> None:
    """A malformed revision cannot expose future raw observations to scoring."""
    item = _raw(at=START, source="Wire One", source_type=NewsSourceType.WIRE)
    original = _revision((item,))
    revision = original.model_copy(
        update={
            "available_at": item.receive_time,
            "event": original.event.model_copy(update={"last_update_time": item.receive_time}),
        }
    )

    with pytest.raises(ValidationError, match="after its canonical revision"):
        _input(revision, (item,))
