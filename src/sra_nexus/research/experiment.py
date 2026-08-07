"""Immutable preregistration contracts for historical SRA experiments."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel, ExactDecimal, NonBlankStr, UtcDatetime
from sra_nexus.common.types import InstrumentId
from sra_nexus.market_data.historical import (
    HistoricalBoundaryKind,
    HistoricalFileIdentity,
    HistoricalSessionSegment,
)
from sra_nexus.market_data.providers.databento import DatabentoMboCsvConfig
from sra_nexus.research.dataset import ResearchDatasetConfig
from sra_nexus.research.enums import PermutationAlternative
from sra_nexus.research.permutation import PermutationTestConfig
from sra_nexus.research.splits import WalkForwardConfig
from sra_nexus.sra.service import ShockResearchConfig
from sra_nexus.sra.shock_pair import ShockPairConfig

EXPERIMENT_SPEC_VERSION = "historical-research-experiment-v1"


class HypothesisStatus(StrEnum):
    """Explicit lifecycle status for every preregistered hypothesis."""

    RUN = "RUN"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED_VALIDATION = "FAILED_VALIDATION"


class ResearchHypothesisKind(StrEnum):
    """Supported non-fitted hypothesis families."""

    FAILED_AGGRESSION = "FAILED_AGGRESSION"
    FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY = "FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY"
    LOWER_TOXICITY = "LOWER_TOXICITY"
    CONTINUOUS_FEATURE = "CONTINUOUS_FEATURE"
    BASELINE_FEATURE = "BASELINE_FEATURE"


class ResearchFeatureName(StrEnum):
    """Frozen fields available to preregistered, transparent statistics."""

    DELTA_AE = "DELTA_AE"
    DELTA_LIQUIDITY_CREDIBILITY = "DELTA_LIQUIDITY_CREDIBILITY"
    DELTA_TOXICITY = "DELTA_TOXICITY"
    ORDER_BOOK_IMBALANCE = "ORDER_BOOK_IMBALANCE"
    MICROPRICE_OFFSET = "MICROPRICE_OFFSET"
    RECENT_RETURN = "RECENT_RETURN"


class ResearchStatisticName(StrEnum):
    """Existing Milestone K statistics exposed to experiment configuration."""

    CONDITIONAL_MEAN_REVERSAL_RETURN = "ConditionalMeanReversalReturn"
    COVARIANCE_ASSOCIATION = "CovarianceAssociation"


class ExpectedEffectDirection(StrEnum):
    """Preregistered sign expectation, distinct from permutation-tail mechanics."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class HistoricalSourceSpec(ContractModel):
    """One provider adapter configuration plus expected immutable file identities."""

    source_id: NonBlankStr
    provider: NonBlankStr = "DATABENTO"
    adapter: DatabentoMboCsvConfig
    expected_files: tuple[HistoricalFileIdentity, ...]

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        """Require expected identities for every configured local source file."""
        if self.provider != "DATABENTO":
            raise ValueError("Milestone L supports only the Databento historical adapter")
        if not self.expected_files:
            raise ValueError("historical source requires expected SHA-256 identities")
        expected_names = tuple(item.source_filename for item in self.expected_files)
        configured_names = tuple(Path(value).name for value in self.adapter.source_paths)
        if len(set(expected_names)) != len(expected_names):
            raise ValueError("expected historical filenames must be unique")
        if tuple(sorted(expected_names)) != tuple(sorted(configured_names)):
            raise ValueError("expected source filenames must match configured source paths")
        return self


class HistoricalStructuralBoundary(ContractModel):
    """Configured known halt/corporate-action boundary in UTC market time."""

    instrument_id: InstrumentId
    venue: NonBlankStr
    exchange_time: UtcDatetime
    kind: HistoricalBoundaryKind
    description: NonBlankStr


class NamedPermutationConfig(ContractModel):
    """Stable reference to one fully frozen Milestone K null configuration."""

    config_id: NonBlankStr
    configuration: PermutationTestConfig


class ResearchHypothesis(ContractModel):
    """One preregistered condition/feature, statistic, horizon, and null reference."""

    hypothesis_id: NonBlankStr
    description: NonBlankStr
    kind: ResearchHypothesisKind
    feature: ResearchFeatureName | None = None
    expected_direction: ExpectedEffectDirection
    statistic: ResearchStatisticName
    forward_horizon_events: int = Field(gt=0)
    alternative: PermutationAlternative
    permutation_config_reference: NonBlankStr
    effectiveness_horizon_events: int | None = Field(default=None, gt=0)
    resiliency_horizon_events: int | None = Field(default=None, gt=0)
    backward_horizon_events: int | None = Field(default=None, gt=0)
    delta_ae_threshold: ExactDecimal = Decimal(0)
    delta_rr_threshold: ExactDecimal = Decimal(0)
    liquidity_credibility_threshold: ExactDecimal = Decimal(0)
    toxicity_threshold: ExactDecimal = Decimal(0)

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        """Require an exact, non-searching definition for the selected family."""
        condition_kinds = {
            ResearchHypothesisKind.FAILED_AGGRESSION,
            ResearchHypothesisKind.FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY,
            ResearchHypothesisKind.LOWER_TOXICITY,
        }
        if self.kind in condition_kinds:
            if self.statistic is not ResearchStatisticName.CONDITIONAL_MEAN_REVERSAL_RETURN:
                raise ValueError("condition hypotheses require the conditional-mean statistic")
            if self.effectiveness_horizon_events is None or self.resiliency_horizon_events is None:
                raise ValueError("failed-aggression hypotheses require both exact horizons")
        else:
            if self.statistic is not ResearchStatisticName.COVARIANCE_ASSOCIATION:
                raise ValueError("continuous hypotheses require covariance association")
            if self.feature is None:
                raise ValueError("continuous hypotheses require one explicit feature")
        if (
            self.feature is ResearchFeatureName.RECENT_RETURN
            and self.backward_horizon_events is None
        ):
            raise ValueError("recent-return hypotheses require one exact backward horizon")
        return self


