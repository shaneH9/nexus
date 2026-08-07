"""Typed post-shock market-side toxicity features and focused orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import InstrumentId, ShockId, ShockPairId
from sra_nexus.market_data.enums import AggressorSide
from sra_nexus.sra.comparison import (
    FailedAggressionComparison,
    ResiliencyHorizonComparison,
)
from sra_nexus.sra.credibility import (
    LiquidityCredibilityResult,
    LiquidityCredibilityUnavailable,
)
from sra_nexus.sra.effectiveness import ShockPairEffectivenessComparison
from sra_nexus.sra.enums import (
    FlowDirection,
    OrderLifecycleTerminalReason,
    ShockDirection,
    StructuralBreakKind,
    ToxicityUnavailableReason,
)
from sra_nexus.sra.lifecycle import OrderLifecycle
from sra_nexus.sra.liquidity_flow import (
    LiquidityFlowFeatures,
    calculate_liquidity_flow_features,
)
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.shock_pair import ShockPair
from sra_nexus.sra.state import (
    MarketEventReference,
    MarketStateObservation,
    elapsed_decimal_seconds,
)
from sra_nexus.sra.toxicity_math import (
    calculate_bounded_positive_ratio,
    calculate_bounded_replenishment_failure,
    calculate_composite_toxicity,
    calculate_credibility_interactions,
    calculate_decimal_median,
    calculate_delta_toxicity,
    calculate_directional_flow_coverage,
    calculate_directional_impact_change,
    calculate_event_time_realized_volatility,
    calculate_flow_persistence,
    calculate_impact_magnitude_ratio,
    calculate_impact_toxicity_component,
    calculate_raw_replenishment_failure,
    calculate_same_direction_run_length,
    calculate_shock_persistence,
    calculate_spread_expansion_ratio,
    calculate_unknown_flow_share,
    calculate_volatility_jump_ratio,
    classify_flow_direction,
    dominant_shock_direction,
)
from sra_nexus.sra.windows import AggressiveTradeObservation

TOXICITY_VERSION = "toxicity-v1"
TOXICITY_COMPARISON_VERSION = "toxicity-comparison-v1"

UnitIntervalDecimal = Annotated[
    ExactDecimal,
    Field(ge=0, le=1, description="Exact dimensionless value in [0, 1]."),
]


class ToxicityWeights(ContractModel):
    """Initial engineering-prior convex weights for market-side toxicity."""

    flow: UnitIntervalDecimal = Decimal("0.20")
    shock_persistence: UnitIntervalDecimal = Decimal("0.15")
    impact_escalation: UnitIntervalDecimal = Decimal("0.15")
    replenishment_failure: UnitIntervalDecimal = Decimal("0.15")
    spread_expansion: UnitIntervalDecimal = Decimal("0.10")
    volatility_jump: UnitIntervalDecimal = Decimal("0.10")
    withdrawal_pressure: UnitIntervalDecimal = Decimal("0.15")

    @model_validator(mode="after")
    def validate_sum(self) -> Self:
        """Require an exact convex combination."""
        if sum(self.as_tuple(), Decimal(0)) != 1:
            raise ValueError("toxicity weights must sum exactly to one")
        return self

    def as_tuple(self) -> tuple[Decimal, ...]:
        """Return weights in the canonical composite-component order."""
        return (
            self.flow,
            self.shock_persistence,
            self.impact_escalation,
            self.replenishment_failure,
            self.spread_expansion,
            self.volatility_jump,
            self.withdrawal_pressure,
        )


class ToxicityConfig(ContractModel):
    """Central event windows, horizons, transforms, and engineering priors."""

    flow_window_events: int = Field(default=50, gt=0)
    shock_window_count: int = Field(default=4, gt=0)
    impact_horizon_events: int = Field(default=25, gt=0)
    replenishment_horizon_events: int = Field(default=25, gt=0)
    spread_baseline_events: int = Field(default=20, gt=0)
    volatility_baseline_events: int = Field(default=20, gt=0)
    volatility_response_events: int = Field(default=25, gt=0)
    withdrawal_depth_levels: int = Field(default=3, gt=0)
    flow_direction_tolerance: NonNegativeDecimal = Decimal(0)
    weights: ToxicityWeights = Field(default_factory=ToxicityWeights)
    epsilon: PositiveDecimal = Decimal("0.000001")
    feature_version: NonBlankStr = TOXICITY_VERSION


class IndexedMarketStateObservation(ContractModel):
    """Market state paired with an optional true normalized-event index."""

    event_index: int | None = Field(default=None, ge=0)
    observation: MarketStateObservation


class IndexedAggressiveFlowObservation(ContractModel):
    """Volume-owning aggressive observation paired with its normalized index."""

    event_index: int | None = Field(default=None, ge=0)
    observation: AggressiveTradeObservation


class IndexedLiquidityShock(ContractModel):
    """Liquidity shock with caller-supplied all-market-event boundaries."""

    shock: LiquidityShock
    start_event_index: int | None = Field(default=None, ge=0)
    end_event_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_boundaries(self) -> Self:
        """Keep optional index availability paired and ordered."""
        if (self.start_event_index is None) != (self.end_event_index is None):
            raise ValueError("shock event indices must be both present or unavailable")
        if (
            self.start_event_index is not None
            and self.end_event_index is not None
            and self.end_event_index < self.start_event_index
        ):
            raise ValueError("shock end event index cannot precede its start")
        return self


class ToxicityStructuralBreak(ContractModel):
    """Known normalized-event boundary that invalidates spanning windows."""

    kind: StructuralBreakKind
    event_index: int | None = Field(default=None, ge=0)
    event_reference: MarketEventReference


class FlowToxicityFeatures(ContractModel):
    """Signed flow persistence and directional coverage over an exact window."""

    instrument_id: InstrumentId
    window_start_event_index: int = Field(ge=0)
    window_end_event_index: int = Field(ge=0)
    window_start_reference: MarketEventReference
    window_end_reference: MarketEventReference
    signed_event_flows: tuple[ExactDecimal, ...]
    buy_volume: NonNegativeDecimal
    sell_volume: NonNegativeDecimal
    unknown_volume: NonNegativeDecimal
    net_signed_flow: ExactDecimal
    flow_persistence: UnitIntervalDecimal
    net_flow_direction: FlowDirection
    unknown_flow_share: UnitIntervalDecimal
    directional_flow_coverage: UnitIntervalDecimal
    direction_tolerance: NonNegativeDecimal
    epsilon: PositiveDecimal

    @model_validator(mode="after")
    def validate_equations(self) -> Self:
        """Require exact event count, volume coverage, persistence, and direction."""
        if (
            self.window_start_reference.instrument_id != self.instrument_id
            or self.window_end_reference.instrument_id != self.instrument_id
        ):
            raise ValueError("flow window references must share instrument_id")
        if (
            self.window_start_reference.exchange_time > self.window_end_reference.exchange_time
            or self.window_start_reference.process_time > self.window_end_reference.process_time
        ):
            raise ValueError("flow window clocks cannot regress")
        expected_count = self.window_end_event_index - self.window_start_event_index + 1
        if len(self.signed_event_flows) != expected_count:
            raise ValueError("signed flow tuple must contain one value per normalized event")
        if self.net_signed_flow != sum(self.signed_event_flows, Decimal(0)):
            raise ValueError("net signed flow must sum event flows")
        if self.net_signed_flow != self.buy_volume - self.sell_volume:
            raise ValueError("net signed flow must equal BUY minus SELL volume")
        if self.flow_persistence != calculate_flow_persistence(
            self.signed_event_flows,
            self.epsilon,
        ):
            raise ValueError("flow persistence is inconsistent")
        if self.net_flow_direction is not classify_flow_direction(
            self.net_signed_flow,
            self.direction_tolerance,
        ):
            raise ValueError("flow direction is inconsistent")
        if self.unknown_flow_share != calculate_unknown_flow_share(
            self.buy_volume,
            self.sell_volume,
            self.unknown_volume,
        ):
            raise ValueError("unknown flow share is inconsistent")
        if self.directional_flow_coverage != calculate_directional_flow_coverage(
            self.buy_volume,
            self.sell_volume,
            self.unknown_volume,
        ):
            raise ValueError("directional flow coverage is inconsistent")
        return self


class ShockRun(ContractModel):
    """Most recent contiguous same-direction suffix of qualifying shocks."""

    instrument_id: InstrumentId
    direction: ShockDirection
    shock_ids: tuple[ShockId, ...]
    count: int = Field(gt=0)
    first_shock_time: UtcDatetime
    last_shock_time: UtcDatetime
    first_start_event_index: int = Field(ge=0)
    last_end_event_index: int = Field(ge=0)
    event_span: int = Field(ge=0)
    exchange_seconds_span: NonNegativeDecimal

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        """Require exact IDs, index span, and nonnegative exchange span."""
        if self.count != len(self.shock_ids):
            raise ValueError("shock run count must equal shock ID count")
        if len(set(self.shock_ids)) != len(self.shock_ids):
            raise ValueError("shock run IDs must be unique")
        if self.event_span != self.last_end_event_index - self.first_start_event_index:
            raise ValueError("shock run event span must equal last end minus first start")
        if self.last_shock_time < self.first_shock_time:
            raise ValueError("shock run exchange time cannot regress")
        expected_seconds = elapsed_decimal_seconds(
            self.first_shock_time,
            self.last_shock_time,
        )
        if self.exchange_seconds_span != expected_seconds:
            raise ValueError("shock run exchange span is inconsistent")
        return self


class ShockPersistenceFeatures(ContractModel):
    """Direction persistence and latest run over the last N qualifying shocks."""

    instrument_id: InstrumentId
    shock_ids: tuple[ShockId, ...]
    directions: tuple[ShockDirection, ...]
    shock_persistence: UnitIntervalDecimal
    dominant_shock_direction: FlowDirection
    same_direction_run_length: int = Field(gt=0)
    latest_run: ShockRun

    @model_validator(mode="after")
    def validate_equations(self) -> Self:
        """Require aligned shocks and exact persistence/run values."""
        if not self.shock_ids or len(self.shock_ids) != len(self.directions):
            raise ValueError("shock persistence requires aligned nonempty shocks")
        if self.shock_persistence != calculate_shock_persistence(self.directions):
            raise ValueError("shock persistence is inconsistent")
        if self.dominant_shock_direction is not dominant_shock_direction(self.directions):
            raise ValueError("dominant shock direction is inconsistent")
        if self.same_direction_run_length != calculate_same_direction_run_length(self.directions):
            raise ValueError("same-direction shock run length is inconsistent")
        if self.latest_run.count != self.same_direction_run_length:
            raise ValueError("latest shock run must match run length")
        if self.latest_run.instrument_id != self.instrument_id:
            raise ValueError("latest shock run must share instrument_id")
        if self.latest_run.direction is not self.directions[-1]:
            raise ValueError("latest shock run must use the latest direction")
        if self.latest_run.shock_ids != self.shock_ids[-self.same_direction_run_length :]:
            raise ValueError("latest shock run IDs must be the same-direction suffix")
        return self


class ImpactToxicityFeatures(ContractModel):
    """Signed impact escalation for an existing comparable same-direction pair."""

    pair_id: ShockPairId
    horizon_events: int = Field(gt=0)
    previous_directional_impact: ExactDecimal
    current_directional_impact: ExactDecimal
    directional_impact_change: ExactDecimal
    impact_magnitude_ratio: NonNegativeDecimal
    impact_toxicity_component: UnitIntervalDecimal
    delta_aggressor_effectiveness: ExactDecimal
    epsilon: PositiveDecimal

    @model_validator(mode="after")
    def validate_equations(self) -> Self:
        """Require exact impact delta, ratio, and bounded component."""
        if self.directional_impact_change != calculate_directional_impact_change(
            self.previous_directional_impact,
            self.current_directional_impact,
        ):
            raise ValueError("directional impact change is inconsistent")
        if self.impact_magnitude_ratio != calculate_impact_magnitude_ratio(
            self.previous_directional_impact,
            self.current_directional_impact,
            self.epsilon,
        ):
            raise ValueError("impact magnitude ratio is inconsistent")
        if self.impact_toxicity_component != calculate_impact_toxicity_component(
            self.previous_directional_impact,
            self.current_directional_impact,
            self.epsilon,
        ):
            raise ValueError("impact toxicity component is inconsistent")
        return self


class ReplenishmentToxicityFeatures(ContractModel):
    """Raw and bounded recovery failure without redefining canonical RR."""

    shock_id: ShockId
    horizon_events: int = Field(gt=0)
    replenishment_ratio: ExactDecimal
    raw_replenishment_failure: ExactDecimal
    bounded_replenishment_failure: UnitIntervalDecimal

    @model_validator(mode="after")
    def validate_equations(self) -> Self:
        """Require exact raw and explicitly bounded failure transforms."""
        if self.raw_replenishment_failure != calculate_raw_replenishment_failure(
            self.replenishment_ratio
        ):
            raise ValueError("raw replenishment failure is inconsistent")
        if self.bounded_replenishment_failure != (
            calculate_bounded_replenishment_failure(self.replenishment_ratio)
        ):
            raise ValueError("bounded replenishment failure is inconsistent")
        return self


class CredibilityToxicityFeatures(ContractModel):
    """Optional RR/LC interactions with explicit ordinary unavailability."""

    available: bool
    liquidity_credibility: UnitIntervalDecimal | None
    credible_absorption: ExactDecimal | None
    toxic_replenishment: ExactDecimal | None
    source_available_at_process_time: UtcDatetime | None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        """Require all interactions together or none without substituting LC."""
        values = (
            self.liquidity_credibility,
            self.credible_absorption,
            self.toxic_replenishment,
            self.source_available_at_process_time,
        )
        if self.available and any(value is None for value in values):
            raise ValueError("available credibility interactions require every value")
        if not self.available and any(value is not None for value in values):
            raise ValueError("unavailable credibility interactions cannot fabricate values")
        return self


class SpreadToxicityFeatures(ContractModel):
    """Median pre-window spread and one explicit post-shock spread response."""

    baseline_event_count: int = Field(gt=0)
    baseline_spread: NonNegativeDecimal
    post_shock_horizon_events: int = Field(gt=0)
    post_shock_spread: NonNegativeDecimal
    absolute_spread_change: ExactDecimal
    spread_expansion_ratio: NonNegativeDecimal
    bounded_spread_expansion: UnitIntervalDecimal
    epsilon: PositiveDecimal

    @model_validator(mode="after")
    def validate_equations(self) -> Self:
        """Require exact absolute, ratio, and bounded spread values."""
        if self.absolute_spread_change != self.post_shock_spread - self.baseline_spread:
            raise ValueError("absolute spread change is inconsistent")
        expected_ratio = calculate_spread_expansion_ratio(
            self.baseline_spread,
            self.post_shock_spread,
            self.epsilon,
        )
        if self.spread_expansion_ratio != expected_ratio:
            raise ValueError("spread expansion ratio is inconsistent")
        if self.bounded_spread_expansion != calculate_bounded_positive_ratio(expected_ratio):
            raise ValueError("bounded spread expansion is inconsistent")
        return self


class VolatilityToxicityFeatures(ContractModel):
    """Pre/post event-time arithmetic-return RMS volatility."""

    baseline_return_count: int = Field(gt=0)
    response_return_count: int = Field(gt=0)
    pre_shock_realized_volatility: NonNegativeDecimal
    post_shock_realized_volatility: NonNegativeDecimal
    volatility_jump_ratio: NonNegativeDecimal
    bounded_volatility_jump: UnitIntervalDecimal
    epsilon: PositiveDecimal

    @model_validator(mode="after")
    def validate_equations(self) -> Self:
        """Require exact zero-safe jump ratio and bounded transform."""
        expected_ratio = calculate_volatility_jump_ratio(
            self.pre_shock_realized_volatility,
            self.post_shock_realized_volatility,
            self.epsilon,
        )
        if self.volatility_jump_ratio != expected_ratio:
            raise ValueError("volatility jump ratio is inconsistent")
        if self.bounded_volatility_jump != calculate_bounded_positive_ratio(expected_ratio):
            raise ValueError("bounded volatility jump is inconsistent")
        return self


class MarketStateToxicityFeatures(ContractModel):
    """Spread and short-horizon event-time volatility response components."""

    instrument_id: InstrumentId
    spread: SpreadToxicityFeatures
    volatility: VolatilityToxicityFeatures
    one_sided_book: Literal[False] = False


class ToxicityVector(ContractModel):
    """Complete post-shock market-side toxicity vector without a trade decision."""

    instrument_id: InstrumentId
    shock_id: ShockId
    as_of_event_reference: MarketEventReference
    flow: FlowToxicityFeatures
    shock_persistence: ShockPersistenceFeatures
    impact: ImpactToxicityFeatures
    replenishment: ReplenishmentToxicityFeatures
    liquidity: LiquidityFlowFeatures
    market_state: MarketStateToxicityFeatures
    credibility: CredibilityToxicityFeatures
    observation_start_event_index: int = Field(ge=0)
    observation_end_event_index: int = Field(ge=0)
    observation_start_reference: MarketEventReference
    observation_end_reference: MarketEventReference
    available_at_process_time: UtcDatetime
    toxicity_score: UnitIntervalDecimal
    configuration: ToxicityConfig
    feature_version: NonBlankStr = TOXICITY_VERSION
    research_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_vector(self) -> Self:
        """Require aligned ownership, exact composite score, and availability."""
        if self.observation_end_event_index < self.observation_start_event_index:
            raise ValueError("toxicity observation window cannot have negative span")
        if self.as_of_event_reference != self.observation_end_reference:
            raise ValueError("toxicity as-of reference must equal observation end")
        if any(
            instrument_id != self.instrument_id
            for instrument_id in (
                self.flow.instrument_id,
                self.shock_persistence.instrument_id,
                self.market_state.instrument_id,
            )
        ):
            raise ValueError("toxicity components must share instrument_id")
        if self.replenishment.shock_id != self.shock_id:
            raise ValueError("replenishment toxicity must belong to vector shock")
        if self.shock_persistence.shock_ids[-1] != self.shock_id:
            raise ValueError("toxicity shock must be the latest persistence shock")
        if (
            self.liquidity.window_start_reference.instrument_id != self.instrument_id
            or self.liquidity.window_end_reference.instrument_id != self.instrument_id
        ):
            raise ValueError("liquidity toxicity must share instrument_id")
        if self.credibility.available:
            credibility = self.credibility.liquidity_credibility
            credible = self.credibility.credible_absorption
            toxic = self.credibility.toxic_replenishment
            if credibility is None or credible is None or toxic is None:
                raise AssertionError("validated credibility interactions are incomplete")
            expected_credible, expected_toxic = calculate_credibility_interactions(
                self.replenishment.replenishment_ratio,
                credibility,
            )
            if (credible, toxic) != (expected_credible, expected_toxic):
                raise ValueError("credibility interactions are inconsistent")
        if self.feature_version != self.configuration.feature_version:
            raise ValueError("toxicity feature version must match its configuration")
        expected_score = calculate_composite_toxicity(
            _score_components(
                self.flow,
                self.shock_persistence,
                self.impact,
                self.replenishment,
                self.liquidity,
                self.market_state,
            ),
            self.configuration.weights.as_tuple(),
        )
        if self.toxicity_score != expected_score:
            raise ValueError("toxicity score is inconsistent with components and weights")
        required_availability = self.observation_end_reference.process_time
        if (
            self.credibility.available
            and self.credibility.source_available_at_process_time is not None
        ):
            required_availability = max(
                required_availability,
                self.credibility.source_available_at_process_time,
            )
        if self.available_at_process_time != required_availability:
            raise ValueError("toxicity availability must equal latest included evidence")
        return self


class ToxicityUnavailable(ContractModel):
    """Typed ordinary unavailability without partially fabricated components."""

    instrument_id: InstrumentId
    shock_id: ShockId
    reasons: tuple[ToxicityUnavailableReason, ...]
    observation_end_event_index: int | None = Field(default=None, ge=0)
    observation_end_reference: MarketEventReference | None = None
    available_at_process_time: UtcDatetime | None = None
    structural_breaks: tuple[ToxicityStructuralBreak, ...] = ()
    one_sided_book: bool = False
    toxicity_available: Literal[False] = False
    feature_version: NonBlankStr = TOXICITY_VERSION

    @model_validator(mode="after")
    def validate_unavailable(self) -> Self:
        """Require reasons and coherent optional observation availability."""
        if not self.reasons:
            raise ValueError("unavailable toxicity requires at least one reason")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("toxicity unavailable reasons must be unique")
        if self.observation_end_reference is None:
            if self.available_at_process_time is not None:
                raise ValueError("availability requires an observation-end reference")
        elif self.available_at_process_time != self.observation_end_reference.process_time:
            raise ValueError("unavailable observation time must own availability")
        return self


type ToxicityAnalysis = ToxicityVector | ToxicityUnavailable


class ToxicityComparison(ContractModel):
    """Optional descriptive toxicity-score change for an existing ShockPair."""

    pair_id: ShockPairId
    shock_1_id: ShockId
    shock_2_id: ShockId
    toxicity_1: UnitIntervalDecimal
    toxicity_2: UnitIntervalDecimal
    delta_toxicity: ExactDecimal
    comparison_version: NonBlankStr = TOXICITY_COMPARISON_VERSION

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        """Require exact ``DeltaToxicity = T_2 - T_1``."""
        if self.delta_toxicity != calculate_delta_toxicity(
            self.toxicity_1,
            self.toxicity_2,
        ):
            raise ValueError("delta toxicity is inconsistent")
        return self


class ToxicityService:
    """Construct complete post-shock market toxicity from explicit windows."""

    def __init__(self, config: ToxicityConfig | None = None) -> None:
        """Configure deterministic windows, horizons, and engineering priors."""
        self._config = ToxicityConfig() if config is None else config

    @property
    def config(self) -> ToxicityConfig:
        """Return the immutable reproducibility policy."""
        return self._config

    def analyze(
        self,
        *,
        current_shock: IndexedLiquidityShock,
        shock_history: Sequence[IndexedLiquidityShock],
        flow_observations: Sequence[IndexedAggressiveFlowObservation],
        market_observations: Sequence[IndexedMarketStateObservation],
        lifecycles: Sequence[OrderLifecycle],
        comparison: FailedAggressionComparison | None,
        liquidity_credibility: (
            LiquidityCredibilityResult | LiquidityCredibilityUnavailable | None
        ) = None,
        structural_breaks: Sequence[ToxicityStructuralBreak] = (),
    ) -> ToxicityAnalysis:
        """Return a complete vector or typed unavailability without look-ahead."""
        shock = current_shock.shock
        reasons: list[ToxicityUnavailableReason] = []
        all_shocks = tuple(shock_history)
        flows = tuple(flow_observations)
        states = tuple(market_observations)
        lifecycle_values = tuple(lifecycles)
        breaks = tuple(structural_breaks)

        if (
            current_shock.start_event_index is None
            or current_shock.end_event_index is None
            or any(
                item.start_event_index is None or item.end_event_index is None
                for item in all_shocks
            )
            or any(item.event_index is None for item in flows)
            or any(item.event_index is None for item in states)
            or any(item.event_index is None for item in breaks)
            or any(
                transition.event.event_index is None
                for lifecycle in lifecycle_values
                for transition in lifecycle.transitions
            )
            or any(
                lifecycle.terminal_reason is OrderLifecycleTerminalReason.RESET
                and lifecycle.last_event_index is None
                for lifecycle in lifecycle_values
            )
        ):
            reasons.append(ToxicityUnavailableReason.MISSING_EVENT_INDEX)
            return _unavailable(
                shock,
                reasons,
                feature_version=self._config.feature_version,
            )

        start_index = _required_shock_start(current_shock)
        end_index = _required_shock_end(current_shock)
        required_end = end_index + max(
            self._config.impact_horizon_events,
            self._config.replenishment_horizon_events,
            self._config.volatility_response_events,
        )
        market_index = _index_market_observations(
            states,
            shock.instrument_id,
            shock.end_reference.venue,
        )
        _validate_indexed_shock_boundaries(current_shock, market_index)
        observation_end = market_index.get(required_end)
        observation_reference = (
            None if observation_end is None else observation_end.observation.event_reference
        )

        selected_shocks = _select_shock_history(
            all_shocks,
            current_shock,
            self._config.shock_window_count,
        )
        if selected_shocks is None:
            reasons.append(ToxicityUnavailableReason.INSUFFICIENT_SHOCK_HISTORY)

        flow_start = required_end - self._config.flow_window_events + 1
        spread_start = start_index - self._config.spread_baseline_events
        spread_end = start_index - 1
        volatility_start = start_index - self._config.volatility_baseline_events - 1
        volatility_end = start_index - 1
        post_volatility_end = end_index + self._config.volatility_response_events
        liquidity_end = end_index + self._config.replenishment_horizon_events
        component_start = min(flow_start, spread_start, volatility_start, start_index)
        structural_start = component_start
        if selected_shocks is not None:
            directions = tuple(item.shock.direction for item in selected_shocks)
            run_length = calculate_same_direction_run_length(directions)
            structural_start = min(
                structural_start,
                _required_shock_start(selected_shocks[-run_length]),
            )
        _validate_window_evidence_references(
            shock,
            flows,
            lifecycle_values,
            breaks,
            market_index,
            flow_start,
            required_end,
            start_index,
            liquidity_end,
        )

        if flow_start < 0 or not _has_complete_range(
            market_index,
            flow_start,
            required_end,
        ):
            reasons.append(ToxicityUnavailableReason.INSUFFICIENT_FLOW_WINDOW)
        pre_snapshot_item = market_index.get(start_index - 1)
        if pre_snapshot_item is None or not lifecycle_values:
            reasons.append(ToxicityUnavailableReason.MISSING_BOOK_STATE)
        spread_items = _range_items(market_index, spread_start, spread_end)
        post_spread_item = market_index.get(end_index + self._config.impact_horizon_events)
        one_sided = (
            any(item.observation.snapshot.spread is None for item in spread_items)
            or post_spread_item is None
            or (
                post_spread_item is not None
                and post_spread_item.observation.snapshot.spread is None
            )
        )
        if (
            spread_start < 0
            or len(spread_items) != self._config.spread_baseline_events
            or post_spread_item is None
            or one_sided
        ):
            reasons.append(ToxicityUnavailableReason.MISSING_SPREAD)
        pre_volatility_items = _range_items(
            market_index,
            volatility_start,
            volatility_end,
        )
        post_volatility_items = _range_items(
            market_index,
            end_index,
            post_volatility_end,
        )
        if (
            volatility_start < 0
            or len(pre_volatility_items) != self._config.volatility_baseline_events + 1
            or len(post_volatility_items) != self._config.volatility_response_events + 1
            or any(
                item.observation.snapshot.midprice is None
                for item in (*pre_volatility_items, *post_volatility_items)
            )
        ):
            reasons.append(ToxicityUnavailableReason.MISSING_VOLATILITY_WINDOW)
        if observation_end is None or not _has_complete_range(
            market_index,
            start_index,
            liquidity_end,
        ):
            reasons.append(ToxicityUnavailableReason.MISSING_BOOK_STATE)

        impact_comparison, resiliency_comparison = _comparison_components(
            comparison,
            shock,
            self._config,
        )
        if impact_comparison is None:
            reasons.append(ToxicityUnavailableReason.MISSING_IMPACT)
        if resiliency_comparison is None:
            reasons.append(ToxicityUnavailableReason.MISSING_RESILIENCY)

        reset_breaks = _reset_breaks_from_lifecycles(
            lifecycle_values,
            structural_start,
            required_end,
        )
        applicable_breaks = tuple(
            item
            for item in breaks
            if item.event_index is not None and structural_start <= item.event_index <= required_end
        )
        if applicable_breaks or reset_breaks:
            reasons.append(ToxicityUnavailableReason.STRUCTURAL_BREAK)

        reasons = list(dict.fromkeys(reasons))
        if reasons:
            return _unavailable(
                shock,
                reasons,
                observation_end_event_index=(
                    required_end if observation_reference is not None else None
                ),
                observation_end_reference=observation_reference,
                structural_breaks=applicable_breaks,
                one_sided_book=one_sided,
                feature_version=self._config.feature_version,
            )

        if (
            selected_shocks is None
            or pre_snapshot_item is None
            or observation_end is None
            or post_spread_item is None
            or impact_comparison is None
            or resiliency_comparison is None
        ):
            raise AssertionError("validated toxicity inputs unexpectedly unavailable")

        flow_features = _build_flow_features(
            shock.instrument_id,
            flows,
            market_index,
            flow_start,
            required_end,
            self._config,
        )
        shock_features = _build_shock_persistence(selected_shocks)
        impact_features = _build_impact_features(
            impact_comparison,
            self._config.epsilon,
        )
        rr_2 = resiliency_comparison.rr_2
        if rr_2 is None:
            raise AssertionError("validated resiliency comparison unexpectedly lacks RR_2")
        replenishment_features = ReplenishmentToxicityFeatures(
            shock_id=shock.shock_id,
            horizon_events=self._config.replenishment_horizon_events,
            replenishment_ratio=rr_2,
            raw_replenishment_failure=calculate_raw_replenishment_failure(rr_2),
            bounded_replenishment_failure=(calculate_bounded_replenishment_failure(rr_2)),
        )
        liquidity_features = calculate_liquidity_flow_features(
            direction=shock.direction,
            pre_shock_snapshot=pre_snapshot_item.observation.snapshot,
            lifecycles=lifecycle_values,
            window_start_event_index=start_index,
            window_end_event_index=liquidity_end,
            window_start_reference=market_index[start_index].observation.event_reference,
            window_end_reference=market_index[liquidity_end].observation.event_reference,
            depth_levels=self._config.withdrawal_depth_levels,
            epsilon=self._config.epsilon,
        )
        market_features = _build_market_state_features(
            shock.instrument_id,
            spread_items,
            post_spread_item,
            pre_volatility_items,
            post_volatility_items,
            self._config,
        )
        credibility_features = _build_credibility_features(
            liquidity_credibility,
            shock,
            rr_2,
            required_end,
        )
        run_start = shock_features.latest_run.first_start_event_index
        observation_start_index = min(component_start, run_start)
        observation_start_reference = _observation_start_reference(
            selected_shocks,
            market_index,
            observation_start_index,
        )
        score = calculate_composite_toxicity(
            _score_components(
                flow_features,
                shock_features,
                impact_features,
                replenishment_features,
                liquidity_features,
                market_features,
            ),
            self._config.weights.as_tuple(),
        )
        availability = observation_end.observation.event_reference.process_time
        if (
            credibility_features.available
            and credibility_features.source_available_at_process_time is not None
        ):
            availability = max(
                availability,
                credibility_features.source_available_at_process_time,
            )
        return ToxicityVector(
            instrument_id=shock.instrument_id,
            shock_id=shock.shock_id,
            as_of_event_reference=observation_end.observation.event_reference,
            flow=flow_features,
            shock_persistence=shock_features,
            impact=impact_features,
            replenishment=replenishment_features,
            liquidity=liquidity_features,
            market_state=market_features,
            credibility=credibility_features,
            observation_start_event_index=observation_start_index,
            observation_end_event_index=required_end,
            observation_start_reference=observation_start_reference,
            observation_end_reference=observation_end.observation.event_reference,
            available_at_process_time=availability,
            toxicity_score=score,
            configuration=self._config,
            feature_version=self._config.feature_version,
        )


def compare_toxicity(
    pair: ShockPair,
    toxicity_1: ToxicityVector,
    toxicity_2: ToxicityVector,
) -> ToxicityComparison:
    """Return exact DeltaToxicity for matching results on an existing pair."""
    if toxicity_1.shock_id != pair.shock_1_id or toxicity_2.shock_id != pair.shock_2_id:
        raise ValueError("toxicity vectors must follow the ordered shock pair")
    if (
        toxicity_1.instrument_id != pair.instrument_id
        or toxicity_2.instrument_id != pair.instrument_id
    ):
        raise ValueError("toxicity vectors must share the pair instrument")
    return ToxicityComparison(
        pair_id=pair.pair_id,
        shock_1_id=pair.shock_1_id,
        shock_2_id=pair.shock_2_id,
        toxicity_1=toxicity_1.toxicity_score,
        toxicity_2=toxicity_2.toxicity_score,
        delta_toxicity=calculate_delta_toxicity(
            toxicity_1.toxicity_score,
            toxicity_2.toxicity_score,
        ),
    )


def _index_market_observations(
    observations: tuple[IndexedMarketStateObservation, ...],
    instrument_id: InstrumentId,
    venue: str,
) -> dict[int, IndexedMarketStateObservation]:
    indexed: dict[int, IndexedMarketStateObservation] = {}
    ordered = sorted(observations, key=lambda item: _required_market_index(item))
    for item in ordered:
        index = _required_market_index(item)
        if item.observation.event_reference.instrument_id != instrument_id:
            raise ValueError("toxicity market observations must share instrument_id")
        if item.observation.event_reference.venue != venue:
            raise ValueError("toxicity market observations must share shock venue")
        if index in indexed:
            raise ValueError("toxicity market-event indices must be unique")
        indexed[index] = item
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if (
            earlier.observation.event_reference.exchange_time
            > later.observation.event_reference.exchange_time
            or earlier.observation.event_reference.process_time
            > later.observation.event_reference.process_time
        ):
            raise ValueError("toxicity market-observation clocks cannot regress")
    return indexed


def _validate_indexed_shock_boundaries(
    shock: IndexedLiquidityShock,
    market_index: dict[int, IndexedMarketStateObservation],
) -> None:
    start = market_index.get(_required_shock_start(shock))
    end = market_index.get(_required_shock_end(shock))
    if start is not None and start.observation.event_reference != shock.shock.start_reference:
        raise ValueError("shock start index must identify its start reference")
    if end is not None and end.observation.event_reference != shock.shock.end_reference:
        raise ValueError("shock end index must identify its end reference")


def _validate_window_evidence_references(
    shock: LiquidityShock,
    flows: tuple[IndexedAggressiveFlowObservation, ...],
    lifecycles: tuple[OrderLifecycle, ...],
    breaks: tuple[ToxicityStructuralBreak, ...],
    market_index: dict[int, IndexedMarketStateObservation],
    flow_start: int,
    required_end: int,
    liquidity_start: int,
    liquidity_end: int,
) -> None:
    if any(
        item.event_reference.instrument_id != shock.instrument_id
        or item.event_reference.venue != shock.end_reference.venue
        for item in breaks
    ):
        raise ValueError("toxicity structural breaks must share shock instrument and venue")
    for break_item in breaks:
        break_index = _required_break_index(break_item)
        market_item = market_index.get(break_index)
        if (
            market_item is not None
            and market_item.observation.event_reference != break_item.event_reference
        ):
            raise ValueError("structural-break index must identify its event reference")
    for flow_item in flows:
        flow_index = _required_flow_index(flow_item)
        if not flow_start <= flow_index <= required_end:
            continue
        market_item = market_index.get(flow_index)
        if market_item is None or flow_item.observation.reference != (
            market_item.observation.event_reference
        ):
            raise ValueError("flow observation must match its normalized market event")
    if any(
        lifecycle.instrument_id != shock.instrument_id
        or lifecycle.venue != shock.end_reference.venue
        for lifecycle in lifecycles
    ):
        raise ValueError("toxicity lifecycles must share shock instrument and venue")
    for lifecycle in lifecycles:
        for transition in lifecycle.transitions:
            transition_index = transition.event.event_index
            if transition_index is None or not liquidity_start <= transition_index <= liquidity_end:
                continue
            market_item = market_index.get(transition_index)
            if market_item is None or transition.event.reference != (
                market_item.observation.event_reference
            ):
                raise ValueError("lifecycle transition must match its normalized market event")


def _select_shock_history(
    history: tuple[IndexedLiquidityShock, ...],
    current: IndexedLiquidityShock,
    count: int,
) -> tuple[IndexedLiquidityShock, ...] | None:
    if any(item.shock.instrument_id != current.shock.instrument_id for item in history):
        raise ValueError("toxicity shock history must share instrument_id")
    ordered = tuple(sorted(history, key=_required_shock_end))
    if len({item.shock.shock_id for item in ordered}) != len(ordered):
        raise ValueError("toxicity shock history cannot repeat shock IDs")
    eligible = tuple(
        item for item in ordered if _required_shock_end(item) <= _required_shock_end(current)
    )
    if not eligible or eligible[-1].shock.shock_id != current.shock.shock_id:
        raise ValueError("current shock must be the latest qualifying history shock")
    if len(eligible) < count:
        return None
    return eligible[-count:]


def _build_flow_features(
    instrument_id: InstrumentId,
    observations: tuple[IndexedAggressiveFlowObservation, ...],
    market_index: dict[int, IndexedMarketStateObservation],
    start_index: int,
    end_index: int,
    config: ToxicityConfig,
) -> FlowToxicityFeatures:
    by_index: dict[int, list[Decimal]] = {
        index: [Decimal(0), Decimal(0), Decimal(0)] for index in range(start_index, end_index + 1)
    }
    for item in observations:
        index = _required_flow_index(item)
        if not start_index <= index <= end_index:
            continue
        observation = item.observation
        if observation.instrument_id != instrument_id:
            raise ValueError("toxicity flow observations must share instrument_id")
        market_item = market_index[index]
        if observation.reference != market_item.observation.event_reference:
            raise ValueError("flow observation must match its normalized market event")
        if observation.aggressor_side is AggressorSide.BUY:
            by_index[index][0] += observation.quantity
        elif observation.aggressor_side is AggressorSide.SELL:
            by_index[index][1] += observation.quantity
        else:
            by_index[index][2] += observation.quantity
    buy = sum((values[0] for values in by_index.values()), Decimal(0))
    sell = sum((values[1] for values in by_index.values()), Decimal(0))
    unknown = sum((values[2] for values in by_index.values()), Decimal(0))
    signed = tuple(
        by_index[index][0] - by_index[index][1] for index in range(start_index, end_index + 1)
    )
    net = buy - sell
    return FlowToxicityFeatures(
        instrument_id=instrument_id,
        window_start_event_index=start_index,
        window_end_event_index=end_index,
        window_start_reference=market_index[start_index].observation.event_reference,
        window_end_reference=market_index[end_index].observation.event_reference,
        signed_event_flows=signed,
        buy_volume=buy,
        sell_volume=sell,
        unknown_volume=unknown,
        net_signed_flow=net,
        flow_persistence=calculate_flow_persistence(signed, config.epsilon),
        net_flow_direction=classify_flow_direction(
            net,
            config.flow_direction_tolerance,
        ),
        unknown_flow_share=calculate_unknown_flow_share(buy, sell, unknown),
        directional_flow_coverage=calculate_directional_flow_coverage(
            buy,
            sell,
            unknown,
        ),
        direction_tolerance=config.flow_direction_tolerance,
        epsilon=config.epsilon,
    )


def _build_shock_persistence(
    shocks: tuple[IndexedLiquidityShock, ...],
) -> ShockPersistenceFeatures:
    directions = tuple(item.shock.direction for item in shocks)
    run_length = calculate_same_direction_run_length(directions)
    run_items = shocks[-run_length:]
    first = run_items[0]
    last = run_items[-1]
    run = ShockRun(
        instrument_id=last.shock.instrument_id,
        direction=last.shock.direction,
        shock_ids=tuple(item.shock.shock_id for item in run_items),
        count=run_length,
        first_shock_time=first.shock.start_exchange_time,
        last_shock_time=last.shock.end_exchange_time,
        first_start_event_index=_required_shock_start(first),
        last_end_event_index=_required_shock_end(last),
        event_span=_required_shock_end(last) - _required_shock_start(first),
        exchange_seconds_span=elapsed_decimal_seconds(
            first.shock.start_exchange_time,
            last.shock.end_exchange_time,
        ),
    )
    return ShockPersistenceFeatures(
        instrument_id=last.shock.instrument_id,
        shock_ids=tuple(item.shock.shock_id for item in shocks),
        directions=directions,
        shock_persistence=calculate_shock_persistence(directions),
        dominant_shock_direction=dominant_shock_direction(directions),
        same_direction_run_length=run_length,
        latest_run=run,
    )


def _comparison_components(
    comparison: FailedAggressionComparison | None,
    shock: LiquidityShock,
    config: ToxicityConfig,
) -> tuple[
    ShockPairEffectivenessComparison | None,
    ResiliencyHorizonComparison | None,
]:
    if (
        comparison is None
        or not comparison.comparison_available
        or comparison.pair is None
        or comparison.pair.shock_2_id != shock.shock_id
        or comparison.pair.instrument_id != shock.instrument_id
        or comparison.pair.direction is not shock.direction
    ):
        return None, None
    impact = next(
        (
            item
            for item in comparison.effectiveness_by_horizon
            if item.horizon_events == config.impact_horizon_events
        ),
        None,
    )
    resiliency = next(
        (
            item
            for item in comparison.resiliency_by_horizon
            if item.horizon_events == config.replenishment_horizon_events and item.available
        ),
        None,
    )
    return impact, resiliency


def _build_impact_features(
    comparison: ShockPairEffectivenessComparison,
    epsilon: Decimal,
) -> ImpactToxicityFeatures:
    previous = comparison.ae_1.directional_price_impact
    current = comparison.ae_2.directional_price_impact
    return ImpactToxicityFeatures(
        pair_id=comparison.pair_id,
        horizon_events=comparison.horizon_events,
        previous_directional_impact=previous,
        current_directional_impact=current,
        directional_impact_change=calculate_directional_impact_change(
            previous,
            current,
        ),
        impact_magnitude_ratio=calculate_impact_magnitude_ratio(
            previous,
            current,
            epsilon,
        ),
        impact_toxicity_component=calculate_impact_toxicity_component(
            previous,
            current,
            epsilon,
        ),
        delta_aggressor_effectiveness=comparison.delta_ae,
        epsilon=epsilon,
    )


def _build_market_state_features(
    instrument_id: InstrumentId,
    spread_items: tuple[IndexedMarketStateObservation, ...],
    post_spread_item: IndexedMarketStateObservation,
    pre_volatility_items: tuple[IndexedMarketStateObservation, ...],
    post_volatility_items: tuple[IndexedMarketStateObservation, ...],
    config: ToxicityConfig,
) -> MarketStateToxicityFeatures:
    spreads = tuple(
        _required_decimal(item.observation.snapshot.spread, "baseline spread")
        for item in spread_items
    )
    baseline_spread = calculate_decimal_median(spreads)
    post_spread = _required_decimal(
        post_spread_item.observation.snapshot.spread,
        "post-shock spread",
    )
    spread_ratio = calculate_spread_expansion_ratio(
        baseline_spread,
        post_spread,
        config.epsilon,
    )
    pre_midprices = tuple(
        _required_decimal(item.observation.snapshot.midprice, "pre-shock midprice")
        for item in pre_volatility_items
    )
    post_midprices = tuple(
        _required_decimal(item.observation.snapshot.midprice, "post-shock midprice")
        for item in post_volatility_items
    )
    pre_rv = calculate_event_time_realized_volatility(pre_midprices)
    post_rv = calculate_event_time_realized_volatility(post_midprices)
    volatility_ratio = calculate_volatility_jump_ratio(
        pre_rv,
        post_rv,
        config.epsilon,
    )
    return MarketStateToxicityFeatures(
        instrument_id=instrument_id,
        spread=SpreadToxicityFeatures(
            baseline_event_count=config.spread_baseline_events,
            baseline_spread=baseline_spread,
            post_shock_horizon_events=config.impact_horizon_events,
            post_shock_spread=post_spread,
            absolute_spread_change=post_spread - baseline_spread,
            spread_expansion_ratio=spread_ratio,
            bounded_spread_expansion=calculate_bounded_positive_ratio(spread_ratio),
            epsilon=config.epsilon,
        ),
        volatility=VolatilityToxicityFeatures(
            baseline_return_count=config.volatility_baseline_events,
            response_return_count=config.volatility_response_events,
            pre_shock_realized_volatility=pre_rv,
            post_shock_realized_volatility=post_rv,
            volatility_jump_ratio=volatility_ratio,
            bounded_volatility_jump=calculate_bounded_positive_ratio(volatility_ratio),
            epsilon=config.epsilon,
        ),
    )


def _build_credibility_features(
    credibility: LiquidityCredibilityResult | LiquidityCredibilityUnavailable | None,
    shock: LiquidityShock,
    replenishment_ratio: Decimal,
    observation_end_index: int,
) -> CredibilityToxicityFeatures:
    if not isinstance(credibility, LiquidityCredibilityResult):
        return CredibilityToxicityFeatures(
            available=False,
            liquidity_credibility=None,
            credible_absorption=None,
            toxic_replenishment=None,
            source_available_at_process_time=None,
        )
    if credibility.shock_id != shock.shock_id:
        raise ValueError("liquidity credibility must belong to the current shock")
    if credibility.observation_end_event_index > observation_end_index:
        raise ValueError("liquidity credibility cannot use events beyond toxicity horizon")
    if credibility.credibility_score is None:
        return CredibilityToxicityFeatures(
            available=False,
            liquidity_credibility=None,
            credible_absorption=None,
            toxic_replenishment=None,
            source_available_at_process_time=None,
        )
    credible_absorption, toxic_replenishment = calculate_credibility_interactions(
        replenishment_ratio,
        credibility.credibility_score,
    )
    return CredibilityToxicityFeatures(
        available=True,
        liquidity_credibility=credibility.credibility_score,
        credible_absorption=credible_absorption,
        toxic_replenishment=toxic_replenishment,
        source_available_at_process_time=credibility.available_at_process_time,
    )


def _score_components(
    flow: FlowToxicityFeatures,
    shock: ShockPersistenceFeatures,
    impact: ImpactToxicityFeatures,
    replenishment: ReplenishmentToxicityFeatures,
    liquidity: LiquidityFlowFeatures,
    market: MarketStateToxicityFeatures,
) -> tuple[Decimal, ...]:
    return (
        flow.flow_persistence,
        shock.shock_persistence,
        impact.impact_toxicity_component,
        replenishment.bounded_replenishment_failure,
        market.spread.bounded_spread_expansion,
        market.volatility.bounded_volatility_jump,
        liquidity.withdrawal_pressure,
    )


def _observation_start_reference(
    shocks: tuple[IndexedLiquidityShock, ...],
    market_index: dict[int, IndexedMarketStateObservation],
    observation_start_index: int,
) -> MarketEventReference:
    run_shock = next(
        (item for item in shocks if _required_shock_start(item) == observation_start_index),
        None,
    )
    if run_shock is not None:
        return run_shock.shock.start_reference
    return market_index[observation_start_index].observation.event_reference


def _reset_breaks_from_lifecycles(
    lifecycles: tuple[OrderLifecycle, ...],
    start_index: int,
    end_index: int,
) -> bool:
    return any(
        lifecycle.terminal_reason is OrderLifecycleTerminalReason.RESET
        and lifecycle.last_event_index is not None
        and start_index <= lifecycle.last_event_index <= end_index
        for lifecycle in lifecycles
    )


def _range_items(
    index: dict[int, IndexedMarketStateObservation],
    start: int,
    end: int,
) -> tuple[IndexedMarketStateObservation, ...]:
    if start < 0 or end < start:
        return ()
    return tuple(index[item] for item in range(start, end + 1) if item in index)


def _has_complete_range(
    index: dict[int, IndexedMarketStateObservation],
    start: int,
    end: int,
) -> bool:
    return start >= 0 and all(item in index for item in range(start, end + 1))


def _unavailable(
    shock: LiquidityShock,
    reasons: Sequence[ToxicityUnavailableReason],
    *,
    observation_end_event_index: int | None = None,
    observation_end_reference: MarketEventReference | None = None,
    structural_breaks: tuple[ToxicityStructuralBreak, ...] = (),
    one_sided_book: bool = False,
    feature_version: str = TOXICITY_VERSION,
) -> ToxicityUnavailable:
    return ToxicityUnavailable(
        instrument_id=shock.instrument_id,
        shock_id=shock.shock_id,
        reasons=tuple(dict.fromkeys(reasons)),
        observation_end_event_index=observation_end_event_index,
        observation_end_reference=observation_end_reference,
        available_at_process_time=(
            None if observation_end_reference is None else observation_end_reference.process_time
        ),
        structural_breaks=structural_breaks,
        one_sided_book=one_sided_book,
        feature_version=feature_version,
    )


def _required_market_index(item: IndexedMarketStateObservation) -> int:
    if item.event_index is None:
        raise ValueError("toxicity market observation requires event index")
    return item.event_index


def _required_flow_index(item: IndexedAggressiveFlowObservation) -> int:
    if item.event_index is None:
        raise ValueError("toxicity flow observation requires event index")
    return item.event_index


def _required_break_index(item: ToxicityStructuralBreak) -> int:
    if item.event_index is None:
        raise ValueError("toxicity structural break requires event index")
    return item.event_index


def _required_shock_start(item: IndexedLiquidityShock) -> int:
    if item.start_event_index is None:
        raise ValueError("toxicity shock requires start event index")
    return item.start_event_index


def _required_shock_end(item: IndexedLiquidityShock) -> int:
    if item.end_event_index is None:
        raise ValueError("toxicity shock requires end event index")
    return item.end_event_index


def _required_decimal(value: Decimal | None, name: str) -> Decimal:
    if value is None:
        raise ValueError(f"{name} is unavailable")
    return value
