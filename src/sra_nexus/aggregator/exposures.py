"""Revision-aware exposure graph contracts and deterministic feature formulas."""

from __future__ import annotations

from functools import reduce
from math import isfinite
from operator import mul
from types import MappingProxyType
from typing import Final

from pydantic import Field, model_validator

from sra_nexus.aggregator.entity_links import (
    EntityLinkAmbiguity,
    EventEntityLink,
    UnresolvedEntityLink,
)
from sra_nexus.aggregator.enums import (
    DirectionPropagation,
    EventSubtype,
    EventType,
    ExposureGenerationStatus,
    RelationshipTraversal,
)
from sra_nexus.aggregator.events import EventExposure
from sra_nexus.aggregator.normalization import normalize_comparison_text
from sra_nexus.common.models import ContractModel, NonBlankStr, UnitIntervalScore, UtcDatetime
from sra_nexus.common.types import (
    CanonicalEventId,
    CanonicalEventRevisionId,
    EntityId,
    EntityRelationshipId,
    ExposurePathId,
    InstrumentId,
    new_exposure_path_id,
)
from sra_nexus.reference.enums import EntityRelationshipType


class ExposureGraphConfig(ContractModel):
    """Centralized, uncalibrated engineering priors for bounded propagation."""

    max_depth: int = Field(default=2, ge=0, le=8)
    direct_magnitude: UnitIntervalScore = 1.0
    direct_relevance: UnitIntervalScore = 1.0
    decay_factor: UnitIntervalScore = 0.75
    relevance_decay: UnitIntervalScore = 0.70


class RelationshipPropagationPolicy(ContractModel):
    """Configured traversal and conservative sign behavior for an edge type."""

    traversal: RelationshipTraversal
    direction: DirectionPropagation
    preserve_direction_for_event_types: tuple[EventType, ...] = ()


DEFAULT_RELATIONSHIP_POLICIES: Final = MappingProxyType(
    {
        EntityRelationshipType.OWNS_OR_ISSUES: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.FORWARD,
            direction=DirectionPropagation.PRESERVE,
        ),
        EntityRelationshipType.COMPETITOR: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.BOTH,
            direction=DirectionPropagation.UNKNOWN,
        ),
        EntityRelationshipType.CUSTOMER_OF: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.FORWARD,
            direction=DirectionPropagation.UNKNOWN,
        ),
        EntityRelationshipType.SUPPLIER_TO: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.FORWARD,
            direction=DirectionPropagation.UNKNOWN,
        ),
        EntityRelationshipType.MEMBER_OF_SECTOR: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.PRESERVE,
            preserve_direction_for_event_types=(EventType.SECTOR,),
        ),
        EntityRelationshipType.MEMBER_OF_INDUSTRY: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.PRESERVE,
            preserve_direction_for_event_types=(EventType.SECTOR,),
        ),
        EntityRelationshipType.LOCATED_IN: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.PRESERVE,
            preserve_direction_for_event_types=(EventType.GEOPOLITICAL,),
        ),
        EntityRelationshipType.OPERATES_IN: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.PRESERVE,
            preserve_direction_for_event_types=(EventType.GEOPOLITICAL,),
        ),
        EntityRelationshipType.EXPOSED_TO_COMMODITY: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.UNKNOWN,
        ),
        EntityRelationshipType.EXPOSED_TO_CURRENCY: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.UNKNOWN,
        ),
        EntityRelationshipType.REGULATED_BY: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.PRESERVE,
            preserve_direction_for_event_types=(EventType.REGULATORY,),
        ),
        EntityRelationshipType.MACRO_SENSITIVE_TO: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.REVERSE,
            direction=DirectionPropagation.UNKNOWN,
        ),
        EntityRelationshipType.OTHER: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.FORWARD,
            direction=DirectionPropagation.UNKNOWN,
        ),
    }
)


class ExposurePath(ContractModel):
    """One fully explainable direct or indirect path to a tradable instrument."""

    path_id: ExposurePathId = Field(default_factory=new_exposure_path_id)
    event_id: CanonicalEventId
    revision_id: CanonicalEventRevisionId
    revision_number: int = Field(ge=1)
    available_at: UtcDatetime
    starting_entity_id: EntityId
    relationship_ids: tuple[EntityRelationshipId, ...] = ()
    entity_ids: tuple[EntityId, ...] = Field(min_length=1)
    target_entity_id: EntityId
    target_instrument_id: InstrumentId
    depth: int = Field(ge=0)
    direction: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    magnitude: UnitIntervalScore
    relevance: UnitIntervalScore
    confidence: UnitIntervalScore

    @model_validator(mode="after")
    def validate_path(self) -> ExposurePath:
        """Require graph depth, edge count, and entity sequence to agree."""
        if len(self.relationship_ids) != self.depth:
            raise ValueError("relationship count must equal depth")
        if len(self.entity_ids) != self.depth + 1:
            raise ValueError("entity path length must equal depth + 1")
        if self.entity_ids[0] != self.starting_entity_id:
            raise ValueError("entity path must begin with starting_entity_id")
        if self.entity_ids[-1] != self.target_entity_id:
            raise ValueError("entity path must end with target_entity_id")
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("exposure paths cannot contain entity cycles")
        return self


