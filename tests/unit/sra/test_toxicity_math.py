"""Exact deterministic equations for market-side toxicity research features."""

from decimal import Decimal

import pytest

from sra_nexus.sra import (
    FlowDirection,
    ShockDirection,
    SpreadToxicityFeatures,
    ToxicityWeights,
    VolatilityToxicityFeatures,
    calculate_bounded_excess_ratio,
    calculate_bounded_positive_ratio,
    calculate_bounded_replenishment_failure,
    calculate_composite_toxicity,
    calculate_credibility_interactions,
    calculate_delta_toxicity,
    calculate_directional_flow_coverage,
    calculate_directional_impact_change,
    calculate_event_time_realized_volatility,
    calculate_flow_persistence,
    calculate_impact_magnitude_ratio,
    calculate_impact_toxicity_component,
    calculate_net_liquidity_provision,
    calculate_normalized_net_liquidity_provision,
    calculate_raw_replenishment_failure,
    calculate_same_direction_run_length,
    calculate_shock_persistence,
    calculate_spread_expansion_ratio,
    calculate_unknown_flow_share,
    calculate_volatility_jump_ratio,
    calculate_withdrawal_pressure,
    classify_flow_direction,
    dominant_shock_direction,
)

EPSILON = Decimal("0.000001")


@pytest.mark.parametrize(
    ("flows", "expected"),
    [
        ((Decimal("100"), Decimal("50"), Decimal("-10")), Decimal("0.875")),
        ((Decimal("100"), Decimal("-100")), Decimal(0)),
        ((Decimal("100"), Decimal("200"), Decimal("50")), Decimal(1)),
    ],
)
def test_flow_persistence_is_exact(flows: tuple[Decimal, ...], expected: Decimal) -> None:
    """Directional persistence should preserve exact balanced and one-way limits."""
    assert calculate_flow_persistence(flows, EPSILON) == expected


def test_unknown_flow_is_excluded_from_signed_persistence_but_reduces_coverage() -> None:
    """UNKNOWN volume must not be silently treated as neutral signed flow."""
    buy = Decimal("100")
    sell = Decimal(0)
    unknown = Decimal("100")

    assert calculate_flow_persistence((buy - sell,), EPSILON) == 1
    assert calculate_unknown_flow_share(buy, sell, unknown) == Decimal("0.5")
    assert calculate_directional_flow_coverage(buy, sell, unknown) == Decimal("0.5")


def test_flow_direction_uses_an_inclusive_neutral_tolerance() -> None:
    """Signed direction should remain separate from absolute persistence magnitude."""
    tolerance = Decimal("2")

    assert classify_flow_direction(Decimal("3"), tolerance) is FlowDirection.BUY
    assert classify_flow_direction(Decimal("-3"), tolerance) is FlowDirection.SELL
    assert classify_flow_direction(Decimal("2"), tolerance) is FlowDirection.NEUTRAL
    assert classify_flow_direction(Decimal("-2"), tolerance) is FlowDirection.NEUTRAL


def test_shock_persistence_direction_and_latest_run_are_exact() -> None:
    """Shock persistence must use qualifying shocks rather than arbitrary trades."""
    directions = (
        ShockDirection.SELL,
        ShockDirection.SELL,
        ShockDirection.SELL,
        ShockDirection.BUY,
    )
    latest_sell_run = (
        ShockDirection.BUY,
        ShockDirection.SELL,
        ShockDirection.SELL,
        ShockDirection.SELL,
    )

    assert calculate_shock_persistence(directions) == Decimal("0.5")
    assert dominant_shock_direction(directions) is FlowDirection.SELL
    assert calculate_same_direction_run_length(directions) == 1
    assert calculate_same_direction_run_length(latest_sell_run) == 3


def test_impact_escalation_retains_sign_and_exact_ratio() -> None:
    """Increasing positive directional impact should increase bounded toxicity."""
    first = Decimal("0.01")
    second = Decimal("0.02")

    assert calculate_directional_impact_change(first, second) == Decimal("0.01")
    assert calculate_impact_magnitude_ratio(first, second, EPSILON) == Decimal(2)
    assert calculate_impact_toxicity_component(first, second, EPSILON) == Decimal(2) / Decimal(3)


