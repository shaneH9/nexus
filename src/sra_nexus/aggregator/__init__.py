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
    EventScoreComponent,
    EventScoringMethod,
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
from sra_nexus.aggregator.news_state_service import (
    NEWS_STATE_VERSION,
    NewsStateConfig,
    NewsStateDataError,
    NewsStateService,
)
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.aggregator.scoring import (
    EVENT_SCORING_VERSION,
    ConfidenceWeights,
    EventDecayPrior,
    EventScore,
    EventScoreFactor,
    EventScoreMethodDetail,
    EventScoringConfig,
    EventScoringInput,
    EventScoringService,
    EventSeverityPrior,
    EventStateUncertaintyPrior,
    NoveltyWeights,
    SourceCredibilityPrior,
    UncertaintyWeights,
)
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
    "EventDecayPrior",
    "EventExposure",
    "EventExposureService",
    "EventState",
    "EventScore",
    "EventScoreComponent",
    "EventScoreFactor",
    "EventScoreMethodDetail",
    "EventScoringConfig",
    "EventScoringInput",
    "EventScoringMethod",
    "EventScoringService",
    "EventSeverityPrior",
    "EventStateUncertaintyPrior",
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
    "NewsStateConfig",
    "NewsStateDataError",
    "NewsStateService",
    "NoveltyWeights",
    "RawNewsItem",
    "RawNewsIngestionService",
    "RawNewsIngestionSummary",
    "RelationshipTraversal",
    "RevisionEventExposure",
    "SourceCredibilityPrior",
    "UnresolvedEntityLink",
    "UncertaintyWeights",
    "ConfidenceWeights",
    "EVENT_SCORING_VERSION",
    "NEWS_STATE_VERSION",
]
