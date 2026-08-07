"""Tests for explicit ordered-shock comparability and stable identity."""

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.support.sra import SRA_BASE_TIME, liquidity_shock

from sra_nexus.common.types import new_instrument_id
from sra_nexus.sra import (
    ShockDirection,
    ShockPairConfig,
    ShockPairIncomparabilityReason,
    ShockPairSpan,
    StructuralBreakKind,
    assess_shock_pair,
    build_shock_pair,
    derive_shock_pair_id,
)


def _reason_codes(assessment: object) -> set[ShockPairIncomparabilityReason]:
    assert hasattr(assessment, "reasons")
    return {item.reason for item in assessment.reasons}


def test_pair_within_event_time_and_aggression_bounds_is_comparable() -> None:
    """Inclusive initial engineering bounds should allow an ordinary pair."""
    shock_1 = liquidity_shock(normalized_aggression="0.5", end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(
        normalized_aggression="0.55",
        end_time=SRA_BASE_TIME + timedelta(seconds=10),
    )

    assessment = assess_shock_pair(shock_1, shock_2, ShockPairSpan(event_distance=25))

    assert assessment.comparable
    assert assessment.event_distance == 25
    assert assessment.exchange_seconds_distance == Decimal("10")
    assert assessment.process_seconds_distance == Decimal("10")
    assert assessment.aggression_ratio == Decimal("1.1")
    assert assessment.reasons == ()


def test_event_distance_above_inclusive_maximum_is_incomparable() -> None:
    """Event distance uses an explicit all-normalized-event count, not trade count."""
    shock_1 = liquidity_shock(end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(seconds=1))
    config = ShockPairConfig(
        max_event_distance=5,
        required_impact_horizons_events=(5,),
        required_resiliency_horizons_events=(5,),
    )

    at_limit = assess_shock_pair(
        shock_1,
        shock_2,
        ShockPairSpan(event_distance=5),
        config,
    )
    beyond = assess_shock_pair(
        shock_1,
        shock_2,
        ShockPairSpan(event_distance=6),
        config,
    )

    assert at_limit.comparable
    assert ShockPairIncomparabilityReason.EVENT_DISTANCE_EXCEEDED in _reason_codes(beyond)


def test_exchange_time_distance_is_checked_separately_from_event_distance() -> None:
    """A small event span must not bypass the exchange-clock maximum."""
    shock_1 = liquidity_shock(end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(seconds=61))

    assessment = assess_shock_pair(
        shock_1,
        shock_2,
        ShockPairSpan(event_distance=1),
    )

    assert ShockPairIncomparabilityReason.EXCHANGE_DISTANCE_EXCEEDED in _reason_codes(assessment)


def test_aggression_ratio_bounds_are_optional_but_enabled_by_default() -> None:
    """A second shock four times as aggressive should fail the default 0.5--2.0 band."""
    shock_1 = liquidity_shock(normalized_aggression="0.5", end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(
        normalized_aggression="2.0",
        end_time=SRA_BASE_TIME + timedelta(seconds=1),
    )

    bounded = assess_shock_pair(shock_1, shock_2, ShockPairSpan(event_distance=1))
    unbounded = assess_shock_pair(
        shock_1,
        shock_2,
        ShockPairSpan(event_distance=1),
        ShockPairConfig(
            min_normalized_aggression_ratio=None,
            max_normalized_aggression_ratio=None,
            required_impact_horizons_events=(5,),
            required_resiliency_horizons_events=(5,),
        ),
    )

    assert ShockPairIncomparabilityReason.AGGRESSION_RATIO_OUTSIDE_BOUNDS in _reason_codes(bounded)
    assert unbounded.comparable


def test_direction_and_instrument_mismatches_are_explicit() -> None:
    """Same-instrument and same-direction requirements should fail independently."""
    shock_1 = liquidity_shock(end_time=SRA_BASE_TIME)
    later = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(seconds=1))
    buy = later.model_copy(update={"direction": ShockDirection.BUY})
    other_instrument = later.model_copy(update={"instrument_id": new_instrument_id()})

    direction_result = assess_shock_pair(
        shock_1,
        buy,
        ShockPairSpan(event_distance=1),
    )
    instrument_result = assess_shock_pair(
        shock_1,
        other_instrument,
        ShockPairSpan(event_distance=1),
    )

    assert ShockPairIncomparabilityReason.DIRECTION_MISMATCH in _reason_codes(direction_result)
    assert ShockPairIncomparabilityReason.INSTRUMENT_MISMATCH in _reason_codes(instrument_result)


@pytest.mark.parametrize("break_kind", tuple(StructuralBreakKind))
def test_known_structural_break_invalidates_pair(break_kind: StructuralBreakKind) -> None:
    """RESET, sequence corruption, and data gaps must all stop comparison."""
    shock_1 = liquidity_shock(end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(seconds=1))

    assessment = assess_shock_pair(
        shock_1,
        shock_2,
        ShockPairSpan(event_distance=1, structural_break=break_kind),
    )

    assert not assessment.comparable
    assert assessment.reasons[0].reason is ShockPairIncomparabilityReason.STRUCTURAL_BREAK
    assert assessment.reasons[0].structural_break is break_kind


def test_same_shock_cannot_pair_with_itself() -> None:
    """Self-comparison is ordinary incomparability and cannot materialize a pair."""
    shock = liquidity_shock(end_time=SRA_BASE_TIME)
    assessment = assess_shock_pair(shock, shock, ShockPairSpan(event_distance=0))

    assert ShockPairIncomparabilityReason.SAME_SHOCK in _reason_codes(assessment)
    with pytest.raises(ValueError, match="incomparable"):
        build_shock_pair(shock, shock, assessment)


def test_pair_id_is_deterministic_versioned_and_order_sensitive() -> None:
    """The same ordered IDs/version should reproduce while reversed IDs differ."""
    shock_1 = liquidity_shock(end_time=SRA_BASE_TIME)
    shock_2 = liquidity_shock(end_time=SRA_BASE_TIME + timedelta(seconds=1))
    assessment = assess_shock_pair(shock_1, shock_2, ShockPairSpan(event_distance=2))

    pair_1 = build_shock_pair(shock_1, shock_2, assessment)
    pair_2 = build_shock_pair(shock_1, shock_2, assessment)
    reversed_id = derive_shock_pair_id(
        shock_2.shock_id,
        shock_1.shock_id,
        pair_1.comparison_version,
    )

    assert pair_1.pair_id == pair_2.pair_id
    assert reversed_id != pair_1.pair_id
