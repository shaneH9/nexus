"""Tests for canonical reference-data contracts."""

import json
from collections.abc import Mapping
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sra_nexus.reference import AssetType, Entity, EntityType, Instrument


def _instrument(**overrides: object) -> Instrument:
    data: dict[str, object] = {
        "ticker": "exm",
        "exchange": " XNYS ",
        "asset_type": AssetType.EQUITY,
        "currency": "usd",
        "sector": "Technology",
        "industry": "Software",
        "country": "US",
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("1"),
    }
    data.update(overrides)
    return Instrument.model_validate(data)


def test_instrument_valid_creation_normalizes_ticker_and_currency() -> None:
    """Instrument metadata should normalize without replacing internal identity."""
    instrument = _instrument()

    assert instrument.ticker == "EXM"
    assert instrument.currency == "USD"
    assert instrument.exchange == "XNYS"
    assert instrument.tick_size == Decimal("0.01")


def test_instrument_allows_unknown_tick_and_lot_sizes() -> None:
    """Reference increments may remain unknown until reference data supplies them."""
    instrument = _instrument(tick_size=None, lot_size=None)

    assert instrument.tick_size is None
    assert instrument.lot_size is None


@pytest.mark.parametrize(("field_name", "value"), [("ticker", " "), ("exchange", "")])
def test_instrument_rejects_blank_required_strings(field_name: str, value: str) -> None:
    """Ticker and exchange identifiers must not be blank."""
    with pytest.raises(ValidationError):
        _instrument(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tick_size", Decimal("0")),
        ("tick_size", Decimal("-0.01")),
        ("lot_size", Decimal("0")),
        ("lot_size", Decimal("-1")),
    ],
)
def test_instrument_rejects_non_positive_reference_increments(
    field_name: str,
    value: Decimal,
) -> None:
    """Supplied tick and lot sizes must be positive exact quantities."""
    with pytest.raises(ValidationError):
        _instrument(**{field_name: value})


def test_instrument_asset_type_enum_matches_milestone_taxonomy() -> None:
    """Asset types should expose only the stable Milestone A categories."""
    assert {member.value for member in AssetType} == {
        "EQUITY",
        "ETF",
        "INDEX",
        "FUTURE",
        "OPTION",
        "FOREX",
        "CRYPTO",
        "OTHER",
    }


def test_entity_valid_creation_normalizes_and_deduplicates_aliases() -> None:
    """Aliases should be trimmed and exact duplicates removed in input order."""
    entity = Entity.model_validate(
        {
            "entity_type": EntityType.COMPANY,
            "canonical_name": "Example Corporation",
            "aliases": [" Example Corp ", "Example Corp", "example corp"],
            "metadata": {"jurisdiction": "US"},
        }
    )

    assert entity.aliases == ("Example Corp", "example corp")


def test_entity_rejects_blank_canonical_name() -> None:
    """A canonical entity must have a meaningful name."""
    with pytest.raises(ValidationError):
        Entity(entity_type=EntityType.COMPANY, canonical_name="   ")


def test_entity_rejects_blank_alias() -> None:
    """Normalized alias collections should not retain blank entries."""
    with pytest.raises(ValidationError, match="blank"):
        Entity.model_validate(
            {
                "entity_type": EntityType.COMPANY,
                "canonical_name": "Example",
                "aliases": [" "],
            }
        )


def test_entity_type_enum_matches_milestone_taxonomy() -> None:
    """Entity types should contain every stable Milestone A category."""
    assert {member.value for member in EntityType} == {
        "COMPANY",
        "PERSON",
        "COUNTRY",
        "GOVERNMENT",
        "CENTRAL_BANK",
        "REGULATOR",
        "SECTOR",
        "INDUSTRY",
        "COMMODITY",
        "CURRENCY",
        "ECONOMIC_INDICATOR",
        "GEOGRAPHIC_REGION",
        "OTHER",
    }


def test_entity_metadata_is_immutable_and_json_serializable() -> None:
    """Opaque canonical metadata should freeze in memory and serialize cleanly."""
    entity = Entity(
        entity_type=EntityType.COMPANY,
        canonical_name="Example Corporation",
        metadata={"classifications": ["issuer"]},
    )

    assert isinstance(entity.metadata, Mapping)
    with pytest.raises(TypeError):
        entity.metadata["country"] = "US"  # type: ignore[index]

    payload = json.loads(entity.model_dump_json())
    assert payload["metadata"] == {"classifications": ["issuer"]}


def test_instrument_serializes_to_json_compatible_values() -> None:
    """Reference models should emit UUID strings and decimal strings in JSON mode."""
    payload = _instrument().model_dump(mode="json")

    assert isinstance(payload["instrument_id"], str)
    assert payload["tick_size"] == "0.01"
