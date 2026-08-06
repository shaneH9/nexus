"""Direct tests for deterministic event-scoring and NewsState formulas."""

from datetime import UTC, datetime, timedelta
from math import exp

import pytest

from sra_nexus.aggregator.scoring_math import (
    bounded_union,
    calculate_directional_intensity,
    calculate_event_decay,
    calculate_event_intensity,
    calculate_event_risk_contribution,
    calculate_news_acceleration,
    calculate_weighted_confidence,
)

START = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def test_decay_matches_exact_exponential_at_zero_one_and_two_tau() -> None:
    """Decay must use seconds and the documented exponential formula exactly."""
    tau = 600.0

    assert calculate_event_decay(START, START, tau) == pytest.approx(1.0)
    assert calculate_event_decay(START, START + timedelta(seconds=tau), tau) == pytest.approx(
        exp(-1.0)
    )
    assert calculate_event_decay(
        START,
        START + timedelta(seconds=2 * tau),
        tau,
    ) == pytest.approx(exp(-2.0))


def test_decay_rejects_future_use_and_invalid_tau() -> None:
    """An event cannot influence state before availability or with an invalid timescale."""
    with pytest.raises(ValueError, match="precede"):
        calculate_event_decay(START, START - timedelta(seconds=1), 60.0)
    with pytest.raises(ValueError, match="greater than zero"):
        calculate_event_decay(START, START, 0.0)


def test_bounded_union_handles_zero_one_and_multiple_contributions() -> None:
    """Risk aggregation must be exact for empty, singleton, and multiple inputs."""
    assert bounded_union(()) == 0.0
    assert bounded_union((0.2,)) == pytest.approx(0.2)
    assert bounded_union((0.2, 0.3)) == pytest.approx(1.0 - (1.0 - 0.2) * (1.0 - 0.3))
    assert bounded_union((1.0, 0.9)) == 1.0


def test_bounded_union_never_exceeds_one_for_many_valid_contributions() -> None:
    """Probabilistic union remains bounded without a post-sum clamp."""
    assert 0.0 <= bounded_union((0.7,) * 100) <= 1.0


@pytest.mark.parametrize(
    ("direction", "expected"),
    [(1.0, 0.4), (-1.0, -0.4), (0.0, 0.0), (0.5, 0.2)],
)
def test_directional_intensity_preserves_positive_negative_and_unknown(
    direction: float,
    expected: float,
) -> None:
    """Direction changes signed intensity but unknown direction remains zero."""
    assert calculate_directional_intensity(0.4, direction) == pytest.approx(expected)


def test_event_intensity_includes_exposure_magnitude() -> None:
    """The selected Milestone E formula must multiply all seven explicit factors."""
    assert calculate_event_intensity(0.5, 0.8, 0.7, 0.6, 0.9, 0.75, 0.4) == pytest.approx(
        0.5 * 0.8 * 0.7 * 0.6 * 0.9 * 0.75 * 0.4
    )


def test_risk_contribution_is_direction_independent_and_bounded() -> None:
    """Event risk uses uncertainty/evidence but accepts no directional input."""
    risk = calculate_event_risk_contribution(0.8, 0.9, 0.7, 0.6, 0.5, 0.4, 0.75)
    expected = 0.8 * 0.9 * 0.7 * 0.75 * (0.5 + 0.25 * 0.5 + 0.25 * 0.4) * (0.5 + 0.5 * 0.6)

    assert risk == pytest.approx(expected)
    assert 0.0 <= risk <= 1.0


def test_news_acceleration_uses_items_per_hour() -> None:
    """Recent and prior counts must be normalized by their distinct windows."""
    acceleration = calculate_news_acceleration(
        recent_count=3,
        recent_window_seconds=15 * 60,
        prior_count=4,
        prior_window_seconds=60 * 60,
    )

    assert acceleration == pytest.approx(8.0)


def test_weighted_confidence_has_explicit_empty_state() -> None:
    """No active evidence yields zero rather than false high confidence."""
    assert calculate_weighted_confidence(()) == 0.0
    assert calculate_weighted_confidence(((0.8, 2.0), (0.2, 1.0))) == pytest.approx(0.6)
