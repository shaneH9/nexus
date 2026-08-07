"""Stable categorical values for SRA shock and resiliency research."""

from enum import StrEnum


class ShockDirection(StrEnum):
    """Observed direction of aggressive liquidity-taking flow."""

    BUY = "BUY"
    SELL = "SELL"


class AggressionUnavailableReason(StrEnum):
    """Explicit reason normalized aggression cannot be calculated."""

    ZERO_OPPOSITE_DEPTH = "ZERO_OPPOSITE_DEPTH"


class ShockDetectionMethod(StrEnum):
    """Version-independent family of shock-candidate classification."""

    DETERMINISTIC_THRESHOLDS = "DETERMINISTIC_THRESHOLDS"


class ShockDetectionRule(StrEnum):
    """Explainable initial engineering rules used by the classifier."""

    NORMALIZED_AGGRESSION = "NORMALIZED_AGGRESSION"
    AGGRESSIVE_VOLUME = "AGGRESSIVE_VOLUME"
    LEVELS_CONSUMED = "LEVELS_CONSUMED"
    AVERAGE_AGGRESSIVE_TRADE_SIZE = "AVERAGE_AGGRESSIVE_TRADE_SIZE"


class ShockResearchStatus(StrEnum):
    """Outcome of one explicitly bounded directional episode analysis."""

    NO_DIRECTIONAL_AGGRESSION = "NO_DIRECTIONAL_AGGRESSION"
    BELOW_THRESHOLDS = "BELOW_THRESHOLDS"
    SHOCK_CANDIDATE = "SHOCK_CANDIDATE"


class ImpactUnavailableReason(StrEnum):
    """Explicit reason a requested event-horizon impact is unavailable."""

    BASELINE_MIDPRICE_UNAVAILABLE = "BASELINE_MIDPRICE_UNAVAILABLE"
    FUTURE_OBSERVATION_UNAVAILABLE = "FUTURE_OBSERVATION_UNAVAILABLE"
    FUTURE_MIDPRICE_UNAVAILABLE = "FUTURE_MIDPRICE_UNAVAILABLE"


class ResiliencyUnavailableReason(StrEnum):
    """Explicit reason a requested replenishment observation is unavailable."""

    NO_DEPLETION = "NO_DEPLETION"
    FUTURE_OBSERVATION_UNAVAILABLE = "FUTURE_OBSERVATION_UNAVAILABLE"
