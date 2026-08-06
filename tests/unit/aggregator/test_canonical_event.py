"""Tests for the provider-neutral canonical-event contract."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import CanonicalEvent, EventState, EventType
from sra_nexus.common import EntityId, InstrumentId, NewsId


def _canonical_event(**overrides: object) -> CanonicalEvent:
    data: dict[str, object] = {
        "event_time": datetime(2026, 2, 3, 14, 0, tzinfo=UTC),
        "receive_time": datetime(2026, 2, 3, 14, 0, 1, tzinfo=UTC),
        "process_time": datetime(2026, 2, 3, 14, 0, 2, tzinfo=UTC),
        "event_type": EventType.COMPANY,
        "event_subtype": "COMPANY.EARNINGS",
        "headline_summary": "Example Corp reports earnings",
        "event_summary": "Example Corp released quarterly results.",
        "source_news_ids": (NewsId(uuid4()), NewsId(uuid4())),
        "entity_ids": (EntityId(uuid4()),),
        "instrument_ids": (InstrumentId(uuid4()),),
        "sectors": ("Technology",),
        "industries": ("Software",),
        "countries": ("US",),
        "sentiment": 0.25,
        "surprise": 1.5,
        "novelty": 0.8,
        "severity": 0.7,
        "relevance": 0.9,
        "confidence": 0.85,
        "credibility": 0.95,
        "expected_duration": timedelta(hours=4),
        "event_state": EventState.CONFIRMED,
    }
    data.update(overrides)
    return CanonicalEvent.model_validate(data)


def test_canonical_event_creation_preserves_source_news_ids() -> None:
    """Canonical events should retain ordered provenance to every raw item."""
    source_ids = (NewsId(uuid4()), NewsId(uuid4()))

    event = _canonical_event(source_news_ids=source_ids)

    assert event.source_news_ids == source_ids
    assert event.event_type is EventType.COMPANY
    assert event.event_state is EventState.CONFIRMED


@pytest.mark.parametrize("event_type", list(EventType))
def test_canonical_event_supports_every_top_level_event_type(event_type: EventType) -> None:
    """The initial architecture taxonomy should be fully represented by enums."""
    event = _canonical_event(
        event_type=event_type,
        event_subtype=f"{event_type.value}.TEST",
    )

    assert event.event_type is event_type


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("sentiment", -1.01),
        ("sentiment", 1.01),
        ("novelty", -0.01),
        ("novelty", 1.01),
        ("severity", -0.01),
        ("severity", 1.01),
        ("relevance", -0.01),
        ("relevance", 1.01),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("credibility", -0.01),
        ("credibility", 1.01),
        ("surprise", float("inf")),
    ],
)
def test_canonical_event_rejects_scores_outside_allowed_ranges(
    field_name: str,
    value: float,
) -> None:
    """Event scores must remain finite and within their documented intervals."""
    with pytest.raises(ValidationError):
        _canonical_event(**{field_name: value})


def test_canonical_event_schema_documents_score_ranges() -> None:
    """Generated schemas should expose score bounds to contract consumers."""
    properties = CanonicalEvent.model_json_schema()["properties"]

    assert properties["sentiment"]["minimum"] == -1.0
    assert properties["sentiment"]["maximum"] == 1.0
    for field_name in ("novelty", "severity", "relevance", "confidence", "credibility"):
        assert properties[field_name]["minimum"] == 0.0
        assert properties[field_name]["maximum"] == 1.0
        assert properties[field_name]["description"]


def test_canonical_event_rejects_mismatched_subtype() -> None:
    """A namespaced subtype must belong to its selected top-level category."""
    with pytest.raises(ValidationError, match="prefix must match"):
        _canonical_event(event_type=EventType.MACRO, event_subtype="COMPANY.EARNINGS")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("source_news_ids", (NewsId(uuid4()),) * 2),
        ("entity_ids", (EntityId(uuid4()),) * 2),
        ("instrument_ids", (InstrumentId(uuid4()),) * 2),
        ("countries", ("US", "US")),
    ],
)
def test_canonical_event_rejects_duplicate_references(field_name: str, value: object) -> None:
    """Canonical reference collections should not contain duplicate identifiers."""
    with pytest.raises(ValidationError, match="unique"):
        _canonical_event(**{field_name: value})


def test_canonical_event_requires_source_news_provenance() -> None:
    """A canonical event must trace back to at least one raw news item."""
    with pytest.raises(ValidationError):
        _canonical_event(source_news_ids=())


def test_canonical_event_rejects_provider_specific_extra_fields() -> None:
    """Provider fields must remain on RawNewsItem.raw_metadata."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _canonical_event(provider_payload={"vendor_rank": 2})


def test_canonical_event_rejects_non_positive_duration() -> None:
    """Expected event duration must be positive when it is known."""
    with pytest.raises(ValidationError):
        _canonical_event(expected_duration=timedelta(0))
