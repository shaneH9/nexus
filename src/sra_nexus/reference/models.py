"""Canonical entity and instrument data contracts."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_serializer, field_validator

from sra_nexus.common.models import (
    ContractModel,
    ImmutableJsonObject,
    NonBlankStr,
    freeze_json_object,
    thaw_json_object,
)
from sra_nexus.common.types import EntityId, InstrumentId, new_entity_id, new_instrument_id
from sra_nexus.reference.enums import AssetType, EntityType


class Instrument(ContractModel):
    """Stable instrument reference data keyed by an internal UUID."""

    instrument_id: InstrumentId = Field(default_factory=new_instrument_id)
    ticker: NonBlankStr = Field(description="Display ticker; never a primary internal key.")
    exchange: NonBlankStr = Field(description="Exchange or venue identifier.")
    asset_type: AssetType
    currency: NonBlankStr = Field(description="Uppercase currency identifier.")
    sector: NonBlankStr | None = None
    industry: NonBlankStr | None = None
    country: NonBlankStr | None = None
    tick_size: Decimal | None = Field(
        default=None,
        gt=0,
        description="Optional minimum price increment in currency units.",
    )
    lot_size: Decimal | None = Field(
        default=None,
        gt=0,
        description="Optional minimum tradable quantity in instrument units.",
    )

    @field_validator("ticker", "currency", mode="before")
    @classmethod
    def normalize_uppercase_fields(cls, value: object) -> object:
        """Uppercase ticker and currency metadata before shared trimming."""
        if isinstance(value, str):
            return value.upper()
        return value


class Entity(ContractModel):
    """Canonical real-world subject referenced by external events."""

    entity_id: EntityId = Field(default_factory=new_entity_id)
    entity_type: EntityType
    canonical_name: NonBlankStr
    aliases: tuple[NonBlankStr, ...] = ()
    metadata: ImmutableJsonObject = Field(
        default_factory=dict,
        description="Canonical JSON metadata only; provider fields belong on RawNewsItem.",
    )

    @field_validator("metadata", mode="after")
    @classmethod
    def make_metadata_immutable(cls, value: ImmutableJsonObject) -> ImmutableJsonObject:
        """Retain validated metadata as a recursively immutable copy."""
        return freeze_json_object(value)

    @field_serializer("metadata", when_used="json")
    def serialize_metadata(self, value: ImmutableJsonObject) -> dict[str, object]:
        """Emit canonical metadata as an independent JSON object."""
        return thaw_json_object(value)

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: object) -> object:
        """Trim aliases and remove exact duplicates while preserving order."""
        if not isinstance(value, (list, tuple)):
            raise ValueError("aliases must be a collection of strings")

        normalized: list[str] = []
        seen: set[str] = set()
        for alias in value:
            if not isinstance(alias, str):
                raise ValueError("aliases must contain only strings")
            stripped = alias.strip()
            if not stripped:
                raise ValueError("aliases must not contain blank values")
            if stripped not in seen:
                normalized.append(stripped)
                seen.add(stripped)
        return tuple(normalized)
