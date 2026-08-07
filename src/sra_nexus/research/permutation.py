"""Instrument-aware block-label permutation tests for non-IID market research."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from itertools import permutations, product
from math import factorial
from random import Random
from typing import Protocol, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import (
    InstrumentId,
    PermutationTestId,
    ResearchObservationId,
    ResearchSplitId,
)
from sra_nexus.research.dataset import ResearchObservation
from sra_nexus.research.enums import (
    PercentileMethod,
    PermutationAlternative,
    PermutationBlockUnit,
    PermutationMode,
    PermutationPValueMethod,
)
from sra_nexus.research.labels import ForwardMarketResponseLabel
from sra_nexus.research.models import PERMUTATION_TEST_VERSION, UnitIntervalDecimal
from sra_nexus.research.splits import WalkForwardSplit

PREDECLARED_BLOCK_SIZES = (25, 50, 100, 250)
_PERMUTATION_TEST_NAMESPACE = UUID("2f737261-2d70-6572-6d75-746174696f6e")


class PermutationDatum(ContractModel):
    """One chronologically anchored feature/label pair supplied to a statistic."""

    observation_id: ResearchObservationId
    instrument_id: InstrumentId
    venue: NonBlankStr
    prediction_anchor_event_index: int = Field(ge=0)
    prediction_anchor_process_time: UtcDatetime
    label: ExactDecimal
    reversal_success: bool
    feature_value: ExactDecimal | None = None
    condition_selected: bool = True
    session_id: NonBlankStr | None = None
    permutation_stratum: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_success(self) -> Self:
        """Keep the binary descriptive label aligned with continuous return."""
        if self.reversal_success != (self.label > 0):
            raise ValueError("reversal_success must be true exactly when label is positive")
        return self


class PermutedLabelAssignment(ContractModel):
    """Auditable source-to-target label assignment from one valid block permutation."""

    target_observation_id: ResearchObservationId
    target_instrument_id: InstrumentId
    source_observation_id: ResearchObservationId
    source_instrument_id: InstrumentId
    label: ExactDecimal
    reversal_success: bool


class PermutationTestConfig(ContractModel):
    """Typed null-generation, RNG, tail, and overlap-safety configuration."""

    permutation_count: int = Field(default=999, gt=0)
    seed: int = 0
    block_size: int = Field(default=250, gt=0)
    block_unit: PermutationBlockUnit = PermutationBlockUnit.NORMALIZED_EVENT_COUNT
    within_instrument: bool = True
    session_restricted: bool = False
    alternative: PermutationAlternative = PermutationAlternative.GREATER
    statistic_name: NonBlankStr
    max_exact_permutations: int = Field(default=10_000, gt=0)
    mode: PermutationMode = PermutationMode.MONTE_CARLO
    max_label_horizon_events: int = Field(default=250, gt=0)
    accept_observation_count_overlap_risk: bool = False
    preserve_null_statistics: bool = False
    test_version: NonBlankStr = PERMUTATION_TEST_VERSION

    @model_validator(mode="after")
    def validate_overlap_safety(self) -> Self:
        """Apply only safety checks whose dimensions match the selected block unit."""
        if (
            self.block_unit is PermutationBlockUnit.NORMALIZED_EVENT_COUNT
            and self.block_size < self.max_label_horizon_events
        ):
            raise ValueError("block_size must be at least max_label_horizon_events")
        if (
            self.block_unit is PermutationBlockUnit.RESEARCH_OBSERVATION_COUNT
            and not self.accept_observation_count_overlap_risk
        ):
            raise ValueError(
                "research-observation-count blocks require explicit acceptance of overlap risk"
            )
        return self


class NullDistributionSummary(ContractModel):
    """Standard-library mean, population deviation, and nearest-rank quantiles."""

    mean: ExactDecimal
    standard_deviation: NonNegativeDecimal
    percentile_05: ExactDecimal
    percentile_50: ExactDecimal
    percentile_95: ExactDecimal
    percentile_method: PercentileMethod = PercentileMethod.NEAREST_RANK


class PermutationTestResult(ContractModel):
    """Immutable observed statistic, null evidence, p-value, and effect size."""

    test_id: PermutationTestId
    statistic_name: NonBlankStr
    observed_statistic: ExactDecimal
    null_summary: NullDistributionSummary
    p_value: UnitIntervalDecimal
    alternative: PermutationAlternative
    permutation_mode: PermutationMode
    p_value_method: PermutationPValueMethod
    permutation_count: int = Field(gt=0)
    seed: int
    block_size: int = Field(gt=0)
    block_unit: PermutationBlockUnit
    instrument_scope: NonBlankStr
    split_id: ResearchSplitId
    forward_horizon: int = Field(gt=0)
    feature_or_condition: NonBlankStr
    configuration: PermutationTestConfig
    observed_minus_null_mean: ExactDecimal
    standardized_effect: ExactDecimal | None
    two_sided_null_center: ExactDecimal | None
    null_statistics: tuple[ExactDecimal, ...] | None
    test_version: NonBlankStr = PERMUTATION_TEST_VERSION

    @model_validator(mode="after")
    def validate_effects(self) -> Self:
        """Require effect values to agree exactly with the null summary."""
        expected = self.observed_statistic - self.null_summary.mean
        if self.observed_minus_null_mean != expected:
            raise ValueError("observed-minus-null effect is inconsistent")
        if self.null_summary.standard_deviation == 0:
            if self.standardized_effect is not None:
                raise ValueError("zero null deviation cannot have standardized effect")
        elif self.standardized_effect != expected / self.null_summary.standard_deviation:
            raise ValueError("standardized effect is inconsistent")
        if self.null_statistics is not None and len(self.null_statistics) != self.permutation_count:
            raise ValueError("preserved null count must equal permutation_count")
        expected_method = _p_value_method(self.configuration.mode)
        if self.p_value_method is not expected_method:
            raise ValueError("p-value method must match permutation mode")
        if self.alternative is PermutationAlternative.TWO_SIDED:
            if self.two_sided_null_center != self.null_summary.mean:
                raise ValueError("two-sided null center must equal the null mean")
        elif self.two_sided_null_center is not None:
            raise ValueError("one-sided tests must not declare a two-sided null center")
        if (
            self.seed != self.configuration.seed
            or self.block_size != self.configuration.block_size
            or self.block_unit is not self.configuration.block_unit
            or self.alternative is not self.configuration.alternative
            or self.permutation_mode is not self.configuration.mode
            or self.statistic_name != self.configuration.statistic_name
            or self.test_version != self.configuration.test_version
        ):
            raise ValueError("permutation result metadata must match its configuration")
        return self


class WalkForwardPermutationSummary(ContractModel):
    """Per-fold results plus an optional count-weighted observed statistic."""

    fold_results: tuple[PermutationTestResult, ...]
    fold_weights: tuple[int, ...]
    weighted_mean_observed_statistic: ExactDecimal

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        """Keep individual fold evidence and validate the transparent weighted mean."""
        if not self.fold_results or len(self.fold_results) != len(self.fold_weights):
            raise ValueError("walk-forward summary requires aligned fold results and weights")
        if any(weight <= 0 for weight in self.fold_weights):
            raise ValueError("walk-forward fold weights must be positive")
        numerator = sum(
            (
                result.observed_statistic * Decimal(weight)
                for result, weight in zip(self.fold_results, self.fold_weights, strict=True)
            ),
            Decimal(0),
        )
        expected = numerator / Decimal(sum(self.fold_weights))
        if self.weighted_mean_observed_statistic != expected:
            raise ValueError("weighted fold statistic is inconsistent")
        return self


class ResearchStatistic(Protocol):
    """Generic transparent statistic accepted by the permutation service."""

    @property
    def name(self) -> str:
        """Return the stable statistic name."""

    def __call__(self, data: Sequence[PermutationDatum]) -> Decimal:
        """Calculate one finite statistic from aligned feature/label rows."""


@dataclass(frozen=True, slots=True)
class MeanReversalAdjustedReturn:
    """Mean continuous reversal-adjusted forward return."""

    name: str = "MeanReversalAdjustedReturn"

    def __call__(self, data: Sequence[PermutationDatum]) -> Decimal:
        """Return the exact arithmetic mean of labels."""
        return _mean(tuple(item.label for item in data))


@dataclass(frozen=True, slots=True)
class MedianReversalAdjustedReturn:
    """Median continuous reversal-adjusted forward return."""

    name: str = "MedianReversalAdjustedReturn"

    def __call__(self, data: Sequence[PermutationDatum]) -> Decimal:
        """Return the standard deterministic midpoint median."""
        values = tuple(sorted(item.label for item in data))
        if not values:
            raise ValueError("median statistic requires observations")
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / Decimal(2)


@dataclass(frozen=True, slots=True)
class ReversalSuccessRate:
    """Fraction of strictly positive reversal-adjusted forward returns."""

    name: str = "ReversalSuccessRate"

    def __call__(self, data: Sequence[PermutationDatum]) -> Decimal:
        """Return exact successes divided by observation count."""
        values = tuple(data)
        if not values:
            raise ValueError("success-rate statistic requires observations")
        return Decimal(sum(item.reversal_success for item in values)) / Decimal(len(values))


@dataclass(frozen=True, slots=True)
class CovarianceAssociation:
    """Population covariance between a continuous feature and forward label."""

    name: str = "CovarianceAssociation"

    def __call__(self, data: Sequence[PermutationDatum]) -> Decimal:
        """Return transparent population covariance without fitting a model."""
        values = tuple(data)
        features = tuple(_required(item.feature_value) for item in values)
        labels = tuple(item.label for item in values)
        feature_mean = _mean(features)
        label_mean = _mean(labels)
        return sum(
            (
                (feature - feature_mean) * (label - label_mean)
                for feature, label in zip(features, labels, strict=True)
            ),
            Decimal(0),
        ) / Decimal(len(values))


@dataclass(frozen=True, slots=True)
class ConditionalMeanReversalReturn:
    """Mean return for rows selected by one caller-predeclared condition."""

    name: str = "ConditionalMeanReversalReturn"

    def __call__(self, data: Sequence[PermutationDatum]) -> Decimal:
        """Return mean labels only where the explicit condition is true."""
        return _mean(tuple(item.label for item in data if item.condition_selected))


@dataclass(frozen=True, slots=True)
class UpperLowerQuantileDifference:
    """Upper-minus-lower feature-quantile mean label difference."""

    quantile_fraction: Decimal = Decimal("0.25")
    name: str = "UpperLowerQuantileDifference"

    def __post_init__(self) -> None:
        """Require two non-overlapping positive tail fractions."""
        if not Decimal(0) < self.quantile_fraction <= Decimal("0.5"):
            raise ValueError("quantile fraction must be in (0, 0.5]")

    def __call__(self, data: Sequence[PermutationDatum]) -> Decimal:
        """Compare label means in the two deterministic feature tails."""
        ordered = tuple(sorted(data, key=lambda item: _required(item.feature_value)))
        if len(ordered) < 2:
            raise ValueError("quantile statistic requires at least two observations")
        count = max(
            1,
            int(
                (Decimal(len(ordered)) * self.quantile_fraction).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            ),
        )
        lower = _mean(tuple(item.label for item in ordered[:count]))
        upper = _mean(tuple(item.label for item in ordered[-count:]))
        return upper - lower


class PermutationTestService:
    """Evaluate generic statistics under valid within-stratum label-block nulls."""

    def run(
        self,
        *,
        data: Sequence[PermutationDatum],
        statistic: ResearchStatistic,
        config: PermutationTestConfig,
        split_id: ResearchSplitId,
        forward_horizon: int,
        feature_or_condition: str,
    ) -> PermutationTestResult:
        """Calculate observed evidence and exact or seeded Monte Carlo null statistics."""
        if statistic.name != config.statistic_name:
            raise ValueError("statistic name must match permutation configuration")
        if forward_horizon <= 0:
            raise ValueError("forward_horizon must be positive")
        canonical = _canonical_data(data)
        assignments = generate_permuted_label_assignments(canonical, config)
        observed = statistic(canonical)
        null_values = tuple(
            statistic(_apply_assignment(canonical, assignment)) for assignment in assignments
        )
        summary = summarize_null_distribution(null_values)
        p_value_method = _p_value_method(config.mode)
        p_value = empirical_permutation_p_value(
            observed,
            null_values,
            config.alternative,
            method=p_value_method,
        )
        effect = observed - summary.mean
        standardized = (
            None if summary.standard_deviation == 0 else effect / summary.standard_deviation
        )
        test_id = _derive_test_id(
            canonical,
            config,
            split_id,
            forward_horizon,
            feature_or_condition,
        )
        return PermutationTestResult(
            test_id=test_id,
            statistic_name=statistic.name,
            observed_statistic=observed,
            null_summary=summary,
            p_value=p_value,
            alternative=config.alternative,
            permutation_mode=config.mode,
            p_value_method=p_value_method,
            permutation_count=len(null_values),
            seed=config.seed,
            block_size=config.block_size,
            block_unit=config.block_unit,
            instrument_scope=_instrument_scope(canonical, config),
            split_id=split_id,
            forward_horizon=forward_horizon,
            feature_or_condition=feature_or_condition,
            configuration=config,
            observed_minus_null_mean=effect,
            standardized_effect=standardized,
            two_sided_null_center=(
                summary.mean if config.alternative is PermutationAlternative.TWO_SIDED else None
            ),
            null_statistics=null_values if config.preserve_null_statistics else None,
            test_version=config.test_version,
        )

    def run_for_split(
        self,
        *,
        data: Sequence[PermutationDatum],
        split: WalkForwardSplit,
        statistic: ResearchStatistic,
        config: PermutationTestConfig,
        forward_horizon: int,
        feature_or_condition: str,
    ) -> PermutationTestResult:
        """Run a null strictly inside one walk-forward test fold."""
        identities = {item.observation_id for item in data}
        expected = set(split.test_observation_ids)
        if not identities or not identities <= expected:
            raise ValueError("permutation data must be a nonempty subset of the test fold")
        return self.run(
            data=data,
            statistic=statistic,
            config=config,
            split_id=split.split_id,
            forward_horizon=forward_horizon,
            feature_or_condition=feature_or_condition,
        )


def permutation_datum_from_observation(
    observation: ResearchObservation,
    *,
    forward_horizon: int,
    feature_value: Decimal | None = None,
    condition_selected: bool = True,
    session_id: str | None = None,
    permutation_stratum: str | None = None,
) -> PermutationDatum:
    """Select one complete label while keeping caller-declared feature context explicit."""
    label = next(
        (item for item in observation.labels if item.horizon_events == forward_horizon),
        None,
    )
    if not isinstance(label, ForwardMarketResponseLabel):
        raise ValueError("permutation datum requires an available forward label")
    return PermutationDatum(
        observation_id=observation.observation_id,
        instrument_id=observation.instrument_id,
        venue=observation.venue,
        prediction_anchor_event_index=observation.prediction_anchor_event_index,
        prediction_anchor_process_time=observation.prediction_anchor_process_time,
        label=label.reversal_adjusted_return,
        reversal_success=label.reversal_success,
        feature_value=feature_value,
        condition_selected=condition_selected,
        session_id=session_id,
        permutation_stratum=permutation_stratum,
    )


def generate_permuted_label_assignments(
    data: Sequence[PermutationDatum],
    config: PermutationTestConfig,
) -> tuple[tuple[PermutedLabelAssignment, ...], ...]:
    """Generate block-preserving label assignments under explicit strata."""
    canonical = _canonical_data(data)
    plans = _build_block_plans(canonical, config)
    grouped_indices = {key: plan.target_indices for key, plan in plans.items()}
    blocks = {key: plan.blocks for key, plan in plans.items()}
    if config.mode is PermutationMode.EXACT:
        count = _exact_permutation_count(blocks)
        if count > config.max_exact_permutations:
            raise ValueError("exact block permutations exceed max_exact_permutations")
        order_iterator = _exact_orders(blocks)
    else:
        order_iterator = _monte_carlo_orders(blocks, config.permutation_count, config.seed)
    return tuple(
        _assignment_for_orders(canonical, grouped_indices, blocks, orders)
        for orders in order_iterator
    )


def build_permutation_blocks(
    data: Sequence[PermutationDatum],
    config: PermutationTestConfig,
) -> tuple[tuple[PermutationDatum, ...], ...]:
    """Expose deterministic block membership for audit and research validation."""
    canonical = _canonical_data(data)
    plans = _build_block_plans(canonical, config)
    return tuple(
        tuple(canonical[index] for index in block)
        for plan in plans.values()
        for block in plan.blocks
    )


def permute_blocks_preserving_order[T](
    blocks: Sequence[Sequence[T]],
    block_order: Sequence[int],
) -> tuple[T, ...]:
    """Flatten whole blocks in a supplied order without changing within-block order."""
    values = tuple(tuple(block) for block in blocks)
    order = tuple(block_order)
    if tuple(sorted(order)) != tuple(range(len(values))):
        raise ValueError("block_order must be a permutation of every block index")
    return tuple(item for index in order for item in values[index])


def empirical_permutation_p_value(
    observed: Decimal,
    null_statistics: Sequence[Decimal],
    alternative: PermutationAlternative,
    *,
    method: PermutationPValueMethod,
) -> Decimal:
    """Calculate an exact or Monte Carlo p-value, including boundary ties."""
    null_values = tuple(null_statistics)
    if not null_values:
        raise ValueError("empirical p-value requires at least one null statistic")
    if alternative is PermutationAlternative.GREATER:
        extreme_count = sum(value >= observed for value in null_values)
    elif alternative is PermutationAlternative.LESS:
        extreme_count = sum(value <= observed for value in null_values)
    else:
        null_center = _mean(null_values)
        observed_distance = abs(observed - null_center)
        extreme_count = sum(abs(value - null_center) >= observed_distance for value in null_values)
    if method is PermutationPValueMethod.EXACT_ENUMERATION:
        return Decimal(extreme_count) / Decimal(len(null_values))
    return Decimal(1 + extreme_count) / Decimal(len(null_values) + 1)


def summarize_null_distribution(values: Sequence[Decimal]) -> NullDistributionSummary:
    """Summarize a finite null using population deviation and nearest-rank percentiles."""
    null_values = tuple(values)
    mean = _mean(null_values)
    variance = sum(((value - mean) ** 2 for value in null_values), Decimal(0)) / Decimal(
        len(null_values)
    )
    ordered = tuple(sorted(null_values))
    return NullDistributionSummary(
        mean=mean,
        standard_deviation=variance.sqrt(),
        percentile_05=_nearest_rank(ordered, Decimal("0.05")),
        percentile_50=_nearest_rank(ordered, Decimal("0.50")),
        percentile_95=_nearest_rank(ordered, Decimal("0.95")),
    )


def summarize_walk_forward_results(
    fold_results: Sequence[PermutationTestResult],
    fold_weights: Sequence[int],
) -> WalkForwardPermutationSummary:
    """Retain every fold and calculate only a transparent weighted statistic mean."""
    results = tuple(fold_results)
    weights = tuple(fold_weights)
    numerator = sum(
        (
            result.observed_statistic * Decimal(weight)
            for result, weight in zip(results, weights, strict=True)
        ),
        Decimal(0),
    )
    return WalkForwardPermutationSummary(
        fold_results=results,
        fold_weights=weights,
        weighted_mean_observed_statistic=numerator / Decimal(sum(weights)),
    )


type _StratumKey = tuple[str, ...]
type _BlockIndex = tuple[int, ...]
type _BlockOrders = dict[_StratumKey, tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class _BlockPlan:
    """Fixed target rows and chronological source blocks for one permutation pool."""

    target_indices: tuple[int, ...]
    blocks: tuple[_BlockIndex, ...]


def _canonical_data(data: Sequence[PermutationDatum]) -> tuple[PermutationDatum, ...]:
    values = tuple(
        sorted(
            data,
            key=lambda item: (
                item.prediction_anchor_process_time,
                str(item.instrument_id),
                item.prediction_anchor_event_index,
                str(item.observation_id),
            ),
        )
    )
    if not values:
        raise ValueError("permutation test requires observations")
    if len({item.observation_id for item in values}) != len(values):
        raise ValueError("permutation observations must have unique identities")
    return values


def _permutation_pool_indices(
    data: tuple[PermutationDatum, ...],
    config: PermutationTestConfig,
) -> dict[_StratumKey, tuple[int, ...]]:
    groups: dict[_StratumKey, list[int]] = {}
    for index, item in enumerate(data):
        parts: list[str] = []
        if config.within_instrument:
            parts.append(f"instrument:{item.instrument_id}")
        parts.append(f"venue:{item.venue}")
        if config.session_restricted:
            if item.session_id is None:
                raise ValueError("session-restricted permutation requires supplied session_id")
            parts.append(f"session:{item.session_id}")
        if item.permutation_stratum is not None:
            parts.append(f"stratum:{item.permutation_stratum}")
        key = tuple(parts)
        groups.setdefault(key, []).append(index)
    return {key: tuple(indices) for key, indices in sorted(groups.items())}


def _build_block_plans(
    data: tuple[PermutationDatum, ...],
    config: PermutationTestConfig,
) -> dict[_StratumKey, _BlockPlan]:
    pools = _permutation_pool_indices(data, config)
    if config.block_unit is PermutationBlockUnit.NORMALIZED_EVENT_COUNT:
        return {
            key: _event_count_block_plan(data, indices, config.block_size)
            for key, indices in pools.items()
        }
    if config.block_unit is PermutationBlockUnit.RESEARCH_OBSERVATION_COUNT:
        return {
            key: _BlockPlan(
                target_indices=indices,
                blocks=tuple(
                    tuple(indices[start : start + config.block_size])
                    for start in range(0, len(indices), config.block_size)
                ),
            )
            for key, indices in pools.items()
        }
    raise NotImplementedError(
        "exchange-time and session permutation block construction are deferred"
    )


def _event_count_block_plan(
    data: tuple[PermutationDatum, ...],
    pool_indices: tuple[int, ...],
    block_size_events: int,
) -> _BlockPlan:
    coordinate_groups: dict[tuple[InstrumentId, str], list[int]] = {}
    for index in pool_indices:
        datum = data[index]
        market_identity = (datum.instrument_id, datum.venue)
        coordinate_groups.setdefault(market_identity, []).append(index)

    blocks: list[_BlockIndex] = []
    for market_identity in sorted(
        coordinate_groups,
        key=lambda identity: (str(identity[0]), identity[1]),
    ):
        indices = tuple(coordinate_groups[market_identity])
        origin = min(data[index].prediction_anchor_event_index for index in indices)
        by_number: dict[int, list[int]] = {}
        for index in indices:
            block_number = (data[index].prediction_anchor_event_index - origin) // block_size_events
            by_number.setdefault(block_number, []).append(index)
        blocks.extend(tuple(by_number[number]) for number in sorted(by_number))

    blocks.sort(key=lambda block: block[0])
    ordered_blocks = tuple(blocks)
    target_indices = tuple(index for block in ordered_blocks for index in block)
    return _BlockPlan(target_indices=target_indices, blocks=ordered_blocks)


def _exact_permutation_count(blocks: dict[_StratumKey, tuple[_BlockIndex, ...]]) -> int:
    count = 1
    for group_blocks in blocks.values():
        count *= factorial(len(group_blocks))
    return count


def _exact_orders(
    blocks: dict[_StratumKey, tuple[_BlockIndex, ...]],
) -> Iterator[_BlockOrders]:
    keys = tuple(blocks)
    choices = tuple(permutations(range(len(blocks[key]))) for key in keys)
    for selected in product(*choices):
        yield {key: tuple(order) for key, order in zip(keys, selected, strict=True)}


def _monte_carlo_orders(
    blocks: dict[_StratumKey, tuple[_BlockIndex, ...]],
    count: int,
    seed: int,
) -> Iterator[_BlockOrders]:
    random = Random(seed)
    for _ in range(count):
        result: _BlockOrders = {}
        for key, group_blocks in blocks.items():
            order = list(range(len(group_blocks)))
            random.shuffle(order)
            result[key] = tuple(order)
        yield result


def _assignment_for_orders(
    data: tuple[PermutationDatum, ...],
    grouped_indices: dict[_StratumKey, tuple[int, ...]],
    blocks: dict[_StratumKey, tuple[_BlockIndex, ...]],
    orders: _BlockOrders,
) -> tuple[PermutedLabelAssignment, ...]:
    assignment_by_target: dict[int, PermutedLabelAssignment] = {}
    for key, target_indices in grouped_indices.items():
        source_indices = permute_blocks_preserving_order(blocks[key], orders[key])
        for target_index, source_index in zip(target_indices, source_indices, strict=True):
            target = data[target_index]
            source = data[source_index]
            assignment_by_target[target_index] = PermutedLabelAssignment(
                target_observation_id=target.observation_id,
                target_instrument_id=target.instrument_id,
                source_observation_id=source.observation_id,
                source_instrument_id=source.instrument_id,
                label=source.label,
                reversal_success=source.reversal_success,
            )
    return tuple(assignment_by_target[index] for index in range(len(data)))


def _apply_assignment(
    data: tuple[PermutationDatum, ...],
    assignments: tuple[PermutedLabelAssignment, ...],
) -> tuple[PermutationDatum, ...]:
    if len(data) != len(assignments):
        raise ValueError("permutation assignment must cover every datum")
    result = []
    for target, assignment in zip(data, assignments, strict=True):
        if target.observation_id != assignment.target_observation_id:
            raise ValueError("permutation assignment target order is invalid")
        result.append(
            target.model_copy(
                update={
                    "label": assignment.label,
                    "reversal_success": assignment.reversal_success,
                }
            )
        )
    return tuple(result)


def _nearest_rank(ordered: tuple[Decimal, ...], probability: Decimal) -> Decimal:
    rank = int((probability * Decimal(len(ordered))).to_integral_value(rounding=ROUND_CEILING))
    return ordered[max(0, rank - 1)]


def _mean(values: Sequence[Decimal]) -> Decimal:
    data = tuple(values)
    if not data:
        raise ValueError("statistic requires observations")
    return sum(data, Decimal(0)) / Decimal(len(data))


def _instrument_scope(
    data: tuple[PermutationDatum, ...],
    config: PermutationTestConfig,
) -> str:
    instruments = ",".join(sorted({str(item.instrument_id) for item in data}))
    return instruments if config.within_instrument else f"CROSS_INSTRUMENT_OPT_IN:{instruments}"


def _p_value_method(mode: PermutationMode) -> PermutationPValueMethod:
    if mode is PermutationMode.EXACT:
        return PermutationPValueMethod.EXACT_ENUMERATION
    return PermutationPValueMethod.MONTE_CARLO_PLUS_ONE


def _derive_test_id(
    data: tuple[PermutationDatum, ...],
    config: PermutationTestConfig,
    split_id: ResearchSplitId,
    forward_horizon: int,
    feature_or_condition: str,
) -> PermutationTestId:
    identity = "|".join(
        (
            config.model_dump_json(),
            str(split_id),
            str(forward_horizon),
            feature_or_condition,
            *(str(item.observation_id) for item in data),
        )
    )
    return PermutationTestId(uuid5(_PERMUTATION_TEST_NAMESPACE, identity))


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("statistic requires an available continuous feature")
    return value
