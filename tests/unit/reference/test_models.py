"""Tests for canonical reference-data contracts."""

import json
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from sra_nexus.reference import AssetType, Entity, EntityType, Instrument


def _instrument(**overrides: object) -> Instrument:
    data: dict[str, object] = {
        "ticker": "EXM",
        "exchange": "XNYS",
        "asset_type": AssetType.EQUITY,
        "currency": "USD",
        "sector": "Technology",
        "industry": "Software",
        "country": "US",
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("1"),
    }
    data.update(overrides)
    return Instrument.model_validate(data)


def test_instrument_creation_uses_internal_uuid_and_exact_increments() -> None:
    """Ticker metadata should coexist with a stable UUID and Decimal units."""
    instrument = _instrument()

    assert isinstance(instrument.instrument_id, UUID)
    assert instrument.asset_type is AssetType.EQUITY
    assert instrument.tick_size == Decimal("0.01")
    assert instrument.lot_size == Decimal("1")


@pytest.mark.parametrize("asset_type", list(AssetType))
def test_instrument_supports_stable_asset_type_enum(asset_type: AssetType) -> None:
    """Every declared asset class should round-trip as its enum member."""
    assert _instrument(asset_type=asset_type).asset_type is asset_type


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("ticker", "  "),
        ("currency", "usd"),
        ("currency", "USDX"),
        ("country", "USA"),
        ("tick_size", Decimal("0")),
        ("tick_size", Decimal("-0.01")),
        ("lot_size", Decimal("0")),
    ],
)
def test_instrument_rejects_invalid_reference_values(field_name: str, value: object) -> None:
    """Reference identifiers and exact trading increments must be valid."""
    with pytest.raises(ValidationError):
        _instrument(**{field_name: value})


def test_ticker_is_not_accepted_as_instrument_id() -> None:
    """An internal instrument key must parse as a UUID, never a ticker."""
    with pytest.raises(ValidationError):
        _instrument(instrument_id="EXM")


def test_instrument_forbids_provider_specific_extra_fields() -> None:
    """Provider-specific symbol data should not leak into Instrument."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _instrument(provider_symbol="EXM.N")


def test_entity_creation_and_immutable_metadata() -> None:
    """Canonical entities should use typed enums and immutable JSON metadata."""
    entity = Entity(
        entity_type=EntityType.COMPANY,
        canonical_name="Example Corporation",
        aliases=("Example Corp", "EXM Corp"),
        metadata={"jurisdiction": "US", "classifications": ["issuer"]},
    )

    assert isinstance(entity.entity_id, UUID)
    assert entity.entity_type is EntityType.COMPANY
    assert entity.metadata["classifications"] == ("issuer",)
    assert isinstance(entity.metadata, Mapping)
    with pytest.raises(TypeError):
        entity.metadata["jurisdiction"] = "CA"  # type: ignore[index]


def test_entity_serializes_immutable_metadata_as_json() -> None:
    """Canonical metadata should serialize without leaking immutable wrappers."""
    entity = Entity(
        entity_type=EntityType.COMPANY,
        canonical_name="Example Corporation",
        metadata={"classifications": ["issuer"]},
    )

    payload = json.loads(entity.model_dump_json())

    assert payload["metadata"] == {"classifications": ["issuer"]}


@pytest.mark.parametrize("entity_type", list(EntityType))
def test_entity_supports_stable_entity_type_enum(entity_type: EntityType) -> None:
    """Every declared entity kind should round-trip as its enum member."""
    entity = Entity(entity_type=entity_type, canonical_name=f"Example {entity_type.value}")

    assert entity.entity_type is entity_type


def test_entity_rejects_duplicate_case_insensitive_aliases() -> None:
    """Alias matching should not carry duplicate case variants."""
    with pytest.raises(ValidationError, match="aliases must be unique"):
        Entity(
            entity_type=EntityType.COMPANY,
            canonical_name="Example Corporation",
            aliases=("Example Corp", "example corp"),
        )


def test_entity_rejects_provider_specific_extra_fields() -> None:
    """Provider fields must remain in the associated raw-news record."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Entity.model_validate(
            {
                "entity_type": EntityType.COMPANY,
                "canonical_name": "Example Corporation",
                "provider_entity_id": "vendor-42",
            }
        )
