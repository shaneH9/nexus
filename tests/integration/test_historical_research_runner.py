"""Offline full-pipeline tests for the frozen historical research runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from sra_nexus.market_data.historical import HistoricalFileIdentity
from sra_nexus.market_data.providers.databento import DatabentoMboCsvConfig
from sra_nexus.market_data.providers.databento.adapter import (
    HistoricalDataValidationError,
    sha256_file,
)
from sra_nexus.research.enums import PermutationAlternative
from sra_nexus.research.experiment import (
    ExpectedEffectDirection,
    HypothesisStatus,
    ResearchExperimentSpec,
    ResearchHypothesis,
    ResearchHypothesisKind,
    ResearchOutputConfig,
    ResearchStatisticName,
    calculate_experiment_hash,
)
from sra_nexus.research.historical_runner import HistoricalResearchRunner
from sra_nexus.research.run import load_experiment

EXPERIMENT = Path("examples/historical/fixture_experiment.json")
FIXTURE = Path("tests/fixtures/historical/databento_mbo_fixture.csv")


def _spec(output: Path) -> ResearchExperimentSpec:
    base = load_experiment(EXPERIMENT)
    return base.model_copy(
        update={
            "output": ResearchOutputConfig(
                root_directory=str(output),
                reuse_identical_completed_run=True,
            )
        }
    )


def test_full_pipeline_writes_all_artifacts_and_uses_only_test_folds(
    tmp_path: Path,
) -> None:
    """Exercise adapter through replay, SRA, pairs, folds, nulls, and both reports."""
    spec = _spec(tmp_path / "runs")

    artifacts = HistoricalResearchRunner(spec).run()

    assert artifacts.report.events_processed == 41
    assert artifacts.report.observations_generated == 7
    assert len(artifacts.report.walk_forward_splits) == 3
    assert artifacts.elapsed_processing_seconds >= 0
    assert {
        Path(artifacts.experiment_json).name,
        Path(artifacts.data_manifest_json).name,
        Path(artifacts.data_quality_json).name,
        Path(artifacts.dataset_manifest_json).name,
        Path(artifacts.results_json).name,
        Path(artifacts.markdown_report).name,
    } == {
        "experiment.json",
        "data_manifest.json",
        "data_quality.json",
        "dataset_manifest.json",
        "results.json",
        "report.md",
    }
    for result in artifacts.report.hypothesis_results:
        assert result.status is HypothesisStatus.RUN
        assert result.observation_count == sum(
            fold.observation_count
            for fold in result.fold_results
            if fold.status is HypothesisStatus.RUN
        )
        configured = next(
            item.configuration
            for item in spec.permutation_configurations
            if item.config_id == result.hypothesis.permutation_config_reference
        )
        assert result.pooled_permutation_result is not None
        assert result.pooled_permutation_result.configuration == configured
        assert result.pooled_permutation_result.seed == configured.seed
        assert result.pooled_permutation_result.block_size == configured.block_size
        assert result.pooled_permutation_result.alternative is configured.alternative


def test_identical_experiment_reuses_byte_identical_substantive_results(
    tmp_path: Path,
) -> None:
    """Exclude runtime only; all machine-readable research evidence is reproducible."""
    spec = _spec(tmp_path / "runs")
    first = HistoricalResearchRunner(spec).run()
    second = HistoricalResearchRunner(spec).run()

    assert first.report == second.report
    assert Path(first.results_json).read_bytes() == Path(second.results_json).read_bytes()
    assert first.report.run_id == second.report.run_id


def test_every_predeclared_hypothesis_is_reported_even_when_unavailable(
    tmp_path: Path,
) -> None:
    """Never silently drop an unavailable secondary feature family."""
    base = _spec(tmp_path / "runs")
    secondary = ResearchHypothesis(
        hypothesis_id="H3_LIQUIDITY_CREDIBILITY",
        description="Failed aggression restricted to positive DeltaLC.",
        kind=ResearchHypothesisKind.FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY,
        expected_direction=ExpectedEffectDirection.POSITIVE,
        statistic=ResearchStatisticName.CONDITIONAL_MEAN_REVERSAL_RETURN,
        forward_horizon_events=2,
        alternative=PermutationAlternative.GREATER,
        permutation_config_reference="failed-aggression-null",
        effectiveness_horizon_events=1,
        resiliency_horizon_events=1,
    )
    payload = base.model_dump(mode="python")
    payload["hypotheses"] = (*base.hypotheses, secondary)
    spec = ResearchExperimentSpec.model_validate(payload)

    report = HistoricalResearchRunner(spec).run().report

    assert tuple(item.hypothesis.hypothesis_id for item in report.hypothesis_results) == (
        "H1_FAILED_AGGRESSION",
        "H2_RECENT_RETURN_BASELINE",
        "H3_LIQUIDITY_CREDIBILITY",
    )
    assert report.hypothesis_results[-1].status is HypothesisStatus.UNAVAILABLE
    assert report.hypothesis_results[-1].unavailable_reason is not None


def test_experiment_hash_changes_with_predeclared_horizon_or_block_size() -> None:
    """Hash the full canonical preregistration, not just its display name."""
    spec = load_experiment(EXPERIMENT)
    same = load_experiment(EXPERIMENT)
    relocated = spec.model_copy(
        update={"output": ResearchOutputConfig(root_directory="/tmp/other-research-root")}
    )
    horizon_payload = spec.model_dump(mode="python")
    changed_hypothesis = spec.hypotheses[0].model_copy(update={"forward_horizon_events": 1})
    horizon_payload["hypotheses"] = (changed_hypothesis, *spec.hypotheses[1:])
    horizon_spec = ResearchExperimentSpec.model_validate(horizon_payload)
    block_payload = spec.model_dump(mode="python")
    first_named = spec.permutation_configurations[0]
    changed_named = first_named.model_copy(
        update={"configuration": first_named.configuration.model_copy(update={"block_size": 3})}
    )
    block_payload["permutation_configurations"] = (
        changed_named,
        *spec.permutation_configurations[1:],
    )
    block_spec = ResearchExperimentSpec.model_validate(block_payload)

    assert calculate_experiment_hash(spec) == calculate_experiment_hash(same)
    assert calculate_experiment_hash(spec) == calculate_experiment_hash(relocated)
    assert calculate_experiment_hash(spec) != calculate_experiment_hash(horizon_spec)
    assert calculate_experiment_hash(spec) != calculate_experiment_hash(block_spec)


def test_runner_rejects_expected_hash_matched_but_corrupt_sequence(
    tmp_path: Path,
) -> None:
    """An updated manifest cannot make unresolved book-history corruption acceptable."""
    corrupt = tmp_path / "corrupt.csv"
    corrupt.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(",6,SRA\n", ",8,SRA\n", 1),
        encoding="utf-8",
    )
    spec = _replace_sources(_spec(tmp_path / "runs"), (corrupt,))

    assert calculate_experiment_hash(spec) != calculate_experiment_hash(load_experiment(EXPERIMENT))
    with pytest.raises(HistoricalDataValidationError):
        HistoricalResearchRunner(spec).dry_run()


def test_two_sessions_do_not_form_cross_session_shock_pair(tmp_path: Path) -> None:
    """Default session segmentation prevents yesterday/today shock comparison."""
    lines = FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)
    first = tmp_path / "session_one.csv"
    second = tmp_path / "session_two.csv"
    first.write_text("".join(lines[:13]), encoding="utf-8")
    second.write_text(
        "".join(lines[:13]).replace("2026-08-03", "2026-08-04"),
        encoding="utf-8",
    )
    spec = _replace_sources(_spec(tmp_path / "runs"), (first, second))
    payload = spec.model_dump(mode="python")
    payload["research_end"] = "2026-08-05T00:00:00Z"
    spec = ResearchExperimentSpec.model_validate(payload)

    dry_run = HistoricalResearchRunner(spec).dry_run()

    assert len(dry_run.source_files) == 2
    with pytest.raises(ValueError, match="no comparable SRA research observations"):
        HistoricalResearchRunner(spec).run()


def _replace_sources(
    spec: ResearchExperimentSpec,
    paths: tuple[Path, ...],
) -> ResearchExperimentSpec:
    source = spec.sources[0]
    adapter_payload = source.adapter.model_dump(mode="python")
    adapter_payload["source_paths"] = tuple(str(path) for path in paths)
    adapter = DatabentoMboCsvConfig.model_validate(adapter_payload)
    identities = tuple(
        HistoricalFileIdentity.model_validate(sha256_file(path).model_dump()) for path in paths
    )
    source_payload = source.model_dump(mode="python")
    source_payload.update({"adapter": adapter, "expected_files": identities})
    payload = spec.model_dump(mode="python")
    payload["sources"] = (source.model_validate(source_payload),)
    return ResearchExperimentSpec.model_validate(payload)
