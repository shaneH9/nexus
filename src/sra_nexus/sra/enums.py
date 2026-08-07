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


class StructuralBreakKind(StrEnum):
    """Known market-data condition that invalidates a shock comparison span."""

    RESET = "RESET"
    SEQUENCE_CORRUPTION = "SEQUENCE_CORRUPTION"
    DATA_GAP = "DATA_GAP"


class ShockPairIncomparabilityReason(StrEnum):
    """Ordinary research reason an ordered shock pair cannot be compared."""

    SAME_SHOCK = "SAME_SHOCK"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    DIRECTION_MISMATCH = "DIRECTION_MISMATCH"
    SHOCK_ORDER_INVALID = "SHOCK_ORDER_INVALID"
    EVENT_DISTANCE_EXCEEDED = "EVENT_DISTANCE_EXCEEDED"
    EXCHANGE_DISTANCE_EXCEEDED = "EXCHANGE_DISTANCE_EXCEEDED"
    STRUCTURAL_BREAK = "STRUCTURAL_BREAK"
    AGGRESSION_RATIO_OUTSIDE_BOUNDS = "AGGRESSION_RATIO_OUTSIDE_BOUNDS"
    REQUIRED_IMPACT_UNAVAILABLE_SHOCK_1 = "REQUIRED_IMPACT_UNAVAILABLE_SHOCK_1"
    REQUIRED_IMPACT_UNAVAILABLE_SHOCK_2 = "REQUIRED_IMPACT_UNAVAILABLE_SHOCK_2"
    IMPACT_VERSION_MISMATCH = "IMPACT_VERSION_MISMATCH"
    REQUIRED_RESILIENCY_UNAVAILABLE_SHOCK_1 = "REQUIRED_RESILIENCY_UNAVAILABLE_SHOCK_1"
    REQUIRED_RESILIENCY_UNAVAILABLE_SHOCK_2 = "REQUIRED_RESILIENCY_UNAVAILABLE_SHOCK_2"
    RESILIENCY_VERSION_MISMATCH = "RESILIENCY_VERSION_MISMATCH"
    RESILIENCY_DEPTH_POLICY_MISMATCH = "RESILIENCY_DEPTH_POLICY_MISMATCH"
    REQUIRED_RECOVERY_THRESHOLD_MISSING_SHOCK_1 = "REQUIRED_RECOVERY_THRESHOLD_MISSING_SHOCK_1"
    REQUIRED_RECOVERY_THRESHOLD_MISSING_SHOCK_2 = "REQUIRED_RECOVERY_THRESHOLD_MISSING_SHOCK_2"


class EffectivenessInterpretation(StrEnum):
    """Interpretation of the second shock's aggressor-effectiveness change."""

    WEAKENING = "WEAKENING"
    STABLE = "STABLE"
    STRENGTHENING = "STRENGTHENING"
    UNAVAILABLE = "UNAVAILABLE"


class RecoveryTimeInterpretation(StrEnum):
    """Interpretation of the second shock's recovery-time change."""

    FASTER = "FASTER"
    STABLE = "STABLE"
    SLOWER = "SLOWER"
    UNAVAILABLE = "UNAVAILABLE"


class RecoveryComparisonUnavailableReason(StrEnum):
    """Reason a recovery-time delta cannot be calculated."""

    SHOCK_1_UNREACHED = "SHOCK_1_UNREACHED"
    SHOCK_2_UNREACHED = "SHOCK_2_UNREACHED"
    BOTH_UNREACHED = "BOTH_UNREACHED"


class OrderLifecycleTerminalReason(StrEnum):
    """Observable reason one tracked MBO lifecycle ended or was censored."""

    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"
    RESET = "RESET"
    OBSERVATION_END = "OBSERVATION_END"


class LiquidityCredibilityUnavailableReason(StrEnum):
    """Explicit reason shock-region credibility cannot be calculated."""

    MBO_REQUIRED = "MBO_REQUIRED"
    EVENT_INDEX_UNAVAILABLE = "EVENT_INDEX_UNAVAILABLE"
    INSUFFICIENT_OBSERVATION_HORIZON = "INSUFFICIENT_OBSERVATION_HORIZON"
    RESET_IN_OBSERVATION_WINDOW = "RESET_IN_OBSERVATION_WINDOW"
    NO_ATTACKED_ORDERS = "NO_ATTACKED_ORDERS"
    SHOCK_QUANTITY_ATTRIBUTION_UNAVAILABLE = "SHOCK_QUANTITY_ATTRIBUTION_UNAVAILABLE"


class FlowDirection(StrEnum):
    """Direction of an exact signed-flow aggregate without implying a trade."""

    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


class ToxicityUnavailableReason(StrEnum):
    """Explicit reason a post-shock toxicity vector cannot be constructed."""

    INSUFFICIENT_FLOW_WINDOW = "INSUFFICIENT_FLOW_WINDOW"
    INSUFFICIENT_SHOCK_HISTORY = "INSUFFICIENT_SHOCK_HISTORY"
    MISSING_IMPACT = "MISSING_IMPACT"
    MISSING_RESILIENCY = "MISSING_RESILIENCY"
    MISSING_BOOK_STATE = "MISSING_BOOK_STATE"
    STRUCTURAL_BREAK = "STRUCTURAL_BREAK"
    MISSING_EVENT_INDEX = "MISSING_EVENT_INDEX"
    MISSING_SPREAD = "MISSING_SPREAD"
    MISSING_VOLATILITY_WINDOW = "MISSING_VOLATILITY_WINDOW"
