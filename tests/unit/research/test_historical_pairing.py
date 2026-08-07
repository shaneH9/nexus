"""Nearest-prior comparable historical shock-search tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tests.support.sra import SRA_BASE_TIME, liquidity_shock
from tests.support.sra_comparison import recovery_time, resiliency_vector, shock_impact

from sra_nexus.research import (
    HistoricalShockCandidate,
    find_most_recent_prior_comparable_shock,
)
from sra_nexus.sra import (
    FailedAggressionComparison,
    ShockDirection,
    ShockPairConfig,
    ShockPairService,
)


def test_opposite_direction_intervening_shock_does_not_block_sell_pair() -> None:
    """SELL A / BUY B / SELL C produces exactly the accepted A-to-C SELL pair."""
    candidates = (
        _candidate(0, ShockDirection.SELL),
        _candidate(3, ShockDirection.BUY),
        _candidate(6, ShockDirection.SELL),
    )

    comparisons = _comparisons(candidates)

    assert len(comparisons) == 1
    comparison = comparisons[0]
    assert comparison.direction is ShockDirection.SELL
    assert comparison.shock_1_id == candidates[0].shock.shock_id
    assert comparison.shock_2_id == candidates[2].shock.shock_id
    assert comparison.event_distance == 4


def test_current_shock_pairs_with_nearest_comparable_same_direction() -> None:
    """SELL C pairs with SELL B and does not additionally pair with SELL A."""
    candidates = (
        _candidate(0, ShockDirection.SELL),
        _candidate(3, ShockDirection.SELL),
        _candidate(6, ShockDirection.SELL),
    )

    comparisons = _comparisons(candidates)

    assert len(comparisons) == 2
    latest = comparisons[-1]
    assert latest.shock_1_id == candidates[1].shock.shock_id
    assert latest.shock_2_id == candidates[2].shock.shock_id


def test_search_falls_back_past_incomparable_nearest_same_direction() -> None:
    """An aggression-ratio failure at B/C permits the compatible A/C pair."""
    candidates = (
        _candidate(0, ShockDirection.SELL, normalized_aggression="1"),
        _candidate(3, ShockDirection.SELL, normalized_aggression="0.1"),
        _candidate(6, ShockDirection.SELL, normalized_aggression="1"),
    )

    comparisons = _comparisons(candidates)

    assert len(comparisons) == 1
    comparison = comparisons[0]
    assert comparison.shock_1_id == candidates[0].shock.shock_id
    assert comparison.shock_2_id == candidates[2].shock.shock_id


def test_search_does_not_cross_structural_segment() -> None:
    """A candidate after RESET cannot search back to a prior segment."""
    candidates = (
        _candidate(0, ShockDirection.SELL, segment=0),
        _candidate(3, ShockDirection.SELL, segment=1),
    )

    assert _comparisons(candidates) == ()


def _comparisons(
    candidates: tuple[HistoricalShockCandidate, ...],
) -> tuple[FailedAggressionComparison, ...]:
    service = ShockPairService(
        ShockPairConfig(
            max_event_distance=20,
            max_exchange_seconds=Decimal("60"),
            required_impact_horizons_events=(1,),
            required_resiliency_horizons_events=(1,),
            required_recovery_thresholds=(Decimal("0.5"),),
        )
    )
    results: list[FailedAggressionComparison] = []
    for index, current in enumerate(candidates):
        comparison = find_most_recent_prior_comparable_shock(
            current,
            candidates[:index],
            service,
        )
        if comparison is not None:
            results.append(comparison)
    return tuple(results)


def _candidate(
    start_event_index: int,
    direction: ShockDirection,
    *,
    normalized_aggression: str = "1",
    segment: int = 0,
) -> HistoricalShockCandidate:
    shock = liquidity_shock(
        direction=direction,
        normalized_aggression=normalized_aggression,
        end_time=SRA_BASE_TIME + timedelta(seconds=start_event_index),
    )
    return HistoricalShockCandidate(
        shock=shock,
        impacts=(shock_impact(shock, 1, "0.01"),),
        resiliency=resiliency_vector(
            shock,
            ((1, "0.5"),),
            (recovery_time("0.5", 1),),
        ),
        start_event_index=start_event_index,
        end_event_index=start_event_index + 1,
        available_event_index=start_event_index + 2,
        segment=segment,
    )
