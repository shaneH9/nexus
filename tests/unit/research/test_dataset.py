"""Tests for causal feature snapshots, observations, manifests, and export."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.support.research import completed_comparison, indexed_state
from tests.support.sra import SRA_BASE_TIME

from sra_nexus.research import (
    FeatureAvailability,
    FeatureBuildResult,
    FeatureSnapshotConfig,
    ForwardLabelConfig,
    ResearchDatasetBuilder,
    ResearchDatasetConfig,
    SRAFeatureInput,
    evaluate_failed_aggression_condition,
    export_research_dataset_jsonl,
    select_prediction_anchor,
)
from sra_nexus.sra import IndexedMarketStateObservation


def _builder() -> ResearchDatasetBuilder:
    return ResearchDatasetBuilder(
        ResearchDatasetConfig(
            feature_config=FeatureSnapshotConfig(
                depth_levels=1,
                backward_horizons_events=(1,),
            ),
            label_config=ForwardLabelConfig(horizons_events=(2,)),
        )
    )


def _feature_input(
    states: tuple[IndexedMarketStateObservation, ...],
    event_index: int = 3,
) -> SRAFeatureInput:
    comparison = completed_comparison()
    reference = states[event_index].observation.event_reference
    return SRAFeatureInput(
        comparison=comparison,
        comparison_event_index=event_index,
        comparison_event_reference=reference,
        comparison_available_at_process_time=reference.process_time,
        comparison_source_data_identifier="pair-fixture-v1",
    )


def test_prediction_anchor_uses_latest_selected_feature_availability() -> None:
    """Pair, LC, and toxicity clocks select the first state at or after the latest."""
    states = tuple(indexed_state(index) for index in range(5))
    availability = (
        FeatureAvailability(
            feature_name="shock_pair",
            available_at_process_time=SRA_BASE_TIME,
            source_data_identifier="pair",
        ),
        FeatureAvailability(
            feature_name="liquidity_credibility",
            available_at_process_time=SRA_BASE_TIME + timedelta(seconds=1),
            source_data_identifier="lc",
        ),
        FeatureAvailability(
            feature_name="toxicity",
            available_at_process_time=SRA_BASE_TIME + timedelta(seconds=2),
            source_data_identifier="toxicity",
        ),
    )

    anchor = select_prediction_anchor(states, availability)

    assert anchor.event_index == 2
    assert anchor.observation.event_reference.process_time >= (
        availability[-1].available_at_process_time
    )


def test_dataset_builder_starts_labels_at_feature_availability_anchor() -> None:
    """The forward horizon starts from the selected anchor, not earlier shock time."""
    states = tuple(indexed_state(index, str(100 + index)) for index in range(7))
    observation = _builder().build_observation(
        feature_input=_feature_input(states),
        market_states=states,
        source_data_identifiers=("market-fixture-v1",),
    )

    assert observation.prediction_anchor_event_index == 3
    assert observation.labels[0].prediction_anchor_event_index == 3
    assert observation.feature_available_at_process_time <= (
        observation.prediction_anchor_process_time
    )
    assert observation.features.baseline.backward_features[0].recent_return == (
        Decimal(1) / Decimal(102)
    )


def test_feature_builder_is_invariant_to_future_price_path() -> None:
    """Changing states after the anchor changes labels but cannot change features."""
    first_path = tuple(indexed_state(index, "100" if index <= 3 else "101") for index in range(7))
    second_path = tuple(indexed_state(index, "100" if index <= 3 else "90") for index in range(7))
    builder = _builder()

    first = builder.build_observation(
        feature_input=_feature_input(first_path),
        market_states=first_path,
        source_data_identifiers=("market-fixture-v1",),
    )
    second = builder.build_observation(
        feature_input=_feature_input(second_path),
        market_states=second_path,
        source_data_identifiers=("market-fixture-v1",),
    )

    assert first.features == second.features
    assert first.labels != second.labels


def test_predeclared_failed_aggression_condition_uses_explicit_horizons() -> None:
    """The research helper applies declared DeltaAE and DeltaRR inequalities only."""
    states = tuple(indexed_state(index) for index in range(7))
    observation = _builder().build_observation(
        feature_input=_feature_input(states),
        market_states=states,
        source_data_identifiers=("market-fixture-v1",),
    )

    assert evaluate_failed_aggression_condition(
        observation.features,
        effectiveness_horizon_events=1,
        resiliency_horizon_events=1,
    )


def test_feature_availability_after_anchor_is_hard_error() -> None:
    """No individual feature may become available after the prediction anchor."""
    states = tuple(indexed_state(index) for index in range(7))
    observation = _builder().build_observation(
        feature_input=_feature_input(states),
        market_states=states,
        source_data_identifiers=("market-fixture-v1",),
    )
    late_time = observation.prediction_anchor_process_time + timedelta(seconds=1)
    late_availability = observation.features.feature_availability[0].model_copy(
        update={"available_at_process_time": late_time}
    )
    late = observation.features.model_copy(
        update={
            "feature_availability": (late_availability,),
            "feature_available_at_process_time": late_time,
        }
    )

    with pytest.raises(ValidationError, match="feature availability cannot follow"):
        FeatureBuildResult(
            features=late,
            prediction_anchor_event_index=observation.prediction_anchor_event_index,
            prediction_anchor_event_reference=observation.prediction_anchor_event_reference,
        )


def test_manifest_and_jsonl_export_are_deterministic() -> None:
    """Identical explicit manifest/config/data produce byte-identical sorted JSONL."""
    states = tuple(indexed_state(index) for index in range(7))
    builder = _builder()
    observation = builder.build_observation(
        feature_input=_feature_input(states),
        market_states=states,
        source_data_identifiers=("market-fixture-v1",),
    )
    dataset = builder.build_dataset(
        observations=(observation,),
        created_at=SRA_BASE_TIME,
        code_revision="deadbeef",
    )

    first = export_research_dataset_jsonl(dataset)
    second = export_research_dataset_jsonl(dataset)

    assert first == second
    assert first.endswith(b"\n")
    assert b'"record_type":"dataset_manifest"' in first
    assert b'"record_type":"research_observation"' in first
    assert b'"delta_ae_h1"' in first
    assert b'"label_unavailable_reason_h2":null' in first
    assert dataset.manifest.observation_count == 1
    assert dataset.manifest.dataset_version == "sra-research-dataset-v1"