def test_movement_against_aggressor_does_not_gain_toxicity_from_absolute_value() -> None:
    """A negative current DI is evidence against current aggressor effectiveness."""
    first = Decimal("0.02")
    failed = Decimal("-0.01")

    assert calculate_directional_impact_change(first, failed) == Decimal("-0.03")
    assert calculate_impact_magnitude_ratio(first, failed, EPSILON) == Decimal("0.5")
    assert calculate_impact_toxicity_component(first, failed, EPSILON) == 0
    assert calculate_impact_toxicity_component(first, failed, EPSILON) < (
        calculate_impact_toxicity_component(first, first, EPSILON)
    )


@pytest.mark.parametrize(
    ("rr", "raw", "bounded"),
    [
        (Decimal("0.3"), Decimal("0.7"), Decimal("0.7")),
        (Decimal("1"), Decimal("0"), Decimal("0")),
        (Decimal("1.4"), Decimal("-0.4"), Decimal("0")),
    ],
)
def test_replenishment_failure_preserves_over_recovery(
    rr: Decimal,
    raw: Decimal,
    bounded: Decimal,
) -> None:
    """Raw failure may be negative while its composite component remains bounded."""
    assert calculate_raw_replenishment_failure(rr) == raw
    assert calculate_bounded_replenishment_failure(rr) == bounded


def test_net_liquidity_and_withdrawal_equations_are_exact() -> None:
    """NLP, NNLP, and withdrawal pressure should use additions and withdrawals only."""
    assert calculate_net_liquidity_provision(Decimal("100"), Decimal("60")) == Decimal("40")
    assert calculate_normalized_net_liquidity_provision(
        Decimal("100"),
        Decimal("60"),
        EPSILON,
    ) == Decimal("0.25")
    assert calculate_withdrawal_pressure(Decimal("20"), Decimal("80"), EPSILON) == Decimal("0.8")


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (Decimal("0"), Decimal("0")),
        (Decimal("0.5"), Decimal("0")),
        (Decimal("1"), Decimal("0")),
        (Decimal("2"), Decimal("0.5")),
        (Decimal("3"), Decimal(2) / Decimal(3)),
    ],
)
def test_bounded_excess_ratio_uses_one_as_its_neutral_baseline(
    ratio: Decimal,
    expected: Decimal,
) -> None:
    """Only positive excess above a raw ratio of one should survive bounding."""
    assert calculate_bounded_excess_ratio(ratio) == expected


def test_absolute_magnitude_and_excess_ratio_transforms_remain_distinct() -> None:
    """The generic magnitude transform should retain its zero-neutral semantics."""
    assert calculate_bounded_positive_ratio(Decimal(1)) == Decimal("0.5")
    assert calculate_bounded_excess_ratio(Decimal(1)) == 0
    with pytest.raises(ValueError, match="non-negative"):
        calculate_bounded_excess_ratio(Decimal("-0.01"))


@pytest.mark.parametrize(
    ("baseline", "post", "expected_ratio", "expected_bounded"),
    [
        (Decimal("0.01"), Decimal("0.01"), Decimal("1"), Decimal("0")),
        (Decimal("0.02"), Decimal("0.01"), Decimal("0.5"), Decimal("0")),
        (Decimal("0.01"), Decimal("0.02"), Decimal("2"), Decimal("0.5")),
    ],
)
def test_spread_component_bounds_only_expansion_above_baseline(
    baseline: Decimal,
    post: Decimal,
    expected_ratio: Decimal,
    expected_bounded: Decimal,
) -> None:
    """Raw spread ratio and change remain intact while contraction contributes zero."""
    component = SpreadToxicityFeatures(
        baseline_event_count=1,
        baseline_spread=baseline,
        post_shock_horizon_events=1,
        post_shock_spread=post,
        absolute_spread_change=post - baseline,
        spread_expansion_ratio=calculate_spread_expansion_ratio(
            baseline,
            post,
            EPSILON,
        ),
        bounded_spread_expansion=calculate_bounded_excess_ratio(expected_ratio),
        epsilon=EPSILON,
    )

    assert component.absolute_spread_change == post - baseline
    assert component.spread_expansion_ratio == expected_ratio
    assert component.bounded_spread_expansion == expected_bounded


def test_event_time_volatility_matches_arithmetic_return_rms() -> None:
    """Pre/post RV should be an unannualized RMS of event-time arithmetic returns."""
    pre = (Decimal("100"), Decimal("110"), Decimal("99"))
    post = (Decimal("100"), Decimal("120"), Decimal("96"))
    expected_pre = ((Decimal("0.1") ** 2 + Decimal("-0.1") ** 2) / 2).sqrt()
    expected_post = ((Decimal("0.2") ** 2 + Decimal("-0.2") ** 2) / 2).sqrt()

    pre_rv = calculate_event_time_realized_volatility(pre)
    post_rv = calculate_event_time_realized_volatility(post)

    assert pre_rv == expected_pre
    assert post_rv == expected_post
    assert calculate_volatility_jump_ratio(pre_rv, post_rv, EPSILON) == Decimal(2)
    assert calculate_bounded_excess_ratio(Decimal(2)) == Decimal("0.5")


