"""Small pure formulas for deterministic event scoring and NewsState aggregation."""

from __future__ import annotations

from datetime import datetime
from math import exp, isfinite, prod

from sra_nexus.common.models import normalize_utc_datetime


def calculate_event_decay(
    available_at: datetime,
    as_of: datetime,
    tau_seconds: float,
) -> float:
    """Return ``exp(-(as_of - available_at) / tau_seconds)``.

    All time values are UTC-normalized and the elapsed duration and ``tau`` use
    seconds. The result is dimensionless in ``(0, 1]``. Scoring before event
    availability is invalid rather than being silently clamped.
    """
    available = normalize_utc_datetime(available_at)
    cutoff = normalize_utc_datetime(as_of)
    if not isfinite(tau_seconds) or tau_seconds <= 0.0:
        raise ValueError("tau_seconds must be finite and greater than zero")
    elapsed_seconds = (cutoff - available).total_seconds()
    if elapsed_seconds < 0.0:
        raise ValueError("as_of must not precede available_at")
    return exp(-elapsed_seconds / tau_seconds)


def bounded_union(values: tuple[float, ...]) -> float:
    """Return ``1 - product(1 - value)`` for dimensionless values in ``[0, 1]``.

    The empty union is zero. This formula is used when multiple independent
    bounded contributions may each establish risk, novelty, or uncertainty.
    """
    _require_unit_interval(values, "bounded union values")
    return 1.0 - prod(1.0 - value for value in values)


def calculate_event_intensity(
    magnitude: float,
    relevance: float,
    severity: float,
    novelty: float,
    credibility: float,
    confidence: float,
    decay: float,
) -> float:
    """Return dimensionless event intensity as the product of its seven factors."""
    values = (magnitude, relevance, severity, novelty, credibility, confidence, decay)
    _require_unit_interval(values, "event-intensity factors")
    return prod(values)


def calculate_directional_intensity(intensity: float, direction: float) -> float:
    """Return event intensity multiplied by dimensionless exposure direction."""
    if not isfinite(intensity) or intensity < 0.0:
        raise ValueError("intensity must be finite and non-negative")
    if not isfinite(direction) or direction < -1.0 or direction > 1.0:
        raise ValueError("direction must be finite and in [-1, 1]")
    return intensity * direction


def calculate_event_risk_contribution(
    magnitude: float,
    relevance: float,
    severity: float,
    uncertainty: float,
    credibility: float,
    confidence: float,
    decay: float,
) -> float:
    """Return one bounded, direction-independent event-risk contribution.

    Exact formula::

        magnitude * relevance * severity * decay
        * (0.5 + 0.25 * credibility + 0.25 * confidence)
        * (0.5 + 0.5 * uncertainty)

    The two affine terms retain some risk for uncertain or weakly corroborated
    reports without treating low credibility as evidence of safety.
    """
    values = (magnitude, relevance, severity, uncertainty, credibility, confidence, decay)
    _require_unit_interval(values, "event-risk factors")
    evidence_factor = 0.5 + 0.25 * credibility + 0.25 * confidence
    uncertainty_factor = 0.5 + 0.5 * uncertainty
    return magnitude * relevance * severity * decay * evidence_factor * uncertainty_factor


def calculate_novelty_contribution(
    novelty: float,
    magnitude: float,
    relevance: float,
    decay: float,
) -> float:
    """Return bounded instrument novelty contribution ``N * M * Rel * Decay``."""
    values = (novelty, magnitude, relevance, decay)
    _require_unit_interval(values, "novelty-intensity factors")
    return prod(values)


def calculate_news_acceleration(
    recent_count: int,
    recent_window_seconds: int,
    prior_count: int,
    prior_window_seconds: int,
    *,
    rate_unit_seconds: int = 3600,
) -> float:
    """Return recent minus prior unique-news rate in items per rate unit.

    With the default ``rate_unit_seconds=3600``, the exact units are items per
    hour. Counts are divided by their own configured windows before subtraction.
    """
    if recent_count < 0 or prior_count < 0:
        raise ValueError("news counts must be non-negative")
    if recent_window_seconds <= 0 or prior_window_seconds <= 0 or rate_unit_seconds <= 0:
        raise ValueError("news-rate window lengths must be positive")
    recent_rate = recent_count * rate_unit_seconds / recent_window_seconds
    prior_rate = prior_count * rate_unit_seconds / prior_window_seconds
    return recent_rate - prior_rate


def calculate_weighted_confidence(
    confidence_and_weight: tuple[tuple[float, float], ...],
) -> float:
    """Return a non-negative-weighted mean, or zero for no effective evidence."""
    for confidence, weight in confidence_and_weight:
        _require_unit_interval((confidence,), "confidence")
        if not isfinite(weight) or weight < 0.0:
            raise ValueError("confidence weights must be finite and non-negative")
    total_weight = sum(weight for _, weight in confidence_and_weight)
    if total_weight == 0.0:
        return 0.0
    return sum(confidence * weight for confidence, weight in confidence_and_weight) / total_weight


def _require_unit_interval(values: tuple[float, ...], description: str) -> None:
    if any(not isfinite(value) or value < 0.0 or value > 1.0 for value in values):
        raise ValueError(f"{description} must be finite and in [0, 1]")
