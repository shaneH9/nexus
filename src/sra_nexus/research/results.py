"""Immutable outputs for preregistered historical hypothesis evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeFiniteFloat,
    UtcDatetime,
)
from sra_nexus.common.types import ResearchRunId, ResearchSplitId
from sra_nexus.market_data.historical import HistoricalDataManifest, HistoricalDataQualityReport
from sra_nexus.research.dataset import DatasetManifest
from sra_nexus.research.experiment import (
    HypothesisStatus,
    ResearchExperimentSpec,
    ResearchHypothesis,
)
from sra_nexus.research.models import UnitIntervalDecimal
from sra_nexus.research.permutation import PermutationTestResult
from sra_nexus.research.splits import WalkForwardSplit

HYPOTHESIS_RESULT_VERSION = "historical-hypothesis-result-v1"
RESEARCH_REPORT_VERSION = "historical-research-report-v1"


class FoldHypothesisResult(ContractModel):
    """One test-fold result or explicit fold-level unavailability."""

    split_id: ResearchSplitId
    test_start: UtcDatetime
    test_end: UtcDatetime
    observation_count: int = Field(ge=0)
    qualifying_count: int = Field(ge=0)
    permutation_block_count: int = Field(ge=0)
    observed_statistic: ExactDecimal | None
    permutation_result: PermutationTestResult | None
    status: HypothesisStatus
    unavailable_reason: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        """Require evidence only for run folds and reasons for unavailable folds."""
        if self.qualifying_count > self.observation_count:
            raise ValueError("qualifying fold count cannot exceed observations")
        if self.status is HypothesisStatus.RUN:
            if self.permutation_result is None or self.observed_statistic is None:
                raise ValueError("run fold requires a complete permutation result")
            if self.unavailable_reason is not None:
                raise ValueError("run fold cannot have an unavailable reason")
        elif self.permutation_result is not None or self.observed_statistic is not None:
            raise ValueError("non-run fold cannot contain permutation evidence")
        elif self.unavailable_reason is None:
            raise ValueError("non-run fold requires an explicit reason")
        return self


class HypothesisTestResult(ContractModel):
    """Declared hypothesis, pooled null evidence, corrections, and every fold result."""

    hypothesis: ResearchHypothesis
    experiment_hash: NonBlankStr = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: ResearchRunId
    status: HypothesisStatus
    statistic: NonBlankStr
    forward_horizon_events: int = Field(gt=0)
    observed_value: ExactDecimal | None
    null_mean: ExactDecimal | None
    null_standard_deviation: Decimal | None = Field(default=None, ge=0)
    observed_minus_null_mean: ExactDecimal | None
    standardized_effect: ExactDecimal | None
    raw_p_value: UnitIntervalDecimal | None
    bonferroni_p_value: UnitIntervalDecimal | None
    fdr_p_value: UnitIntervalDecimal | None
    observation_count: int = Field(ge=0)
    qualifying_count: int = Field(ge=0)
    permutation_block_count: int = Field(ge=0)
    permutation_count: int = Field(ge=0)
    pooled_permutation_result: PermutationTestResult | None
    fold_results: tuple[FoldHypothesisResult, ...]
    unavailable_reason: NonBlankStr | None = None
    result_version: NonBlankStr = HYPOTHESIS_RESULT_VERSION

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Keep run and unavailable result shapes explicit and internally aligned."""
        evidence = (
            self.observed_value,
            self.null_mean,
            self.null_standard_deviation,
            self.observed_minus_null_mean,
            self.raw_p_value,
            self.bonferroni_p_value,
            self.fdr_p_value,
            self.pooled_permutation_result,
        )
        if self.qualifying_count > self.observation_count:
            raise ValueError("qualifying result count cannot exceed observations")
        if self.status is HypothesisStatus.RUN:
            if any(value is None for value in evidence):
                raise ValueError("run hypothesis requires pooled evidence and corrections")
            if self.unavailable_reason is not None:
                raise ValueError("run hypothesis cannot have an unavailable reason")
        elif any(value is not None for value in evidence):
            raise ValueError("non-run hypothesis cannot contain pooled evidence")
        elif self.unavailable_reason is None:
            raise ValueError("non-run hypothesis requires a reason")
        return self


class HistoricalResearchReport(ContractModel):
    """Deterministic machine-readable output excluding wall-clock runtime."""

    experiment: ResearchExperimentSpec
    experiment_hash: NonBlankStr = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: ResearchRunId
    code_revision: NonBlankStr
    data_manifest: HistoricalDataManifest
    data_quality: HistoricalDataQualityReport
    dataset_manifest: DatasetManifest
    walk_forward_splits: tuple[WalkForwardSplit, ...]
    hypothesis_results: tuple[HypothesisTestResult, ...]
    events_processed: int = Field(ge=0)
    observations_generated: int = Field(ge=0)
    limitations: tuple[NonBlankStr, ...]
    report_version: NonBlankStr = RESEARCH_REPORT_VERSION

    @model_validator(mode="after")
    def validate_declared_hypotheses(self) -> Self:
        """Forbid silent omission or reordering of preregistered hypotheses."""
        declared = tuple(item.hypothesis_id for item in self.experiment.hypotheses)
        reported = tuple(item.hypothesis.hypothesis_id for item in self.hypothesis_results)
        if declared != reported:
            raise ValueError("report must preserve every declared hypothesis in order")
        if self.events_processed != self.data_manifest.event_count:
            raise ValueError("report event count must equal historical manifest")
        if self.observations_generated != self.dataset_manifest.observation_count:
            raise ValueError("report observation count must equal dataset manifest")
        return self


class ResearchRunArtifacts(ContractModel):
    """Paths and deterministic report returned by the public runner."""

    output_directory: NonBlankStr
    experiment_json: NonBlankStr
    data_manifest_json: NonBlankStr
    data_quality_json: NonBlankStr
    dataset_manifest_json: NonBlankStr
    results_json: NonBlankStr
    markdown_report: NonBlankStr
    elapsed_processing_seconds: NonNegativeFiniteFloat
    report: HistoricalResearchReport


class HistoricalDryRunReport(ContractModel):
    """Pre-flight identity and estimated scope without replay or statistical testing."""

    experiment_hash: NonBlankStr = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: NonBlankStr
    source_files: tuple[NonBlankStr, ...]
    source_hashes: tuple[NonBlankStr, ...]
    estimated_normalized_events: int = Field(ge=0)
    declared_hypotheses: tuple[NonBlankStr, ...]
    warnings: tuple[NonBlankStr, ...] = ()
