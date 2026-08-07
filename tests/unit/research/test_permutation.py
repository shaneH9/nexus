"""Tests for block-preserving, instrument-aware permutation inference."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.support.market_data import INSTRUMENT
from tests.support.research import research_observation
from tests.support.sra import SRA_BASE_TIME

from sra_nexus.common.types import (
    InstrumentId,
    ResearchObservationId,
    ResearchSplitId,
)
from sra_nexus.research import (
    ConditionalMeanReversalReturn,
    CovarianceAssociation,
    MeanReversalAdjustedReturn,
    MedianReversalAdjustedReturn,
    PermutationAlternative,
    PermutationDatum,
    PermutationMode,
    PermutationTestConfig,
    PermutationTestService,
    ReversalSuccessRate,
    UpperLowerQuantileDifference,
    WalkForwardConfig,
    WalkForwardSplitter,
    empirical_permutation_p_value,
    generate_permuted_label_assignments,
    permute_blocks_preserving_order,
)

SECOND_INSTRUMENT = InstrumentId(UUID("30000000-0000-4000-8000-000000000202"))
SPLIT_ID = ResearchSplitId(UUID("40000000-0000-4000-8000-000000000303"))


def _datum(
    index: int,
    label: str,
    *,
    feature: str | None = None,
    instrument_id: InstrumentId = INSTRUMENT.instrument_id,
    session_id: str | None = None,
    condition_selected: bool = True,
) -> PermutationDatum:
    value = Decimal(label)
    return PermutationDatum(
        observation_id=ResearchObservationId(UUID(int=index + 1)),
        instrument_id=instrument_id,
        venue="NASDAQ",
        prediction_anchor_event_index=index,
        prediction_anchor_process_time=SRA_BASE_TIME + timedelta(seconds=index),
        label=value,
        reversal_success=value > 0,
        feature_value=None if feature is None else Decimal(feature),
        condition_selected=condition_selected,
        session_id=session_id,
    )


def _config(
    *,
    statistic_name: str = "MeanReversalAdjustedReturn",
    seed: int = 7,
    block_size: int = 2,
    permutation_count: int = 20,
    mode: PermutationMode = PermutationMode.MONTE_CARLO,
    within_instrument: bool = True,
    session_restricted: bool = False,
    preserve_null_statistics: bool = True,
) -> PermutationTestConfig:
    return PermutationTestConfig(
        permutation_count=permutation_count,
        seed=seed,
        block_size=block_size,
        max_label_horizon_events=block_size,
        within_instrument=within_instrument,
        session_restricted=session_restricted,
        alternative=PermutationAlternative.GREATER,
        statistic_name=statistic_name,
        mode=mode,
        preserve_null_statistics=preserve_null_statistics,
    )


def test_empirical_greater_p_value_exact_example() -> None:
    """Observed 10 against 1,2,3,4 gives the required exact p-value 0.2."""
    result = empirical_permutation_p_value(
        Decimal(10),
        tuple(Decimal(value) for value in (1, 2, 3, 4)),
        PermutationAlternative.GREATER,
    )
    assert result == Decimal("0.2")


def test_empirical_greater_p_value_counts_ties() -> None:
    """Observed 4 counts both the tied 4 and larger 5, giving p-value 0.6."""
    result = empirical_permutation_p_value(
        Decimal(4),
        tuple(Decimal(value) for value in (1, 2, 4, 5)),
        PermutationAlternative.GREATER,
    )
    assert result == Decimal("0.6")


def test_empirical_less_and_two_sided_alternatives() -> None:
    """Tail selection is explicit and two-sided evidence compares absolute values."""
    null = (Decimal(-3), Decimal(-1), Decimal(2))
    assert empirical_permutation_p_value(Decimal(-2), null, PermutationAlternative.LESS) == Decimal(
        "0.5"
    )
    assert empirical_permutation_p_value(
        Decimal(2), null, PermutationAlternative.TWO_SIDED
    ) == Decimal("0.75")


def test_block_permutation_preserves_order_inside_each_block() -> None:
    """Only A/B/C placement changes; suffixes 1,2,3 remain chronological."""
    blocks = (("A1", "A2", "A3"), ("B1", "B2", "B3"), ("C1", "C2", "C3"))
    result = permute_blocks_preserving_order(blocks, (2, 0, 1))
    assert result == ("C1", "C2", "C3", "A1", "A2", "A3", "B1", "B2", "B3")


def test_same_seed_produces_identical_permutation_sequence() -> None:
    """Seeded Monte Carlo assignments are reproducible byte-for-byte as contracts."""
    data = tuple(_datum(index, str(index - 3)) for index in range(8))
    config = _config(seed=11)
    assert generate_permuted_label_assignments(data, config) == (
        generate_permuted_label_assignments(data, config)
    )


def test_different_seed_can_produce_different_permutation_sequence() -> None:
    """A different explicit seed samples a different deterministic null sequence."""
    data = tuple(_datum(index, str(index - 3)) for index in range(8))
    first = generate_permuted_label_assignments(data, _config(seed=1))
    second = generate_permuted_label_assignments(data, _config(seed=2))
    assert first != second


def test_within_instrument_never_cross_assigns_labels() -> None:
    """Every source label retains the target instrument under the default stratum."""
    data = (
        _datum(0, "1"),
        _datum(1, "2"),
        _datum(2, "3", instrument_id=SECOND_INSTRUMENT),
        _datum(3, "4", instrument_id=SECOND_INSTRUMENT),
    )
    assignments = generate_permuted_label_assignments(
        data,
        _config(block_size=1, permutation_count=10),
    )
    assert all(
        assignment.target_instrument_id == assignment.source_instrument_id
        for permutation in assignments
        for assignment in permutation
    )


def test_cross_instrument_permutation_is_explicit_opt_in() -> None:
    """Disabling instrument strata is marked in result metadata."""
    data = (
        _datum(0, "1"),
        _datum(1, "2", instrument_id=SECOND_INSTRUMENT),
    )
    result = PermutationTestService().run(
        data=data,
        statistic=MeanReversalAdjustedReturn(),
        config=_config(block_size=1, within_instrument=False),
        split_id=SPLIT_ID,
        forward_horizon=1,
        feature_or_condition="all_rows",
    )
    assert result.instrument_scope.startswith("CROSS_INSTRUMENT_OPT_IN:")


def test_session_restriction_requires_real_supplied_metadata() -> None:
    """The service refuses to fabricate trading sessions from timestamps."""
    data = (_datum(0, "1"), _datum(1, "2"))
    with pytest.raises(ValueError, match="requires supplied session_id"):
        generate_permuted_label_assignments(data, _config(session_restricted=True))


def test_session_restriction_keeps_sources_in_same_session() -> None:
    """When callers provide sessions they become explicit permutation strata."""
    data = (
        _datum(0, "1", session_id="open"),
        _datum(1, "2", session_id="open"),
        _datum(2, "3", session_id="midday"),
        _datum(3, "4", session_id="midday"),
    )
    assignments = generate_permuted_label_assignments(
        data,
        _config(block_size=1, session_restricted=True),
    )
    session_by_id = {item.observation_id: item.session_id for item in data}
    assert all(
        session_by_id[assignment.target_observation_id]
        == session_by_id[assignment.source_observation_id]
        for permutation in assignments
        for assignment in permutation
    )


def test_exact_mode_enumerates_all_small_block_orders() -> None:
    """Three one-row blocks produce exactly 3! complete block arrangements."""
    data = tuple(_datum(index, str(index + 1)) for index in range(3))
    assignments = generate_permuted_label_assignments(
        data,
        _config(block_size=1, mode=PermutationMode.EXACT),
    )
    assert len(assignments) == 6


def test_block_size_shorter_than_label_horizon_is_rejected() -> None:
    """Overlapping labels cannot silently use an incompatible short block."""
    with pytest.raises(ValidationError, match="at least max_label_horizon"):
        PermutationTestConfig(
            statistic_name="MeanReversalAdjustedReturn",
            block_size=25,
            max_label_horizon_events=100,
        )


def test_initial_statistics_are_transparent_and_exact() -> None:
    """Mean, median, success, covariance, condition, and quantile helpers are explicit."""
    data = (
        _datum(0, "-1", feature="1", condition_selected=False),
        _datum(1, "1", feature="2"),
        _datum(2, "3", feature="3"),
    )
    assert MeanReversalAdjustedReturn()(data) == Decimal(1)
    assert MedianReversalAdjustedReturn()(data) == Decimal(1)
    assert ReversalSuccessRate()(data) == Decimal(2) / Decimal(3)
    assert CovarianceAssociation()(data) == Decimal(4) / Decimal(3)
    assert ConditionalMeanReversalReturn()(data) == Decimal(2)
    assert UpperLowerQuantileDifference()(data) == Decimal(4)


def test_permutation_result_has_valid_null_metadata_and_effect_size() -> None:
    """A null fixture produces a bounded p-value, exact null count, and fold metadata."""
    data = tuple(_datum(index, str((index % 3) - 1), feature=str(index % 2)) for index in range(12))
    config = _config(statistic_name="CovarianceAssociation", permutation_count=40)
    result = PermutationTestService().run(
        data=data,
        statistic=CovarianceAssociation(),
        config=config,
        split_id=SPLIT_ID,
        forward_horizon=2,
        feature_or_condition="synthetic_null_feature",
    )
    assert Decimal(0) <= result.p_value <= Decimal(1)
    assert result.permutation_count == 40
    assert result.null_statistics is not None
    assert len(result.null_statistics) == 40
    assert result.split_id == SPLIT_ID
    assert result.observed_minus_null_mean == (result.observed_statistic - result.null_summary.mean)


def test_fold_runner_refuses_data_outside_declared_test_region() -> None:
    """Null permutations cannot silently cross the train/test fold boundary."""
    observations = tuple(
        research_observation(index, maximum_horizon=1) for index in (0, 10, 20, 30)
    )
    split = WalkForwardSplitter(
        WalkForwardConfig(
            minimum_train_observations=2,
            test_observations=2,
            maximum_label_horizon_events=1,
        )
    ).split(observations)[0]
    valid_data = (_datum(20, "1"), _datum(30, "2"))
    service = PermutationTestService()

    result = service.run_for_split(
        data=valid_data,
        split=split,
        statistic=MeanReversalAdjustedReturn(),
        config=_config(block_size=1),
        forward_horizon=1,
        feature_or_condition="test_fold_only",
    )
    assert result.split_id == split.split_id

    with pytest.raises(ValueError, match="subset of the test fold"):
        service.run_for_split(
            data=(*valid_data, _datum(10, "3")),
            split=split,
            statistic=MeanReversalAdjustedReturn(),
            config=_config(block_size=1),
            forward_horizon=1,
            feature_or_condition="contains_training_row",
        )


def test_deterministic_synthetic_signal_exceeds_most_block_nulls() -> None:
    """An intentionally aligned continuous signal beats the 95th null percentile."""
    data = tuple(_datum(index, str(index), feature=str(index)) for index in range(20))
    result = PermutationTestService().run(
        data=data,
        statistic=CovarianceAssociation(),
        config=_config(
            statistic_name="CovarianceAssociation",
            block_size=2,
            permutation_count=199,
            seed=19,
        ),
        split_id=SPLIT_ID,
        forward_horizon=2,
        feature_or_condition="perfect_monotone_fixture",
    )
    assert result.observed_statistic > result.null_summary.percentile_95
    assert result.p_value < Decimal("0.1")
