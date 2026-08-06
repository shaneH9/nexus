"""Deterministic orchestration for canonical event creation and evolution."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from sra_nexus.aggregator.anchors import EventAnchors, extract_event_anchors
from sra_nexus.aggregator.classification import EventClassification, EventClassifier
from sra_nexus.aggregator.enums import EventState, EventType, NewsSourceType
from sra_nexus.aggregator.events import CanonicalEvent
from sra_nexus.aggregator.normalization import comparison_tokens
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.revisions import (
    CanonicalEventCandidateQuery,
    CanonicalEventRevision,
)
from sra_nexus.aggregator.similarity import (
    ClusteringConfig,
    EventSimilarity,
    score_event_similarity,
)
from sra_nexus.common.models import ContractModel, NonBlankStr
from sra_nexus.common.types import CanonicalEventId, NewsId
from sra_nexus.storage.canonical import CanonicalEventRepository
from sra_nexus.storage.raw import RawNewsRepository


class CanonicalizationDecisionType(StrEnum):
    """Stable outcomes for processing one raw-news observation."""

    NEW_EVENT = "NEW_EVENT"
    CLUSTERED = "CLUSTERED"
    AMBIGUOUS = "AMBIGUOUS"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"


class CanonicalizationCandidateScore(ContractModel):
    """Inspectable similarity result for one retrieved event candidate."""

    event_id: CanonicalEventId
    revision_number: int = Field(ge=1)
    similarity: EventSimilarity


class CanonicalizationResult(ContractModel):
    """Typed, explainable outcome of one canonicalization attempt."""

    decision: CanonicalizationDecisionType
    news_id: NewsId
    event_id: CanonicalEventId | None = None
    revision_number: int | None = Field(default=None, ge=1)
    classification: EventClassification | None = None
    candidate_scores: tuple[CanonicalizationCandidateScore, ...] = ()
    explanation: NonBlankStr

    @model_validator(mode="after")
    def validate_decision_shape(self) -> CanonicalizationResult:
        """Require event identity for persisted and already-processed outcomes."""
        if (
            self.decision
            in {
                CanonicalizationDecisionType.NEW_EVENT,
                CanonicalizationDecisionType.CLUSTERED,
                CanonicalizationDecisionType.ALREADY_PROCESSED,
            }
            and self.event_id is None
        ):
            raise ValueError("this decision requires event_id")
        if self.decision is CanonicalizationDecisionType.AMBIGUOUS and self.event_id is not None:
            raise ValueError("an ambiguous decision must not select event_id")
        return self


class CanonicalizationService:
    """Classify, compare, decide, and persist without embedding SQL or entity logic."""

    def __init__(
        self,
        repository: CanonicalEventRepository,
        classifier: EventClassifier,
        config: ClusteringConfig | None = None,
    ) -> None:
        """Configure repository, classifier, and centralized engineering values."""
        self._repository = repository
        self._classifier = classifier
        self._config = ClusteringConfig() if config is None else config

    def canonicalize(self, item: RawNewsItem) -> CanonicalizationResult:
        """Canonicalize one available raw item idempotently and explainably."""
        existing_event_id = self._repository.get_event_id_for_news(item.news_id)
        if existing_event_id is not None:
            current = self._repository.get_current_revision(existing_event_id)
            return CanonicalizationResult(
                decision=CanonicalizationDecisionType.ALREADY_PROCESSED,
                news_id=item.news_id,
                event_id=existing_event_id,
                revision_number=None if current is None else current.revision_number,
                explanation="Raw NewsId is already assigned to this canonical event.",
            )

        classification = self._classifier.classify(item)
        anchors = extract_event_anchors(item)
        query = CanonicalEventCandidateQuery(
            event_type=classification.event_type,
            event_subtype=classification.event_subtype,
            as_of=item.process_time,
            not_before=item.process_time - self._config.maximum_candidate_age,
            anchors=tuple(sorted(anchors.all)),
        )
        candidates = self._repository.find_candidates(query)
        scored = tuple(
            sorted(
                (
                    CanonicalizationCandidateScore(
                        event_id=candidate.event.event_id,
                        revision_number=candidate.revision_number,
                        similarity=score_event_similarity(
                            item,
                            classification,
                            anchors,
                            candidate,
                            self._config,
                        ),
                    )
                    for candidate in candidates
                ),
                key=lambda candidate: (
                    -candidate.similarity.total_score,
                    str(candidate.event_id),
                ),
            )
        )
        eligible = tuple(
            candidate
            for candidate in scored
            if not candidate.similarity.guard_failures
            and candidate.similarity.total_score >= self._config.clustering_threshold
        )

        if len(eligible) >= 2 and (
            eligible[0].similarity.total_score - eligible[1].similarity.total_score
            < self._config.ambiguity_margin
        ):
            return CanonicalizationResult(
                decision=CanonicalizationDecisionType.AMBIGUOUS,
                news_id=item.news_id,
                classification=classification,
                candidate_scores=scored,
                explanation=(
                    "Top qualifying candidates fall within the configured ambiguity margin."
                ),
            )

        if eligible:
            chosen = eligible[0]
            current = next(
                candidate for candidate in candidates if candidate.event.event_id == chosen.event_id
            )
            revision = self._append_revision(item, classification, anchors, current)
            return CanonicalizationResult(
                decision=CanonicalizationDecisionType.CLUSTERED,
                news_id=item.news_id,
                event_id=revision.event.event_id,
                revision_number=revision.revision_number,
                classification=classification,
                candidate_scores=scored,
                explanation="Best candidate passed hard guards and clustering threshold.",
            )

        revision = self._create_revision(item, classification, anchors)
        return CanonicalizationResult(
            decision=CanonicalizationDecisionType.NEW_EVENT,
            news_id=item.news_id,
            event_id=revision.event.event_id,
            revision_number=revision.revision_number,
            classification=classification,
            candidate_scores=scored,
            explanation="No unambiguous candidate met the configured clustering threshold.",
        )

    def canonicalize_many(
        self,
        items: Iterable[RawNewsItem],
    ) -> tuple[CanonicalizationResult, ...]:
        """Process a sequence by process_time then NewsId regardless of input order."""
        ordered = sorted(items, key=lambda item: (item.process_time, str(item.news_id)))
        return tuple(self.canonicalize(item) for item in ordered)

    def canonicalize_available_raw_news(
        self,
        raw_repository: RawNewsRepository,
        as_of: datetime,
    ) -> tuple[CanonicalizationResult, ...]:
        """Process every raw observation available by an explicit historical cutoff."""
        return self.canonicalize_many(raw_repository.list_available_as_of(as_of))

    def _create_revision(
        self,
        item: RawNewsItem,
        classification: EventClassification,
        anchors: EventAnchors,
    ) -> CanonicalEventRevision:
        event = CanonicalEvent(
            first_event_time=item.event_time,
            first_receive_time=item.receive_time,
            last_update_time=item.process_time,
            event_type=classification.event_type,
            event_subtype=classification.event_subtype,
            headline_summary=item.headline,
            event_summary=None,
            source_news_ids=(item.news_id,),
            event_state=EventState.NEW,
        )
        revision = CanonicalEventRevision(
            revision_number=1,
            available_at=item.process_time,
            event=event,
            headline_tokens=tuple(sorted(comparison_tokens(item.headline))),
            anchors=tuple(sorted(anchors.all)),
            ticker_anchors=tuple(sorted(anchors.tickers)),
            source_names=(item.source,),
            source_types=(item.source_type,),
        )
        self._repository.create_event(revision)
        return revision

    def _append_revision(
        self,
        item: RawNewsItem,
        classification: EventClassification,
        anchors: EventAnchors,
        current: CanonicalEventRevision,
    ) -> CanonicalEventRevision:
        event_data = current.event.model_dump()
        event_data.update(
            {
                "last_update_time": item.process_time,
                "headline_summary": item.headline,
                "source_news_ids": (*current.event.source_news_ids, item.news_id),
                "event_state": _next_event_state(current, item),
            }
        )
        event = CanonicalEvent.model_validate(event_data)
        source_names = _append_unique(current.source_names, item.source)
        source_types = _append_unique(current.source_types, item.source_type)
        revision = CanonicalEventRevision(
            revision_number=current.revision_number + 1,
            available_at=item.process_time,
            event=event,
            headline_tokens=tuple(sorted(comparison_tokens(item.headline))),
            anchors=tuple(sorted(set(current.anchors) | anchors.all)),
            ticker_anchors=tuple(sorted(set(current.ticker_anchors) | anchors.tickers)),
            source_names=source_names,
            source_types=source_types,
        )
        self._repository.append_revision(revision)
        return revision


def _append_unique[T](values: tuple[T, ...], value: T) -> tuple[T, ...]:
    return values if value in values else (*values, value)


def _next_event_state(current: CanonicalEventRevision, item: RawNewsItem) -> EventState:
    if current.event.event_state is EventState.CONFIRMED:
        return EventState.CONFIRMED
    if item.source not in current.source_names and _is_official_confirmation(
        item.source_type,
        current.event.event_type,
    ):
        return EventState.CONFIRMED
    if item.source not in current.source_names:
        return EventState.DEVELOPING
    return EventState.UPDATED


def _is_official_confirmation(source_type: NewsSourceType, event_type: EventType) -> bool:
    if source_type is NewsSourceType.COMPANY_RELEASE:
        return event_type is EventType.COMPANY
    if source_type is NewsSourceType.SEC:
        return event_type in {EventType.COMPANY, EventType.REGULATORY}
    if source_type is NewsSourceType.GOVERNMENT:
        return event_type in {
            EventType.GEOPOLITICAL,
            EventType.MACRO,
            EventType.REGULATORY,
            EventType.SYSTEMIC,
        }
    if source_type is NewsSourceType.CENTRAL_BANK:
        return event_type in {EventType.MACRO, EventType.RATE}
    return False
