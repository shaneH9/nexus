"""Tests for the provider-neutral canonical-event contract."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import CanonicalEvent, EventState, EventType
from sra_nexus.common import CanonicalEventId, EntityId, InstrumentId, NewsId


def _canonical_event(**overrides: object) -> CanonicalEvent:
    data: dict[str, object] = {
        "first_event_time": datetime(2026, 2, 3, 14, 0, tzinfo=UTC),
        "first_receive_time": datetime(2026, 2, 3, 14, 0, 1, tzinfo=UTC),
        "last_update_time": datetime(2026, 2, 3, 14, 0, 2, tzinfo=UTC),
        "event_type": EventType.COMPANY,
        "event_subtype": "earnings.release",
        "headline_summary": "Example Corp reports earnings",
        "event_summary": None,
        "source_news_ids": [NewsId.new()],
        "entity_ids": [EntityId.new()],
        "instrument_ids": [InstrumentId.new()],
        "sectors": ["Technology"],
        "industries": ["Software"],
        "countries": ["US"],
        "commodities": [],
        "macro_factors": [],
        "sentiment": None,
        "surprise": None,
        "novelty": None,
        "severity": None,
        "relevance": None,
        "confidence": None,
        "credibility": None,
        "expected_duration_seconds": None,
        "event_state": EventState.NEW,
    }
    data.update(overrides)
    return CanonicalEvent.model_validate(data)


def test_canonical_event_valid_creation_with_optional_scores() -> None:
    """Canonical events may exist before any derived score has been calculated."""
    event = _canonical_event()

    assert isinstance(event.event_id, CanonicalEventId)
    assert event.sentiment is None
    assert event.event_summary is None


def test_event_enums_match_milestone_taxonomy() -> None:
    """Canonical event category and lifecycle enums should remain stable."""
    assert {member.value for member in EventType} == {
        "COMPANY",
        "SECTOR",
        "MACRO",
        "GEOPOLITICAL",
        "REGULATORY",
        "MARKET_STRUCTURE",
        "SYSTEMIC",
        "COMMODITY",
        "CURRENCY",
        "RATE",
    }
    assert {member.value for member in EventState} == {
        "NEW",
        "DEVELOPING",
        "CONFIRMED",
        "UPDATED",
        "RESOLVED",
        "RETRACTED",
    }


@pytest.mark.parametrize("field_name", ["headline_summary", "event_subtype"])
def test_canonical_event_rejects_blank_validated_text(field_name: str) -> None:
    """Required summaries and supplied subtypes must not be blank."""
    with pytest.raises(ValidationError):
        _canonical_event(**{field_name: "   "})


@pytest.mark.parametrize(
    ("field_name", "boundary"),
    [
        ("sentiment", -1.0),
        ("sentiment", 1.0),
        ("novelty", 0.0),
        ("novelty", 1.0),
        ("severity", 0.0),
        ("severity", 1.0),
        ("relevance", 0.0),
        ("relevance", 1.0),
        ("confidence", 0.0),
        ("confidence", 1.0),
        ("credibility", 0.0),
        ("credibility", 1.0),
    ],
)
def test_canonical_event_accepts_score_boundaries(field_name: str, boundary: float) -> None:
    """Closed score intervals should accept both documented endpoints."""
    assert getattr(_canonical_event(**{field_name: boundary}), field_name) == boundary


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
    ],
)
def test_canonical_event_rejects_scores_outside_ranges(
    field_name: str,
    value: float,
) -> None:
    """Bounded scores outside their documented intervals are invalid."""
    with pytest.raises(ValidationError):
        _canonical_event(**{field_name: value})


@pytest.mark.parametrize("surprise", [-4.5, 3.25])
def test_canonical_event_allows_unbounded_finite_surprise(surprise: float) -> None:
    """Surprise may hold raw values or standardized scores outside [-1, 1]."""
    assert _canonical_event(surprise=surprise).surprise == surprise


@pytest.mark.parametrize("surprise", [float("nan"), float("inf"), float("-inf")])
def test_canonical_event_rejects_non_finite_surprise(surprise: float) -> None:
    """Unbounded surprise values must still be finite."""
    with pytest.raises(ValidationError):
        _canonical_event(surprise=surprise)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_canonical_event_rejects_non_finite_duration(duration: float) -> None:
    """Expected duration must remain finite in addition to being non-negative."""
    with pytest.raises(ValidationError):
        _canonical_event(expected_duration_seconds=duration)


@pytest.mark.parametrize(
    ("first_event_time", "first_receive_time", "last_update_time"),
    [
        (
            datetime(2026, 2, 3, 14, 0, 2, tzinfo=UTC),
            datetime(2026, 2, 3, 14, 0, 1, tzinfo=UTC),
            datetime(2026, 2, 3, 14, 0, 3, tzinfo=UTC),
        ),
        (
            datetime(2026, 2, 3, 14, 0, tzinfo=UTC),
            datetime(2026, 2, 3, 14, 0, 3, tzinfo=UTC),
            datetime(2026, 2, 3, 14, 0, 2, tzinfo=UTC),
        ),
    ],
)
def test_canonical_event_rejects_invalid_timestamp_order(
    first_event_time: datetime,
    first_receive_time: datetime,
    last_update_time: datetime,
) -> None:
    """Canonical event history must preserve causal ordering."""
    with pytest.raises(ValidationError, match="must not be after"):
        _canonical_event(
            first_event_time=first_event_time,
            first_receive_time=first_receive_time,
            last_update_time=last_update_time,
        )


def test_canonical_event_normalizes_aware_timestamps_to_utc() -> None:
    """Equivalent offset-aware event times should normalize to UTC."""
    eastern = timezone(timedelta(hours=-5))

    event = _canonical_event(first_event_time=datetime(2026, 2, 3, 9, 0, tzinfo=eastern))

    assert event.first_event_time == datetime(2026, 2, 3, 14, 0, tzinfo=UTC)
    assert event.first_event_time.tzinfo is UTC


@pytest.mark.parametrize(
    "field_name",
    ["first_event_time", "first_receive_time", "last_update_time"],
)
def test_canonical_event_rejects_naive_timestamps(field_name: str) -> None:
    """All canonical event timestamps must be timezone-aware."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        _canonical_event(**{field_name: datetime(2026, 2, 3, 14, 0)})


