"""Deterministic end-to-end execution of preregistered historical SRA research."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import cast
from uuid import UUID, uuid5

from sra_nexus.backtest.market_replay import MarketReplay
from sra_nexus.common.types import ResearchObservationId, ResearchRunId, ResearchSplitId
from sra_nexus.market_data.book import OrderBook
from sra_nexus.market_data.enums import AggressorSide, BookAction
from sra_nexus.market_data.events import BookEvent, TradeEvent
from sra_nexus.market_data.exceptions import BookNotInitializedError
from sra_nexus.market_data.historical import (
    HistoricalDataManifest,
    HistoricalDataQualityReport,
    HistoricalFileInspection,
    HistoricalInstrumentMapping,
    HistoricalNormalizedEvent,
)
from sra_nexus.market_data.providers.base import HistoricalMarketDataAdapter
from sra_nexus.market_data.providers.databento import DatabentoMboCsvAdapter
from sra_nexus.market_data.providers.databento.adapter import HistoricalDataValidationError
from sra_nexus.market_data.snapshots import BookSnapshot
from sra_nexus.research.aggression_episodes import (
    AggressionEpisode,
    AggressionEpisodeBuilder,
    ReconciledAggressiveExecution,
    analyze_historical_aggression_episode,
)
from sra_nexus.research.dataset import (
    ResearchDataset,
    ResearchDatasetBuilder,
    ResearchObservation,
)
from sra_nexus.research.experiment import (
    HistoricalSourceSpec,
    HypothesisStatus,
    ResearchExperimentSpec,
    ResearchFeatureName,
    ResearchHypothesis,
    ResearchHypothesisKind,
    ResearchStatisticName,
    calculate_experiment_hash,
)
from sra_nexus.research.features import SRAFeatureInput, evaluate_failed_aggression_condition
from sra_nexus.research.historical_pairing import (
    HistoricalShockCandidate,
    find_most_recent_prior_comparable_shock,
)
from sra_nexus.research.labels import ForwardMarketResponseLabel, UnavailableForwardLabel
from sra_nexus.research.multiple_testing import ResearchPValue, adjust_research_p_values
from sra_nexus.research.permutation import (
    ConditionalMeanReversalReturn,
    CovarianceAssociation,
    PermutationDatum,
    PermutationTestConfig,
    PermutationTestResult,
    PermutationTestService,
    ResearchStatistic,
    build_permutation_blocks,
    permutation_datum_from_observation,
)
from sra_nexus.research.results import (
    FoldHypothesisResult,
    HistoricalDryRunReport,
    HistoricalResearchReport,
    HypothesisTestResult,
    ResearchRunArtifacts,
)
from sra_nexus.research.splits import WalkForwardSplit, WalkForwardSplitter
from sra_nexus.sra.comparison import ShockPairService
from sra_nexus.sra.enums import ShockResearchStatus
from sra_nexus.sra.service import ShockResearchService
from sra_nexus.sra.shock import BookExecutionState
from sra_nexus.sra.state import MarketStateObservation, market_event_reference
from sra_nexus.sra.toxicity import IndexedMarketStateObservation
from sra_nexus.sra.windows import reconcile_aggressive_trade_observations

_RUN_NAMESPACE = UUID("42975b08-dc80-5cae-9859-7399a118ef90")
_POOLED_SPLIT_NAMESPACE = UUID("662e5950-f660-5f45-b0c0-2489c2bb0806")


@dataclass(frozen=True, slots=True)
class _Evaluation:
    hypothesis: ResearchHypothesis
    status: HypothesisStatus
    fold_results: tuple[FoldHypothesisResult, ...]
    pooled: PermutationTestResult | None
    observation_count: int
    qualifying_count: int
    block_count: int
    reason: str | None


class HistoricalResearchRunner:
    """Inspect, normalize, replay, research, split, permute, and report offline data."""

    def __init__(self, spec: ResearchExperimentSpec) -> None:
        """Freeze one already validated experiment specification."""
        self._spec = spec
        self._experiment_hash = calculate_experiment_hash(spec)
        self._code_revision = discover_code_revision()

    @property
    def spec(self) -> ResearchExperimentSpec:
        """Return the immutable preregistered experiment."""
        return self._spec

    @property
    def experiment_hash(self) -> str:
        """Return SHA-256 of the canonical experiment specification."""
        return self._experiment_hash

    def dry_run(self) -> HistoricalDryRunReport:
        """Validate exact source bytes and estimate normalization without replay."""
        _, inspections = self._inspect_sources()
        identities = tuple(item.file_identity for item in inspections)
        return HistoricalDryRunReport(
            experiment_hash=self._experiment_hash,
            code_revision=self._code_revision,
            source_files=tuple(item.source_filename for item in identities),
            source_hashes=tuple(item.sha256 for item in identities),
            estimated_normalized_events=sum(item.normalized_event_estimate for item in inspections),
            declared_hypotheses=tuple(item.hypothesis_id for item in self._spec.hypotheses),
            warnings=tuple(warning for item in inspections for warning in item.sequence_gaps),
        )

    def run(self) -> ResearchRunArtifacts:
        """Execute the complete frozen gross-return research pipeline."""
        started = perf_counter()
        adapters, inspections = self._inspect_sources()
        envelopes = self._selected_events(adapters)
        dataset_builder = ResearchDatasetBuilder(self._spec.dataset_config)
        shock_service = ShockResearchService(self._spec.shock_research_config)
        pair_service = ShockPairService(self._spec.shock_pair_config)
        observations: list[ResearchObservation] = []
        observation_sessions: dict[ResearchObservationId, str] = {}
        event_counts = {"book": 0, "trade": 0, "quote": 0}
        times: list[datetime] = []
        instruments = set()
        venues = set()
        reset_count = 0
        structural_break_count = 0
        one_sided = 0
        directional_trades = 0
        unknown_trades = 0
        reconciled_trade_observations = 0
        aggression_episode_count = 0
        aggression_episode_observations = 0
        maximum_episode_observations = 0
        global_indices: dict[tuple[str, str], int] = {}

        for session_envelopes in _session_batches(envelopes):
            session_result = self._research_session(
                session_envelopes,
                dataset_builder,
                shock_service,
                pair_service,
                global_indices,
            )
            observations.extend(session_result.observations)
            observation_sessions.update(session_result.observation_sessions)
            one_sided += session_result.one_sided_book_periods
            directional_trades += session_result.directional_trade_count
            unknown_trades += session_result.unknown_trade_count
            reconciled_trade_observations += session_result.reconciled_trade_observation_count
            aggression_episode_count += session_result.aggression_episode_count
            aggression_episode_observations += session_result.aggression_episode_observation_count
            maximum_episode_observations = max(
                maximum_episode_observations,
                session_result.maximum_observations_per_aggression_episode,
            )
            reset_count += session_result.reset_count
            structural_break_count += session_result.structural_break_count
            for envelope in session_envelopes:
                event = envelope.event
                instruments.add(event.instrument_id)
                venues.add(event.venue)
                times.append(event.exchange_time)
                if isinstance(event, BookEvent):
                    event_counts["book"] += 1
                elif isinstance(event, TradeEvent):
                    event_counts["trade"] += 1
                else:
                    event_counts["quote"] += 1

        if not times:
            raise HistoricalDataValidationError("experiment scope contains no normalized events")
        if not observations:
            raise ValueError("historical scope produced no comparable SRA research observations")
        source_files = tuple(item.file_identity for item in inspections)
        event_count = sum(event_counts.values())
        data_manifest = HistoricalDataManifest(
            provider=adapters[0].provider_name,
            provider_schema_version=adapters[0].format_version,
            source_files=source_files,
            instruments=tuple(sorted(instruments, key=str)),
            venues=tuple(sorted(venues)),
            start_exchange_time=min(times),
            end_exchange_time=max(times),
            event_count=event_count,
            book_event_count=event_counts["book"],
            trade_event_count=event_counts["trade"],
            quote_event_count=event_counts["quote"],
            normalization_version=self._spec.sources[0].adapter.normalization_version,
            process_time_policy=self._spec.sources[0].adapter.process_time_policy,
            synthetic_process_time_used=True,
            instrument_mappings=tuple(
                mapping
                for source in self._spec.sources
                for mapping in source.adapter.instrument_mappings
            ),
            created_at=self._spec.created_at,
        )
        dataset = dataset_builder.build_dataset(
            observations=observations,
            created_at=self._spec.created_at,
            source_data_identifiers=tuple(item.sha256 for item in source_files),
            code_revision=self._code_revision,
        )
        splits = WalkForwardSplitter(self._spec.walk_forward_config).split(dataset.observations)
        if not splits:
            raise ValueError("walk-forward configuration produced no valid test folds")
        run_id = derive_research_run_id(
            self._experiment_hash,
            dataset,
            self._code_revision,
        )
        hypothesis_results = self._evaluate_hypotheses(
            dataset.observations,
            splits,
            observation_sessions,
            run_id,
        )
        unavailable_labels = sum(
            isinstance(label, UnavailableForwardLabel)
            for observation in dataset.observations
            for label in observation.labels
        )
        total_labels = sum(len(item.labels) for item in dataset.observations)
        total_trades = directional_trades + unknown_trades
        missing_feature_count = sum(
            result.status is not HypothesisStatus.RUN for result in hypothesis_results
        )
        quality = HistoricalDataQualityReport(
            source_files=source_files,
            sequence_gaps=tuple(value for item in inspections for value in item.sequence_gaps),
            sequence_regressions=tuple(
                value for item in inspections for value in item.sequence_regressions
            ),
            duplicate_records=tuple(
                value for item in inspections for value in item.duplicate_records
            ),
            reset_count=reset_count,
            structural_break_count=structural_break_count,
            invalid_records=tuple(
                value for item in inspections for value in item.malformed_records
            ),
            one_sided_book_periods=one_sided,
            missing_aggressor_side_count=unknown_trades,
            reconciled_trade_observation_count=reconciled_trade_observations,
            aggression_episode_count=aggression_episode_count,
            mean_observations_per_aggression_episode=(
                0.0
                if aggression_episode_count == 0
                else aggression_episode_observations / aggression_episode_count
            ),
            maximum_observations_per_aggression_episode=maximum_episode_observations,
            directional_flow_coverage=(
                0.0 if total_trades == 0 else directional_trades / total_trades
            ),
            unknown_flow_share=0.0 if total_trades == 0 else unknown_trades / total_trades,
            missing_feature_count=missing_feature_count,
            missing_feature_share=missing_feature_count / len(hypothesis_results),
            unavailable_label_count=unavailable_labels,
            total_label_count=total_labels,
            unavailable_label_share=(
                0.0 if total_labels == 0 else unavailable_labels / total_labels
            ),
            warnings=_quality_warnings(inspections, unavailable_labels, total_labels),
        )
        report = HistoricalResearchReport(
            experiment=self._spec,
            experiment_hash=self._experiment_hash,
            run_id=run_id,
            code_revision=self._code_revision,
            data_manifest=data_manifest,
            data_quality=quality,
            dataset_manifest=dataset.manifest,
            walk_forward_splits=splits,
            hypothesis_results=hypothesis_results,
            events_processed=event_count,
            observations_generated=len(dataset.observations),
            limitations=(
                *self._spec.limitations,
                "Gross historical market response only; costs, alpha models, and trading "
                "are deferred.",
                "Liquidity credibility and toxicity are evaluated only when upstream "
                "features exist.",
                "The runner retains one normalized session in memory; checkpoint/cache "
                "support is deferred.",
            ),
        )
        return write_research_artifacts(
            report,
            elapsed_processing_seconds=perf_counter() - started,
        )

    def _inspect_sources(
        self,
    ) -> tuple[tuple[HistoricalMarketDataAdapter, ...], tuple[HistoricalFileInspection, ...]]:
        adapters = tuple(_adapter(source) for source in self._spec.sources)
        inspections = tuple(item for adapter in adapters for item in adapter.inspect())
        if any(item.has_fatal_issues for item in inspections):
            raise HistoricalDataValidationError("historical pre-flight found fatal corruption")
        if any(item.sequence_gaps for item in inspections):
            raise HistoricalDataValidationError(
                "historical research refuses unresolved provider sequence gaps"
            )
        expected = {
            (item.source_filename, item.sha256, item.byte_count)
            for source in self._spec.sources
            for item in source.expected_files
        }
        actual = {
            (
                item.file_identity.source_filename,
                item.file_identity.sha256,
                item.file_identity.byte_count,
            )
            for item in inspections
        }
        if actual != expected:
            raise HistoricalDataValidationError("historical source SHA-256/size mismatch")
        return adapters, inspections

    def _selected_events(
        self,
        adapters: Sequence[HistoricalMarketDataAdapter],
    ) -> Iterator[HistoricalNormalizedEvent]:
        allowed_instruments = set(self._spec.instruments)
        allowed_venues = set(self._spec.venues)
        allowed_sessions = set(self._spec.sessions)
        warmup: deque[HistoricalNormalizedEvent] = deque(
            maxlen=max(1, self._spec.warmup_event_count)
        )
        warmup_emitted = False
        for adapter in adapters:
            for envelope in adapter.normalize():
                event = envelope.event
                if (
                    event.instrument_id not in allowed_instruments
                    or event.venue not in allowed_venues
                ):
                    continue
                if (
                    envelope.session_segment not in allowed_sessions
                    and not envelope.is_recovery_snapshot
                ):
                    continue
                if envelope.is_recovery_snapshot:
                    if event.receive_time < self._spec.research_end:
                        yield envelope
                    continue
                if event.exchange_time < self._spec.research_start:
                    if self._spec.warmup_event_count:
                        warmup.append(envelope)
                    continue
                if event.exchange_time >= self._spec.research_end:
                    continue
                if not warmup_emitted:
                    yield from warmup
                    warmup_emitted = True
                yield envelope

    def _research_session(
        self,
        envelopes: tuple[HistoricalNormalizedEvent, ...],
        dataset_builder: ResearchDatasetBuilder,
        shock_service: ShockResearchService,
        pair_service: ShockPairService,
        global_indices: dict[tuple[str, str], int],
    ) -> _SessionResult:
        first = envelopes[0]
        mapping = _mapping_for_event(self._spec.sources, first)
        book = OrderBook(
            mapping.instrument,
            venue=first.event.venue,
            sequence_stream_id=first.event.sequence_stream_id,
        )
        replay = MarketReplay(book)
        states: list[IndexedMarketStateObservation] = []
        state_by_index: dict[int, IndexedMarketStateObservation] = {}
        segment_by_index: dict[int, int] = {}
        pending: dict[str, tuple[BookExecutionState, int, int]] = {}
        reconciled_executions: list[ReconciledAggressiveExecution] = []
        market_key = (str(first.event.instrument_id), first.event.venue)
        next_index = global_indices.get(market_key, 0)
        segment = 0
        configured_boundaries = tuple(
            item
            for item in self._spec.structural_boundaries
            if item.instrument_id == first.event.instrument_id and item.venue == first.event.venue
        )
        crossed_boundaries: set[int] = set()
        reset_count = 0
        structural_break_count = 0
        one_sided = 0
        directional_trades = 0
        unknown_trades = 0

        for envelope in envelopes:
            event = envelope.event
            for boundary_index, boundary in enumerate(configured_boundaries):
                if boundary_index not in crossed_boundaries and event.exchange_time >= (
                    boundary.exchange_time
                ):
                    crossed_boundaries.add(boundary_index)
                    segment += 1
                    structural_break_count += 1
                    pending.clear()
            if envelope.boundary_before is not None:
                segment += 1
                structural_break_count += 1
                pending.clear()
            event_index = next_index
            next_index += 1
            pre_snapshot = None
            if isinstance(event, BookEvent) and event.action is BookAction.EXECUTE:
                try:
                    pre_snapshot = book.snapshot()
                except BookNotInitializedError as error:
                    raise HistoricalDataValidationError(
                        "execution occurred before a reconstructable book baseline"
                    ) from error
            replay.replay((event,))
            try:
                current_snapshot = book.snapshot()
            except BookNotInitializedError:
                continue
            state = IndexedMarketStateObservation(
                event_index=event_index,
                observation=MarketStateObservation(
                    event_reference=market_event_reference(event),
                    snapshot=current_snapshot,
                ),
            )
            states.append(state)
            state_by_index[event_index] = state
            segment_by_index[event_index] = segment
            if current_snapshot.best_bid is None or current_snapshot.best_ask is None:
                one_sided += 1
            if isinstance(event, BookEvent):
                if event.action is BookAction.RESET:
                    reset_count += 1
                    pending.clear()
                elif event.action is BookAction.EXECUTE:
                    execution = BookExecutionState(
                        event=event,
                        pre_snapshot=_required_snapshot(pre_snapshot),
                        post_snapshot=current_snapshot,
                    )
                    if event.trade_id is not None:
                        pending[str(event.trade_id)] = (execution, event_index, segment)
            elif isinstance(event, TradeEvent):
                if event.aggressor_side is AggressorSide.UNKNOWN:
                    unknown_trades += 1
                else:
                    directional_trades += 1
                matched = None if event.trade_id is None else pending.pop(str(event.trade_id), None)
                if matched is not None:
                    execution, start_index, execution_segment = matched
                    batch = reconcile_aggressive_trade_observations(execution.event, event)
                    if batch.observations:
                        reconciled_executions.append(
                            ReconciledAggressiveExecution(
                                observation=batch.observations[0],
                                execution=execution,
                                execution_event_index=start_index,
                                observation_event_index=event_index,
                                segment=execution_segment,
                            )
                        )

        global_indices[market_key] = next_index
        episodes = AggressionEpisodeBuilder(
            self._spec.aggression_episode_config,
            maximum_observations=(
                shock_service.config.shock_detection.aggression_window_event_count
            ),
        ).build(reconciled_executions)
        candidates = self._analyze_episodes(
            episodes,
            state_by_index,
            segment_by_index,
            shock_service,
        )
        observations: list[ResearchObservation] = []
        observation_sessions: dict[ResearchObservationId, str] = {}
        for candidate_index, current in enumerate(candidates):
            comparison = find_most_recent_prior_comparable_shock(
                current,
                candidates[:candidate_index],
                pair_service,
            )
            if comparison is None:
                continue
            anchor_state = state_by_index.get(current.available_event_index)
            if anchor_state is None:
                continue
            reference = anchor_state.observation.event_reference
            feature_input = SRAFeatureInput(
                comparison=comparison,
                comparison_event_index=current.available_event_index,
                comparison_event_reference=reference,
                comparison_available_at_process_time=reference.process_time,
                comparison_source_data_identifier=f"shock-pair:{comparison.pair_id}",
            )
            observation = dataset_builder.build_observation(
                feature_input=feature_input,
                market_states=states,
                source_data_identifiers=tuple(sorted({item.source_sha256 for item in envelopes})),
            )
            if observation.prediction_anchor_exchange_time < self._spec.research_start:
                continue
            observations.append(observation)
            observation_sessions[observation.observation_id] = first.session_id
        return _SessionResult(
            observations=tuple(observations),
            observation_sessions=observation_sessions,
            one_sided_book_periods=one_sided,
            directional_trade_count=directional_trades,
            unknown_trade_count=unknown_trades,
            reset_count=reset_count,
            structural_break_count=structural_break_count,
            reconciled_trade_observation_count=len(reconciled_executions),
            aggression_episode_count=len(episodes),
            aggression_episode_observation_count=sum(
                len(episode.observations) for episode in episodes
            ),
            maximum_observations_per_aggression_episode=max(
                (len(episode.observations) for episode in episodes),
                default=0,
            ),
        )

    def _analyze_episodes(
        self,
        episodes: Sequence[AggressionEpisode],
        state_by_index: dict[int, IndexedMarketStateObservation],
        segment_by_index: dict[int, int],
        service: ShockResearchService,
    ) -> tuple[HistoricalShockCandidate, ...]:
        horizon = max(
            *service.config.impact.horizons_events,
            *service.config.resiliency.recovery_horizons_events,
        )
        candidates: list[HistoricalShockCandidate] = []
        for episode in episodes:
            responses: list[MarketStateObservation] = []
            for index in range(episode.end_event_index + 1, episode.end_event_index + horizon + 1):
                state = state_by_index.get(index)
                if state is None or segment_by_index.get(index) != episode.segment:
                    break
                responses.append(state.observation)
            result = analyze_historical_aggression_episode(
                episode,
                responses,
                service,
            )
            if result.status is ShockResearchStatus.SHOCK_CANDIDATE:
                shock = result.liquidity_shock
                resiliency = result.resiliency
                if shock is None or resiliency is None:
                    raise AssertionError("shock candidate unexpectedly lacks complete outputs")
                candidates.append(
                    HistoricalShockCandidate(
                        shock=shock,
                        impacts=result.impacts,
                        resiliency=resiliency,
                        start_event_index=episode.start_event_index,
                        end_event_index=episode.end_event_index,
                        available_event_index=episode.end_event_index + horizon,
                        segment=episode.segment,
                    )
                )
        return tuple(candidates)

    def _evaluate_hypotheses(
        self,
        observations: Sequence[ResearchObservation],
        splits: Sequence[WalkForwardSplit],
        sessions: dict[ResearchObservationId, str],
        run_id: ResearchRunId,
    ) -> tuple[HypothesisTestResult, ...]:
        configurations = {
            item.config_id: item.configuration for item in self._spec.permutation_configurations
        }
        evaluations: list[_Evaluation] = []
        for hypothesis in self._spec.hypotheses:
            try:
                evaluation = self._evaluate_hypothesis(
                    hypothesis,
                    observations,
                    splits,
                    sessions,
                    configurations[hypothesis.permutation_config_reference],
                )
            except ValueError as error:
                fold_results = tuple(
                    FoldHypothesisResult(
                        split_id=split.split_id,
                        test_start=split.test_start,
                        test_end=split.test_end,
                        observation_count=0,
                        qualifying_count=0,
                        permutation_block_count=0,
                        observed_statistic=None,
                        permutation_result=None,
                        status=HypothesisStatus.FAILED_VALIDATION,
                        unavailable_reason=str(error),
                    )
                    for split in splits
                )
                evaluation = _Evaluation(
                    hypothesis=hypothesis,
                    status=HypothesisStatus.FAILED_VALIDATION,
                    fold_results=fold_results,
                    pooled=None,
                    observation_count=0,
                    qualifying_count=0,
                    block_count=0,
                    reason=str(error),
                )
            evaluations.append(evaluation)
        evaluation_values = tuple(evaluations)
        runnable = tuple(item for item in evaluation_values if item.pooled is not None)
        adjustments = (
            {}
            if not runnable
            else {
                item.test_id: item
                for item in adjust_research_p_values(
                    tuple(
                        ResearchPValue(test_id=value.pooled.test_id, p_value=value.pooled.p_value)
                        for value in runnable
                        if value.pooled is not None
                    )
                )
            }
        )
        results: list[HypothesisTestResult] = []
        for evaluation in evaluation_values:
            pooled = evaluation.pooled
            adjusted = None if pooled is None else adjustments[pooled.test_id]
            results.append(
                HypothesisTestResult(
                    hypothesis=evaluation.hypothesis,
                    experiment_hash=self._experiment_hash,
                    run_id=run_id,
                    status=evaluation.status,
                    statistic=evaluation.hypothesis.statistic.value,
                    forward_horizon_events=evaluation.hypothesis.forward_horizon_events,
                    observed_value=None if pooled is None else pooled.observed_statistic,
                    null_mean=None if pooled is None else pooled.null_summary.mean,
                    null_standard_deviation=(
                        None if pooled is None else pooled.null_summary.standard_deviation
                    ),
                    observed_minus_null_mean=(
                        None if pooled is None else pooled.observed_minus_null_mean
                    ),
                    standardized_effect=None if pooled is None else pooled.standardized_effect,
                    raw_p_value=None if adjusted is None else adjusted.raw_p_value,
                    bonferroni_p_value=(None if adjusted is None else adjusted.bonferroni_p_value),
                    fdr_p_value=(None if adjusted is None else adjusted.benjamini_hochberg_p_value),
                    observation_count=evaluation.observation_count,
                    qualifying_count=evaluation.qualifying_count,
                    permutation_block_count=evaluation.block_count,
                    permutation_count=0 if pooled is None else pooled.permutation_count,
                    pooled_permutation_result=pooled,
                    fold_results=evaluation.fold_results,
                    unavailable_reason=evaluation.reason,
                )
            )
        return tuple(results)

    def _evaluate_hypothesis(
        self,
        hypothesis: ResearchHypothesis,
        observations: Sequence[ResearchObservation],
        splits: Sequence[WalkForwardSplit],
        sessions: dict[ResearchObservationId, str],
        config: PermutationTestConfig,
    ) -> _Evaluation:
        statistic = _statistic(hypothesis)
        observation_index = {item.observation_id: item for item in observations}
        fold_results: list[FoldHypothesisResult] = []
        pooled_data: list[PermutationDatum] = []
        for split in splits:
            data = tuple(
                datum
                for identity in split.test_observation_ids
                if (observation := observation_index.get(identity)) is not None
                if (
                    datum := _hypothesis_datum(
                        observation,
                        hypothesis,
                        sessions.get(identity),
                        str(split.split_id),
                    )
                )
                is not None
            )
            qualifying = _qualifying_count(data, hypothesis)
            if len(data) < 2 or qualifying == 0:
                fold_results.append(
                    FoldHypothesisResult(
                        split_id=split.split_id,
                        test_start=split.test_start,
                        test_end=split.test_end,
                        observation_count=len(data),
                        qualifying_count=qualifying,
                        permutation_block_count=0,
                        observed_statistic=None,
                        permutation_result=None,
                        status=HypothesisStatus.UNAVAILABLE,
                        unavailable_reason=(
                            "fewer than two complete test labels or no qualifying rows"
                        ),
                    )
                )
                continue
            blocks = build_permutation_blocks(data, config)
            result = PermutationTestService().run_for_split(
                data=data,
                split=split,
                statistic=statistic,
                config=config,
                forward_horizon=hypothesis.forward_horizon_events,
                feature_or_condition=_hypothesis_definition(hypothesis),
            )
            fold_results.append(
                FoldHypothesisResult(
                    split_id=split.split_id,
                    test_start=split.test_start,
                    test_end=split.test_end,
                    observation_count=len(data),
                    qualifying_count=qualifying,
                    permutation_block_count=len(blocks),
                    observed_statistic=result.observed_statistic,
                    permutation_result=result,
                    status=HypothesisStatus.RUN,
                )
            )
            pooled_data.extend(data)
        qualifying = _qualifying_count(tuple(pooled_data), hypothesis)
        if len(pooled_data) < 2 or qualifying == 0:
            return _Evaluation(
                hypothesis=hypothesis,
                status=HypothesisStatus.UNAVAILABLE,
                fold_results=tuple(fold_results),
                pooled=None,
                observation_count=len(pooled_data),
                qualifying_count=qualifying,
                block_count=0,
                reason="no valid out-of-sample fold evidence",
            )
        blocks = build_permutation_blocks(pooled_data, config)
        pooled_split_id = ResearchSplitId(
            uuid5(_POOLED_SPLIT_NAMESPACE, f"{self._experiment_hash}|{hypothesis.hypothesis_id}")
        )
        pooled = PermutationTestService().run(
            data=pooled_data,
            statistic=statistic,
            config=config,
            split_id=pooled_split_id,
            forward_horizon=hypothesis.forward_horizon_events,
            feature_or_condition=_hypothesis_definition(hypothesis),
        )
        return _Evaluation(
            hypothesis=hypothesis,
            status=HypothesisStatus.RUN,
            fold_results=tuple(fold_results),
            pooled=pooled,
            observation_count=len(pooled_data),
            qualifying_count=qualifying,
            block_count=len(blocks),
            reason=None,
        )


@dataclass(frozen=True, slots=True)
class _SessionResult:
    observations: tuple[ResearchObservation, ...]
    observation_sessions: dict[ResearchObservationId, str]
    one_sided_book_periods: int
    directional_trade_count: int
    unknown_trade_count: int
    reset_count: int
    structural_break_count: int
    reconciled_trade_observation_count: int
    aggression_episode_count: int
    aggression_episode_observation_count: int
    maximum_observations_per_aggression_episode: int


def derive_research_run_id(
    experiment_hash: str,
    dataset: ResearchDataset,
    code_revision: str,
) -> ResearchRunId:
    """Derive traceable identity from experiment, dataset manifest, and code."""
    manifest_json = json.dumps(
        dataset.manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    return ResearchRunId(
        uuid5(_RUN_NAMESPACE, f"{experiment_hash}|{manifest_hash}|{code_revision}")
    )


def discover_code_revision(directory: Path | None = None) -> str:
    """Return current Git commit or explicit UNKNOWN without failing research."""
    try:
        result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=directory,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    revision = result.stdout.strip()
    return revision if revision else "UNKNOWN"


def write_research_artifacts(
    report: HistoricalResearchReport,
    *,
    elapsed_processing_seconds: float = 0.0,
) -> ResearchRunArtifacts:
    """Write deterministic artifacts once, reusing only byte-identical results."""
    root = Path(report.experiment.output.root_directory).expanduser().resolve()
    output = root / report.experiment_hash
    payloads = {
        "experiment.json": _pretty_json(report.experiment.model_dump(mode="json")),
        "data_manifest.json": _pretty_json(report.data_manifest.model_dump(mode="json")),
        "data_quality.json": _pretty_json(report.data_quality.model_dump(mode="json")),
        "dataset_manifest.json": _pretty_json(report.dataset_manifest.model_dump(mode="json")),
        "results.json": _pretty_json(report.model_dump(mode="json")),
        "report.md": render_markdown_report(report),
    }
    if output.exists():
        if not report.experiment.output.reuse_identical_completed_run:
            raise FileExistsError(f"completed research output already exists: {output}")
        for filename, content in payloads.items():
            path = output / filename
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise FileExistsError(
                    f"existing research output conflicts with deterministic artifact {filename}"
                )
    else:
        output.mkdir(parents=True)
        for filename, content in payloads.items():
            (output / filename).write_text(content, encoding="utf-8")
    return ResearchRunArtifacts(
        output_directory=str(output),
        experiment_json=str(output / "experiment.json"),
        data_manifest_json=str(output / "data_manifest.json"),
        data_quality_json=str(output / "data_quality.json"),
        dataset_manifest_json=str(output / "dataset_manifest.json"),
        results_json=str(output / "results.json"),
        markdown_report=str(output / "report.md"),
        elapsed_processing_seconds=elapsed_processing_seconds,
        report=report,
    )


def render_markdown_report(report: HistoricalResearchReport) -> str:
    """Render neutral, deterministic human-readable research evidence."""
    lines = [
        f"# Historical research report: {report.experiment.experiment_name}",
        "",
        f"- Experiment hash: `{report.experiment_hash}`",
        f"- Research run ID: `{report.run_id}`",
        f"- Code revision: `{report.code_revision}`",
        f"- Events processed: {report.events_processed}",
        f"- Observations generated: {report.observations_generated}",
        f"- Instruments: {', '.join(str(value) for value in report.data_manifest.instruments)}",
        f"- Venues: {', '.join(report.data_manifest.venues)}",
        f"- Sessions: {', '.join(value.value for value in report.experiment.sessions)}",
        f"- Scope: {report.experiment.research_start.isoformat()} to "
        f"{report.experiment.research_end.isoformat()} (half-open)",
        "",
        "## Exact source identities",
        "",
        *(
            f"- `{item.source_filename}`: `{item.sha256}` ({item.byte_count} bytes)"
            for item in report.data_manifest.source_files
        ),
        "",
        "## Declared hypotheses and evidence",
        "",
    ]
    for result in report.hypothesis_results:
        pooled = result.pooled_permutation_result
        block_size = None if pooled is None else pooled.block_size
        block_unit = None if pooled is None else pooled.block_unit.value
        lines.extend(
            (
                f"### {result.hypothesis.hypothesis_id}: {result.status.value}",
                "",
                result.hypothesis.description,
                "",
                f"- Exact definition: `{_hypothesis_definition(result.hypothesis)}`",
                f"- Forward horizon: {result.forward_horizon_events} normalized events",
                f"- Statistic: `{result.statistic}`",
                f"- Observed value: {_display(result.observed_value)}",
                f"- Null mean: {_display(result.null_mean)}",
                f"- Standardized effect: {_display(result.standardized_effect)}",
                f"- Raw p-value: {_display(result.raw_p_value)}",
                f"- Bonferroni p-value: {_display(result.bonferroni_p_value)}",
                f"- BH-FDR p-value: {_display(result.fdr_p_value)}",
                f"- Observations / qualifying: {result.observation_count} / "
                f"{result.qualifying_count}",
                f"- Blocks / permutations: {result.permutation_block_count} / "
                f"{result.permutation_count}",
                f"- Block size / unit: {_display(block_size)} / {_display(block_unit)}",
                f"- Seed: {report.experiment.seed}",
                "",
                "Fold evidence:",
                "",
            )
        )
        for fold in result.fold_results:
            fold_p_value = (
                None if fold.permutation_result is None else fold.permutation_result.p_value
            )
            lines.append(
                f"- `{fold.split_id}` {fold.status.value}: n={fold.observation_count}, "
                f"qualifying={fold.qualifying_count}, observed="
                f"{_display(fold.observed_statistic)}, p="
                f"{_display(fold_p_value)}"
            )
        if result.unavailable_reason is not None:
            lines.extend(("", f"Unavailable reason: {result.unavailable_reason}"))
        lines.append("")
    lines.extend(
        (
            "## Data quality",
            "",
            f"- Sequence gaps / regressions / duplicates: "
            f"{len(report.data_quality.sequence_gaps)} / "
            f"{len(report.data_quality.sequence_regressions)} / "
            f"{len(report.data_quality.duplicate_records)}",
            f"- Resets / structural breaks / one-sided states: "
            f"{report.data_quality.reset_count} / "
            f"{report.data_quality.structural_break_count} / "
            f"{report.data_quality.one_sided_book_periods}",
            f"- Directional flow coverage / unknown share: "
            f"{report.data_quality.directional_flow_coverage} / "
            f"{report.data_quality.unknown_flow_share}",
            f"- Reconciled trade observations / aggression episodes: "
            f"{report.data_quality.reconciled_trade_observation_count} / "
            f"{report.data_quality.aggression_episode_count}",
            f"- Mean / maximum observations per aggression episode: "
            f"{report.data_quality.mean_observations_per_aggression_episode} / "
            f"{report.data_quality.maximum_observations_per_aggression_episode}",
            f"- Missing feature share: {report.data_quality.missing_feature_share}",
            f"- Unavailable label share: {report.data_quality.unavailable_label_share}",
            "",
        )
    )
    lines.extend(f"- {warning}" for warning in report.data_quality.warnings)
    lines.extend(("", "## Limitations", ""))
    lines.extend(f"- {value}" for value in report.limitations)
    lines.extend(
        (
            "",
            "This report presents gross historical statistical evidence only. It does not "
            "make trading, profitability, or live-execution claims.",
            "",
        )
    )
    return "\n".join(lines)


def _adapter(source: HistoricalSourceSpec) -> HistoricalMarketDataAdapter:
    return DatabentoMboCsvAdapter(source.adapter)


def _session_batches(
    envelopes: Iterable[HistoricalNormalizedEvent],
) -> Iterator[tuple[HistoricalNormalizedEvent, ...]]:
    current: list[HistoricalNormalizedEvent] = []
    key: tuple[str, str, str, str] | None = None
    closed: set[tuple[str, str, str, str]] = set()
    for envelope in envelopes:
        event = envelope.event
        next_key = (
            str(event.instrument_id),
            event.venue,
            str(event.sequence_stream_id),
            envelope.session_id,
        )
        if key is not None and next_key != key:
            closed.add(key)
            yield tuple(current)
            current = []
        if next_key in closed:
            raise HistoricalDataValidationError(
                "historical source reopens a completed session; files must be chronological"
            )
        current.append(envelope)
        key = next_key
    if current:
        yield tuple(current)


def _mapping_for_event(
    sources: Sequence[HistoricalSourceSpec],
    envelope: HistoricalNormalizedEvent,
) -> HistoricalInstrumentMapping:
    event = envelope.event
    candidates = tuple(
        mapping
        for source in sources
        for mapping in source.adapter.instrument_mappings
        if mapping.instrument.instrument_id == event.instrument_id
        and mapping.venue == event.venue
        and mapping.is_valid_at(event.exchange_time)
    )
    if len(candidates) != 1:
        raise HistoricalDataValidationError("canonical event has no unique instrument mapping")
    return candidates[0]


def _hypothesis_datum(
    observation: ResearchObservation,
    hypothesis: ResearchHypothesis,
    session_id: str | None,
    stratum: str,
) -> PermutationDatum | None:
    if not any(
        isinstance(label, ForwardMarketResponseLabel)
        and label.horizon_events == hypothesis.forward_horizon_events
        for label in observation.labels
    ):
        return None
    condition = True
    feature: Decimal | None = None
    if hypothesis.kind in {
        ResearchHypothesisKind.FAILED_AGGRESSION,
        ResearchHypothesisKind.FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY,
        ResearchHypothesisKind.LOWER_TOXICITY,
    }:
        condition = evaluate_failed_aggression_condition(
            observation.features,
            effectiveness_horizon_events=cast("int", hypothesis.effectiveness_horizon_events),
            resiliency_horizon_events=cast("int", hypothesis.resiliency_horizon_events),
            delta_ae_threshold=hypothesis.delta_ae_threshold,
            delta_rr_threshold=hypothesis.delta_rr_threshold,
        )
        if hypothesis.kind is ResearchHypothesisKind.FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY:
            credibility = observation.features.liquidity_credibility
            if credibility is None:
                return None
            condition = condition and (
                credibility.delta_liquidity_credibility > hypothesis.liquidity_credibility_threshold
            )
        if hypothesis.kind is ResearchHypothesisKind.LOWER_TOXICITY:
            toxicity = observation.features.toxicity
            if toxicity is None or toxicity.delta_toxicity is None:
                return None
            condition = condition and toxicity.delta_toxicity < hypothesis.toxicity_threshold
    else:
        feature = _feature_value(observation, hypothesis)
        if feature is None:
            return None
    return permutation_datum_from_observation(
        observation,
        forward_horizon=hypothesis.forward_horizon_events,
        feature_value=feature,
        condition_selected=condition,
        session_id=session_id,
        permutation_stratum=stratum,
    )


def _feature_value(
    observation: ResearchObservation,
    hypothesis: ResearchHypothesis,
) -> Decimal | None:
    feature = hypothesis.feature
    if feature is ResearchFeatureName.DELTA_AE:
        horizon = hypothesis.effectiveness_horizon_events
        return next(
            (
                item.delta_ae
                for item in observation.features.effectiveness_by_horizon
                if horizon is None or item.horizon_events == horizon
            ),
            None,
        )
    if feature is ResearchFeatureName.DELTA_LIQUIDITY_CREDIBILITY:
        credibility_value = observation.features.liquidity_credibility
        return None if credibility_value is None else credibility_value.delta_liquidity_credibility
    if feature is ResearchFeatureName.DELTA_TOXICITY:
        toxicity_value = observation.features.toxicity
        return None if toxicity_value is None else toxicity_value.delta_toxicity
    if feature is ResearchFeatureName.ORDER_BOOK_IMBALANCE:
        return observation.features.baseline.order_book_imbalance
    if feature is ResearchFeatureName.MICROPRICE_OFFSET:
        return observation.features.baseline.microprice_offset
    if feature is ResearchFeatureName.RECENT_RETURN:
        return next(
            (
                item.recent_return
                for item in observation.features.baseline.backward_features
                if item.horizon_events == hypothesis.backward_horizon_events
            ),
            None,
        )
    return None


def _statistic(hypothesis: ResearchHypothesis) -> ResearchStatistic:
    if hypothesis.statistic is ResearchStatisticName.CONDITIONAL_MEAN_REVERSAL_RETURN:
        return ConditionalMeanReversalReturn()
    return CovarianceAssociation()


def _qualifying_count(
    data: Sequence[PermutationDatum],
    hypothesis: ResearchHypothesis,
) -> int:
    if hypothesis.statistic is ResearchStatisticName.CONDITIONAL_MEAN_REVERSAL_RETURN:
        return sum(item.condition_selected for item in data)
    return len(data)


def _hypothesis_definition(hypothesis: ResearchHypothesis) -> str:
    if hypothesis.kind in {
        ResearchHypothesisKind.FAILED_AGGRESSION,
        ResearchHypothesisKind.FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY,
        ResearchHypothesisKind.LOWER_TOXICITY,
    }:
        value = (
            f"DeltaAE(h={hypothesis.effectiveness_horizon_events}) < "
            f"{hypothesis.delta_ae_threshold} AND "
            f"DeltaRR(h={hypothesis.resiliency_horizon_events}) > "
            f"{hypothesis.delta_rr_threshold}"
        )
        if hypothesis.kind is ResearchHypothesisKind.FAILED_AGGRESSION_WITH_LIQUIDITY_CREDIBILITY:
            value += f" AND DeltaLC > {hypothesis.liquidity_credibility_threshold}"
        if hypothesis.kind is ResearchHypothesisKind.LOWER_TOXICITY:
            value += f" AND DeltaToxicity < {hypothesis.toxicity_threshold}"
        return value
    return f"continuous association: {hypothesis.feature.value if hypothesis.feature else 'NONE'}"


def _quality_warnings(
    inspections: Sequence[HistoricalFileInspection],
    unavailable_labels: int,
    total_labels: int,
) -> tuple[str, ...]:
    warnings = [
        "Historical process_time is deterministic synthetic metadata, not file-read wall time."
    ]
    if any(item.sequence_gaps for item in inspections):
        warnings.append("Configured source contains explicitly accepted provider sequence gaps.")
    if unavailable_labels:
        warnings.append(
            f"{unavailable_labels} of {total_labels} forward labels were explicitly unavailable."
        )
    return tuple(warnings)


def _pretty_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _display(value: object) -> str:
    return "UNAVAILABLE" if value is None else str(value)


def _required_snapshot(value: BookSnapshot | None) -> BookSnapshot:
    if value is None:
        raise HistoricalDataValidationError("required pre-execution snapshot is unavailable")
    return value