@pytest.mark.parametrize(
    ("baseline", "post", "expected_ratio", "expected_bounded"),
    [
        (Decimal("0.01"), Decimal("0.01"), Decimal("1"), Decimal("0")),
        (Decimal("0.02"), Decimal("0.01"), Decimal("0.5"), Decimal("0")),
        (Decimal("0.01"), Decimal("0.02"), Decimal("2"), Decimal("0.5")),
    ],
)
def test_volatility_component_bounds_only_jump_above_baseline(
    baseline: Decimal,
    post: Decimal,
    expected_ratio: Decimal,
    expected_bounded: Decimal,
) -> None:
    """Raw RV ratio remains intact while flat or lower volatility contributes zero."""
    component = VolatilityToxicityFeatures(
        baseline_return_count=1,
        response_return_count=1,
        pre_shock_realized_volatility=baseline,
        post_shock_realized_volatility=post,
        volatility_jump_ratio=calculate_volatility_jump_ratio(
            baseline,
            post,
            EPSILON,
        ),
        bounded_volatility_jump=calculate_bounded_excess_ratio(expected_ratio),
        epsilon=EPSILON,
    )

    assert component.pre_shock_realized_volatility == baseline
    assert component.post_shock_realized_volatility == post
    assert component.volatility_jump_ratio == expected_ratio
    assert component.bounded_volatility_jump == expected_bounded


def test_zero_baseline_volatility_uses_epsilon_without_nonfinite_output() -> None:
    """A flat pre-window should yield an explicit finite zero-baseline ratio."""
    pre_rv = calculate_event_time_realized_volatility((Decimal("100"),) * 3)
    post_rv = calculate_event_time_realized_volatility(
        (Decimal("100"), Decimal("101"), Decimal("100"))
    )

    ratio = calculate_volatility_jump_ratio(pre_rv, post_rv, EPSILON)

    assert pre_rv == 0
    assert ratio == post_rv / EPSILON
    assert ratio.is_finite()
    assert calculate_bounded_excess_ratio(ratio) < 1


@pytest.mark.parametrize(
    ("credibility", "credible", "toxic"),
    [
        (Decimal("0.9"), Decimal("0.72"), Decimal("0.08")),
        (Decimal("0.2"), Decimal("0.16"), Decimal("0.64")),
    ],
)
def test_credibility_interactions_do_not_redefine_rr_or_lc(
    credibility: Decimal,
    credible: Decimal,
    toxic: Decimal,
) -> None:
    """RR/LC products should remain transparent optional interaction features."""
    assert calculate_credibility_interactions(Decimal("0.8"), credibility) == (
        credible,
        toxic,
    )


def test_composite_is_an_exact_convex_engineering_prior() -> None:
    """Known bounded components and weights should produce one exact score."""
    components = tuple(Decimal(value) for value in ("0.1", "0.2", "0.3", "0.4"))
    weights = tuple(Decimal(value) for value in ("0.1", "0.2", "0.3", "0.4"))

    score = calculate_composite_toxicity(components, weights)

    assert score == Decimal("0.30")
    assert Decimal(0) <= score <= Decimal(1)
    assert calculate_composite_toxicity((Decimal(0),), (Decimal(1),)) == 0
    assert calculate_composite_toxicity((Decimal(1),), (Decimal(1),)) == 1
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        calculate_composite_toxicity((Decimal("1.1"),), (Decimal(1),))


def test_neutral_spread_and_volatility_add_nothing_to_composite() -> None:
    """Raw ratios of one must not create the former artificial 0.5 components."""
    weights = ToxicityWeights().as_tuple()
    components = (
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        calculate_bounded_excess_ratio(Decimal(1)),
        calculate_bounded_excess_ratio(Decimal(1)),
        Decimal(0),
    )

    assert calculate_composite_toxicity(components, weights) == 0


def test_pair_delta_toxicity_is_exact_and_signed() -> None:
    """A less-toxic second shock should retain a negative descriptive delta."""
    assert calculate_delta_toxicity(Decimal("0.75"), Decimal("0.40")) == Decimal("-0.35")