def test_canonical_event_requires_source_news_id() -> None:
    """Every canonical event must retain at least one raw-news provenance ID."""
    with pytest.raises(ValidationError):
        _canonical_event(source_news_ids=[])


def test_canonical_event_deduplicates_repeated_collection_values() -> None:
    """Repeated IDs and classification labels should collapse deterministically."""
    news_id = NewsId.new()
    entity_id = EntityId.new()
    instrument_id = InstrumentId.new()

    event = _canonical_event(
        source_news_ids=[news_id, news_id],
        entity_ids=[entity_id, entity_id],
        instrument_ids=[instrument_id, instrument_id],
        sectors=["Technology", "Technology"],
    )

    assert event.source_news_ids == (news_id,)
    assert event.entity_ids == (entity_id,)
    assert event.instrument_ids == (instrument_id,)
    assert event.sectors == ("Technology",)


@pytest.mark.parametrize("duration", [0.0, 3600.0])
def test_canonical_event_accepts_non_negative_duration(duration: float) -> None:
    """Expected duration is measured in seconds and may be zero."""
    assert (
        _canonical_event(expected_duration_seconds=duration).expected_duration_seconds == duration
    )


def test_canonical_event_rejects_negative_duration() -> None:
    """An expected event duration cannot be negative."""
    with pytest.raises(ValidationError):
        _canonical_event(expected_duration_seconds=-0.01)


def test_canonical_event_serializes_to_json_compatible_values() -> None:
    """Canonical contracts should serialize IDs and timestamps without adapters."""
    payload = _canonical_event().model_dump(mode="json")

    assert isinstance(payload["event_id"], str)
    assert isinstance(payload["source_news_ids"][0], str)
    assert payload["first_event_time"].endswith("Z")
