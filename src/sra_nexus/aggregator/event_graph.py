"""Revision-aware deterministic entity graph propagation into event exposures."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID, uuid5

from sra_nexus.aggregator.entity_linking import DeterministicEntityLinker
from sra_nexus.aggregator.entity_links import EventEntityLink
from sra_nexus.aggregator.enums import (
    ExposureGenerationStatus,
    ExposureRelationType,
    RelationshipTraversal,
)
from sra_nexus.aggregator.events import EventExposure
from sra_nexus.aggregator.exposures import (
    DEFAULT_RELATIONSHIP_POLICIES,
    ExposureGenerationResult,
    ExposureGraphConfig,
    ExposurePath,
    RelationshipPropagationPolicy,
    RevisionEventExposure,
    apply_direction_policy,
    calculate_propagated_confidence,
    calculate_propagated_magnitude,
    calculate_relevance,
    combine_bounded,
    deterministic_event_direction,
)
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.common.types import (
    CanonicalEventId,
    EntityId,
    EntityRelationshipId,
    ExposurePathId,
    InstrumentId,
)
from sra_nexus.reference.enums import (
    EntityRelationshipType,
    EntityType,
    RelationshipDirection,
)
from sra_nexus.reference.models import EntityRelationship
from sra_nexus.reference.repositories import (
    EntityRepository,
    InstrumentRepository,
    RelationshipRepository,
)
from sra_nexus.storage.canonical import CanonicalEventRepository
from sra_nexus.storage.event_graph import EventEntityLinkRepository, EventExposureRepository

_PATH_NAMESPACE = UUID("f137d5c2-53a8-4dcf-a112-d48e3cc54d8e")

_EXPOSURE_RELATIONS = MappingProxyType(
    {
        EntityRelationshipType.COMPETITOR: ExposureRelationType.COMPETITOR,
        EntityRelationshipType.CUSTOMER_OF: ExposureRelationType.CUSTOMER,
        EntityRelationshipType.SUPPLIER_TO: ExposureRelationType.SUPPLIER,
        EntityRelationshipType.MEMBER_OF_SECTOR: ExposureRelationType.SECTOR,
        EntityRelationshipType.MEMBER_OF_INDUSTRY: ExposureRelationType.INDUSTRY,
        EntityRelationshipType.LOCATED_IN: ExposureRelationType.COUNTRY,
        EntityRelationshipType.OPERATES_IN: ExposureRelationType.COUNTRY,
        EntityRelationshipType.EXPOSED_TO_COMMODITY: ExposureRelationType.COMMODITY,
        EntityRelationshipType.MACRO_SENSITIVE_TO: ExposureRelationType.MACRO,
        EntityRelationshipType.REGULATED_BY: ExposureRelationType.REGULATORY,
    }
)


class CanonicalRevisionNotFoundError(RuntimeError):
    """Raised when exposure generation targets an unknown canonical revision."""


@dataclass(frozen=True, slots=True)
class _TraversalState:
    starting_entity_id: EntityId
    current_entity_id: EntityId
    entity_ids: tuple[EntityId, ...]
    relationships: tuple[EntityRelationship, ...]
    parent_confidence: float
    direction: float

    @property
    def depth(self) -> int:
        return len(self.relationships)


class EventExposureService:
    """Link one canonical revision, traverse bounded relationships, and persist."""

    def __init__(
        self,
        canonical_repository: CanonicalEventRepository,
        entity_repository: EntityRepository,
        instrument_repository: InstrumentRepository,
        relationship_repository: RelationshipRepository,
        entity_link_repository: EventEntityLinkRepository,
        exposure_repository: EventExposureRepository,
        entity_linker: DeterministicEntityLinker,
        config: ExposureGraphConfig | None = None,
        relationship_policies: dict[EntityRelationshipType, RelationshipPropagationPolicy]
        | None = None,
    ) -> None:
        """Configure focused dependencies and centralized propagation parameters."""
        self._canonical = canonical_repository
        self._entities = entity_repository
        self._instruments = instrument_repository
        self._relationships = relationship_repository
        self._entity_links = entity_link_repository
        self._exposures = exposure_repository
        self._linker = entity_linker
        self._config = ExposureGraphConfig() if config is None else config
        policies = dict(DEFAULT_RELATIONSHIP_POLICIES)
        if relationship_policies is not None:
            policies.update(relationship_policies)
        self._policies = MappingProxyType(policies)

    def process_revision(
        self,
        event_id: CanonicalEventId,
        revision_number: int,
    ) -> ExposureGenerationResult:
        """Generate one immutable revision graph or return an idempotent result."""
        revision = self._canonical.get_event_revision(event_id, revision_number)
        if revision is None:
            raise CanonicalRevisionNotFoundError(
                f"event {event_id} revision {revision_number} does not exist"
            )
        if self._exposures.is_revision_processed(revision.revision_id):
            return ExposureGenerationResult(
                status=ExposureGenerationStatus.ALREADY_PROCESSED,
                event_id=event_id,
                revision_id=revision.revision_id,
                revision_number=revision.revision_number,
                available_at=revision.available_at,
                entity_links=self._entity_links.list_entity_links_for_revision(
                    revision.revision_id
                ),
                exposures=self._exposures.list_exposures_for_revision(revision.revision_id),
                paths=self._exposures.list_paths_for_revision(revision.revision_id),
                explanation="Canonical revision exposure graph was already processed.",
            )

        linking = self._linker.link_revision(revision)
        entity_links = self._linker.materialize_event_links(revision, linking)
        paths = self._build_paths(revision, entity_links)
        exposures = self._materialize_exposures(revision, paths)
        self._entity_links.save_revision_links(revision, entity_links)
        self._exposures.save_revision_exposures(revision, exposures, paths)
        return ExposureGenerationResult(
            status=ExposureGenerationStatus.PROCESSED,
            event_id=event_id,
            revision_id=revision.revision_id,
            revision_number=revision.revision_number,
            available_at=revision.available_at,
            entity_links=entity_links,
            ambiguities=linking.ambiguities,
            unresolved=linking.unresolved,
            exposures=exposures,
            paths=paths,
            explanation="Generated deterministic revision-aware entity links and exposures.",
        )

    def _build_paths(
        self,
        revision: CanonicalEventRevision,
        entity_links: tuple[EventEntityLink, ...],
    ) -> tuple[ExposurePath, ...]:
        event_subtype = revision.event.event_subtype
        if event_subtype is None:
            raise CanonicalRevisionNotFoundError("canonical revision event_subtype is missing")
        initial_direction = deterministic_event_direction(
            revision.event.event_type,
            event_subtype,
            revision.event.headline_summary,
            revision.event.event_summary,
        )
        paths: dict[ExposurePathId, ExposurePath] = {}
        for event_link in entity_links:
            stack = [
                _TraversalState(
                    starting_entity_id=event_link.entity_id,
                    current_entity_id=event_link.entity_id,
                    entity_ids=(event_link.entity_id,),
                    relationships=(),
                    parent_confidence=event_link.confidence,
                    direction=initial_direction,
                )
            ]
            while stack:
                state = stack.pop()
                for instrument_link in self._instruments.list_instrument_links_for_entity(
                    state.current_entity_id,
                    revision.available_at,
                ):
                    relationship_magnitudes = tuple(
                        relationship.magnitude for relationship in state.relationships
                    )
                    relationship_confidences = tuple(
                        relationship.confidence for relationship in state.relationships
                    )
                    path_id = _path_id(
                        revision,
                        state,
                        instrument_link.instrument_id,
                    )
                    paths[path_id] = ExposurePath(
                        path_id=path_id,
                        event_id=revision.event.event_id,
                        revision_id=revision.revision_id,
                        revision_number=revision.revision_number,
                        available_at=revision.available_at,
                        starting_entity_id=state.starting_entity_id,
                        relationship_ids=tuple(
                            relationship.relationship_id for relationship in state.relationships
                        ),
                        entity_ids=state.entity_ids,
                        target_entity_id=state.current_entity_id,
                        target_instrument_id=instrument_link.instrument_id,
                        depth=state.depth,
                        direction=state.direction,
                        magnitude=calculate_propagated_magnitude(
                            self._config.direct_magnitude,
                            relationship_magnitudes,
                            state.depth,
                            self._config.decay_factor,
                        ),
                        relevance=calculate_relevance(
                            min(self._config.direct_relevance, event_link.relevance),
                            self._config.relevance_decay,
                            state.depth,
                        ),
                        confidence=calculate_propagated_confidence(
                            state.parent_confidence,
                            relationship_confidences,
                            instrument_link.confidence,
                        ),
                    )
                if state.depth >= self._config.max_depth:
                    continue
                next_states = self._next_states(revision, state)
                stack.extend(reversed(next_states))
        return tuple(sorted(paths.values(), key=lambda path: (path.depth, str(path.path_id))))

    def _next_states(
        self,
        revision: CanonicalEventRevision,
        state: _TraversalState,
    ) -> tuple[_TraversalState, ...]:
        steps: dict[tuple[EntityRelationshipId, EntityId], tuple[EntityRelationship, EntityId]] = {}
        for relationship in self._relationships.list_outgoing_relationships(
            state.current_entity_id,
            revision.available_at,
        ):
            policy = self._policy(relationship.relation_type)
            if relationship.direction is RelationshipDirection.SYMMETRIC or policy.traversal in {
                RelationshipTraversal.FORWARD,
                RelationshipTraversal.BOTH,
            }:
                steps[(relationship.relationship_id, relationship.target_entity_id)] = (
                    relationship,
                    relationship.target_entity_id,
                )
        for relationship in self._relationships.list_incoming_relationships(
            state.current_entity_id,
            revision.available_at,
        ):
            policy = self._policy(relationship.relation_type)
            if relationship.direction is RelationshipDirection.SYMMETRIC or policy.traversal in {
                RelationshipTraversal.REVERSE,
                RelationshipTraversal.BOTH,
            }:
                steps[(relationship.relationship_id, relationship.source_entity_id)] = (
                    relationship,
                    relationship.source_entity_id,
                )

        next_states: list[_TraversalState] = []
        for relationship, next_entity_id in sorted(
            steps.values(),
            key=lambda item: (str(item[0].relationship_id), str(item[1])),
        ):
            if next_entity_id in state.entity_ids:
                continue
            policy = self._policy(relationship.relation_type)
            next_states.append(
                _TraversalState(
                    starting_entity_id=state.starting_entity_id,
                    current_entity_id=next_entity_id,
                    entity_ids=(*state.entity_ids, next_entity_id),
                    relationships=(*state.relationships, relationship),
                    parent_confidence=state.parent_confidence,
                    direction=apply_direction_policy(
                        state.direction,
                        policy,
                        revision.event.event_type,
                    ),
                )
            )
        return tuple(next_states)

    def _materialize_exposures(
        self,
        revision: CanonicalEventRevision,
        paths: tuple[ExposurePath, ...],
    ) -> tuple[RevisionEventExposure, ...]:
        grouped: dict[tuple[object, bool], list[ExposurePath]] = {}
        for path in paths:
            grouped.setdefault((path.target_instrument_id, path.depth == 0), []).append(path)

        records: list[RevisionEventExposure] = []
        for (_, is_direct), supporting_paths in grouped.items():
            ordered = tuple(sorted(supporting_paths, key=lambda path: str(path.path_id)))
            signs = {1 if path.direction > 0 else -1 for path in ordered if path.direction != 0}
            direction_conflict = len(signs) > 1
            if direction_conflict or not signs:
                direction = 0.0
            else:
                direction = float(next(iter(signs)))
            representative = sorted(
                ordered,
                key=lambda path: (-path.magnitude, -path.confidence, str(path.path_id)),
            )[0]
            relation_type = self._exposure_relation(representative, is_direct)
            records.append(
                RevisionEventExposure(
                    revision_id=revision.revision_id,
                    revision_number=revision.revision_number,
                    available_at=revision.available_at,
                    exposure=EventExposure(
                        event_id=revision.event.event_id,
                        instrument_id=representative.target_instrument_id,
                        relation_type=relation_type,
                        direction=direction,
                        magnitude=combine_bounded(tuple(path.magnitude for path in ordered)),
                        relevance=max(path.relevance for path in ordered),
                        confidence=combine_bounded(tuple(path.confidence for path in ordered)),
                        is_direct=is_direct,
                    ),
                    direction_conflict=direction_conflict,
                    path_ids=tuple(path.path_id for path in ordered),
                )
            )
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    str(record.exposure.instrument_id),
                    not record.exposure.is_direct,
                ),
            )
        )

    def _exposure_relation(
        self,
        path: ExposurePath,
        is_direct: bool,
    ) -> ExposureRelationType:
        if is_direct:
            entity = self._entities.get_entity(path.target_entity_id)
            if entity is not None and entity.entity_type is EntityType.COMPANY:
                return ExposureRelationType.DIRECT_COMPANY
            return ExposureRelationType.OTHER
        relationship = self._relationships.get_relationship(path.relationship_ids[-1])
        if relationship is None:
            raise CanonicalRevisionNotFoundError("stored path relationship is missing")
        return _EXPOSURE_RELATIONS.get(relationship.relation_type, ExposureRelationType.OTHER)

    def _policy(self, relation_type: EntityRelationshipType) -> RelationshipPropagationPolicy:
        try:
            return self._policies[relation_type]
        except KeyError as error:
            raise ValueError(f"missing propagation policy for {relation_type.value}") from error


def _path_id(
    revision: CanonicalEventRevision,
    state: _TraversalState,
    instrument_id: InstrumentId,
) -> ExposurePathId:
    relationship_part = ",".join(
        str(relationship.relationship_id) for relationship in state.relationships
    )
    entity_part = ",".join(str(entity_id) for entity_id in state.entity_ids)
    name = "|".join(
        (
            str(revision.revision_id),
            str(state.starting_entity_id),
            relationship_part,
            entity_part,
            str(instrument_id),
        )
    )
    return ExposurePathId(uuid5(_PATH_NAMESPACE, name))
