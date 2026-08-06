"""Tests for canonical-event exposure contracts."""

import pytest
from pydantic import ValidationError

from sra_nexus.aggregator import EventExposure, ExposureRelationType
from sra_nexus.common import CanonicalEventId, InstrumentId


def _event_exposure(**overrides: object) -> EventExposure:
    data: dict[str, object] = {
        "event_id": CanonicalEventId.new(),
        "instrument_id": InstrumentId.new(),
        "relation_type": ExposureRelationType.SUPPLIER,
        "direction": -0.8,
        "magnitude": 0.5,
        "relevance": 0.75,
        "confidence": 0.9,
        "is_direct": False,
    }
    data.update(overrides)
    return EventExposure.model_validate(data)


def test_event_exposure_valid_creation_and_derived_exposure() -> None:
    """Exposure should be derived solely from direction and magnitude."""
    exposure = _event_exposure()

    assert exposure.relation_type is ExposureRelationType.SUPPLIER
    assert exposure.exposure == pytest.approx(-0.4)


def test_exposure_relation_enum_matches_milestone_taxonomy() -> None:
    """Exposure relation categories should remain stable and extensible."""
    assert {member.value for member in ExposureRelationType} == {
        "DIRECT_COMPANY",
        "COMPETITOR",
        "CUSTOMER",
        "SUPPLIER",
        "SECTOR",
        "INDUSTRY",
        "COUNTRY",
        "COMMODITY",
        "MACRO",
        "REGULATORY",
        "OTHER",
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("direction", -1.01),
        ("direction", 1.01),
        ("magnitude", -0.01),
        ("magnitude", 1.01),
        ("relevance", -0.01),
        ("relevance", 1.01),
        ("confidence", -0.01),
        ("confidence", 1.01),
    ],
)
def test_event_exposure_rejects_values_outside_ranges(field_name: str, value: float) -> None:
    """Direction and normalized exposure scores must obey documented ranges."""
    with pytest.raises(ValidationError):
        _event_exposure(**{field_name: value})


def test_event_exposure_rejects_supplied_derived_value() -> None:
    """Callers cannot persist an exposure value inconsistent with its components."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _event_exposure(exposure=0.99)


def test_event_exposure_serializes_cleanly() -> None:
    """Exposure contracts should emit JSON-compatible enum and identifier values."""
    payload = _event_exposure().model_dump(mode="json")

    assert isinstance(payload["event_id"], str)
    assert payload["relation_type"] == "SUPPLIER"
    assert "exposure" not in payload
