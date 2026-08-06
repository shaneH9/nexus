"""Revision-safe, on-demand aggregation of deterministic instrument NewsState."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import Field

from sra_nexus.aggregator.enums import EventState, EventType
from sra_nexus.aggregator.events import EventExposure
from sra_nexus.aggregator.exposures import RevisionEventExposure
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.aggregator.scoring import (
    EventScore,
    EventScoringInput,
    EventScoringService,
)
from sra_nexus.aggregator.scoring_math import (
    bounded_union,
    calculate_directional_intensity,
    calculate_event_decay,
    calculate_event_intensity,
    calculate_event_risk_contribution,
    calculate_news_acceleration,
    calculate_novelty_contribution,
    calculate_weighted_confidence,
)
from sra_nexus.aggregator.state import NewsState
from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    UnitIntervalScore,
    normalize_utc_datetime,
)
from sra_nexus.common.types import CanonicalEventId, InstrumentId, NewsId
from sra_nexus.reference.enums import ReferenceDataPolicy
from sra_nexus.storage.canonical import CanonicalEventRepository
from sra_nexus.storage.event_graph import EventEntityLinkRepository, EventExposureRepository
from sra_nexus.storage.raw import RawNewsRepository

NEWS_STATE_VERSION = "news-state-v1"


class NewsStateConfig(ContractModel):
    """Central INITIAL ENGINEERING PRIORS for on-demand state aggregation."""

    news_state_version: NonBlankStr = NEWS_STATE_VERSION
    reference_data_policy: ReferenceDataPolicy = ReferenceDataPolicy.CURRENT_REFERENCE_DATA
    news_volume_window_seconds: int = Field(default=24 * 60 * 60, gt=0)
    acceleration_recent_window_seconds: int = Field(default=15 * 60, gt=0)
    acceleration_prior_window_seconds: int = Field(default=60 * 60, gt=0)
    acceleration_rate_unit_seconds: int = Field(default=60 * 60, gt=0)
    minimum_active_influence: UnitIntervalScore = 0.01
    contradiction_uncertainty_contribution: UnitIntervalScore = 0.35


class NewsStateDataError(RuntimeError):
    """Raised when persisted historical evidence is missing or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class _ActiveEvent:
    revision: CanonicalEventRevision
    score: EventScore
    decay: float
    effective_exposure: EventExposure
    records: tuple[RevisionEventExposure, ...]
    raw_items: tuple[RawNewsItem, ...]


