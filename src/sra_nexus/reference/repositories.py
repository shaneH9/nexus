"""Provider-independent repositories for deterministic reference data."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import model_validator

from sra_nexus.common.models import ContractModel, NonBlankStr
from sra_nexus.common.types import EntityId, EntityRelationshipId, InstrumentId
from sra_nexus.reference.enums import AssetType, ReferenceResolutionStatus
from sra_nexus.reference.models import (
    Entity,
    EntityInstrumentLink,
    EntityRelationship,
    Instrument,
)


class ReferenceRepositoryError(RuntimeError):
    """Base error for deterministic reference-data persistence."""


class ReferenceRecordAlreadyExistsError(ReferenceRepositoryError):
    """Raised when an immutable reference identity already exists."""


class ReferenceRecordNotFoundError(ReferenceRepositoryError):
    """Raised when a relationship references an unknown object."""


class EntityResolution(ContractModel):
    """Exact entity lookup with explicit ambiguity and no insertion-order tie break."""

    status: ReferenceResolutionStatus
    query: NonBlankStr
    candidates: tuple[Entity, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> EntityResolution:
        """Require candidate counts consistent with the resolution status."""
        count = len(self.candidates)
        if self.status is ReferenceResolutionStatus.RESOLVED and count != 1:
            raise ValueError("RESOLVED entity lookup requires exactly one candidate")
        if self.status is ReferenceResolutionStatus.AMBIGUOUS and count < 2:
            raise ValueError("AMBIGUOUS entity lookup requires at least two candidates")
        if self.status is ReferenceResolutionStatus.NOT_FOUND and count:
            raise ValueError("NOT_FOUND entity lookup cannot contain candidates")
        return self

    @property
    def entity(self) -> Entity | None:
        """Return the single resolved entity, if one exists."""
        return self.candidates[0] if self.status is ReferenceResolutionStatus.RESOLVED else None


class InstrumentResolution(ContractModel):
    """Ticker lookup with explicit ambiguity across venues or instrument classes."""

    status: ReferenceResolutionStatus
    ticker: NonBlankStr
    exchange: NonBlankStr | None = None
    asset_type: AssetType | None = None
    candidates: tuple[Instrument, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> InstrumentResolution:
        """Require candidate counts consistent with the resolution status."""
        count = len(self.candidates)
        if self.status is ReferenceResolutionStatus.RESOLVED and count != 1:
            raise ValueError("RESOLVED instrument lookup requires exactly one candidate")
        if self.status is ReferenceResolutionStatus.AMBIGUOUS and count < 2:
            raise ValueError("AMBIGUOUS instrument lookup requires at least two candidates")
        if self.status is ReferenceResolutionStatus.NOT_FOUND and count:
            raise ValueError("NOT_FOUND instrument lookup cannot contain candidates")
        return self

    @property
    def instrument(self) -> Instrument | None:
        """Return the single resolved instrument, if one exists."""
        return self.candidates[0] if self.status is ReferenceResolutionStatus.RESOLVED else None


class EntityRepository(Protocol):
    """Storage boundary for canonical entities and deterministic name resolution."""

    def insert_entity(self, entity: Entity) -> None:
        """Insert one canonical entity without replacing an existing identity."""
        ...

    def get_entity(self, entity_id: EntityId) -> Entity | None:
        """Return one entity by stable internal identity."""
        ...

    def list_entities(self) -> tuple[Entity, ...]:
        """Return all entities in deterministic identity order."""
        ...

    def resolve_canonical_name(self, name: str) -> EntityResolution:
        """Resolve one normalized exact canonical name without guessing."""
        ...

    def resolve_alias(self, alias: str) -> EntityResolution:
        """Resolve one normalized exact alias without guessing."""
        ...

    def list_aliases(self, entity_id: EntityId) -> tuple[str, ...]:
        """Return aliases in their canonical stored order."""
        ...


class InstrumentRepository(Protocol):
    """Storage boundary for instruments and explicit entity-instrument links."""

    def insert_instrument(self, instrument: Instrument) -> None:
        """Insert one instrument without replacing an existing identity."""
        ...

    def get_instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        """Return one instrument by stable internal identity."""
        ...

    def resolve_ticker(
        self,
        ticker: str,
        *,
        exchange: str | None = None,
        asset_type: AssetType | None = None,
    ) -> InstrumentResolution:
        """Resolve ticker metadata, returning ambiguity rather than selecting silently."""
        ...

    def insert_entity_instrument_link(self, link: EntityInstrumentLink) -> None:
        """Insert one immutable explicit entity-to-instrument mapping."""
        ...

    def list_instrument_links_for_entity(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[EntityInstrumentLink, ...]:
        """Return mappings valid at the aware historical cutoff."""
        ...

    def list_entity_links_for_instrument(
        self,
        instrument_id: InstrumentId,
        as_of: datetime,
    ) -> tuple[EntityInstrumentLink, ...]:
        """Return entity mappings valid at the aware historical cutoff."""
        ...

    def list_instruments_for_entity(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[Instrument, ...]:
        """Return instruments explicitly associated with an entity at the cutoff."""
        ...


class RelationshipRepository(Protocol):
    """Storage boundary for validity-aware structural entity relationships."""

    def insert_relationship(self, relationship: EntityRelationship) -> None:
        """Insert one immutable structural relationship."""
        ...

    def get_relationship(
        self,
        relationship_id: EntityRelationshipId,
    ) -> EntityRelationship | None:
        """Return one structural relationship by internal identity."""
        ...

    def list_outgoing_relationships(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[EntityRelationship, ...]:
        """Return valid directed edges whose source is the entity."""
        ...

    def list_incoming_relationships(
        self,
        entity_id: EntityId,
        as_of: datetime,
    ) -> tuple[EntityRelationship, ...]:
        """Return valid directed edges whose target is the entity."""
        ...
