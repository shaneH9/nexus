"""Causal research observations, manifests, and focused dataset orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel, NonBlankStr, UtcDatetime
from sra_nexus.common.types import (
    InstrumentId,
    ResearchObservationId,
    ShockId,
    ShockPairId,
)
from sra_nexus.research.features import (
    SRAFeatureInput,
    SRAFeatureSnapshotBuilder,
    feature_availability_from_input,
    select_prediction_anchor,
)
from sra_nexus.research.labels import (
    ForwardLabel,
    ForwardLabelConfig,
    LabelBuilder,
)
from sra_nexus.research.models import (
    RESEARCH_DATASET_VERSION,
    FeatureSnapshotConfig,
    FeatureVersion,
    SRAFeatureSnapshot,
)
from sra_nexus.sra.state import MarketEventReference
from sra_nexus.sra.toxicity import IndexedMarketStateObservation

_RESEARCH_OBSERVATION_NAMESPACE = UUID("2f737261-2d72-4573-9561-726368763100")


class ResearchDatasetConfig(ContractModel):
    """Versioned feature and label policy for one historical dataset."""

    feature_config: FeatureSnapshotConfig = Field(default_factory=FeatureSnapshotConfig)
    label_config: ForwardLabelConfig = Field(default_factory=ForwardLabelConfig)
    dataset_version: NonBlankStr = RESEARCH_DATASET_VERSION


class ResearchObservation(ContractModel):
    """One immutable causal feature snapshot with logically separate future labels."""

    observation_id: ResearchObservationId
    instrument_id: InstrumentId
    venue: NonBlankStr
    feature_event_reference: MarketEventReference
    feature_exchange_time: UtcDatetime
    feature_process_time: UtcDatetime
    feature_available_at_process_time: UtcDatetime
    prediction_anchor_event_index: int = Field(ge=0)
    prediction_anchor_event_reference: MarketEventReference
    prediction_anchor_exchange_time: UtcDatetime
    prediction_anchor_process_time: UtcDatetime
    shock_1_id: ShockId
    shock_2_id: ShockId
    pair_id: ShockPairId
    feature_version_bundle: tuple[FeatureVersion, ...]
    features: SRAFeatureSnapshot
    labels: tuple[ForwardLabel, ...]
    maximum_label_horizon_events: int = Field(gt=0)
    label_window_end_event_index: int = Field(ge=0)
    source_data_identifiers: tuple[NonBlankStr, ...]
    dataset_version: NonBlankStr = RESEARCH_DATASET_VERSION

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        """Enforce identity, anchor, availability, and label-boundary invariants."""
        feature_reference = self.feature_event_reference
        anchor_reference = self.prediction_anchor_event_reference
        if (
            feature_reference.instrument_id != self.instrument_id
            or anchor_reference.instrument_id != self.instrument_id
            or feature_reference.venue != self.venue
            or anchor_reference.venue != self.venue
        ):
            raise ValueError("research references must share observation instrument and venue")
        if (
            self.feature_exchange_time != feature_reference.exchange_time
            or self.feature_process_time != feature_reference.process_time
        ):
            raise ValueError("feature clocks must be copied from feature event reference")
        if (
            self.prediction_anchor_exchange_time != anchor_reference.exchange_time
            or self.prediction_anchor_process_time != anchor_reference.process_time
        ):
            raise ValueError("prediction-anchor clocks must be copied from its reference")
        if self.feature_available_at_process_time != (
            self.features.feature_available_at_process_time
        ):
            raise ValueError("observation and snapshot feature availability must agree")
        if self.feature_available_at_process_time > self.prediction_anchor_process_time:
            raise ValueError("features cannot become available after prediction anchor")
        if any(
            item.available_at_process_time > self.prediction_anchor_process_time
            for item in self.features.feature_availability
        ):
            raise ValueError("individual feature availability cannot cross prediction anchor")
        if self.label_window_end_event_index != (
            self.prediction_anchor_event_index + self.maximum_label_horizon_events
        ):
            raise ValueError("label window end must use the configured maximum horizon")
        horizons = tuple(label.horizon_events for label in self.labels)
        if horizons != tuple(sorted(set(horizons))) or max(horizons) != (
            self.maximum_label_horizon_events
        ):
            raise ValueError("labels must contain unique horizons through the configured maximum")
        if any(
            label.prediction_anchor_event_index != self.prediction_anchor_event_index
            or label.prediction_anchor_event_reference != anchor_reference
            for label in self.labels
        ):
            raise ValueError("every label must begin from the prediction anchor")
        version_names = tuple(item.feature_name for item in self.feature_version_bundle)
        if version_names != tuple(sorted(set(version_names))):
            raise ValueError("feature version bundle must be unique and sorted by name")
        if not self.source_data_identifiers or self.source_data_identifiers != tuple(
            sorted(set(self.source_data_identifiers))
        ):
            raise ValueError("source data identifiers must be nonempty, unique, and sorted")
        return self


class DatasetManifest(ContractModel):
    """Self-describing identity, coverage, and configuration for a research export."""

    dataset_version: NonBlankStr
    created_at: UtcDatetime
    instruments: tuple[InstrumentId, ...]
    venues: tuple[NonBlankStr, ...]
    start_time: UtcDatetime
    end_time: UtcDatetime
    observation_count: int = Field(ge=0)
    feature_versions: tuple[FeatureVersion, ...]
    label_config: ForwardLabelConfig
    dataset_config: ResearchDatasetConfig
    source_data_identifiers: tuple[NonBlankStr, ...]
    code_revision: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        """Require chronological coverage and canonical deterministic collections."""
        if self.end_time < self.start_time:
            raise ValueError("dataset end_time cannot precede start_time")
        if self.dataset_version != self.dataset_config.dataset_version:
            raise ValueError("manifest and dataset config versions must agree")
        if self.label_config != self.dataset_config.label_config:
            raise ValueError("manifest and dataset label configurations must agree")
        if self.venues != tuple(sorted(set(self.venues))):
            raise ValueError("manifest venues must be unique and sorted")
        if tuple(str(item) for item in self.instruments) != tuple(
            sorted(set(str(item) for item in self.instruments))
        ):
            raise ValueError("manifest instruments must be unique and sorted")
        if self.source_data_identifiers != tuple(sorted(set(self.source_data_identifiers))):
            raise ValueError("manifest source identifiers must be unique and sorted")
        return self


class ResearchDataset(ContractModel):
    """Immutable manifest and deterministic collection of historical observations."""

    manifest: DatasetManifest
    observations: tuple[ResearchObservation, ...]

    @model_validator(mode="after")
    def validate_dataset(self) -> Self:
        """Require manifest coverage to describe every observation exactly."""
        if self.manifest.observation_count != len(self.observations):
            raise ValueError("manifest observation count must match dataset rows")
        if not self.observations:
            if self.manifest.observation_count != 0:
                raise ValueError("empty dataset manifest must report zero observations")
            return self
        if any(
            observation.dataset_version != self.manifest.dataset_version
            for observation in self.observations
        ):
            raise ValueError("all observations must share manifest dataset version")
        expected_instruments = tuple(
            sorted({item.instrument_id for item in self.observations}, key=str)
        )
        expected_venues = tuple(sorted({item.venue for item in self.observations}))
        times = tuple(item.prediction_anchor_process_time for item in self.observations)
        if self.manifest.instruments != expected_instruments:
            raise ValueError("manifest instruments do not match observations")
        if self.manifest.venues != expected_venues:
            raise ValueError("manifest venues do not match observations")
        if self.manifest.start_time != min(times) or self.manifest.end_time != max(times):
            raise ValueError("manifest time coverage does not match prediction anchors")
        return self


class ResearchDatasetBuilder:
    """Coordinate feature availability, anchoring, labels, and no-lookahead validation."""

    def __init__(self, config: ResearchDatasetConfig | None = None) -> None:
        """Configure feature and label builders without model training or persistence."""
        self._config = ResearchDatasetConfig() if config is None else config
        self._feature_builder = SRAFeatureSnapshotBuilder(self._config.feature_config)
        self._label_builder = LabelBuilder(self._config.label_config)

    @property
    def config(self) -> ResearchDatasetConfig:
        """Return the immutable dataset construction policy."""
        return self._config

    def build_observation(
        self,
        *,
        feature_input: SRAFeatureInput,
        market_states: Sequence[IndexedMarketStateObservation],
        source_data_identifiers: Sequence[str],
    ) -> ResearchObservation:
        """Construct one deterministic row while exposing future states only to labels."""
        relevant_states = tuple(
            state
            for state in market_states
            if state.observation.event_reference.instrument_id
            == feature_input.comparison_event_reference.instrument_id
            and state.observation.event_reference.venue
            == feature_input.comparison_event_reference.venue
        )
        anchor = select_prediction_anchor(
            relevant_states,
            feature_availability_from_input(feature_input),
            minimum_event_index=feature_input.comparison_event_index,
        )
        anchor_index = _required(anchor.event_index)
        historical_states = tuple(
            state for state in relevant_states if _required(state.event_index) <= anchor_index
        )
        feature_result = self._feature_builder.build(feature_input, historical_states)
        comparison = feature_input.comparison
        direction = _required(comparison.direction)
        anchor_reference = feature_result.prediction_anchor_event_reference
        anchor_index = feature_result.prediction_anchor_event_index
        labels = self._label_builder.build(
            direction=direction,
            prediction_anchor_event_index=anchor_index,
            prediction_anchor_event_reference=anchor_reference,
            market_states=market_states,
        )
        pair_id = _required(comparison.pair_id)
        identifiers = tuple(sorted(set(source_data_identifiers)))
        observation_id = _derive_observation_id(
            comparison.instrument_id,
            pair_id,
            anchor_index,
            self._config.dataset_version,
        )
        versions = _feature_versions(feature_input, feature_result.features)
        return ResearchObservation(
            observation_id=observation_id,
            instrument_id=_required(comparison.instrument_id),
            venue=anchor_reference.venue,
            feature_event_reference=anchor_reference,
            feature_exchange_time=anchor_reference.exchange_time,
            feature_process_time=anchor_reference.process_time,
            feature_available_at_process_time=(
                feature_result.features.feature_available_at_process_time
            ),
            prediction_anchor_event_index=anchor_index,
            prediction_anchor_event_reference=anchor_reference,
            prediction_anchor_exchange_time=anchor_reference.exchange_time,
            prediction_anchor_process_time=anchor_reference.process_time,
            shock_1_id=comparison.shock_1_id,
            shock_2_id=comparison.shock_2_id,
            pair_id=pair_id,
            feature_version_bundle=versions,
            features=feature_result.features,
            labels=labels,
            maximum_label_horizon_events=max(self._config.label_config.horizons_events),
            label_window_end_event_index=(
                anchor_index + max(self._config.label_config.horizons_events)
            ),
            source_data_identifiers=identifiers,
            dataset_version=self._config.dataset_version,
        )

    def build_dataset(
        self,
        *,
        observations: Sequence[ResearchObservation],
        created_at: UtcDatetime,
        source_data_identifiers: Sequence[str] = (),
        code_revision: str | None = None,
    ) -> ResearchDataset:
        """Create a self-describing immutable dataset from completed rows."""
        values = tuple(observations)
        if not values:
            raise ValueError("research dataset requires at least one observation")
        instruments = tuple(sorted({item.instrument_id for item in values}, key=str))
        venues = tuple(sorted({item.venue for item in values}))
        times = tuple(item.prediction_anchor_process_time for item in values)
        version_values = tuple(
            version for item in values for version in item.feature_version_bundle
        )
        versions_by_name: dict[str, FeatureVersion] = {}
        for version in version_values:
            previous = versions_by_name.get(version.feature_name)
            if previous is not None and previous.version != version.version:
                raise ValueError("one dataset cannot mix versions of a feature family")
            versions_by_name[version.feature_name] = version
        versions = tuple(versions_by_name[name] for name in sorted(versions_by_name))
        sources = tuple(
            sorted(
                {
                    *source_data_identifiers,
                    *(source for item in values for source in item.source_data_identifiers),
                }
            )
        )
        manifest = DatasetManifest(
            dataset_version=self._config.dataset_version,
            created_at=created_at,
            instruments=instruments,
            venues=venues,
            start_time=min(times),
            end_time=max(times),
            observation_count=len(values),
            feature_versions=versions,
            label_config=self._config.label_config,
            dataset_config=self._config,
            source_data_identifiers=sources,
            code_revision=code_revision,
        )
        return ResearchDataset(manifest=manifest, observations=values)


def _feature_versions(
    feature_input: SRAFeatureInput,
    snapshot: SRAFeatureSnapshot,
) -> tuple[FeatureVersion, ...]:
    comparison = feature_input.comparison
    versions = {
        "dataset_features": snapshot.feature_version,
        "failed_aggression_comparison": comparison.feature_version,
    }
    if comparison.pair is not None:
        versions["shock_pair"] = comparison.pair.comparison_version
    for effectiveness in comparison.effectiveness_by_horizon:
        versions["aggressor_effectiveness"] = effectiveness.ae_2.effectiveness_version
    for absorption in comparison.absorption_efficiency_by_horizon:
        versions["absorption_efficiency"] = absorption.abs_eff_2.absorption_version
    if feature_input.liquidity_credibility_2 is not None:
        versions["liquidity_credibility"] = feature_input.liquidity_credibility_2.feature_version
    if feature_input.liquidity_credibility_comparison is not None:
        versions["liquidity_credibility_comparison"] = (
            feature_input.liquidity_credibility_comparison.comparison_version
        )
    if feature_input.toxicity is not None:
        versions["toxicity"] = feature_input.toxicity.feature_version
    if feature_input.toxicity_comparison is not None:
        versions["toxicity_comparison"] = feature_input.toxicity_comparison.comparison_version
    return tuple(
        FeatureVersion(feature_name=name, version=version)
        for name, version in sorted(versions.items())
    )


def _derive_observation_id(
    instrument_id: InstrumentId | None,
    pair_id: ShockPairId,
    anchor_index: int,
    dataset_version: str,
) -> ResearchObservationId:
    if instrument_id is None:
        raise ValueError("available comparison requires instrument_id")
    identity = f"{instrument_id}|{pair_id}|{anchor_index}|{dataset_version}"
    return ResearchObservationId(uuid5(_RESEARCH_OBSERVATION_NAMESPACE, identity))


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("required research dataset value is unavailable")
    return value
