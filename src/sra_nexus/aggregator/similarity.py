"""Transparent deterministic similarity scoring and conservative hard guards."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from math import isclose, isfinite

from sra_nexus.aggregator.anchors import EventAnchors
from sra_nexus.aggregator.classification import EventClassification
from sra_nexus.aggregator.enums import EventType
from sra_nexus.aggregator.normalization import comparison_tokens
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.common.models import ContractModel, NonBlankStr, UnitIntervalScore


@dataclass(frozen=True, slots=True)
class SimilarityWeights:
    """Initial engineering weights; not statistically calibrated parameters."""

    headline: float = 0.55
    anchor: float = 0.25
    temporal: float = 0.10
    event_type: float = 0.10

    def __post_init__(self) -> None:
        """Require finite non-negative weights summing to one."""
        values = (self.headline, self.anchor, self.temporal, self.event_type)
        if any(not isfinite(value) or value < 0.0 for value in values):
            raise ValueError("similarity weights must be finite and non-negative")
        if not isclose(sum(values), 1.0, abs_tol=1e-12):
            raise ValueError("similarity weights must sum to 1")


@dataclass(frozen=True, slots=True)
class ClusteringConfig:
    """Central initial engineering configuration for deterministic clustering."""

    maximum_candidate_age: timedelta = timedelta(hours=36)
    clustering_threshold: float = 0.55
    ambiguity_margin: float = 0.05
    weights: SimilarityWeights = field(default_factory=SimilarityWeights)

    def __post_init__(self) -> None:
        """Reject invalid horizons, thresholds, and ambiguity margins."""
        if self.maximum_candidate_age <= timedelta(0):
            raise ValueError("maximum_candidate_age must be positive")
        for name, value in (
            ("clustering_threshold", self.clustering_threshold),
            ("ambiguity_margin", self.ambiguity_margin),
        ):
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1]")


class EventSimilarity(ContractModel):
    """Inspectible mathematical components and guards for one event candidate."""

    total_score: UnitIntervalScore
    headline_similarity: UnitIntervalScore
    anchor_similarity: UnitIntervalScore
    temporal_score: UnitIntervalScore
    type_score: UnitIntervalScore
    type_compatible: bool
    subtype_compatible: bool
    guard_failures: tuple[NonBlankStr, ...] = ()
    explanation: NonBlankStr


def jaccard_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Return set Jaccard similarity, defining two empty sets as zero evidence."""
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def temporal_proximity_score(age: timedelta, maximum_age: timedelta) -> float:
    """Return linear unitless proximity from 1 now to 0 at the maximum age."""
    if maximum_age <= timedelta(0):
        raise ValueError("maximum_age must be positive")
    elapsed = abs(age.total_seconds())
    return max(0.0, 1.0 - (elapsed / maximum_age.total_seconds()))


def score_event_similarity(
    item: RawNewsItem,
    classification: EventClassification,
    item_anchors: EventAnchors,
    candidate: CanonicalEventRevision,
    config: ClusteringConfig,
) -> EventSimilarity:
    """Calculate weighted similarity after evaluating conservative hard guards."""
    candidate_subtype = candidate.event.event_subtype
    type_compatible = classification.event_type is candidate.event.event_type
    subtype_compatible = classification.event_subtype is candidate_subtype
    age = item.process_time - candidate.available_at
    headline_score = jaccard_similarity(
        comparison_tokens(item.headline),
        frozenset(candidate.headline_tokens),
    )
    candidate_anchors = frozenset(candidate.anchors)
    anchor_score = jaccard_similarity(item_anchors.all, candidate_anchors)
    temporal_score = temporal_proximity_score(age, config.maximum_candidate_age)
    type_score = 1.0 if type_compatible and subtype_compatible else 0.0

    guards = _hard_guard_failures(
        classification=classification,
        item_anchors=item_anchors,
        candidate=candidate,
        age=age,
        config=config,
    )
    weighted_score = (
        config.weights.headline * headline_score
        + config.weights.anchor * anchor_score
        + config.weights.temporal * temporal_score
        + config.weights.event_type * type_score
    )
    total_score = 0.0 if guards else weighted_score
    explanation = (
        "Candidate rejected by hard guards: " + ", ".join(guards)
        if guards
        else "Candidate passed hard guards; total is the configured weighted component sum."
    )
    return EventSimilarity(
        total_score=total_score,
        headline_similarity=headline_score,
        anchor_similarity=anchor_score,
        temporal_score=temporal_score,
        type_score=type_score,
        type_compatible=type_compatible,
        subtype_compatible=subtype_compatible,
        guard_failures=guards,
        explanation=explanation,
    )


def _hard_guard_failures(
    *,
    classification: EventClassification,
    item_anchors: EventAnchors,
    candidate: CanonicalEventRevision,
    age: timedelta,
    config: ClusteringConfig,
) -> tuple[str, ...]:
    failures: list[str] = []
    if classification.event_type is not candidate.event.event_type:
        failures.append("incompatible_event_type")
    if classification.event_subtype is not candidate.event.event_subtype:
        failures.append("incompatible_event_subtype")
    if abs(age) > config.maximum_candidate_age:
        failures.append("outside_candidate_horizon")

    candidate_tickers = frozenset(candidate.ticker_anchors)
    if (
        item_anchors.tickers
        and candidate_tickers
        and not (item_anchors.tickers & candidate_tickers)
    ):
        failures.append("conflicting_ticker_anchors")

    if classification.event_type is EventType.COMPANY and not (
        item_anchors.all & frozenset(candidate.anchors)
    ):
        failures.append("missing_shared_company_anchor")
    return tuple(failures)
