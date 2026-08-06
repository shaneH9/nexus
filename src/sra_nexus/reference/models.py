"""Canonical entity and instrument data contracts."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_serializer, field_validator, model_validator

from sra_nexus.common.models import (
    ContractModel,
    CountryCode,
    CurrencyCode,
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
    currency: CurrencyCode = Field(description="ISO 4217 currency code.")
    sector: NonBlankStr | None = None
    industry: NonBlankStr | None = None
    country: CountryCode | None = Field(default=None, description="ISO 3166-1 alpha-2 code.")
    tick_size: Decimal = Field(gt=0, description="Minimum price increment in currency units.")
    lot_size: Decimal = Field(gt=0, description="Minimum tradable quantity in instrument units.")


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

    @model_validator(mode="after")
    def validate_aliases(self) -> Entity:
        """Reject aliases that repeat under case-insensitive comparison."""
        normalized = [alias.casefold() for alias in self.aliases]
        if len(normalized) != len(set(normalized)):
            raise ValueError("aliases must be unique ignoring case")
        return self
