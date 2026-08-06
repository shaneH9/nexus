"""Canonical entity and instrument data contracts."""

from __future__ import annotations

from pydantic import Field, field_serializer, field_validator, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ImmutableJsonObject,
    NonBlankStr,
    PositiveDecimal,
    UnitIntervalScore,
    UtcDatetime,
    freeze_json_object,
    thaw_json_object,
)
from sra_nexus.common.types import (
    EntityId,
    EntityInstrumentLinkId,
    EntityRelationshipId,
    InstrumentId,
    new_entity_id,
    new_entity_instrument_link_id,
    new_entity_relationship_id,
    new_instrument_id,
)
from sra_nexus.reference.enums import (
    AssetType,
    EntityInstrumentRelationType,
    EntityRelationshipType,
    EntityType,
    RelationshipDirection,
)


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
    tick_size: PositiveDecimal | None = Field(
        default=None,
        description="Optional minimum price increment in currency units.",
    )
    lot_size: PositiveDecimal | None = Field(
        default=None,
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


class EntityRelationship(ContractModel):
    """A time-bounded structural edge in the economic entity graph.

    ``direction`` describes graph orientation, not expected event or return
    direction. ``magnitude`` and ``confidence`` are dimensionless values in
    ``[0, 1]``.
    """

    relationship_id: EntityRelationshipId = Field(default_factory=new_entity_relationship_id)
    source_entity_id: EntityId
    target_entity_id: EntityId
    relation_type: EntityRelationshipType
    direction: RelationshipDirection = RelationshipDirection.DIRECTED
    magnitude: UnitIntervalScore
    confidence: UnitIntervalScore
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_relationship(self) -> EntityRelationship:
        """Reject self-edges and invalid half-open validity intervals."""
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("entity relationships must connect distinct entities")
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from >= self.valid_to:
                raise ValueError("valid_from must be before valid_to")
        if (
            self.relation_type is EntityRelationshipType.COMPETITOR
            and self.direction is not RelationshipDirection.SYMMETRIC
        ):
            raise ValueError("COMPETITOR relationships must be explicitly SYMMETRIC")
        return self

    def is_valid_at(self, as_of: UtcDatetime) -> bool:
        """Return validity at ``as_of`` using ``[valid_from, valid_to)`` semantics."""
        if self.valid_from is not None and as_of < self.valid_from:
            return False
        return self.valid_to is None or as_of < self.valid_to


class EntityInstrumentLink(ContractModel):
    """A time-bounded explicit mapping from an entity to a tradable instrument."""

    link_id: EntityInstrumentLinkId = Field(default_factory=new_entity_instrument_link_id)
    entity_id: EntityId
    instrument_id: InstrumentId
    relationship_type: EntityInstrumentRelationType
    confidence: UnitIntervalScore = Field(
        description="Dimensionless mapping confidence in the closed interval [0, 1]."
    )
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_validity(self) -> EntityInstrumentLink:
        """Require a valid optional half-open interval."""
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from >= self.valid_to:
                raise ValueError("valid_from must be before valid_to")
        return self

    def is_valid_at(self, as_of: UtcDatetime) -> bool:
        """Return validity at ``as_of`` using ``[valid_from, valid_to)`` semantics."""
        if self.valid_from is not None and as_of < self.valid_from:
            return False
        return self.valid_to is None or as_of < self.valid_to
