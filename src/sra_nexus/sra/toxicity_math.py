"""Pure exact-arithmetic equations for descriptive market-side toxicity."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sra_nexus.sra.enums import FlowDirection, ShockDirection


def calculate_flow_persistence(
    signed_event_flows: Sequence[Decimal],
    epsilon: Decimal,
) -> Decimal:
    """Return ``abs(sum(OF)) / sum(abs(OF))`` with an explicit zero guard."""
    _require_positive_epsilon(epsilon)
    flows = tuple(signed_event_flows)
    _require_finite(flows, "signed event flows")
    absolute_flow = sum((abs(flow) for flow in flows), Decimal(0))
    if absolute_flow == 0:
        return Decimal(0)
    return abs(sum(flows, Decimal(0))) / absolute_flow


def classify_flow_direction(
    net_flow: Decimal,
    tolerance: Decimal,
) -> FlowDirection:
    """Classify signed flow with an inclusive configurable neutral band."""
    _require_finite((net_flow, tolerance), "flow direction inputs")
    if tolerance < 0:
        raise ValueError("flow direction tolerance must be non-negative")
    if net_flow > tolerance:
        return FlowDirection.BUY
    if net_flow < -tolerance:
        return FlowDirection.SELL
    return FlowDirection.NEUTRAL


def calculate_unknown_flow_share(
    buy_volume: Decimal,
    sell_volume: Decimal,
    unknown_volume: Decimal,
) -> Decimal:
    """Return UNKNOWN volume divided by all observed aggressive-trade volume."""
    _require_non_negative((buy_volume, sell_volume, unknown_volume), "flow volumes")
    total = buy_volume + sell_volume + unknown_volume
    return Decimal(0) if total == 0 else unknown_volume / total


def calculate_directional_flow_coverage(
    buy_volume: Decimal,
    sell_volume: Decimal,
    unknown_volume: Decimal,
) -> Decimal:
    """Return known BUY/SELL volume divided by all observed trade volume."""
    _require_non_negative((buy_volume, sell_volume, unknown_volume), "flow volumes")
    known = buy_volume + sell_volume
    total = known + unknown_volume
    return Decimal(0) if total == 0 else known / total


def calculate_shock_persistence(directions: Sequence[ShockDirection]) -> Decimal:
    """Return ``abs(sum(direction signs)) / N`` for a nonempty shock window."""
    observed = tuple(directions)
    if not observed:
        raise ValueError("shock persistence requires at least one shock")
    signed = sum((_shock_sign(direction) for direction in observed), Decimal(0))
    return abs(signed) / Decimal(len(observed))


def dominant_shock_direction(directions: Sequence[ShockDirection]) -> FlowDirection:
    """Return BUY, SELL, or NEUTRAL from the signed shock-window aggregate."""
    observed = tuple(directions)
    if not observed:
        raise ValueError("dominant shock direction requires at least one shock")
    signed = sum((_shock_sign(direction) for direction in observed), Decimal(0))
    return classify_flow_direction(signed, Decimal(0))


def calculate_same_direction_run_length(directions: Sequence[ShockDirection]) -> int:
    """Return the length of the most recent same-direction shock suffix."""
    observed = tuple(directions)
    if not observed:
        raise ValueError("shock run requires at least one shock")
    latest = observed[-1]
    count = 0
    for direction in reversed(observed):
        if direction is not latest:
            break
        count += 1
    return count


def calculate_directional_impact_change(
    previous_directional_impact: Decimal,
    current_directional_impact: Decimal,
) -> Decimal:
    """Return exact ``DeltaDI = DI_current - DI_previous`` in price units."""
    _require_finite(
        (previous_directional_impact, current_directional_impact),
        "directional impacts",
    )
    return current_directional_impact - previous_directional_impact


def calculate_impact_magnitude_ratio(
    previous_directional_impact: Decimal,
    current_directional_impact: Decimal,
    epsilon: Decimal,
) -> Decimal:
    """Return ``abs(DI_current) / abs(DI_previous)`` with zero-baseline epsilon."""
    _require_positive_epsilon(epsilon)
    _require_finite(
        (previous_directional_impact, current_directional_impact),
        "directional impacts",
    )
    baseline = abs(previous_directional_impact)
    denominator = epsilon if baseline == 0 else baseline
    return abs(current_directional_impact) / denominator


def calculate_impact_toxicity_component(
    previous_directional_impact: Decimal,
    current_directional_impact: Decimal,
    epsilon: Decimal,
) -> Decimal:
    """Bound positive current aggressor impact relative to prior impact magnitude.

    The component is zero when current directional impact is non-positive. For
    positive current impact it is ``DI_current / (abs(DI_previous)+DI_current)``.
    Epsilon is used only when that natural denominator is zero.
    """
    _require_positive_epsilon(epsilon)
    _require_finite(
        (previous_directional_impact, current_directional_impact),
        "directional impacts",
    )
    positive_current = max(current_directional_impact, Decimal(0))
    denominator = abs(previous_directional_impact) + positive_current
    if denominator == 0:
        denominator = epsilon
    return positive_current / denominator


def calculate_raw_replenishment_failure(replenishment_ratio: Decimal) -> Decimal:
    """Return raw unclamped ``RF = 1 - RR``."""
    _require_finite((replenishment_ratio,), "replenishment ratio")
    return Decimal(1) - replenishment_ratio


def calculate_bounded_replenishment_failure(replenishment_ratio: Decimal) -> Decimal:
    """Return ``1 - min(max(RR, 0), 1)`` in the closed interval [0, 1]."""
    _require_finite((replenishment_ratio,), "replenishment ratio")
    bounded_recovery = min(max(replenishment_ratio, Decimal(0)), Decimal(1))
    return Decimal(1) - bounded_recovery


def calculate_credibility_interactions(
    replenishment_ratio: Decimal,
    liquidity_credibility: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return ``RR*LC`` and ``RR*(1-LC)`` without changing RR or LC."""
    _require_finite(
        (replenishment_ratio, liquidity_credibility),
        "credibility interaction inputs",
    )
    if not Decimal(0) <= liquidity_credibility <= Decimal(1):
        raise ValueError("liquidity credibility must be in [0, 1]")
    return (
        replenishment_ratio * liquidity_credibility,
        replenishment_ratio * (Decimal(1) - liquidity_credibility),
    )


