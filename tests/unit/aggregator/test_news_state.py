"""Tests for instrument-level news-state contracts."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import (
    EventExposure,
    ExposurePath,
    ExposureRelationship,
    NewsState,
)
from sra_nexus.common import EventId, InstrumentId

EVENT_TIME = datetime(2026, 4, 5, 13, 0, tzinfo=UTC)
RECEIVE_TIME = EVENT_TIME + timedelta(seconds=1)
PROCESS_TIME = EVENT_TIME + timedelta(seconds=2)
AS_OF = EVENT_TIME + timedelta(seconds=3)


def _exposure(
    *,
    event_id: EventId,
    instrument_id: InstrumentId,
    exposure_path: ExposurePath,
    process_time: datetime = PROCESS_TIME,
) -> EventExposure:
    return EventExposure(
        event_time=EVENT_TIME,
        receive_time=RECEIVE_TIME,
        process_time=process_time,
        event_id=event_id,
        instrument_id=instrument_id,
        relationship=ExposureRelationship.DIRECT_COMPANY,
        exposure_path=exposure_path,
        directional_exposure=0.6,
        relationship_magnitude=0.8,
        relevance=0.9,
        confidence=0.95,
    )


def _news_state(**overrides: object) -> NewsState:
    instrument_id = InstrumentId(uuid4())
    direct_event_id = EventId(uuid4())
    indirect_event_id = EventId(uuid4())
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
        "active_event_ids": (direct_event_id, indirect_event_id),
        "direct_event_exposures": (
            _exposure(
                event_id=direct_event_id,
                instrument_id=instrument_id,
                exposure_path=ExposurePath.DIRECT,
            ),
        ),
        "indirect_event_exposures": (
            _exposure(
                event_id=indirect_event_id,
                instrument_id=instrument_id,
                exposure_path=ExposurePath.INDIRECT,
            ),
        ),
    }
    data.update(overrides)
    return NewsState.model_validate(data)


def test_news_state_creation() -> None:
    """A complete state should retain bounded risks and classified exposures."""
    state = _news_state()

    assert state.as_of is AS_OF
    assert state.news_volume == 12
    assert len(state.direct_event_exposures) == 1
    assert len(state.indirect_event_exposures) == 1


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
def test_news_state_rejects_bounded_scores_outside_unit_interval(
    field_name: str,
    value: float,
) -> None:
    """Every normalized NewsState score must remain in [0, 1]."""
    with pytest.raises(ValidationError):
        _news_state(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("positive_event_intensity", -0.01),
        ("negative_event_intensity", -0.01),
        ("news_volume", -1),
        ("news_acceleration", float("nan")),
    ],
)
def test_news_state_rejects_impossible_aggregate_values(
    field_name: str,
    value: float,
) -> None:
    """Counts and magnitudes cannot be negative and all values must be finite."""
    with pytest.raises(ValidationError):
        _news_state(**{field_name: value})


def test_news_state_rejects_future_exposure() -> None:
    """An exposure processed after as_of must not leak into historical state."""
    instrument_id = InstrumentId(uuid4())
    event_id = EventId(uuid4())
    future_exposure = _exposure(
        event_id=event_id,
        instrument_id=instrument_id,
        exposure_path=ExposurePath.DIRECT,
        process_time=AS_OF + timedelta(microseconds=1),
    )

    with pytest.raises(ValidationError, match="unavailable at as_of"):
        _news_state(
            instrument_id=instrument_id,
            active_event_ids=(event_id,),
            direct_event_exposures=(future_exposure,),
            indirect_event_exposures=(),
        )


def test_news_state_rejects_cross_instrument_exposure() -> None:
    """An instrument state cannot contain another instrument's exposure."""
    state_instrument_id = InstrumentId(uuid4())
    event_id = EventId(uuid4())
    exposure = _exposure(
        event_id=event_id,
        instrument_id=InstrumentId(uuid4()),
        exposure_path=ExposurePath.DIRECT,
    )

    with pytest.raises(ValidationError, match="instrument_id must match"):
        _news_state(
            instrument_id=state_instrument_id,
            active_event_ids=(event_id,),
            direct_event_exposures=(exposure,),
            indirect_event_exposures=(),
        )


def test_news_state_rejects_misclassified_exposure_path() -> None:
    """Direct and indirect collections must agree with each exposure path."""
    instrument_id = InstrumentId(uuid4())
    event_id = EventId(uuid4())
    indirect_exposure = _exposure(
        event_id=event_id,
        instrument_id=instrument_id,
        exposure_path=ExposurePath.INDIRECT,
    )

    with pytest.raises(ValidationError, match="wrong exposure_path"):
        _news_state(
            instrument_id=instrument_id,
            active_event_ids=(event_id,),
            direct_event_exposures=(indirect_exposure,),
            indirect_event_exposures=(),
        )


def test_news_state_rejects_exposure_to_inactive_event() -> None:
    """Every retained exposure must identify an event active at the cutoff."""
    instrument_id = InstrumentId(uuid4())
    exposure = _exposure(
        event_id=EventId(uuid4()),
        instrument_id=instrument_id,
        exposure_path=ExposurePath.DIRECT,
    )

    with pytest.raises(ValidationError, match="active event"):
        _news_state(
            instrument_id=instrument_id,
            active_event_ids=(EventId(uuid4()),),
            direct_event_exposures=(exposure,),
            indirect_event_exposures=(),
        )


def test_news_state_rejects_duplicate_active_events() -> None:
    """The active-event collection should contain each canonical event once."""
    event_id = EventId(uuid4())

    with pytest.raises(ValidationError, match="active_event_ids must be unique"):
        _news_state(
            active_event_ids=(event_id, event_id),
            direct_event_exposures=(),
            indirect_event_exposures=(),
        )
