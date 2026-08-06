"""Tests for instrument-level news-state contracts."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import EventExposure, ExposureRelationType, NewsState
from sra_nexus.common import CanonicalEventId, InstrumentId

AS_OF = datetime(2026, 4, 5, 13, 0, tzinfo=UTC)


def _exposure(*, instrument_id: InstrumentId, is_direct: bool) -> EventExposure:
    return EventExposure(
        event_id=CanonicalEventId.new(),
        instrument_id=instrument_id,
        relation_type=ExposureRelationType.DIRECT_COMPANY,
        direction=0.6,
        magnitude=0.8,
        relevance=0.9,
        confidence=0.95,
        is_direct=is_direct,
    )


def _news_state(**overrides: object) -> NewsState:
    instrument_id = InstrumentId.new()
    direct_exposure = _exposure(instrument_id=instrument_id, is_direct=True)
    indirect_exposure = _exposure(instrument_id=instrument_id, is_direct=False)
    data: dict[str, object] = {
        "instrument_id": instrument_id,
        "as_of": AS_OF,
        "positive_event_intensity": 1.2,
        "negative_event_intensity": 0.4,
        "company_event_risk": 0.7,
        "sector_event_risk": 0.3,
        "macro_event_risk": 0.2,
        "geopolitical_event_risk": 0.1,
        "regulatory_event_risk": 0.4,
        "systemic_event_risk": 0.05,
        "news_volume": 12,
        "news_acceleration": -0.25,
        "novelty_intensity": 0.8,
        "uncertainty": 0.35,
        "confidence": 0.9,
        "active_event_ids": [direct_exposure.event_id, indirect_exposure.event_id],
        "direct_event_exposures": [direct_exposure],
        "indirect_event_exposures": [indirect_exposure],
    }
    data.update(overrides)
    return NewsState.model_validate(data)


def test_news_state_valid_creation_allows_negative_acceleration() -> None:
    """News acceleration may be signed while intensity remains non-negative."""
    state = _news_state()

    assert state.news_acceleration == -0.25
    assert state.positive_event_intensity == 1.2


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_news_state_rejects_non_finite_news_acceleration(value: float) -> None:
    """Signed news acceleration may be unbounded but must remain finite."""
    with pytest.raises(ValidationError):
        _news_state(news_acceleration=value)


@pytest.mark.parametrize(
    "field_name",
    [
        "company_event_risk",
        "sector_event_risk",
        "macro_event_risk",
        "geopolitical_event_risk",
        "regulatory_event_risk",
        "systemic_event_risk",
        "novelty_intensity",
        "uncertainty",
        "confidence",
    ],
)
@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_news_state_enforces_normalized_risk_bounds(field_name: str, value: float) -> None:
    """Every normalized NewsState field must remain in [0, 1]."""
    with pytest.raises(ValidationError):
        _news_state(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("positive_event_intensity", -0.01),
        ("negative_event_intensity", -0.01),
        ("news_volume", -1),
    ],
)
def test_news_state_rejects_negative_aggregates(field_name: str, value: float) -> None:
    """News counts and unsigned intensity magnitudes cannot be negative."""
    with pytest.raises(ValidationError):
        _news_state(**{field_name: value})


def test_news_state_rejects_naive_as_of() -> None:
    """A historical information cutoff must be timezone-aware."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        _news_state(as_of=datetime(2026, 4, 5, 13, 0))


def test_news_state_normalizes_aware_as_of_to_utc() -> None:
    """Offset-aware cutoffs should normalize to their equivalent UTC instant."""
    eastern = timezone(timedelta(hours=-5))

    state = _news_state(as_of=datetime(2026, 4, 5, 8, 0, tzinfo=eastern))

    assert state.as_of == AS_OF
    assert state.as_of.tzinfo is UTC


@pytest.mark.parametrize(
    ("collection_name", "is_direct"),
    [("direct_event_exposures", False), ("indirect_event_exposures", True)],
)
def test_news_state_rejects_direct_indirect_mismatch(
    collection_name: str,
    is_direct: bool,
) -> None:
    """Exposure groups must agree with each exposure's directness flag."""
    instrument_id = InstrumentId.new()
    exposure = _exposure(instrument_id=instrument_id, is_direct=is_direct)

    with pytest.raises(ValidationError, match="wrong is_direct"):
        _news_state(
            instrument_id=instrument_id,
            direct_event_exposures=[exposure]
            if collection_name == "direct_event_exposures"
            else [],
            indirect_event_exposures=(
                [exposure] if collection_name == "indirect_event_exposures" else []
            ),
        )


def test_news_state_rejects_exposure_for_another_instrument() -> None:
    """A one-instrument state cannot contain a different instrument's exposure."""
    state_instrument_id = InstrumentId.new()
    exposure = _exposure(instrument_id=InstrumentId.new(), is_direct=True)

    with pytest.raises(ValidationError, match="instrument_id must match"):
        _news_state(
            instrument_id=state_instrument_id,
            direct_event_exposures=[exposure],
            indirect_event_exposures=[],
        )


def test_news_state_rejects_exposure_for_inactive_event() -> None:
    """An exposure cannot enter state without its event being active."""
    instrument_id = InstrumentId.new()
    exposure = _exposure(instrument_id=instrument_id, is_direct=True)

    with pytest.raises(ValidationError, match="active event"):
        _news_state(
            instrument_id=instrument_id,
            active_event_ids=[],
            direct_event_exposures=[exposure],
            indirect_event_exposures=[],
        )


def test_news_state_accepts_exposure_for_active_event() -> None:
    """A correctly classified exposure should validate when its event is active."""
    instrument_id = InstrumentId.new()
    exposure = _exposure(instrument_id=instrument_id, is_direct=True)

    state = _news_state(
        instrument_id=instrument_id,
        active_event_ids=[exposure.event_id],
        direct_event_exposures=[exposure],
        indirect_event_exposures=[],
    )

    assert state.direct_event_exposures == (exposure,)


def test_news_state_deduplicates_active_event_ids() -> None:
    """Repeated active event IDs should collapse while preserving order."""
    event_id = CanonicalEventId.new()
    state = _news_state(
        active_event_ids=[event_id, event_id],
        direct_event_exposures=[],
        indirect_event_exposures=[],
    )

    assert state.active_event_ids == (event_id,)


def test_news_state_serializes_to_json_compatible_values() -> None:
    """News state identifiers, timestamps, and nested exposures should serialize."""
    payload = _news_state().model_dump(mode="json")

    assert isinstance(payload["instrument_id"], str)
    assert payload["as_of"].endswith("Z")
    assert isinstance(payload["direct_event_exposures"], list)