def calculate_net_liquidity_provision(
    added_quantity: Decimal,
    withdrawn_quantity: Decimal,
) -> Decimal:
    """Return exact ``NLP = added quantity - withdrawn quantity``."""
    _require_non_negative(
        (added_quantity, withdrawn_quantity),
        "liquidity provision quantities",
    )
    return added_quantity - withdrawn_quantity


def calculate_normalized_net_liquidity_provision(
    added_quantity: Decimal,
    withdrawn_quantity: Decimal,
    epsilon: Decimal,
) -> Decimal:
    """Return exact NLP divided by gross additions plus withdrawals."""
    _require_positive_epsilon(epsilon)
    net = calculate_net_liquidity_provision(added_quantity, withdrawn_quantity)
    gross = added_quantity + withdrawn_quantity
    return Decimal(0) if gross == 0 else net / gross


def calculate_withdrawal_pressure(
    added_quantity: Decimal,
    withdrawn_quantity: Decimal,
    epsilon: Decimal,
) -> Decimal:
    """Return withdrawals divided by additions plus withdrawals."""
    _require_positive_epsilon(epsilon)
    _require_non_negative(
        (added_quantity, withdrawn_quantity),
        "withdrawal-pressure quantities",
    )
    gross = added_quantity + withdrawn_quantity
    return Decimal(0) if gross == 0 else withdrawn_quantity / gross


def calculate_spread_expansion_ratio(
    baseline_spread: Decimal,
    post_shock_spread: Decimal,
    epsilon: Decimal,
) -> Decimal:
    """Return post-shock spread divided by baseline with zero-baseline epsilon."""
    _require_positive_epsilon(epsilon)
    _require_non_negative(
        (baseline_spread, post_shock_spread),
        "spread values",
    )
    denominator = epsilon if baseline_spread == 0 else baseline_spread
    return post_shock_spread / denominator


