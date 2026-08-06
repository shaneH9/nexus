"""External event observation and aggregation interfaces."""

from sra_nexus.aggregator.entity_linking import DeterministicEntityLinker, EntityLinkingConfig
from sra_nexus.aggregator.entity_links import (
    EntityLinkAmbiguity,
    EntityLinkingResult,
    EntityLinkResult,
    EventEntityLink,
    UnresolvedEntityLink,
)
from sra_nexus.aggregator.enums import (
    DirectionPropagation,
    EntityMatchMethod,
    EventEntityRole,
    EventState,
    EventSubtype,
    EventType,
    ExposureGenerationStatus,
    ExposureRelationType,
    LinkAmbiguityKind,
    NewsSourceType,
    RelationshipTraversal,
)
from sra_nexus.aggregator.event_graph import EventExposureService
from sra_nexus.aggregator.events import CanonicalEvent, EventExposure
from sra_nexus.aggregator.exposures import (
    ExposureGenerationResult,
    ExposureGraphConfig,
    ExposurePath,
    RevisionEventExposure,
)
from sra_nexus.aggregator.ingestion import RawNewsIngestionService, RawNewsIngestionSummary
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.aggregator.state import NewsState

__all__ = [
    "CanonicalEvent",
    "CanonicalEventRevision",
    "DeterministicEntityLinker",
    "DirectionPropagation",
    "EntityLinkAmbiguity",
    "EntityLinkingConfig",
    "EntityLinkingResult",
    "EntityLinkResult",
    "EntityMatchMethod",
    "EventEntityLink",
    "EventEntityRole",
    "EventExposure",
    "EventExposureService",
    "EventState",
    "EventSubtype",
    "EventType",
    "ExposureRelationType",
    "ExposureGenerationResult",
    "ExposureGenerationStatus",
    "ExposureGraphConfig",
    "ExposurePath",
    "LinkAmbiguityKind",
    "NewsSourceType",
    "NewsState",
    "RawNewsItem",
    "RawNewsIngestionService",
    "RawNewsIngestionSummary",
    "RelationshipTraversal",
    "RevisionEventExposure",
    "UnresolvedEntityLink",
]