class RevisionEventExposure(ContractModel):
    """One materialized exposure associated with an immutable event revision."""

    revision_id: CanonicalEventRevisionId
    revision_number: int = Field(ge=1)
    available_at: UtcDatetime
    exposure: EventExposure
    direction_conflict: bool = False
    path_ids: tuple[ExposurePathId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_conflict(self) -> RevisionEventExposure:
        """A declared directional conflict must resolve conservatively to zero."""
        if self.direction_conflict and self.exposure.direction != 0.0:
            raise ValueError("direction conflicts require zero materialized direction")
        return self


class ExposureGenerationResult(ContractModel):
    """Complete idempotent output for one canonical-event revision."""

    status: ExposureGenerationStatus
    event_id: CanonicalEventId
    revision_id: CanonicalEventRevisionId
    revision_number: int = Field(ge=1)
    available_at: UtcDatetime
    entity_links: tuple[EventEntityLink, ...] = ()
    ambiguities: tuple[EntityLinkAmbiguity, ...] = ()
    unresolved: tuple[UnresolvedEntityLink, ...] = ()
    exposures: tuple[RevisionEventExposure, ...] = ()
    paths: tuple[ExposurePath, ...] = ()
    explanation: NonBlankStr


def calculate_propagated_magnitude(
    parent_magnitude: float,
    relationship_magnitudes: tuple[float, ...],
    depth: int,
    decay_factor: float,
) -> float:
    """Return dimensionless path magnitude.

    Definition: ``parent_magnitude * product(relationship_magnitudes) *
    decay_factor**depth``. Inputs and output are dimensionless and expected in
    ``[0, 1]``. Depth must equal the relationship count.
    """
    if depth < 0 or len(relationship_magnitudes) != depth:
        raise ValueError("depth must be non-negative and equal relationship count")
    values = (parent_magnitude, decay_factor, *relationship_magnitudes)
    _require_unit_interval(values, "magnitude inputs and decay_factor")
    return parent_magnitude * _product(relationship_magnitudes) * decay_factor**depth


def calculate_propagated_confidence(
    parent_confidence: float,
    relationship_confidences: tuple[float, ...],
    instrument_link_confidence: float,
) -> float:
    """Multiply dimensionless confidence through every auditable mapping edge."""
    values = (parent_confidence, instrument_link_confidence, *relationship_confidences)
    _require_unit_interval(values, "confidence inputs")
    return parent_confidence * _product(relationship_confidences) * instrument_link_confidence


def calculate_relevance(direct_relevance: float, relevance_decay: float, depth: int) -> float:
    """Return ``direct_relevance * relevance_decay**depth`` in dimensionless units."""
    if depth < 0:
        raise ValueError("depth must be non-negative")
    _require_unit_interval((direct_relevance, relevance_decay), "relevance inputs")
    return direct_relevance * relevance_decay**depth


def combine_bounded(values: tuple[float, ...]) -> float:
    """Combine path magnitudes/confidences as ``1 - product(1 - value_i)``."""
    if not values:
        raise ValueError("at least one value is required")
    _require_unit_interval(values, "bounded combination inputs")
    return min(1.0, max(0.0, 1.0 - _product(tuple(1.0 - value for value in values))))


def deterministic_event_direction(
    event_type: EventType,
    event_subtype: EventSubtype,
    headline: str,
    summary: str | None,
) -> float:
    """Return only direction supported by a small explicit semantic rule.

    The output is a dimensionless sign in ``{-1, 0, 1}``. Zero means no
    deterministic directional conclusion, not neutral predicted return.
    """
    text = headline if summary is None else f"{headline} {summary}"
    normalized = f" {normalize_comparison_text(text)} "
    if event_subtype is EventSubtype.COMPANY_BUYBACK:
        return 1.0
    if event_subtype is EventSubtype.COMPANY_DIVIDEND and _contains_any(
        normalized,
        ("suspends dividend", "suspend dividend", "cuts dividend", "cancels dividend"),
    ):
        return -1.0
    if event_type is EventType.REGULATORY:
        if _contains_any(normalized, ("rejects", "rejection", "denies", "denied")):
            return -1.0
        if event_subtype is EventSubtype.REGULATORY_APPROVAL:
            return 1.0
    if event_subtype is EventSubtype.GEOPOLITICAL_SANCTION:
        return -1.0
    return 0.0


def apply_direction_policy(
    direction: float,
    policy: RelationshipPropagationPolicy,
    event_type: EventType,
) -> float:
    """Apply configured sign semantics, conservatively returning zero when unknown."""
    propagation = policy.direction
    if (
        policy.preserve_direction_for_event_types
        and event_type not in policy.preserve_direction_for_event_types
    ):
        propagation = DirectionPropagation.UNKNOWN
    if propagation is DirectionPropagation.UNKNOWN:
        return 0.0
    if propagation is DirectionPropagation.REVERSE:
        return -direction
    return direction


def _contains_any(normalized_text: str, phrases: tuple[str, ...]) -> bool:
    return any(f" {normalize_comparison_text(phrase)} " in normalized_text for phrase in phrases)


def _product(values: tuple[float, ...]) -> float:
    return reduce(mul, values, 1.0)


def _require_unit_interval(values: tuple[float, ...], description: str) -> None:
    if any(not isfinite(value) or value < 0.0 or value > 1.0 for value in values):
        raise ValueError(f"{description} must be finite and in [0, 1]")