def calculate_decimal_median(values: Sequence[Decimal]) -> Decimal:
    """Return the deterministic exact median of a nonempty finite sequence."""
    observed = tuple(values)
    if not observed:
        raise ValueError("median requires at least one value")
    _require_finite(observed, "median values")
    ordered = tuple(sorted(observed))
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def calculate_event_time_realized_volatility(midprices: Sequence[Decimal]) -> Decimal:
    """Return RMS arithmetic return across consecutive positive midprices."""
    prices = tuple(midprices)
    if len(prices) < 2:
        raise ValueError("event-time realized volatility requires at least two midprices")
    _require_finite(prices, "midprices")
    if any(price <= 0 for price in prices):
        raise ValueError("midprices must be positive")
    returns = tuple(
        (current - previous) / previous
        for previous, current in zip(prices, prices[1:], strict=False)
    )
    mean_squared = sum((value * value for value in returns), Decimal(0)) / Decimal(len(returns))
    return mean_squared.sqrt()


def calculate_volatility_jump_ratio(
    pre_shock_rv: Decimal,
    post_shock_rv: Decimal,
    epsilon: Decimal,
) -> Decimal:
    """Return post/pre event-time RV with explicit zero-baseline epsilon."""
    _require_positive_epsilon(epsilon)
    _require_non_negative((pre_shock_rv, post_shock_rv), "realized volatility")
    denominator = epsilon if pre_shock_rv == 0 else pre_shock_rv
    return post_shock_rv / denominator


def calculate_bounded_positive_ratio(value: Decimal) -> Decimal:
    """Bound absolute non-negative magnitude with ``value / (1 + value)``.

    This transform has a neutral baseline of zero. It must not be used for an
    expansion or jump ratio whose neutral baseline is one.
    """
    _require_non_negative((value,), "positive ratio")
    return value / (Decimal(1) + value)


def calculate_bounded_excess_ratio(ratio: Decimal) -> Decimal:
    """Bound only the non-negative excess above a neutral ratio of one.

    The exact transform is ``max(ratio - 1, 0) / (1 + max(ratio - 1, 0))``.
    A ratio at or below one contributes no increase; a finite ratio above one
    produces a result in ``[0, 1)`` without changing the raw ratio.
    """
    _require_non_negative((ratio,), "excess ratio")
    excess = max(ratio - Decimal(1), Decimal(0))
    return excess / (Decimal(1) + excess)


def calculate_composite_toxicity(
    components: Sequence[Decimal],
    weights: Sequence[Decimal],
) -> Decimal:
    """Return an exact convex combination of bounded toxicity components."""
    values = tuple(components)
    coefficients = tuple(weights)
    if not values or len(values) != len(coefficients):
        raise ValueError("toxicity components and weights must be equally sized and nonempty")
    _require_finite((*values, *coefficients), "toxicity components and weights")
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("toxicity components must be in [0, 1]")
    if any(weight < 0 or weight > 1 for weight in coefficients):
        raise ValueError("toxicity weights must be in [0, 1]")
    if sum(coefficients, Decimal(0)) != 1:
        raise ValueError("toxicity weights must sum exactly to one")
    return sum(
        (component * weight for component, weight in zip(values, coefficients, strict=True)),
        Decimal(0),
    )


def calculate_delta_toxicity(
    toxicity_1: Decimal,
    toxicity_2: Decimal,
) -> Decimal:
    """Return exact descriptive ``Toxicity_2 - Toxicity_1``."""
    _require_finite((toxicity_1, toxicity_2), "toxicity scores")
    if any(score < 0 or score > 1 for score in (toxicity_1, toxicity_2)):
        raise ValueError("toxicity scores must be in [0, 1]")
    return toxicity_2 - toxicity_1


def _shock_sign(direction: ShockDirection) -> Decimal:
    return Decimal(1) if direction is ShockDirection.BUY else Decimal(-1)


def _require_positive_epsilon(epsilon: Decimal) -> None:
    _require_finite((epsilon,), "epsilon")
    if epsilon <= 0:
        raise ValueError("epsilon must be positive")


def _require_non_negative(values: Sequence[Decimal], name: str) -> None:
    _require_finite(values, name)
    if any(value < 0 for value in values):
        raise ValueError(f"{name} must be non-negative")


def _require_finite(values: Sequence[Decimal], name: str) -> None:
    if any(not value.is_finite() for value in values):
        raise ValueError(f"{name} must be finite")