class ResearchOutputConfig(ContractModel):
    """Local immutable-artifact policy for one experiment."""

    root_directory: NonBlankStr = "research_runs"
    reuse_identical_completed_run: bool = True


class ResearchExperimentSpec(ContractModel):
    """Complete immutable historical scope, SRA policy, folds, and hypotheses."""

    experiment_name: NonBlankStr
    experiment_version: NonBlankStr
    created_at: UtcDatetime
    instruments: tuple[InstrumentId, ...]
    venues: tuple[NonBlankStr, ...]
    research_start: UtcDatetime
    research_end: UtcDatetime
    sessions: tuple[HistoricalSessionSegment, ...]
    warmup_event_count: int = Field(default=0, ge=0)
    sources: tuple[HistoricalSourceSpec, ...]
    structural_boundaries: tuple[HistoricalStructuralBoundary, ...] = ()
    shock_research_config: ShockResearchConfig
    shock_pair_config: ShockPairConfig
    dataset_config: ResearchDatasetConfig
    walk_forward_config: WalkForwardConfig
    permutation_configurations: tuple[NamedPermutationConfig, ...]
    hypotheses: tuple[ResearchHypothesis, ...]
    output: ResearchOutputConfig = Field(default_factory=ResearchOutputConfig)
    seed: int
    limitations: tuple[NonBlankStr, ...] = ()
    spec_version: NonBlankStr = EXPERIMENT_SPEC_VERSION

    @model_validator(mode="after")
    def validate_experiment(self) -> Self:
        """Freeze aligned scopes, horizons, references, and unique preregistration IDs."""
        if self.research_start >= self.research_end:
            raise ValueError("research_start must precede research_end")
        if not self.instruments or not self.venues or not self.sessions or not self.sources:
            raise ValueError("experiment scope and sources must be nonempty")
        if not self.hypotheses or not self.permutation_configurations:
            raise ValueError("experiment must preregister hypotheses and null configurations")
        _require_unique(tuple(item.source_id for item in self.sources), "source IDs")
        _require_unique(
            tuple(item.config_id for item in self.permutation_configurations),
            "permutation config IDs",
        )
        _require_unique(tuple(item.hypothesis_id for item in self.hypotheses), "hypothesis IDs")
        source_policies = {
            (
                item.provider,
                item.adapter.provider_schema_version,
                item.adapter.normalization_version,
                item.adapter.process_time_policy,
            )
            for item in self.sources
        }
        if len(source_policies) != 1:
            raise ValueError(
                "one experiment requires a uniform provider/schema/normalization/clock policy"
            )
        configs = {item.config_id: item.configuration for item in self.permutation_configurations}
        for hypothesis in self.hypotheses:
            config = configs.get(hypothesis.permutation_config_reference)
            if config is None:
                raise ValueError("hypothesis references an unknown permutation configuration")
            if config.statistic_name != hypothesis.statistic.value:
                raise ValueError("hypothesis statistic must match referenced permutation config")
            if config.alternative is not hypothesis.alternative:
                raise ValueError("hypothesis alternative must match referenced permutation config")
            if config.seed != self.seed:
                raise ValueError("permutation seeds must equal the frozen experiment seed")
            if hypothesis.forward_horizon_events not in (
                self.dataset_config.label_config.horizons_events
            ):
                raise ValueError("hypothesis horizon must be present in label configuration")
        maximum_horizon = max(self.dataset_config.label_config.horizons_events)
        if self.walk_forward_config.maximum_label_horizon_events != maximum_horizon:
            raise ValueError("walk-forward maximum horizon must match dataset label maximum")
        if any(
            item.configuration.max_label_horizon_events != maximum_horizon
            for item in self.permutation_configurations
        ):
            raise ValueError("permutation maximum horizon must match dataset label maximum")
        if self.shock_pair_config.required_impact_horizons_events != (
            self.shock_research_config.impact.horizons_events
        ):
            raise ValueError("shock-pair impact horizons must match shock research horizons")
        if self.shock_pair_config.required_resiliency_horizons_events != (
            self.shock_research_config.resiliency.recovery_horizons_events
        ):
            raise ValueError("shock-pair resiliency horizons must match shock research horizons")
        return self


def canonical_experiment_json(spec: ResearchExperimentSpec) -> str:
    """Serialize semantic configuration while canonicalizing local locations."""
    payload = spec.model_dump(mode="json")
    sources = payload["sources"]
    if not isinstance(sources, list):
        raise TypeError("validated experiment sources must serialize as a list")
    for source_payload, source in zip(sources, spec.sources, strict=True):
        if not isinstance(source_payload, dict):
            raise TypeError("validated historical source must serialize as an object")
        adapter_payload = source_payload["adapter"]
        if not isinstance(adapter_payload, dict):
            raise TypeError("validated adapter must serialize as an object")
        adapter_payload["source_paths"] = [item.source_filename for item in source.expected_files]
    output = payload["output"]
    if not isinstance(output, dict):
        raise TypeError("validated experiment output must serialize as an object")
    output["root_directory"] = "<OUTPUT_ROOT>"
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def calculate_experiment_hash(spec: ResearchExperimentSpec) -> str:
    """Return SHA-256 of the exact canonical experiment specification."""
    return hashlib.sha256(canonical_experiment_json(spec).encode("utf-8")).hexdigest()


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"experiment {label} must be unique")
