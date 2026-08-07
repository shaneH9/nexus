"""Stable categorical values for historical SRA research evaluation."""

from enum import StrEnum


class LabelUnavailableReason(StrEnum):
    """Reasons a complete forward event-horizon label cannot be produced."""

    MISSING_FUTURE_EVENT = "MISSING_FUTURE_EVENT"
    MISSING_MIDPRICE = "MISSING_MIDPRICE"
    STRUCTURAL_GAP = "STRUCTURAL_GAP"


class WalkForwardMode(StrEnum):
    """Supported chronological training-window policies."""

    EXPANDING = "EXPANDING"
    ROLLING = "ROLLING"


class PermutationAlternative(StrEnum):
    """Tail alternatives supported by empirical permutation p-values."""

    GREATER = "GREATER"
    LESS = "LESS"
    TWO_SIDED = "TWO_SIDED"


class PermutationMode(StrEnum):
    """Whether all block arrangements or a seeded sample are evaluated."""

    EXACT = "EXACT"
    MONTE_CARLO = "MONTE_CARLO"


class PermutationBlockUnit(StrEnum):
    """Units in which a chronological permutation block may be defined."""

    NORMALIZED_EVENT_COUNT = "NORMALIZED_EVENT_COUNT"
    EXCHANGE_TIME = "EXCHANGE_TIME"
    SESSION = "SESSION"


class PercentileMethod(StrEnum):
    """Explicit standard-library percentile convention for null summaries."""

    NEAREST_RANK = "NEAREST_RANK"
