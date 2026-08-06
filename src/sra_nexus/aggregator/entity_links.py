"""Auditable entity-linking contracts for canonical event revisions."""

from __future__ import annotations

from pydantic import Field, model_validator

from sra_nexus.aggregator.enums import (
    EntityMatchMethod,
    EventEntityRole,
    LinkAmbiguityKind,
)
from sra_nexus.common.models import ContractModel, NonBlankStr, UnitIntervalScore, UtcDatetime
from sra_nexus.common.types import (
    CanonicalEventId,
    CanonicalEventRevisionId,
    EntityId,
    InstrumentId,
)


class EntityLinkResult(ContractModel):
    """One deterministic entity match with retained evidence and explanation."""

    entity_id: EntityId
    matched_text: NonBlankStr
    match_method: EntityMatchMethod
    confidence: UnitIntervalScore
    is_primary: bool
    explanation: NonBlankStr

    @model_validator(mode="after")
    def reject_unresolved_method(self) -> EntityLinkResult:
        """Reserve ``UNRESOLVED`` for the explicit unresolved contract."""
        if self.match_method is EntityMatchMethod.UNRESOLVED:
            raise ValueError("resolved entity links cannot use UNRESOLVED")
        return self


class EntityLinkAmbiguity(ContractModel):
    """Explicit unresolved ambiguity across entity or ticker candidates."""

    kind: LinkAmbiguityKind
    matched_text: NonBlankStr
    match_method: EntityMatchMethod
    candidate_entity_ids: tuple[EntityId, ...] = ()
    candidate_instrument_ids: tuple[InstrumentId, ...] = ()
    explanation: NonBlankStr

    @model_validator(mode="after")
    def validate_candidates(self) -> EntityLinkAmbiguity:
        """Require at least two candidates of the ambiguity's declared kind."""
        candidates: tuple[object, ...]
        if self.kind is LinkAmbiguityKind.ENTITY:
            candidates = self.candidate_entity_ids
            if self.candidate_instrument_ids:
                raise ValueError("ENTITY ambiguity cannot contain instrument candidates")
        else:
            candidates = self.candidate_instrument_ids
            if self.candidate_entity_ids:
                raise ValueError("TICKER ambiguity cannot contain entity candidates")
        if len(candidates) < 2:
            raise ValueError("ambiguity requires at least two candidates")
        return self


class UnresolvedEntityLink(ContractModel):
    """Provider metadata that could not be resolved against local reference data."""

    matched_text: NonBlankStr
    match_method: EntityMatchMethod = EntityMatchMethod.UNRESOLVED
    explanation: NonBlankStr

    @model_validator(mode="after")
    def require_unresolved_method(self) -> UnresolvedEntityLink:
        """Keep the explicit unresolved representation categorical."""
        if self.match_method is not EntityMatchMethod.UNRESOLVED:
            raise ValueError("unresolved links must use UNRESOLVED")
        return self


class EntityLinkingResult(ContractModel):
    """Complete auditable entity-linking result for one canonical revision."""

    links: tuple[EntityLinkResult, ...] = ()
    ambiguities: tuple[EntityLinkAmbiguity, ...] = ()
    unresolved: tuple[UnresolvedEntityLink, ...] = ()


class EventEntityLink(ContractModel):
    """Immutable entity membership for one canonical-event revision."""

    event_id: CanonicalEventId
    revision_id: CanonicalEventRevisionId
    revision_number: int = Field(ge=1)
    entity_id: EntityId
    role: EventEntityRole
    relevance: UnitIntervalScore
    confidence: UnitIntervalScore
    is_direct: bool
    matched_text: NonBlankStr
    match_method: EntityMatchMethod
    explanation: NonBlankStr
    available_at: UtcDatetime

    @model_validator(mode="after")
    def reject_unresolved_method(self) -> EventEntityLink:
        """Persist only resolved entities as event links."""
        if self.match_method is EntityMatchMethod.UNRESOLVED:
            raise ValueError("event entity links cannot be unresolved")
        return self
