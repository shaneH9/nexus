"""Tests for chronological walk-forward, purging, and embargo semantics."""

from decimal import Decimal

from tests.support.research import research_observation

from sra_nexus.research import (
    WalkForwardConfig,
    WalkForwardMode,
    WalkForwardSplitter,
)


def test_expanding_splits_never_place_future_rows_in_training() -> None:
    """Every expanding fold fixes its historical start and precedes its test rows."""
    observations = tuple(
        research_observation(index, maximum_horizon=2) for index in range(0, 1200, 200)
    )
    splits = WalkForwardSplitter(
        WalkForwardConfig(
            mode=WalkForwardMode.EXPANDING,
            minimum_train_observations=2,
            test_observations=1,
            maximum_label_horizon_events=2,
        )
    ).split(observations)

    assert len(splits) == 4
    assert all(split.train_end <= split.test_start for split in splits)
    assert splits[0].train_observation_ids[0] == splits[-1].train_observation_ids[0]
    assert len(splits[-1].train_observation_ids) > len(splits[0].train_observation_ids)


def test_rolling_splits_use_only_configured_trailing_history() -> None:
    """Rolling mode drops older rows while preserving chronological evaluation."""
    observations = tuple(
        research_observation(index, maximum_horizon=2) for index in range(0, 1400, 200)
    )
    splits = WalkForwardSplitter(
        WalkForwardConfig(
            mode=WalkForwardMode.ROLLING,
            minimum_train_observations=2,
            rolling_train_observations=3,
            test_observations=1,
            maximum_label_horizon_events=2,
        )
    ).split(observations)

    assert len(splits[-1].train_observation_ids) == 3
    assert splits[0].train_observation_ids[0] != splits[-1].train_observation_ids[0]


def test_training_label_crossing_test_boundary_is_purged() -> None:
    """A +100-event training label touching the first test event is removed."""
    observations = tuple(
        research_observation(index, maximum_horizon=100) for index in (0, 50, 100, 150, 200, 250)
    )
    split = WalkForwardSplitter(
        WalkForwardConfig(
            minimum_train_observations=4,
            test_observations=1,
            maximum_label_horizon_events=100,
        )
    ).split(observations)[0]

    purged = set(split.purged_observation_ids)
    assert observations[2].observation_id in purged
    assert observations[3].observation_id in purged
    assert observations[1].observation_id not in purged
    assert not set(split.train_observation_ids) & purged


def test_25_event_embargo_excludes_only_rows_inside_pretest_gap() -> None:
    """Test anchors 10 and 20 events after train end are embargoed; 30 is retained."""
    observations = tuple(
        research_observation(index, maximum_horizon=1) for index in (0, 10, 20, 30, 40, 50, 60)
    )
    split = WalkForwardSplitter(
        WalkForwardConfig(
            minimum_train_observations=3,
            test_observations=3,
            maximum_label_horizon_events=1,
            embargo_event_count=25,
        )
    ).split(observations)[0]

    assert split.embargoed_observation_ids == (
        observations[3].observation_id,
        observations[4].observation_id,
    )
    assert split.test_observation_ids == (observations[5].observation_id,)


def test_exchange_time_embargo_is_independent_of_event_count() -> None:
    """An optional 25-second rule excludes rows even when event embargo is disabled."""
    observations = tuple(
        research_observation(index, maximum_horizon=1) for index in (0, 10, 20, 30, 40, 50)
    )
    split = WalkForwardSplitter(
        WalkForwardConfig(
            minimum_train_observations=3,
            test_observations=3,
            maximum_label_horizon_events=1,
            embargo_event_count=0,
            embargo_exchange_seconds=Decimal(25),
        )
    ).split(observations)[0]

    assert split.embargoed_observation_ids == (
        observations[3].observation_id,
        observations[4].observation_id,
    )
    assert split.test_observation_ids == (observations[5].observation_id,)


def test_splitter_rejects_mismatched_label_horizon_policy() -> None:
    """Purging cannot claim a different maximum horizon than dataset rows."""
    observations = tuple(
        research_observation(index, maximum_horizon=100) for index in (0, 200, 400)
    )
    splitter = WalkForwardSplitter(
        WalkForwardConfig(
            minimum_train_observations=2,
            test_observations=1,
            maximum_label_horizon_events=50,
        )
    )

    try:
        splitter.split(observations)
    except ValueError as error:
        assert "maximum label horizon" in str(error)
    else:
        raise AssertionError("mismatched purge horizon should fail")
