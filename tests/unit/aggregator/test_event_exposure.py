"""Tests for canonical-event exposure contracts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import EventExposure, ExposurePath, ExposureRelationship
from sra_nexus.common import EntityId, EventId, InstrumentId


def _event_exposure(**overrides: object) -> EventExposure:
    data: dict[str, object] = {
        "event_time": datetime(2026, 3, 4, 9, 30, tzinfo=UTC),
        "receive_time": datetime(2026, 3, 4, 9, 30, 1, tzinfo=UTC),
        "process_time": datetime(2026, 3, 4, 9, 30, 2, tzinfo=UTC),
        "event_id": EventId(uuid4()),
        "instrument_id": InstrumentId(uuid4()),
        "entity_id": EntityId(uuid4()),
        "relationship": ExposureRelationship.SUPPLIER,
        "exposure_path": ExposurePath.INDIRECT,
        "directional_exposure": -0.8,
        "relationship_magnitude": 0.5,
        "relevance": 0.75,
        "confidence": 0.9,
    }
    data.update(overrides)
    return EventExposure.model_validate(data)


def test_event_exposure_creation_and_net_exposure() -> None:
    """Signed exposure should preserve direction and relationship magnitude."""
    exposure = _event_exposure()

    assert exposure.relationship is ExposureRelationship.SUPPLIER
    assert exposure.exposure_path is ExposurePath.INDIRECT
    assert exposure.net_exposure == pytest.approx(-0.4)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("directional_exposure", -1.01),
        ("directional_exposure", 1.01),
        ("relationship_magnitude", -0.01),
        ("relationship_magnitude", 1.01),
        ("relevance", -0.01),
        ("relevance", 1.01),
        ("confidence", -0.01),
        ("confidence", 1.01),
    ],
)
def test_event_exposure_rejects_impossible_ranges(field_name: str, value: float) -> None:
    """Every exposure score should obey its documented closed interval."""
    with pytest.raises(ValidationError):
        _event_exposure(**{field_name: value})


def test_event_exposure_schema_documents_ranges() -> None:
    """The machine-readable exposure schema should include numeric bounds."""
    properties = EventExposure.model_json_schema()["properties"]

    assert properties["directional_exposure"]["minimum"] == -1.0
    assert properties["directional_exposure"]["maximum"] == 1.0
    for field_name in ("relationship_magnitude", "relevance", "confidence"):
        assert properties[field_name]["minimum"] == 0.0
        assert properties[field_name]["maximum"] == 1.0


def test_event_exposure_is_frozen() -> None:
    """Normalized exposure records should not be mutated after construction."""
    exposure = _event_exposure()

    with pytest.raises(ValidationError, match="frozen"):
        exposure.confidence = 0.1