class NewsStateService:
    """Construct one historical NewsState using only repository-visible evidence."""

    def __init__(
        self,
        canonical_repository: CanonicalEventRepository,
        raw_repository: RawNewsRepository,
        exposure_repository: EventExposureRepository,
        entity_link_repository: EventEntityLinkRepository,
        scoring_service: EventScoringService,
        config: NewsStateConfig | None = None,
    ) -> None:
        """Configure focused repository boundaries and immutable aggregation policy."""
        self._canonical = canonical_repository
        self._raw = raw_repository
        self._exposures = exposure_repository
        self._entity_links = entity_link_repository
        self._scoring = scoring_service
        self._config = NewsStateConfig() if config is None else config
        if self._scoring.config.reference_data_policy is not self._config.reference_data_policy:
            raise ValueError("event scoring and NewsState reference-data policies must match")

    @property
    def config(self) -> NewsStateConfig:
        """Expose the immutable state policy used for reproducibility."""
        return self._config

    def get_news_state(self, instrument_id: InstrumentId, as_of: datetime) -> NewsState:
        """Return state visible at a timezone-aware cutoff, computed on demand."""
        cutoff = normalize_utc_datetime(as_of)
        visible_records = self._exposures.list_instrument_exposures_as_of(
            instrument_id,
            cutoff,
        )
        grouped = _group_revision_records(visible_records)
        active: list[_ActiveEvent] = []
        for records in grouped:
            revision = self._load_exact_revision(records)
            if revision.available_at > cutoff:
                raise NewsStateDataError("future canonical revision entered historical query")
            if revision.event.event_state is EventState.RETRACTED:
                continue
            score, raw_items = self._score_revision(revision, cutoff)
            decay = calculate_event_decay(
                revision.available_at,
                cutoff,
                score.decay_tau_seconds,
            )
            if decay < self._config.minimum_active_influence:
                continue
            active.append(
                _ActiveEvent(
                    revision=revision,
                    score=score,
                    decay=decay,
                    effective_exposure=_effective_exposure(records),
                    records=records,
                    raw_items=raw_items,
                )
            )

        active.sort(key=lambda item: (item.revision.available_at, str(item.score.event_id)))
        return self._aggregate(instrument_id, cutoff, tuple(active))

    def _load_exact_revision(
        self,
        records: tuple[RevisionEventExposure, ...],
    ) -> CanonicalEventRevision:
        first = records[0]
        revision = self._canonical.get_event_revision(
            first.exposure.event_id,
            first.revision_number,
        )
        if revision is None or revision.revision_id != first.revision_id:
            raise NewsStateDataError("exposure snapshot has no matching canonical revision")
        if any(record.revision_id != revision.revision_id for record in records):
            raise NewsStateDataError("instrument exposure group mixes canonical revisions")
        return revision

    def _score_revision(
        self,
        revision: CanonicalEventRevision,
        cutoff: datetime,
    ) -> tuple[EventScore, tuple[RawNewsItem, ...]]:
        raw_items = self._raw.get_many_available_as_of(
            revision.event.source_news_ids,
            cutoff,
        )
        if {item.news_id for item in raw_items} != set(revision.event.source_news_ids):
            raise NewsStateDataError("canonical revision source observations are not available")
        previous = (
            None
            if revision.revision_number == 1
            else self._canonical.get_event_revision(
                revision.event.event_id,
                revision.revision_number - 1,
            )
        )
        if revision.revision_number > 1 and previous is None:
            raise NewsStateDataError("canonical revision history is incomplete")
        current_links = self._entity_links.list_entity_links_for_revision(revision.revision_id)
        current_exposures = self._exposures.list_exposures_for_revision(revision.revision_id)
        previous_links = (
            ()
            if previous is None
            else self._entity_links.list_entity_links_for_revision(previous.revision_id)
        )
        previous_exposures = (
            ()
            if previous is None
            else self._exposures.list_exposures_for_revision(previous.revision_id)
        )
        score = self._scoring.score_revision(
            EventScoringInput(
                revision=revision,
                previous_revision=previous,
                raw_items=raw_items,
                entity_links=current_links,
                previous_entity_links=previous_links,
                exposures=current_exposures,
                previous_exposures=previous_exposures,
            )
        )
        return score, raw_items

    def _aggregate(
        self,
        instrument_id: InstrumentId,
        cutoff: datetime,
        active: tuple[_ActiveEvent, ...],
    ) -> NewsState:
        positive_intensity = 0.0
        negative_intensity = 0.0
        risk_contributions: dict[str, list[float]] = {
            "company_event_risk": [],
            "sector_event_risk": [],
            "macro_event_risk": [],
            "geopolitical_event_risk": [],
            "regulatory_event_risk": [],
            "systemic_event_risk": [],
        }
        novelty_contributions: list[float] = []
        uncertainty_contributions: list[float] = []
        confidence_weights: list[tuple[float, float]] = []
        known_signs: set[int] = set()
        relevant_raw: dict[NewsId, RawNewsItem] = {}
        direct: list[EventExposure] = []
        indirect: list[EventExposure] = []

        for item in active:
            score = item.score
            exposure = item.effective_exposure
            intensity = calculate_event_intensity(
                exposure.magnitude,
                exposure.relevance,
                score.severity,
                score.novelty,
                score.credibility,
                score.confidence,
                item.decay,
            )
            directional = calculate_directional_intensity(intensity, exposure.direction)
            positive_intensity += max(directional, 0.0)
            negative_intensity += abs(min(directional, 0.0))
            if exposure.direction != 0.0:
                known_signs.add(1 if exposure.direction > 0.0 else -1)

            risk = calculate_event_risk_contribution(
                exposure.magnitude,
                exposure.relevance,
                score.severity,
                score.uncertainty,
                score.credibility,
                score.confidence,
                item.decay,
            )
            risk_contributions[_risk_field(item.revision.event.event_type)].append(risk)
            novelty_contributions.append(
                calculate_novelty_contribution(
                    score.novelty,
                    exposure.magnitude,
                    exposure.relevance,
                    item.decay,
                )
            )
            relevance_weight = exposure.magnitude * exposure.relevance * item.decay
            uncertainty_contributions.append(score.uncertainty * relevance_weight)
            confidence_weights.append((score.confidence, relevance_weight))
            for raw_item in item.raw_items:
                relevant_raw[raw_item.news_id] = raw_item
            for record in item.records:
                if record.exposure.is_direct:
                    direct.append(record.exposure)
                else:
                    indirect.append(record.exposure)

        if len(known_signs) > 1:
            uncertainty_contributions.append(self._config.contradiction_uncertainty_contribution)
        volume, acceleration = self._news_flow(tuple(relevant_raw.values()), cutoff)
        risks = {name: bounded_union(tuple(values)) for name, values in risk_contributions.items()}
        return NewsState(
            instrument_id=instrument_id,
            as_of=cutoff,
            event_scoring_version=self._scoring.config.event_scoring_version,
            news_state_version=self._config.news_state_version,
            reference_data_policy=self._config.reference_data_policy,
            positive_event_intensity=positive_intensity,
            negative_event_intensity=negative_intensity,
            **risks,
            news_volume=volume,
            news_acceleration=acceleration,
            novelty_intensity=bounded_union(tuple(novelty_contributions)),
            uncertainty=bounded_union(tuple(uncertainty_contributions)),
            confidence=calculate_weighted_confidence(tuple(confidence_weights)),
            active_event_ids=tuple(item.score.event_id for item in active),
            direct_event_exposures=tuple(direct),
            indirect_event_exposures=tuple(indirect),
        )

    def _news_flow(
        self,
        items: tuple[RawNewsItem, ...],
        cutoff: datetime,
    ) -> tuple[int, float]:
        volume_start = cutoff - timedelta(seconds=self._config.news_volume_window_seconds)
        recent_start = cutoff - timedelta(seconds=self._config.acceleration_recent_window_seconds)
        prior_start = recent_start - timedelta(
            seconds=self._config.acceleration_prior_window_seconds
        )
        volume = sum(volume_start < item.process_time <= cutoff for item in items)
        recent = sum(recent_start < item.process_time <= cutoff for item in items)
        prior = sum(prior_start < item.process_time <= recent_start for item in items)
        acceleration = calculate_news_acceleration(
            recent,
            self._config.acceleration_recent_window_seconds,
            prior,
            self._config.acceleration_prior_window_seconds,
            rate_unit_seconds=self._config.acceleration_rate_unit_seconds,
        )
        return volume, acceleration


