"""Deterministic standard-library export for immutable research datasets."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sra_nexus.research.dataset import ResearchDataset, ResearchObservation
from sra_nexus.research.models import CredibilityRawComponents


def export_research_dataset_jsonl(dataset: ResearchDataset) -> bytes:
    """Return canonical UTF-8 JSONL with one manifest followed by sorted rows.

    Object keys are lexicographically ordered, rows are ordered by instrument,
    prediction anchor, then observation ID, and unavailable values use JSON
    ``null``. The explicit manifest ``created_at`` is included, so callers obtain
    byte-identical output by supplying the same manifest and data.
    """
    records: list[dict[str, Any]] = [
        {
            "record_type": "dataset_manifest",
            "value": dataset.manifest.model_dump(mode="json"),
        }
    ]
    rows = tuple(
        _observation_columns(observation)
        for observation in sorted(dataset.observations, key=_row_key)
    )
    all_columns = tuple(sorted({key for row in rows for key in row}))
    records.extend(
        {
            "record_type": "research_observation",
            **{column: _json_value(row.get(column)) for column in all_columns},
        }
        for row in rows
    )
    text = "".join(
        json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for record in records
    )
    return text.encode("utf-8")


def _row_key(observation: ResearchObservation) -> tuple[str, int, str]:
    return (
        str(observation.instrument_id),
        observation.prediction_anchor_event_index,
        str(observation.observation_id),
    )


def _observation_columns(observation: ResearchObservation) -> dict[str, object]:
    features = observation.features
    baseline = features.baseline
    columns: dict[str, object] = {
        "observation_id": str(observation.observation_id),
        "instrument_id": str(observation.instrument_id),
        "venue": observation.venue,
        "feature_event_id": str(observation.feature_event_reference.event_id),
        "feature_exchange_time": observation.feature_exchange_time,
        "feature_process_time": observation.feature_process_time,
        "feature_available_at_process_time": observation.feature_available_at_process_time,
        "prediction_anchor_event_index": observation.prediction_anchor_event_index,
        "prediction_anchor_event_id": str(observation.prediction_anchor_event_reference.event_id),
        "prediction_anchor_exchange_time": observation.prediction_anchor_exchange_time,
        "prediction_anchor_process_time": observation.prediction_anchor_process_time,
        "shock_1_id": str(observation.shock_1_id),
        "shock_2_id": str(observation.shock_2_id),
        "pair_id": str(observation.pair_id),
        "feature_versions": ";".join(
            f"{item.feature_name}={item.version}" for item in observation.feature_version_bundle
        ),
        "feature_availability": ";".join(
            (
                f"{item.feature_name}@{item.available_at_process_time.isoformat()}"
                f"@{item.source_data_identifier}"
            )
            for item in features.feature_availability
        ),
        "source_data_identifiers": observation.source_data_identifiers,
        "direction": features.direction,
        "normalized_aggression_1": features.normalized_aggression_1,
        "normalized_aggression_2": features.normalized_aggression_2,
        "aggression_ratio": features.aggression_ratio,
        "spread": baseline.spread,
        "midprice": baseline.midprice,
        "microprice": baseline.microprice,
        "microprice_offset": baseline.microprice_offset,
        "obi": baseline.order_book_imbalance,
        "raw_bid_depth": baseline.raw_bid_depth,
        "raw_ask_depth": baseline.raw_ask_depth,
        "weighted_bid_depth": baseline.weighted_bid_depth,
        "weighted_ask_depth": baseline.weighted_ask_depth,
        "weighted_depth_weights": baseline.weighted_depth_weights,
        "liquidity_credibility_1": None,
        "liquidity_credibility_2": None,
        "delta_liquidity_credibility": None,
        "flow_persistence": None,
        "shock_persistence": None,
        "directional_flow_coverage": None,
        "unknown_flow_share": None,
        "raw_replenishment_failure": None,
        "bounded_replenishment_failure": None,
        "attacked_nnlp": None,
        "opposite_nnlp": None,
        "withdrawal_pressure": None,
        "spread_expansion_ratio": None,
        "bounded_spread_expansion": None,
        "volatility_jump_ratio": None,
        "bounded_volatility_jump": None,
        "toxicity_score": None,
        "delta_toxicity": None,
        "dataset_version": observation.dataset_version,
    }
    for effectiveness in features.effectiveness_by_horizon:
        suffix = f"h{effectiveness.horizon_events}"
        columns[f"ae_1_{suffix}"] = effectiveness.ae_1
        columns[f"ae_2_{suffix}"] = effectiveness.ae_2
        columns[f"delta_ae_{suffix}"] = effectiveness.delta_ae
        columns[f"relative_ae_change_{suffix}"] = effectiveness.relative_ae_change
    for resiliency in features.resiliency_by_horizon:
        suffix = f"h{resiliency.horizon_events}"
        columns[f"rr_1_{suffix}"] = resiliency.rr_1
        columns[f"rr_2_{suffix}"] = resiliency.rr_2
        columns[f"delta_rr_{suffix}"] = resiliency.delta_rr
    for recovery in features.recovery_time_deltas:
        threshold = _threshold_name(recovery.threshold)
        columns[f"delta_tau_{threshold}_events"] = recovery.delta_events
        columns[f"delta_tau_{threshold}_exchange_seconds"] = recovery.delta_exchange_seconds
        columns[f"delta_tau_{threshold}_process_seconds"] = recovery.delta_process_seconds
    for absorption in features.absorption_by_horizon:
        suffix = f"h{absorption.horizon_events}"
        columns[f"absorption_efficiency_1_{suffix}"] = absorption.absorption_efficiency_1
        columns[f"absorption_efficiency_2_{suffix}"] = absorption.absorption_efficiency_2
        columns[f"delta_absorption_efficiency_{suffix}"] = absorption.delta_absorption_efficiency
    for backward in baseline.backward_features:
        suffix = f"b{backward.horizon_events}"
        columns[f"recent_return_{suffix}"] = backward.recent_return
        columns[f"recent_volatility_{suffix}"] = backward.recent_volatility
    if features.liquidity_credibility is not None:
        credibility = features.liquidity_credibility
        columns.update(
            {
                "liquidity_credibility_1": credibility.liquidity_credibility_1,
                "liquidity_credibility_2": credibility.liquidity_credibility_2,
                "delta_liquidity_credibility": (credibility.delta_liquidity_credibility),
            }
        )
        _add_credibility_components(columns, credibility.raw_components_1, "1")
        _add_credibility_components(columns, credibility.raw_components_2, "2")
    if features.toxicity is not None:
        toxicity = features.toxicity
        columns.update(
            {
                "flow_persistence": toxicity.flow_persistence,
                "shock_persistence": toxicity.shock_persistence,
                "directional_flow_coverage": toxicity.directional_flow_coverage,
                "unknown_flow_share": toxicity.unknown_flow_share,
                "raw_replenishment_failure": toxicity.raw_replenishment_failure,
                "bounded_replenishment_failure": toxicity.bounded_replenishment_failure,
                "attacked_nnlp": toxicity.attacked_nnlp,
                "opposite_nnlp": toxicity.opposite_nnlp,
                "withdrawal_pressure": toxicity.withdrawal_pressure,
                "spread_expansion_ratio": toxicity.spread_expansion_ratio,
                "bounded_spread_expansion": toxicity.bounded_spread_expansion,
                "volatility_jump_ratio": toxicity.volatility_jump_ratio,
                "bounded_volatility_jump": toxicity.bounded_volatility_jump,
                "toxicity_score": toxicity.toxicity_score,
                "delta_toxicity": toxicity.delta_toxicity,
            }
        )
    for label in observation.labels:
        suffix = f"h{label.horizon_events}"
        columns[f"label_available_{suffix}"] = label.available
        if label.available:
            columns[f"forward_return_{suffix}"] = label.forward_return
            columns[f"reversal_adjusted_return_{suffix}"] = label.reversal_adjusted_return
            columns[f"maximum_favorable_excursion_{suffix}"] = (
                label.maximum_favorable_excursion.magnitude
            )
            columns[f"maximum_adverse_excursion_{suffix}"] = (
                label.maximum_adverse_excursion.magnitude
            )
            columns[f"events_to_max_favorable_excursion_{suffix}"] = (
                label.events_to_max_favorable_excursion
            )
            columns[f"exchange_seconds_to_max_favorable_excursion_{suffix}"] = (
                label.exchange_seconds_to_max_favorable_excursion
            )
            columns[f"reversal_success_{suffix}"] = label.reversal_success
            columns[f"label_unavailable_reason_{suffix}"] = None
        else:
            columns[f"forward_return_{suffix}"] = None
            columns[f"reversal_adjusted_return_{suffix}"] = None
            columns[f"maximum_favorable_excursion_{suffix}"] = None
            columns[f"maximum_adverse_excursion_{suffix}"] = None
            columns[f"events_to_max_favorable_excursion_{suffix}"] = None
            columns[f"exchange_seconds_to_max_favorable_excursion_{suffix}"] = None
            columns[f"reversal_success_{suffix}"] = None
            columns[f"label_unavailable_reason_{suffix}"] = label.unavailable_reason
    return columns


def _add_credibility_components(
    columns: dict[str, object],
    components: CredibilityRawComponents,
    suffix: str,
) -> None:
    for name in (
        "quantity_weighted_order_credibility",
        "shock_executed_fraction",
        "shock_withdrawal_fraction",
        "order_survival_fraction",
        "quantity_survival_fraction",
        "replenishment_component",
        "cycle_component",
        "credible_depth",
        "credible_depth_ratio",
    ):
        columns[f"{name}_{suffix}"] = getattr(components, name)


def _threshold_name(threshold: Decimal) -> str:
    percent = threshold * Decimal(100)
    normalized = format(percent.normalize(), "f")
    return normalized.replace("-", "neg_").replace(".", "_")


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
