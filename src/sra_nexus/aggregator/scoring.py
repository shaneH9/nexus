"""Deterministic, auditable engineering-prior scoring for event revisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from pydantic import Field, field_validator, model_validator

from sra_nexus.aggregator.classification import EventClassifier
from sra_nexus.aggregator.entity_links import EventEntityLink
from sra_nexus.aggregator.enums import (
    EntityMatchMethod,
    EventScoreComponent,
    EventScoringMethod,
    EventState,
    EventSubtype,
    EventType,
    NewsSourceType,
)
from sra_nexus.aggregator.exposures import (
    RevisionEventExposure,
    deterministic_event_direction,
)
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.aggregator.scoring_math import bounded_union
from sra_nexus.common.models import (
    ContractModel,
    FiniteFloat,
    NonBlankStr,
    SignedUnitScore,
    UnitIntervalScore,
    UtcDatetime,
)
from sra_nexus.common.types import (
    CanonicalEventId,
    CanonicalEventRevisionId,
    NewsId,
)
from sra_nexus.reference.enums import ReferenceDataPolicy

EVENT_SCORING_VERSION = "event-scoring-v1"


class SourceCredibilityPrior(ContractModel):
    """Initial dimensionless credibility prior for one source category."""

    source_type: NewsSourceType
    credibility: UnitIntervalScore


class EventSeverityPrior(ContractModel):
    """Initial dimensionless economic-significance prior for an event category."""

    event_type: EventType
    event_subtype: EventSubtype | None = None
    severity: UnitIntervalScore

    @model_validator(mode="after")
    def validate_subtype(self) -> Self:
        """Require a specific subtype prior to belong to its declared type."""
        if self.event_subtype is not None and self.event_subtype.event_type is not self.event_type:
            raise ValueError("severity-prior subtype must belong to event_type")
        return self


class EventDecayPrior(ContractModel):
    """Initial event-class influence timescale in seconds."""

    event_type: EventType
    event_subtype: EventSubtype | None = None
    tau_seconds: float = Field(gt=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_subtype(self) -> Self:
        """Require a specific subtype prior to belong to its declared type."""
        if self.event_subtype is not None and self.event_subtype.event_type is not self.event_type:
            raise ValueError("decay-prior subtype must belong to event_type")
        return self


class EventStateUncertaintyPrior(ContractModel):
    """Initial uncertainty factor associated with a canonical lifecycle state."""

    event_state: EventState
    uncertainty: UnitIntervalScore


class NoveltyWeights(ContractModel):
    """Weights for deterministic revision-delta novelty factors."""

    headline_token_change: UnitIntervalScore = 0.40
    independent_source_addition: UnitIntervalScore = 0.10
    new_entity: UnitIntervalScore = 0.15
    new_instrument: UnitIntervalScore = 0.15
    official_confirmation: UnitIntervalScore = 0.20

    @model_validator(mode="after")
    def require_unit_sum(self) -> Self:
        """Keep revision-delta novelty a convex bounded combination."""
        if abs(sum(self.model_dump().values()) - 1.0) > 1e-12:
            raise ValueError("novelty weights must sum to 1")
        return self


class ConfidenceWeights(ContractModel):
    """Weights for confidence in structured event interpretation."""

    source_credibility: UnitIntervalScore = 0.30
    corroboration: UnitIntervalScore = 0.15
    classifier: UnitIntervalScore = 0.20
    entity_linking: UnitIntervalScore = 0.15
    exposure_mapping: UnitIntervalScore = 0.15
    official_confirmation: UnitIntervalScore = 0.05

    @model_validator(mode="after")
    def require_unit_sum(self) -> Self:
        """Keep confidence a convex bounded combination."""
        if abs(sum(self.model_dump().values()) - 1.0) > 1e-12:
            raise ValueError("confidence weights must sum to 1")
        return self


class UncertaintyWeights(ContractModel):
    """Independent bounded-union weights for interpretable uncertainty causes."""

    event_state: UnitIntervalScore = 0.35
    source_credibility_dispersion: UnitIntervalScore = 0.15
    speculative_only: UnitIntervalScore = 0.25
    lack_official_confirmation: UnitIntervalScore = 0.15
    unresolved_reference_evidence: UnitIntervalScore = 0.20
    entity_link_confidence_deficit: UnitIntervalScore = 0.10
    exposure_direction_conflict: UnitIntervalScore = 0.30


def _initial_source_priors() -> tuple[SourceCredibilityPrior, ...]:
    return tuple(
        SourceCredibilityPrior(source_type=source_type, credibility=value)
        for source_type, value in (
            (NewsSourceType.SEC, 0.96),
            (NewsSourceType.GOVERNMENT, 0.95),
            (NewsSourceType.CENTRAL_BANK, 0.95),
            (NewsSourceType.COMPANY_RELEASE, 0.88),
            (NewsSourceType.MACRO_CALENDAR, 0.85),
            (NewsSourceType.WIRE, 0.82),
            (NewsSourceType.FINANCIAL_NEWS, 0.78),
            (NewsSourceType.GLOBAL_NEWS, 0.65),
            (NewsSourceType.OTHER, 0.50),
            (NewsSourceType.SPECULATIVE, 0.35),
            (NewsSourceType.SOCIAL, 0.25),
        )
    )


def _initial_severity_priors() -> tuple[EventSeverityPrior, ...]:
    defaults = (
        (EventType.COMPANY, 0.40),
        (EventType.SECTOR, 0.50),
        (EventType.MACRO, 0.55),
        (EventType.GEOPOLITICAL, 0.65),
        (EventType.REGULATORY, 0.60),
        (EventType.MARKET_STRUCTURE, 0.65),
        (EventType.SYSTEMIC, 0.80),
        (EventType.COMMODITY, 0.55),
        (EventType.CURRENCY, 0.55),
        (EventType.RATE, 0.60),
    )
    specifics = (
        (EventSubtype.COMPANY_EARNINGS, 0.70),
        (EventSubtype.COMPANY_GUIDANCE, 0.60),
        (EventSubtype.COMPANY_MERGER_ACQUISITION, 0.75),
        (EventSubtype.COMPANY_CAPITAL_RAISE, 0.65),
        (EventSubtype.COMPANY_BUYBACK, 0.45),
        (EventSubtype.MACRO_CPI, 0.70),
        (EventSubtype.MACRO_JOBS, 0.65),
        (EventSubtype.MACRO_GDP, 0.65),
        (EventSubtype.RATE_CENTRAL_BANK_DECISION, 0.75),
        (EventSubtype.GEOPOLITICAL_CONFLICT, 0.90),
        (EventSubtype.GEOPOLITICAL_SANCTION, 0.80),
        (EventSubtype.REGULATORY_APPROVAL, 0.65),
        (EventSubtype.REGULATORY_ENFORCEMENT, 0.75),
        (EventSubtype.SYSTEMIC_BANK_FAILURE, 0.95),
        (EventSubtype.SYSTEMIC_EXCHANGE_OUTAGE, 0.85),
        (EventSubtype.SYSTEMIC_MARKET_DISRUPTION, 0.90),
    )
    return (
        *(
            EventSeverityPrior(event_type=event_type, severity=value)
            for event_type, value in defaults
        ),
        *(
            EventSeverityPrior(
                event_type=event_subtype.event_type,
                event_subtype=event_subtype,
                severity=value,
            )
            for event_subtype, value in specifics
        ),
    )


def _initial_decay_priors() -> tuple[EventDecayPrior, ...]:
    hour = 3600.0
    defaults = (
        (EventType.COMPANY, 6 * hour),
        (EventType.SECTOR, 12 * hour),
        (EventType.MACRO, 6 * hour),
        (EventType.GEOPOLITICAL, 48 * hour),
        (EventType.REGULATORY, 24 * hour),
        (EventType.MARKET_STRUCTURE, 4 * hour),
        (EventType.SYSTEMIC, 48 * hour),
        (EventType.COMMODITY, 24 * hour),
        (EventType.CURRENCY, 12 * hour),
        (EventType.RATE, 12 * hour),
    )
    specifics = (
        (EventSubtype.COMPANY_EARNINGS, 12 * hour),
        (EventSubtype.COMPANY_MERGER_ACQUISITION, 72 * hour),
        (EventSubtype.COMPANY_BUYBACK, 24 * hour),
        (EventSubtype.MACRO_CPI, 12 * hour),
        (EventSubtype.RATE_CENTRAL_BANK_DECISION, 24 * hour),
        (EventSubtype.GEOPOLITICAL_CONFLICT, 96 * hour),
        (EventSubtype.GEOPOLITICAL_SANCTION, 72 * hour),
        (EventSubtype.SYSTEMIC_BANK_FAILURE, 96 * hour),
    )
    return (
        *(
            EventDecayPrior(event_type=event_type, tau_seconds=value)
            for event_type, value in defaults
        ),
        *(
            EventDecayPrior(
                event_type=event_subtype.event_type,
                event_subtype=event_subtype,
                tau_seconds=value,
            )
            for event_subtype, value in specifics
        ),
    )


def _initial_state_uncertainty() -> tuple[EventStateUncertaintyPrior, ...]:
    return tuple(
        EventStateUncertaintyPrior(event_state=state, uncertainty=value)
        for state, value in (
            (EventState.NEW, 0.80),
            (EventState.DEVELOPING, 0.70),
            (EventState.UPDATED, 0.50),
            (EventState.CONFIRMED, 0.15),
            (EventState.RESOLVED, 0.10),
            (EventState.RETRACTED, 1.00),
        )
    )


class EventScoringConfig(ContractModel):
    """Central INITIAL ENGINEERING PRIORS for deterministic revision scoring."""

    event_scoring_version: NonBlankStr = EVENT_SCORING_VERSION
    reference_data_policy: ReferenceDataPolicy = ReferenceDataPolicy.CURRENT_REFERENCE_DATA
    source_credibility_priors: tuple[SourceCredibilityPrior, ...] = Field(
        default_factory=_initial_source_priors
    )
    severity_priors: tuple[EventSeverityPrior, ...] = Field(
        default_factory=_initial_severity_priors
    )
    decay_priors: tuple[EventDecayPrior, ...] = Field(default_factory=_initial_decay_priors)
    state_uncertainty_priors: tuple[EventStateUncertaintyPrior, ...] = Field(
        default_factory=_initial_state_uncertainty
    )
    novelty_weights: NoveltyWeights = Field(default_factory=NoveltyWeights)
    confidence_weights: ConfidenceWeights = Field(default_factory=ConfidenceWeights)
    uncertainty_weights: UncertaintyWeights = Field(default_factory=UncertaintyWeights)
    first_revision_novelty: UnitIntervalScore = 1.0

    @model_validator(mode="after")
    def validate_prior_coverage(self) -> Self:
        """Require unique source/state priors and a fallback for every event type."""
        source_types = [prior.source_type for prior in self.source_credibility_priors]
        if len(source_types) != len(set(source_types)) or set(source_types) != set(NewsSourceType):
            raise ValueError("source credibility priors must cover each NewsSourceType once")
        states = [prior.event_state for prior in self.state_uncertainty_priors]
        if len(states) != len(set(states)) or set(states) != set(EventState):
            raise ValueError("state uncertainty priors must cover each EventState once")
        for priors, name in (
            (self.severity_priors, "severity"),
            (self.decay_priors, "decay"),
        ):
            fallback_types = [prior.event_type for prior in priors if prior.event_subtype is None]
            if len(fallback_types) != len(set(fallback_types)) or set(fallback_types) != set(
                EventType
            ):
                raise ValueError(f"{name} priors must have one fallback per EventType")
        return self

    def source_credibility(self, source_type: NewsSourceType) -> float:
        """Return the configured dimensionless prior for a source category."""
        return next(
            prior.credibility
            for prior in self.source_credibility_priors
            if prior.source_type is source_type
        )

    def severity(self, event_type: EventType, event_subtype: EventSubtype) -> float:
        """Return the most specific configured dimensionless severity prior."""
        specific = next(
            (
                prior.severity
                for prior in self.severity_priors
                if prior.event_subtype is event_subtype
            ),
            None,
        )
        if specific is not None:
            return specific
        return next(
            prior.severity
            for prior in self.severity_priors
            if prior.event_type is event_type and prior.event_subtype is None
        )

    def tau_seconds(self, event_type: EventType, event_subtype: EventSubtype) -> float:
        """Return the most specific configured decay timescale in seconds."""
        specific = next(
            (
                prior.tau_seconds
                for prior in self.decay_priors
                if prior.event_subtype is event_subtype
            ),
            None,
        )
        if specific is not None:
            return specific
        return next(
            prior.tau_seconds
            for prior in self.decay_priors
            if prior.event_type is event_type and prior.event_subtype is None
        )

    def state_uncertainty(self, event_state: EventState) -> float:
        """Return the configured lifecycle uncertainty factor."""
        return next(
            prior.uncertainty
            for prior in self.state_uncertainty_priors
            if prior.event_state is event_state
        )


class EventScoringInput(ContractModel):
    """Availability-safe evidence for scoring one exact immutable revision."""

    revision: CanonicalEventRevision
    previous_revision: CanonicalEventRevision | None = None
    raw_items: tuple[RawNewsItem, ...] = Field(min_length=1)
    entity_links: tuple[EventEntityLink, ...] = ()
    previous_entity_links: tuple[EventEntityLink, ...] = ()
    exposures: tuple[RevisionEventExposure, ...] = ()
    previous_exposures: tuple[RevisionEventExposure, ...] = ()

    @model_validator(mode="after")
    def validate_revision_evidence(self) -> Self:
        """Reject missing, future, or cross-revision evidence."""
        expected_news = set(self.revision.event.source_news_ids)
        actual_news = {item.news_id for item in self.raw_items}
        if actual_news != expected_news or len(actual_news) != len(self.raw_items):
            raise ValueError("raw_items must exactly match revision source_news_ids")
        if any(item.process_time > self.revision.available_at for item in self.raw_items):
            raise ValueError("raw item cannot become available after its canonical revision")
        self._validate_graph_records(self.revision, self.entity_links, self.exposures)
        if self.previous_revision is None:
            if self.revision.revision_number != 1:
                raise ValueError("non-first revisions require previous_revision")
            if self.previous_entity_links or self.previous_exposures:
                raise ValueError("first revisions cannot have previous graph evidence")
        else:
            previous = self.previous_revision
            if previous.event.event_id != self.revision.event.event_id or (
                previous.revision_number != self.revision.revision_number - 1
            ):
                raise ValueError("previous_revision must be the immediately preceding revision")
            if previous.available_at > self.revision.available_at:
                raise ValueError("previous_revision cannot be available after current revision")
            self._validate_graph_records(
                previous,
                self.previous_entity_links,
                self.previous_exposures,
            )
        return self

    @staticmethod
    def _validate_graph_records(
        revision: CanonicalEventRevision,
        links: tuple[EventEntityLink, ...],
        exposures: tuple[RevisionEventExposure, ...],
    ) -> None:
        for link in links:
            if link.revision_id != revision.revision_id or link.event_id != revision.event.event_id:
                raise ValueError("entity links must belong to the scored revision")
            if link.available_at > revision.available_at:
                raise ValueError("entity link cannot be available after its revision")
        for record in exposures:
            if record.revision_id != revision.revision_id or (
                record.exposure.event_id != revision.event.event_id
            ):
                raise ValueError("event exposures must belong to the scored revision")
            if record.available_at > revision.available_at:
                raise ValueError("event exposure cannot be available after its revision")


class EventScoreFactor(ContractModel):
    """One finite input retained for audit of a score component."""

    component: EventScoreComponent
    name: NonBlankStr
    value: FiniteFloat
    weight: UnitIntervalScore | None = None
    rule_reference: NonBlankStr
    explanation: NonBlankStr


class EventScoreMethodDetail(ContractModel):
    """Method and concise interpretation for one score component."""

    component: EventScoreComponent
    method: EventScoringMethod
    explanation: NonBlankStr


class EventScore(ContractModel):
    """Auditable deterministic feature scores for one immutable event revision."""

    event_id: CanonicalEventId
    revision_id: CanonicalEventRevisionId
    revision_number: int = Field(ge=1)
    available_at: UtcDatetime
    sentiment: SignedUnitScore
    surprise: FiniteFloat | None = None
    novelty: UnitIntervalScore
    severity: UnitIntervalScore
    credibility: UnitIntervalScore
    confidence: UnitIntervalScore
    uncertainty: UnitIntervalScore
    decay_tau_seconds: float = Field(gt=0.0, allow_inf_nan=False)
    source_news_ids: tuple[NewsId, ...] = Field(min_length=1)
    source_names: tuple[NonBlankStr, ...] = Field(min_length=1)
    source_types: tuple[NewsSourceType, ...] = Field(min_length=1)
    scoring_methods: tuple[EventScoreMethodDetail, ...] = Field(min_length=7)
    contributing_factors: tuple[EventScoreFactor, ...] = Field(min_length=1)
    explanations: tuple[NonBlankStr, ...] = Field(min_length=1)
    event_scoring_version: NonBlankStr
    reference_data_policy: ReferenceDataPolicy

    @field_validator("source_news_ids", "source_names", "source_types", mode="after")
    @classmethod
    def deduplicate_sources(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        """Retain deterministic provenance without duplicate identities."""
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def require_method_for_each_component(self) -> Self:
        """Require one and only one audit method per scalar score component."""
        components = [detail.component for detail in self.scoring_methods]
        if len(components) != len(set(components)) or set(components) != set(EventScoreComponent):
            raise ValueError("scoring_methods must cover each EventScoreComponent once")
        return self


@dataclass(frozen=True, slots=True)
class _ComponentResult:
    value: float | None
    method: EventScoringMethod
    factors: tuple[EventScoreFactor, ...]
    explanation: str


class EventScoringService:
    """Score supplied revision evidence without persistence, SQL, or trading logic."""

    def __init__(
        self,
        classifier: EventClassifier,
        config: EventScoringConfig | None = None,
    ) -> None:
        """Configure the deterministic classifier and explicit engineering priors."""
        self._classifier = classifier
        self._config = EventScoringConfig() if config is None else config

    @property
    def config(self) -> EventScoringConfig:
        """Expose the immutable scoring policy used for reproducibility."""
        return self._config

    def score_revision(self, scoring_input: EventScoringInput) -> EventScore:
        """Return an auditable deterministic score for one exact revision."""
        revision = scoring_input.revision
        event = revision.event
        event_subtype = event.event_subtype
        if event_subtype is None:
            raise ValueError("scored canonical revisions require an event_subtype")

        sources = _independent_sources(scoring_input.raw_items, self._config)
        credibility = _score_credibility(sources)
        official_confirmation = _has_official_confirmation(
            event.event_state,
            event.event_type,
            scoring_input.raw_items,
        )
        latest_item = _latest_revision_item(revision, scoring_input.raw_items)
        classifier_confidence = self._classifier.classify(latest_item).confidence

        sentiment = _score_sentiment(scoring_input)
        surprise = _score_surprise(scoring_input)
        novelty = _score_novelty(scoring_input, official_confirmation, self._config)
        severity = _score_severity(scoring_input, self._config)
        credibility_result = _credibility_result(sources, credibility)
        confidence = _score_confidence(
            scoring_input,
            credibility,
            classifier_confidence,
            official_confirmation,
            len(sources),
            self._config,
        )
        uncertainty = _score_uncertainty(
            scoring_input,
            sources,
            official_confirmation,
            self._config,
        )
        results = {
            EventScoreComponent.SENTIMENT: sentiment,
            EventScoreComponent.SURPRISE: surprise,
            EventScoreComponent.NOVELTY: novelty,
            EventScoreComponent.SEVERITY: severity,
            EventScoreComponent.CREDIBILITY: credibility_result,
            EventScoreComponent.CONFIDENCE: confidence,
            EventScoreComponent.UNCERTAINTY: uncertainty,
        }
        return EventScore(
            event_id=event.event_id,
            revision_id=revision.revision_id,
            revision_number=revision.revision_number,
            available_at=revision.available_at,
            sentiment=_required_value(sentiment),
            surprise=surprise.value,
            novelty=_required_value(novelty),
            severity=_required_value(severity),
            credibility=_required_value(credibility_result),
            confidence=_required_value(confidence),
            uncertainty=_required_value(uncertainty),
            decay_tau_seconds=self._config.tau_seconds(event.event_type, event_subtype),
            source_news_ids=event.source_news_ids,
            source_names=tuple(source.name for source in sources),
            source_types=tuple(dict.fromkeys(source.source_type for source in sources)),
            scoring_methods=tuple(
                EventScoreMethodDetail(
                    component=component,
                    method=result.method,
                    explanation=result.explanation,
                )
                for component, result in results.items()
            ),
            contributing_factors=tuple(
                factor for result in results.values() for factor in result.factors
            ),
            explanations=tuple(result.explanation for result in results.values()),
            event_scoring_version=self._config.event_scoring_version,
            reference_data_policy=self._config.reference_data_policy,
        )


@dataclass(frozen=True, slots=True)
class _IndependentSource:
    name: str
    source_type: NewsSourceType
    credibility: float


def _independent_sources(
    raw_items: tuple[RawNewsItem, ...],
    config: EventScoringConfig,
) -> tuple[_IndependentSource, ...]:
    by_name: dict[str, _IndependentSource] = {}
    for item in sorted(raw_items, key=lambda raw: (raw.process_time, str(raw.news_id))):
        candidate = _IndependentSource(
            name=item.source,
            source_type=item.source_type,
            credibility=config.source_credibility(item.source_type),
        )
        key = item.source.casefold()
        existing = by_name.get(key)
        if existing is None or candidate.credibility > existing.credibility:
            by_name[key] = candidate
    return tuple(by_name.values())


def _score_credibility(sources: tuple[_IndependentSource, ...]) -> float:
    return bounded_union(tuple(source.credibility for source in sources))


def _credibility_result(
    sources: tuple[_IndependentSource, ...],
    credibility: float,
) -> _ComponentResult:
    factors = tuple(
        EventScoreFactor(
            component=EventScoreComponent.CREDIBILITY,
            name=f"source:{source.name}",
            value=source.credibility,
            rule_reference=f"source-prior:{source.source_type.value}",
            explanation="Configured provenance prior for one independent source name.",
        )
        for source in sources
    )
    return _ComponentResult(
        credibility,
        EventScoringMethod.SOURCE_PRIOR_CORROBORATION,
        factors,
        "Combined independent source priors as 1 - product(1 - prior_source).",
    )


def _score_sentiment(scoring_input: EventScoringInput) -> _ComponentResult:
    event = scoring_input.revision.event
    if event.sentiment is not None:
        value = event.sentiment
        method = EventScoringMethod.EXPLICIT_CANONICAL_VALUE
        rule = "canonical-event:sentiment"
        explanation = "Used the explicit finite canonical sentiment supplied by structured data."
    else:
        event_subtype = event.event_subtype
        if event_subtype is None:
            raise ValueError("event_subtype is required")
        value = deterministic_event_direction(
            event.event_type,
            event_subtype,
            event.headline_summary,
            event.event_summary,
        )
        method = EventScoringMethod.EXPLICIT_EVENT_SEMANTICS
        rule = f"event-semantics:{event_subtype.value}"
        explanation = (
            "Applied only explicit subtype/outcome semantics; unknown direction remains zero."
        )
    return _ComponentResult(
        value,
        method,
        (
            EventScoreFactor(
                component=EventScoreComponent.SENTIMENT,
                name="deterministic_sentiment",
                value=value,
                rule_reference=rule,
                explanation=explanation,
            ),
        ),
        explanation,
    )


def _score_surprise(scoring_input: EventScoringInput) -> _ComponentResult:
    surprise = scoring_input.revision.event.surprise
    if surprise is None:
        return _ComponentResult(
            None,
            EventScoringMethod.UNAVAILABLE,
            (),
            "No explicit structured surprise was available; no expectation was invented.",
        )
    explanation = "Used the explicit finite canonical surprise without fabricating a consensus."
    return _ComponentResult(
        surprise,
        EventScoringMethod.EXPLICIT_CANONICAL_VALUE,
        (
            EventScoreFactor(
                component=EventScoreComponent.SURPRISE,
                name="explicit_surprise",
                value=surprise,
                rule_reference="canonical-event:surprise",
                explanation=explanation,
            ),
        ),
        explanation,
    )


def _score_novelty(
    scoring_input: EventScoringInput,
    official_confirmation: bool,
    config: EventScoringConfig,
) -> _ComponentResult:
    previous = scoring_input.previous_revision
    if previous is None:
        value = config.first_revision_novelty
        return _ComponentResult(
            value,
            EventScoringMethod.FIRST_REVISION_PRIOR,
            (
                EventScoreFactor(
                    component=EventScoreComponent.NOVELTY,
                    name="first_revision_prior",
                    value=value,
                    rule_reference="novelty:first-revision",
                    explanation="A newly observed canonical event begins at the configured prior.",
                ),
            ),
            "Applied the configured high first-revision novelty prior.",
        )

    current_tokens = set(scoring_input.revision.headline_tokens)
    previous_tokens = set(previous.headline_tokens)
    token_union = current_tokens | previous_tokens
    token_delta = (
        0.0 if not token_union else 1.0 - len(current_tokens & previous_tokens) / len(token_union)
    )
    current_sources = {item.source.casefold() for item in scoring_input.raw_items}
    previous_sources = {name.casefold() for name in previous.source_names}
    new_source_fraction = len(current_sources - previous_sources) / max(len(current_sources), 1)
    current_entities = {link.entity_id for link in scoring_input.entity_links}
    previous_entities = {link.entity_id for link in scoring_input.previous_entity_links}
    new_entity_fraction = len(current_entities - previous_entities) / max(len(current_entities), 1)
    current_instruments = {record.exposure.instrument_id for record in scoring_input.exposures}
    previous_instruments = {
        record.exposure.instrument_id for record in scoring_input.previous_exposures
    }
    new_instrument_fraction = len(current_instruments - previous_instruments) / max(
        len(current_instruments), 1
    )
    previous_official = (
        _has_official_source(
            previous.event.event_type,
            previous.source_types,
        )
        and previous.event.event_state is EventState.CONFIRMED
    )
    official_confirmation_delta = float(official_confirmation and not previous_official)
    weights = config.novelty_weights
    named = (
        ("headline_token_change", token_delta, weights.headline_token_change),
        ("independent_source_addition", new_source_fraction, weights.independent_source_addition),
        ("new_entity", new_entity_fraction, weights.new_entity),
        ("new_instrument", new_instrument_fraction, weights.new_instrument),
        ("official_confirmation", official_confirmation_delta, weights.official_confirmation),
    )
    value = sum(factor * weight for _, factor, weight in named)
    factors = tuple(
        EventScoreFactor(
            component=EventScoreComponent.NOVELTY,
            name=name,
            value=factor,
            weight=weight,
            rule_reference=f"novelty-weight:{name}",
            explanation="Deterministic current-versus-previous revision delta factor.",
        )
        for name, factor, weight in named
    )
    return _ComponentResult(
        value,
        EventScoringMethod.REVISION_DELTA,
        factors,
        "Computed a weighted deterministic delta from the immediately preceding revision.",
    )


def _score_severity(
    scoring_input: EventScoringInput,
    config: EventScoringConfig,
) -> _ComponentResult:
    event = scoring_input.revision.event
    event_subtype = event.event_subtype
    if event_subtype is None:
        raise ValueError("event_subtype is required")
    if event.severity is not None:
        value = event.severity
        method = EventScoringMethod.EXPLICIT_CANONICAL_VALUE
        rule = "canonical-event:severity"
        explanation = "Used an explicit structured canonical severity value."
    else:
        value = config.severity(event.event_type, event_subtype)
        method = EventScoringMethod.EVENT_SEVERITY_PRIOR
        rule = f"severity-prior:{event_subtype.value}"
        explanation = "Applied the configured uncalibrated event-class severity prior."
    return _ComponentResult(
        value,
        method,
        (
            EventScoreFactor(
                component=EventScoreComponent.SEVERITY,
                name="severity",
                value=value,
                rule_reference=rule,
                explanation=explanation,
            ),
        ),
        explanation,
    )


def _score_confidence(
    scoring_input: EventScoringInput,
    credibility: float,
    classifier_confidence: float,
    official_confirmation: bool,
    independent_source_count: int,
    config: EventScoringConfig,
) -> _ComponentResult:
    corroboration = min(max(independent_source_count - 1, 0) / 2.0, 1.0)
    entity_confidence = _mean(tuple(link.confidence for link in scoring_input.entity_links))
    exposure_confidence = _mean(
        tuple(record.exposure.confidence for record in scoring_input.exposures)
    )
    weights = config.confidence_weights
    named = (
        ("source_credibility", credibility, weights.source_credibility),
        ("corroboration", corroboration, weights.corroboration),
        ("classifier", classifier_confidence, weights.classifier),
        ("entity_linking", entity_confidence, weights.entity_linking),
        ("exposure_mapping", exposure_confidence, weights.exposure_mapping),
        ("official_confirmation", float(official_confirmation), weights.official_confirmation),
    )
    value = sum(factor * weight for _, factor, weight in named)
    factors = tuple(
        EventScoreFactor(
            component=EventScoreComponent.CONFIDENCE,
            name=name,
            value=factor,
            weight=weight,
            rule_reference=f"confidence-weight:{name}",
            explanation="Bounded evidence for confidence in structured interpretation.",
        )
        for name, factor, weight in named
    )
    return _ComponentResult(
        value,
        EventScoringMethod.STRUCTURED_INTERPRETATION_CONFIDENCE,
        factors,
        "Computed a convex weighted sum; this is not probability of a price direction.",
    )


def _score_uncertainty(
    scoring_input: EventScoringInput,
    sources: tuple[_IndependentSource, ...],
    official_confirmation: bool,
    config: EventScoringConfig,
) -> _ComponentResult:
    credibilities = tuple(source.credibility for source in sources)
    dispersion = max(credibilities) - min(credibilities) if credibilities else 0.0
    speculative_only = float(
        bool(sources)
        and all(source.source_type is NewsSourceType.SPECULATIVE for source in sources)
    )
    provider_evidence = {
        (kind, value.casefold())
        for item in scoring_input.raw_items
        for kind, values in (
            ("ticker", item.provider_tickers),
            ("entity", item.provider_entities),
        )
        for value in values
    }
    provider_evidence_count = len(provider_evidence)
    provider_link_count = sum(
        link.match_method in {EntityMatchMethod.PROVIDER_TICKER, EntityMatchMethod.PROVIDER_ENTITY}
        for link in scoring_input.entity_links
    )
    unresolved_reference = (
        0.0
        if provider_evidence_count == 0
        else 1.0 - min(provider_link_count / provider_evidence_count, 1.0)
    )
    entity_deficit = (
        1.0 - _mean(tuple(link.confidence for link in scoring_input.entity_links))
        if scoring_input.entity_links
        else float(provider_evidence_count > 0)
    )
    direction_conflict = float(
        any(record.direction_conflict for record in scoring_input.exposures)
        or _has_opposing_directions(scoring_input.exposures)
    )
    weights = config.uncertainty_weights
    named = (
        (
            "event_state",
            config.state_uncertainty(scoring_input.revision.event.event_state),
            weights.event_state,
        ),
        ("source_credibility_dispersion", dispersion, weights.source_credibility_dispersion),
        ("speculative_only", speculative_only, weights.speculative_only),
        (
            "lack_official_confirmation",
            float(not official_confirmation),
            weights.lack_official_confirmation,
        ),
        (
            "unresolved_reference_evidence",
            unresolved_reference,
            weights.unresolved_reference_evidence,
        ),
        (
            "entity_link_confidence_deficit",
            entity_deficit,
            weights.entity_link_confidence_deficit,
        ),
        ("exposure_direction_conflict", direction_conflict, weights.exposure_direction_conflict),
    )
    contributions = tuple(factor * weight for _, factor, weight in named)
    factors = tuple(
        EventScoreFactor(
            component=EventScoreComponent.UNCERTAINTY,
            name=name,
            value=factor,
            weight=weight,
            rule_reference=f"uncertainty-weight:{name}",
            explanation="Independent bounded uncertainty cause used in a probabilistic union.",
        )
        for name, factor, weight in named
    )
    return _ComponentResult(
        bounded_union(contributions),
        EventScoringMethod.BOUNDED_UNCERTAINTY_FACTORS,
        factors,
        (
            "Combined weighted causes as 1 - product(1 - weight * factor), "
            "independently of confidence."
        ),
    )


def _latest_revision_item(
    revision: CanonicalEventRevision,
    raw_items: tuple[RawNewsItem, ...],
) -> RawNewsItem:
    by_id = {item.news_id: item for item in raw_items}
    return by_id[revision.event.source_news_ids[-1]]


def _has_official_confirmation(
    event_state: EventState,
    event_type: EventType,
    raw_items: tuple[RawNewsItem, ...],
) -> bool:
    return event_state is EventState.CONFIRMED and _has_official_source(
        event_type,
        tuple(item.source_type for item in raw_items),
    )


def _has_official_source(
    event_type: EventType,
    source_types: tuple[NewsSourceType, ...],
) -> bool:
    for source_type in source_types:
        if source_type is NewsSourceType.COMPANY_RELEASE and event_type is EventType.COMPANY:
            return True
        if source_type is NewsSourceType.SEC and event_type in {
            EventType.COMPANY,
            EventType.REGULATORY,
        }:
            return True
        if source_type is NewsSourceType.GOVERNMENT and event_type in {
            EventType.GEOPOLITICAL,
            EventType.MACRO,
            EventType.REGULATORY,
            EventType.SYSTEMIC,
        }:
            return True
        if source_type is NewsSourceType.CENTRAL_BANK and event_type in {
            EventType.MACRO,
            EventType.RATE,
        }:
            return True
    return False


def _has_opposing_directions(exposures: tuple[RevisionEventExposure, ...]) -> bool:
    signs = {
        1 if record.exposure.direction > 0.0 else -1
        for record in exposures
        if record.exposure.direction != 0.0
    }
    return len(signs) > 1


def _mean(values: tuple[float, ...]) -> float:
    return 0.0 if not values else sum(values) / len(values)


def _required_value(result: _ComponentResult) -> float:
    if result.value is None:
        raise ValueError("required event-score component is unavailable")
    return result.value