def _group_revision_records(
    records: tuple[RevisionEventExposure, ...],
) -> tuple[tuple[RevisionEventExposure, ...], ...]:
    grouped: dict[tuple[CanonicalEventId, object], list[RevisionEventExposure]] = {}
    for record in records:
        key = (record.exposure.event_id, record.revision_id)
        grouped.setdefault(key, []).append(record)
    return tuple(
        tuple(sorted(group, key=lambda record: not record.exposure.is_direct))
        for _, group in sorted(
            grouped.items(),
            key=lambda item: (item[1][0].available_at, str(item[0][0])),
        )
    )


def _effective_exposure(records: tuple[RevisionEventExposure, ...]) -> EventExposure:
    """Use direct evidence when present; otherwise use strongest indirect evidence."""
    direct = tuple(record.exposure for record in records if record.exposure.is_direct)
    candidates = direct or tuple(record.exposure for record in records)
    return sorted(
        candidates,
        key=lambda exposure: (-exposure.magnitude, -exposure.relevance, -exposure.confidence),
    )[0]


def _risk_field(event_type: EventType) -> str:
    if event_type is EventType.COMPANY:
        return "company_event_risk"
    if event_type is EventType.SECTOR:
        return "sector_event_risk"
    if event_type in {EventType.MACRO, EventType.RATE, EventType.CURRENCY, EventType.COMMODITY}:
        return "macro_event_risk"
    if event_type is EventType.GEOPOLITICAL:
        return "geopolitical_event_risk"
    if event_type is EventType.REGULATORY:
        return "regulatory_event_risk"
    return "systemic_event_risk"
