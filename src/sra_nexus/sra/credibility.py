"""MBO-only attacked-liquidity credibility research features."""

from __future__ import annotations

from dataclasses import dataclass
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
from sra_nexus.common.types import (
    InstrumentId,
    MarketOrderId,
    OrderLifecycleId,
    ShockId,
    ShockPairId,
)
from sra_nexus.market_data.enums import BookAction, BookDataMode, BookSide
from sra_nexus.market_data.snapshots import BookSnapshot
from sra_nexus.sra.enums import (
    LiquidityCredibilityUnavailableReason,
    OrderLifecycleTerminalReason,
    ShockDirection,
)
from sra_nexus.sra.lifecycle import (
    IndexedMarketEventReference,
    OrderLifecycle,
    OrderLifecycleTransition,
)
from sra_nexus.sra.replenishment import (
    ReplenishmentEpisode,
    absorption_cycle_count,
    identify_replenishment_episodes,
)
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.shock_pair import ShockPair
from sra_nexus.sra.state import MarketEventReference, elapsed_decimal_seconds

LIQUIDITY_CREDIBILITY_VERSION = "liquidity-credibility-v1"
LIQUIDITY_CREDIBILITY_COMPARISON_VERSION = "liquidity-credibility-comparison-v1"

UnitIntervalDecimal = Annotated[
    ExactDecimal,
    Field(ge=0, le=1, description="Exact dimensionless value in [0, 1]."),
]


class OrderCredibilityWeights(ContractModel):
    """Initial engineering-prior weights for descriptive order credibility."""

    execution: UnitIntervalDecimal = Decimal("0.40")
    survival: UnitIntervalDecimal = Decimal("0.20")
    lifetime: UnitIntervalDecimal = Decimal("0.20")
    cancellation: UnitIntervalDecimal = Decimal("0.20")

    @model_validator(mode="after")
    def validate_sum(self) -> Self:
        """Require an exact convex combination."""
        if self.execution + self.survival + self.lifetime + self.cancellation != 1:
            raise ValueError("order credibility weights must sum exactly to one")
        return self


class SideCredibilityWeights(ContractModel):
    """Initial engineering-prior weights for descriptive side credibility."""

    order: UnitIntervalDecimal = Decimal("0.35")
    shock_execution: UnitIntervalDecimal = Decimal("0.20")
    withdrawal: UnitIntervalDecimal = Decimal("0.20")
    replenishment: UnitIntervalDecimal = Decimal("0.15")
    cycles: UnitIntervalDecimal = Decimal("0.10")

    @model_validator(mode="after")
    def validate_sum(self) -> Self:
        """Require an exact convex combination."""
        total = (
            self.order + self.shock_execution + self.withdrawal + self.replenishment + self.cycles
        )
        if total != 1:
            raise ValueError("side credibility weights must sum exactly to one")
        return self


class LiquidityCredibilityConfig(ContractModel):
    """Central MBO region, horizon, bounded transforms, and engineering priors."""

    attack_depth_levels: int = Field(default=3, gt=0)
    post_shock_event_horizon: int = Field(default=25, gt=0)
    lifetime_tau_seconds: PositiveDecimal = Decimal("30")
    replenishment_delay_tau_seconds: PositiveDecimal = Decimal("5")
    cycle_tau: PositiveDecimal = Decimal("2")
    replenishment_episode_event_gap: int = Field(default=2, ge=0)
    order_score_weights: OrderCredibilityWeights = Field(default_factory=OrderCredibilityWeights)
    side_score_weights: SideCredibilityWeights = Field(default_factory=SideCredibilityWeights)
    epsilon: PositiveDecimal = Decimal("0.000001")
    feature_version: NonBlankStr = LIQUIDITY_CREDIBILITY_VERSION


class OrderBehaviorFeatures(ContractModel):
    """Lifecycle behavior observed no later than the declared feature horizon."""

    lifecycle_id: OrderLifecycleId
    order_id: MarketOrderId
    exchange_lifetime_seconds: NonNegativeDecimal
    process_lifetime_seconds: NonNegativeDecimal
    event_lifetime: int | None = Field(default=None, ge=0)
    observed_added_quantity: PositiveDecimal
    executed_quantity: NonNegativeDecimal
    withdrawn_quantity: NonNegativeDecimal
    unresolved_quantity: NonNegativeDecimal
    executed_fraction: UnitIntervalDecimal
    withdrawn_fraction: UnitIntervalDecimal
    modify_count: int = Field(ge=0)
    price_change_count: int = Field(ge=0)
    execution_count: int = Field(ge=0)
    cancel_count: int = Field(ge=0)
    terminal_reason: OrderLifecycleTerminalReason
    credibility_available: Literal[True] = True

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        """Require exact bounded lifecycle fractions at the observation cutoff."""
        total = self.executed_quantity + self.withdrawn_quantity + self.unresolved_quantity
        if total != self.observed_added_quantity:
            raise ValueError("order behavior quantities must exactly account for additions")
        if self.executed_fraction != self.executed_quantity / self.observed_added_quantity:
            raise ValueError("executed fraction must equal executed / observed added")
        if self.withdrawn_fraction != self.withdrawn_quantity / self.observed_added_quantity:
            raise ValueError("withdrawn fraction must equal withdrawn / observed added")
        return self


class AttackedOrderCredibility(ContractModel):
    """Pre-shock attacked-order outcomes and transparent credibility components."""

    lifecycle_id: OrderLifecycleId
    order_id: MarketOrderId
    pre_shock_price: PositiveDecimal
    pre_shock_remaining_quantity: PositiveDecimal
    executed_during_shock: NonNegativeDecimal
    withdrawn_during_shock: NonNegativeDecimal
    remaining_at_shock_end: NonNegativeDecimal
    shock_executed_fraction: UnitIntervalDecimal
    shock_withdrawal_fraction: UnitIntervalDecimal
    survived_shock: bool
    behavior: OrderBehaviorFeatures
    execution_component: UnitIntervalDecimal
    survival_component: UnitIntervalDecimal
    lifetime_component: UnitIntervalDecimal
    cancellation_component: UnitIntervalDecimal
    order_credibility_score: UnitIntervalDecimal
    lifetime_tau_seconds: PositiveDecimal
    score_weights: OrderCredibilityWeights

    @model_validator(mode="after")
    def validate_shock_quantities(self) -> Self:
        """Keep shock-window quantities and fractions exact without clamping."""
        accounted = (
            self.executed_during_shock + self.withdrawn_during_shock + self.remaining_at_shock_end
        )
        if accounted != self.pre_shock_remaining_quantity:
            raise ValueError("shock outcomes must account for pre-shock remaining quantity")
        if (
            self.shock_executed_fraction
            != self.executed_during_shock / self.pre_shock_remaining_quantity
        ):
            raise ValueError("shock executed fraction is inconsistent")
        if (
            self.shock_withdrawal_fraction
            != self.withdrawn_during_shock / self.pre_shock_remaining_quantity
        ):
            raise ValueError("shock withdrawal fraction is inconsistent")
        if self.survived_shock != (self.remaining_at_shock_end > 0):
            raise ValueError("survival must mean a positive remainder at shock end")
        if self.execution_component != self.behavior.executed_fraction:
            raise ValueError("execution component must equal lifecycle executed fraction")
        expected_survival = Decimal(
            int(self.survived_shock or self.shock_executed_fraction == Decimal(1))
        )
        if self.survival_component != expected_survival:
            raise ValueError("survival component is inconsistent with shock outcomes")
        expected_lifetime = (
            Decimal(1)
            - (-self.behavior.exchange_lifetime_seconds / self.lifetime_tau_seconds).exp()
        )
        if self.lifetime_component != expected_lifetime:
            raise ValueError("lifetime component is inconsistent with configured tau")
        if self.cancellation_component != Decimal(1) - self.behavior.withdrawn_fraction:
            raise ValueError("cancellation component must invert withdrawal fraction")
        expected_score = (
            self.score_weights.execution * self.execution_component
            + self.score_weights.survival * self.survival_component
            + self.score_weights.lifetime * self.lifetime_component
            + self.score_weights.cancellation * self.cancellation_component
        )
        if self.order_credibility_score != expected_score:
            raise ValueError("order credibility score is inconsistent with components")
        return self


class LiquidityCredibilityUnavailable(ContractModel):
    """Explicit unsupported or not-yet-observable shock credibility state."""

    shock_id: ShockId
    instrument_id: InstrumentId
    attacked_side: BookSide
    credibility_available: Literal[False] = False
    reasons: tuple[LiquidityCredibilityUnavailableReason, ...]
    observation_end_event_reference: MarketEventReference | None = None
    observation_end_event_index: int | None = Field(default=None, ge=0)
    available_at_process_time: UtcDatetime | None = None
    feature_version: NonBlankStr = LIQUIDITY_CREDIBILITY_VERSION

    @model_validator(mode="after")
    def require_reason(self) -> Self:
        """Unavailable output must explain why no feature was fabricated."""
        if not self.reasons:
            raise ValueError("unavailable liquidity credibility requires a reason")
        return self


class LiquidityCredibilityResult(ContractModel):
    """Complete attacked-region lifecycle and replenishment credibility features."""

    shock_id: ShockId
    instrument_id: InstrumentId
    attacked_side: BookSide
    original_attack_prices: tuple[PositiveDecimal, ...]
    order_count: int = Field(gt=0)
    raw_displayed_depth: PositiveDecimal
    attacked_orders: tuple[AttackedOrderCredibility, ...]
    quantity_weighted_order_credibility: UnitIntervalDecimal
    side_observed_added_quantity: PositiveDecimal
    side_executed_quantity: NonNegativeDecimal
    side_withdrawn_quantity: NonNegativeDecimal
    side_executed_fraction: UnitIntervalDecimal
    side_withdrawal_fraction: UnitIntervalDecimal
    shock_executed_quantity: NonNegativeDecimal
    shock_withdrawn_quantity: NonNegativeDecimal
    surviving_quantity: NonNegativeDecimal
    shock_executed_fraction: UnitIntervalDecimal
    shock_withdrawal_fraction: UnitIntervalDecimal
    order_survival_fraction: UnitIntervalDecimal
    quantity_survival_fraction: UnitIntervalDecimal
    replenishment_episodes: tuple[ReplenishmentEpisode, ...]
    replenishment_count: int = Field(ge=0)
    replenishment_quantity: NonNegativeDecimal
    replenishment_executed_fraction: UnitIntervalDecimal | None
    replenishment_withdrawal_fraction: UnitIntervalDecimal | None
    absorption_cycle_count: int = Field(ge=0)
    replenishment_component: UnitIntervalDecimal | None
    cycle_component: UnitIntervalDecimal
    credible_depth: NonNegativeDecimal
    credible_depth_ratio: UnitIntervalDecimal
    credibility_score: UnitIntervalDecimal | None
    observation_end_exchange_time: UtcDatetime
    observation_end_process_time: UtcDatetime
    observation_end_event_reference: MarketEventReference
    observation_end_event_index: int = Field(ge=0)
    available_at_process_time: UtcDatetime
    credibility_available: Literal[True] = True
    configuration: LiquidityCredibilityConfig
    feature_version: NonBlankStr = LIQUIDITY_CREDIBILITY_VERSION

    @model_validator(mode="after")
    def validate_aggregates(self) -> Self:
        """Require exact quantity weighting, depth ratio, and observation availability."""
        if self.order_count != len(self.attacked_orders):
            raise ValueError("order_count must equal attacked order count")
        if len(set(self.original_attack_prices)) != len(self.original_attack_prices):
            raise ValueError("original attack prices must be unique")
        if len({order.lifecycle_id for order in self.attacked_orders}) != len(self.attacked_orders):
            raise ValueError("attacked lifecycle identities must be unique")
        if any(
            order.pre_shock_price not in self.original_attack_prices
            for order in self.attacked_orders
        ):
            raise ValueError("attacked orders must belong to original attack prices")
        if self.replenishment_count != len(self.replenishment_episodes):
            raise ValueError("replenishment_count must equal episode count")
        if any(
            episode.shock_id != self.shock_id
            or episode.side is not self.attacked_side
            or episode.price not in self.original_attack_prices
            for episode in self.replenishment_episodes
        ):
            raise ValueError("replenishment episodes must belong to the analyzed region")
        if self.credible_depth > self.raw_displayed_depth:
            raise ValueError("credible depth cannot exceed raw displayed depth")
        if self.credible_depth_ratio != self.credible_depth / self.raw_displayed_depth:
            raise ValueError("credible depth ratio must equal credible / raw depth")
        if self.available_at_process_time != self.observation_end_process_time:
            raise ValueError("credibility availability must equal observation-end process time")
        if self.observation_end_event_reference.process_time != self.available_at_process_time:
            raise ValueError("observation reference must own feature availability")
        raw_depth = sum(
            (order.pre_shock_remaining_quantity for order in self.attacked_orders),
            Decimal(0),
        )
        if raw_depth != self.raw_displayed_depth:
            raise ValueError("raw displayed depth must sum pre-shock order quantities")
        expected_credible = sum(
            (
                order.pre_shock_remaining_quantity * order.order_credibility_score
                for order in self.attacked_orders
            ),
            Decimal(0),
        )
        if expected_credible != self.credible_depth:
            raise ValueError("credible depth must quantity-weight order credibility")
        if self.quantity_weighted_order_credibility != self.credible_depth_ratio:
            raise ValueError("QWOC and credible-depth ratio must match for the same region")
        expected_added = sum(
            (order.behavior.observed_added_quantity for order in self.attacked_orders),
            Decimal(0),
        )
        expected_executed = sum(
            (order.behavior.executed_quantity for order in self.attacked_orders),
            Decimal(0),
        )
        expected_withdrawn = sum(
            (order.behavior.withdrawn_quantity for order in self.attacked_orders),
            Decimal(0),
        )
        if self.side_observed_added_quantity != expected_added:
            raise ValueError("side observed quantity must aggregate attacked orders")
        if self.side_executed_quantity != expected_executed:
            raise ValueError("side executed quantity must aggregate attacked orders")
        if self.side_withdrawn_quantity != expected_withdrawn:
            raise ValueError("side withdrawn quantity must aggregate attacked orders")
        if self.side_executed_fraction != expected_executed / expected_added:
            raise ValueError("side executed fraction must use aggregate quantities")
        if self.side_withdrawal_fraction != expected_withdrawn / expected_added:
            raise ValueError("side withdrawal fraction must use aggregate quantities")
        expected_shock_executed = sum(
            (order.executed_during_shock for order in self.attacked_orders),
            Decimal(0),
        )
        expected_shock_withdrawn = sum(
            (order.withdrawn_during_shock for order in self.attacked_orders),
            Decimal(0),
        )
        expected_surviving = sum(
            (order.remaining_at_shock_end for order in self.attacked_orders),
            Decimal(0),
        )
        if self.shock_executed_quantity != expected_shock_executed:
            raise ValueError("shock executed quantity must aggregate attacked orders")
        if self.shock_withdrawn_quantity != expected_shock_withdrawn:
            raise ValueError("shock withdrawn quantity must aggregate attacked orders")
        if self.surviving_quantity != expected_surviving:
            raise ValueError("surviving quantity must aggregate attacked orders")
        if self.shock_executed_fraction != expected_shock_executed / raw_depth:
            raise ValueError("shock executed fraction must use pre-shock raw depth")
        if self.shock_withdrawal_fraction != expected_shock_withdrawn / raw_depth:
            raise ValueError("shock withdrawal fraction must use pre-shock raw depth")
        if self.quantity_survival_fraction != expected_surviving / raw_depth:
            raise ValueError("quantity survival fraction must use pre-shock raw depth")
        expected_order_survival = Decimal(
            sum(order.survived_shock for order in self.attacked_orders)
        ) / Decimal(len(self.attacked_orders))
        if self.order_survival_fraction != expected_order_survival:
            raise ValueError("order survival fraction must count attacked orders")
        expected_replenishment = sum(
            (episode.quantity_added for episode in self.replenishment_episodes),
            Decimal(0),
        )
        if self.replenishment_quantity != expected_replenishment:
            raise ValueError("replenishment quantity must aggregate episodes")
        if self.absorption_cycle_count != absorption_cycle_count(self.replenishment_episodes):
            raise ValueError("absorption cycle count must aggregate episodes")
        expected_cycle_component = (
            Decimal(1)
            - (-Decimal(self.absorption_cycle_count) / self.configuration.cycle_tau).exp()
        )
        if self.cycle_component != expected_cycle_component:
            raise ValueError("cycle component is inconsistent with configured tau")
        attribution_complete = all(
            episode.attribution_complete for episode in self.replenishment_episodes
        )
        expected_replenishment_executed = sum(
            (episode.subsequent_executed_quantity for episode in self.replenishment_episodes),
            Decimal(0),
        )
        expected_replenishment_withdrawn = sum(
            (episode.subsequent_withdrawn_quantity for episode in self.replenishment_episodes),
            Decimal(0),
        )
        if self.replenishment_episodes and attribution_complete:
            if (
                self.replenishment_executed_fraction
                != expected_replenishment_executed / expected_replenishment
            ):
                raise ValueError("replenishment executed fraction is inconsistent")
            if (
                self.replenishment_withdrawal_fraction
                != expected_replenishment_withdrawn / expected_replenishment
            ):
                raise ValueError("replenishment withdrawal fraction is inconsistent")
        elif (
            self.replenishment_executed_fraction is not None
            or self.replenishment_withdrawal_fraction is not None
        ):
            raise ValueError("unattributed replenishment cannot expose side fractions")
        expected_replenishment_component = _replenishment_component(
            self.replenishment_episodes,
            self.shock_executed_quantity,
            self.replenishment_executed_fraction,
            self.replenishment_withdrawal_fraction,
            self.configuration,
        )
        if self.replenishment_component != expected_replenishment_component:
            raise ValueError("replenishment component is inconsistent with episodes")
        if self.credibility_score is not None:
            if self.replenishment_component is None:
                raise ValueError("side score requires available replenishment component")
            weights = self.configuration.side_score_weights
            expected_score = (
                weights.order * self.quantity_weighted_order_credibility
                + weights.shock_execution * self.shock_executed_fraction
                + weights.withdrawal * (Decimal(1) - self.shock_withdrawal_fraction)
                + weights.replenishment * self.replenishment_component
                + weights.cycles * self.cycle_component
            )
            if self.credibility_score != expected_score:
                raise ValueError("side credibility score is inconsistent with components")
        return self


type LiquidityCredibilityAnalysis = LiquidityCredibilityResult | LiquidityCredibilityUnavailable


class LiquidityCredibilityComparison(ContractModel):
    """Additional shock-pair feature measuring descriptive credibility change."""

    pair_id: ShockPairId
    shock_1_id: ShockId
    shock_2_id: ShockId
    liquidity_credibility_1: UnitIntervalDecimal
    liquidity_credibility_2: UnitIntervalDecimal
    delta_liquidity_credibility: ExactDecimal
    comparison_version: NonBlankStr = LIQUIDITY_CREDIBILITY_COMPARISON_VERSION

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        """Require exact ``DeltaLC = LC_2 - LC_1``."""
        expected = self.liquidity_credibility_2 - self.liquidity_credibility_1
        if self.delta_liquidity_credibility != expected:
            raise ValueError("liquidity credibility delta is inconsistent")
        return self


@dataclass(frozen=True, slots=True)
class _BoundaryState:
    lifecycle: OrderLifecycle
    price: Decimal
    remaining_quantity: Decimal


class LiquidityCredibilityService:
    """Calculate MBO shock-region behavior through one explicit event horizon."""

    def __init__(self, config: LiquidityCredibilityConfig | None = None) -> None:
        """Configure region, horizon, transforms, and descriptive score priors."""
        self._config = LiquidityCredibilityConfig() if config is None else config

    @property
    def config(self) -> LiquidityCredibilityConfig:
        """Return the immutable policy used for reproducible feature construction."""
        return self._config

    def analyze(
        self,
        *,
        shock: LiquidityShock,
        book_mode: BookDataMode,
        pre_shock_snapshot: BookSnapshot,
        lifecycles: tuple[OrderLifecycle, ...],
        reset_events: tuple[IndexedMarketEventReference, ...],
        shock_start_event_index: int,
        shock_end_event_index: int,
        observation_end_event_index: int,
        observation_end_event_reference: MarketEventReference,
    ) -> LiquidityCredibilityAnalysis:
        """Return complete MBO evidence or an explicit unavailable state."""
        attacked_side = _attacked_side(shock.direction)
        if book_mode is not BookDataMode.MARKET_BY_ORDER:
            return _unavailable(
                shock,
                attacked_side,
                LiquidityCredibilityUnavailableReason.MBO_REQUIRED,
                observation_end_event_reference,
                observation_end_event_index,
            )
        _validate_analysis_boundaries(
            shock,
            pre_shock_snapshot,
            shock_start_event_index,
            shock_end_event_index,
            observation_end_event_index,
            observation_end_event_reference,
            self._config,
        )
        required_end = shock_end_event_index + self._config.post_shock_event_horizon
        if observation_end_event_index < required_end:
            return _unavailable(
                shock,
                attacked_side,
                LiquidityCredibilityUnavailableReason.INSUFFICIENT_OBSERVATION_HORIZON,
                observation_end_event_reference,
                observation_end_event_index,
            )
        if observation_end_event_index > required_end:
            raise ValueError("observation end must equal the configured post-shock horizon")
        if any(
            transition.event.event_index is None
            for lifecycle in lifecycles
            for transition in lifecycle.transitions
        ) or any(reset.event_index is None for reset in reset_events):
            return _unavailable(
                shock,
                attacked_side,
                LiquidityCredibilityUnavailableReason.EVENT_INDEX_UNAVAILABLE,
                observation_end_event_reference,
                observation_end_event_index,
            )
        if any(
            reset.event_index is not None
            and shock_start_event_index <= reset.event_index <= observation_end_event_index
            for reset in reset_events
        ):
            return _unavailable(
                shock,
                attacked_side,
                LiquidityCredibilityUnavailableReason.RESET_IN_OBSERVATION_WINDOW,
                observation_end_event_reference,
                observation_end_event_index,
            )

        original_prices = _original_attack_prices(
            pre_shock_snapshot,
            attacked_side,
            self._config.attack_depth_levels,
        )
        boundary_states = tuple(
            state
            for lifecycle in lifecycles
            if (state := _state_before_event(lifecycle, shock_start_event_index)) is not None
            and lifecycle.side is attacked_side
            and state.price in original_prices
        )
        _validate_boundary_depth_by_price(
            pre_shock_snapshot,
            attacked_side,
            original_prices,
            boundary_states,
        )
        if not boundary_states:
            return _unavailable(
                shock,
                attacked_side,
                LiquidityCredibilityUnavailableReason.NO_ATTACKED_ORDERS,
                observation_end_event_reference,
                observation_end_event_index,
            )
        if any(
            _has_quantity_increase_during_shock(
                state.lifecycle,
                shock_start_event_index,
                shock_end_event_index,
            )
            for state in boundary_states
        ):
            return _unavailable(
                shock,
                attacked_side,
                LiquidityCredibilityUnavailableReason.SHOCK_QUANTITY_ATTRIBUTION_UNAVAILABLE,
                observation_end_event_reference,
                observation_end_event_index,
            )

        attacked_orders = tuple(
            _attacked_order_features(
                state,
                shock_start_event_index,
                shock_end_event_index,
                observation_end_event_index,
                observation_end_event_reference,
                self._config,
            )
            for state in boundary_states
        )
        episodes = identify_replenishment_episodes(
            shock=shock,
            attacked_side=attacked_side,
            original_prices=original_prices,
            lifecycles=lifecycles,
            shock_start_event_index=shock_start_event_index,
            observation_end_event_index=observation_end_event_index,
            episode_event_gap=self._config.replenishment_episode_event_gap,
        )
        return _aggregate_liquidity_credibility(
            shock,
            attacked_side,
            original_prices,
            attacked_orders,
            episodes,
            observation_end_event_index,
            observation_end_event_reference,
            self._config,
        )


def compare_liquidity_credibility(
    pair: ShockPair,
    credibility_1: LiquidityCredibilityResult,
    credibility_2: LiquidityCredibilityResult,
) -> LiquidityCredibilityComparison:
    """Calculate exact descriptive ``DeltaLC`` for an existing valid ShockPair."""
    if credibility_1.shock_id != pair.shock_1_id or credibility_2.shock_id != pair.shock_2_id:
        raise ValueError("liquidity credibility results must follow the ordered shock pair")
    if credibility_1.credibility_score is None or credibility_2.credibility_score is None:
        raise ValueError("DeltaLC requires both optional side credibility scores")
    return LiquidityCredibilityComparison(
        pair_id=pair.pair_id,
        shock_1_id=pair.shock_1_id,
        shock_2_id=pair.shock_2_id,
        liquidity_credibility_1=credibility_1.credibility_score,
        liquidity_credibility_2=credibility_2.credibility_score,
        delta_liquidity_credibility=calculate_delta_liquidity_credibility(
            credibility_1.credibility_score,
            credibility_2.credibility_score,
        ),
    )


def calculate_delta_liquidity_credibility(
    liquidity_credibility_1: Decimal,
    liquidity_credibility_2: Decimal,
) -> Decimal:
    """Return exact descriptive ``LC_2 - LC_1`` for two bounded scores."""
    for score in (liquidity_credibility_1, liquidity_credibility_2):
        if not score.is_finite() or score < 0 or score > 1:
            raise ValueError("liquidity credibility scores must be finite values in [0, 1]")
    return liquidity_credibility_2 - liquidity_credibility_1


def calculate_quantity_weighted_order_credibility(
    quantities: tuple[Decimal, ...],
    credibility_scores: tuple[Decimal, ...],
) -> Decimal:
    """Return exact QWOC, rejecting empty, invalid, or unbounded inputs."""
    if not quantities or len(quantities) != len(credibility_scores):
        raise ValueError("QWOC requires equally sized non-empty quantity and score tuples")
    if any(quantity <= 0 or not quantity.is_finite() for quantity in quantities):
        raise ValueError("QWOC quantities must be finite and positive")
    if any(not score.is_finite() or score < 0 or score > 1 for score in credibility_scores):
        raise ValueError("QWOC scores must be finite values in [0, 1]")
    return calculate_credible_depth(quantities, credibility_scores) / sum(
        quantities,
        Decimal(0),
    )


def calculate_credible_depth(
    quantities: tuple[Decimal, ...],
    credibility_scores: tuple[Decimal, ...],
) -> Decimal:
    """Return exact credibility-weighted displayed depth."""
    if not quantities or len(quantities) != len(credibility_scores):
        raise ValueError(
            "credible depth requires equally sized non-empty quantity and score tuples"
        )
    if any(quantity <= 0 or not quantity.is_finite() for quantity in quantities):
        raise ValueError("credible-depth quantities must be finite and positive")
    if any(not score.is_finite() or score < 0 or score > 1 for score in credibility_scores):
        raise ValueError("credible-depth scores must be finite values in [0, 1]")
    return sum(
        (quantity * score for quantity, score in zip(quantities, credibility_scores, strict=True)),
        Decimal(0),
    )


def _attacked_order_features(
    state: _BoundaryState,
    shock_start_index: int,
    shock_end_index: int,
    observation_end_index: int,
    observation_end_reference: MarketEventReference,
    config: LiquidityCredibilityConfig,
) -> AttackedOrderCredibility:
    lifecycle = state.lifecycle
    shock_transitions = _transitions_between(
        lifecycle,
        shock_start_index,
        shock_end_index,
    )
    executed = sum(
        (transition.executed_quantity for transition in shock_transitions),
        Decimal(0),
    )
    withdrawn = sum(
        (transition.withdrawn_quantity for transition in shock_transitions),
        Decimal(0),
    )
    remaining = state.remaining_quantity - executed - withdrawn
    if remaining < 0:
        raise ValueError("shock outcomes exceed pre-shock displayed quantity")
    behavior = _behavior_at_observation(
        lifecycle,
        observation_end_index,
        observation_end_reference,
    )
    shock_executed_fraction = executed / state.remaining_quantity
    shock_withdrawal_fraction = withdrawn / state.remaining_quantity
    survived = remaining > 0
    execution_component = behavior.executed_fraction
    survival_component = Decimal(int(survived or shock_executed_fraction == Decimal(1)))
    lifetime_component = (
        Decimal(1) - (-behavior.exchange_lifetime_seconds / config.lifetime_tau_seconds).exp()
    )
    cancellation_component = Decimal(1) - behavior.withdrawn_fraction
    weights = config.order_score_weights
    score = (
        weights.execution * execution_component
        + weights.survival * survival_component
        + weights.lifetime * lifetime_component
        + weights.cancellation * cancellation_component
    )
    return AttackedOrderCredibility(
        lifecycle_id=lifecycle.lifecycle_id,
        order_id=lifecycle.order_id,
        pre_shock_price=state.price,
        pre_shock_remaining_quantity=state.remaining_quantity,
        executed_during_shock=executed,
        withdrawn_during_shock=withdrawn,
        remaining_at_shock_end=remaining,
        shock_executed_fraction=shock_executed_fraction,
        shock_withdrawal_fraction=shock_withdrawal_fraction,
        survived_shock=survived,
        behavior=behavior,
        execution_component=execution_component,
        survival_component=survival_component,
        lifetime_component=lifetime_component,
        cancellation_component=cancellation_component,
        order_credibility_score=score,
        lifetime_tau_seconds=config.lifetime_tau_seconds,
        score_weights=config.order_score_weights,
    )


def _behavior_at_observation(
    lifecycle: OrderLifecycle,
    observation_end_index: int,
    observation_end_reference: MarketEventReference,
) -> OrderBehaviorFeatures:
    transitions = tuple(
        transition
        for transition in lifecycle.transitions
        if _required_transition_index(transition) <= observation_end_index
    )
    if not transitions or transitions[0].action is not BookAction.ADD:
        raise ValueError("observed lifecycle must begin with ADD by the feature horizon")
    added = sum((transition.added_quantity for transition in transitions), Decimal(0))
    executed = sum((transition.executed_quantity for transition in transitions), Decimal(0))
    withdrawn = sum((transition.withdrawn_quantity for transition in transitions), Decimal(0))
    remaining = transitions[-1].post_remaining_quantity
    actually_terminal = (
        lifecycle.last_event_index is not None
        and lifecycle.last_event_index <= observation_end_index
        and lifecycle.terminal_reason is not OrderLifecycleTerminalReason.OBSERVATION_END
    )
    terminal_reason = (
        lifecycle.terminal_reason
        if actually_terminal
        else OrderLifecycleTerminalReason.OBSERVATION_END
    )
    last_exchange = (
        lifecycle.last_seen_exchange_time
        if actually_terminal
        else observation_end_reference.exchange_time
    )
    last_process = (
        lifecycle.last_seen_process_time
        if actually_terminal
        else observation_end_reference.process_time
    )
    first = transitions[0].event.reference
    first_index = _required_transition_index(transitions[0])
    return OrderBehaviorFeatures(
        lifecycle_id=lifecycle.lifecycle_id,
        order_id=lifecycle.order_id,
        exchange_lifetime_seconds=elapsed_decimal_seconds(first.exchange_time, last_exchange),
        process_lifetime_seconds=elapsed_decimal_seconds(first.process_time, last_process),
        event_lifetime=(
            _required_lifecycle_last_index(lifecycle) - first_index
            if actually_terminal
            else observation_end_index - first_index
        ),
        observed_added_quantity=added,
        executed_quantity=executed,
        withdrawn_quantity=withdrawn,
        unresolved_quantity=remaining,
        executed_fraction=executed / added,
        withdrawn_fraction=withdrawn / added,
        modify_count=sum(transition.action is BookAction.MODIFY for transition in transitions),
        price_change_count=sum(
            transition.action is BookAction.MODIFY and transition.pre_price != transition.post_price
            for transition in transitions
        ),
        execution_count=sum(transition.action is BookAction.EXECUTE for transition in transitions),
        cancel_count=sum(transition.action is BookAction.CANCEL for transition in transitions),
        terminal_reason=terminal_reason,
    )


def _aggregate_liquidity_credibility(
    shock: LiquidityShock,
    attacked_side: BookSide,
    original_prices: tuple[Decimal, ...],
    orders: tuple[AttackedOrderCredibility, ...],
    episodes: tuple[ReplenishmentEpisode, ...],
    observation_end_index: int,
    observation_end_reference: MarketEventReference,
    config: LiquidityCredibilityConfig,
) -> LiquidityCredibilityResult:
    raw_depth = sum(
        (order.pre_shock_remaining_quantity for order in orders),
        Decimal(0),
    )
    quantities = tuple(order.pre_shock_remaining_quantity for order in orders)
    scores = tuple(order.order_credibility_score for order in orders)
    credible_depth = calculate_credible_depth(quantities, scores)
    qwoc = calculate_quantity_weighted_order_credibility(
        quantities,
        scores,
    )
    lifecycle_denominator = sum(
        (order.behavior.observed_added_quantity for order in orders),
        Decimal(0),
    )
    lifecycle_executed = sum(
        (order.behavior.executed_quantity for order in orders),
        Decimal(0),
    )
    lifecycle_withdrawn = sum(
        (order.behavior.withdrawn_quantity for order in orders),
        Decimal(0),
    )
    shock_executed = sum((order.executed_during_shock for order in orders), Decimal(0))
    shock_withdrawn = sum((order.withdrawn_during_shock for order in orders), Decimal(0))
    surviving_quantity = sum(
        (order.remaining_at_shock_end for order in orders),
        Decimal(0),
    )
    replenishment_quantity = sum((item.quantity_added for item in episodes), Decimal(0))
    replenishment_executed = sum(
        (item.subsequent_executed_quantity for item in episodes),
        Decimal(0),
    )
    replenishment_withdrawn = sum(
        (item.subsequent_withdrawn_quantity for item in episodes),
        Decimal(0),
    )
    attribution_complete = all(item.attribution_complete for item in episodes)
    replenishment_executed_fraction = (
        replenishment_executed / replenishment_quantity
        if episodes and attribution_complete
        else None
    )
    replenishment_withdrawal_fraction = (
        replenishment_withdrawn / replenishment_quantity
        if episodes and attribution_complete
        else None
    )
    replenishment_component = _replenishment_component(
        episodes,
        shock_executed,
        replenishment_executed_fraction,
        replenishment_withdrawal_fraction,
        config,
    )
    cycles = absorption_cycle_count(episodes)
    cycle_component = Decimal(1) - (-Decimal(cycles) / config.cycle_tau).exp()
    score = None
    if replenishment_component is not None:
        weights = config.side_score_weights
        score = (
            weights.order * qwoc
            + weights.shock_execution * (shock_executed / raw_depth)
            + weights.withdrawal * (Decimal(1) - shock_withdrawn / raw_depth)
            + weights.replenishment * replenishment_component
            + weights.cycles * cycle_component
        )
    return LiquidityCredibilityResult(
        shock_id=shock.shock_id,
        instrument_id=shock.instrument_id,
        attacked_side=attacked_side,
        original_attack_prices=original_prices,
        order_count=len(orders),
        raw_displayed_depth=raw_depth,
        attacked_orders=orders,
        quantity_weighted_order_credibility=qwoc,
        side_observed_added_quantity=lifecycle_denominator,
        side_executed_quantity=lifecycle_executed,
        side_withdrawn_quantity=lifecycle_withdrawn,
        side_executed_fraction=lifecycle_executed / lifecycle_denominator,
        side_withdrawal_fraction=lifecycle_withdrawn / lifecycle_denominator,
        shock_executed_quantity=shock_executed,
        shock_withdrawn_quantity=shock_withdrawn,
        surviving_quantity=surviving_quantity,
        shock_executed_fraction=shock_executed / raw_depth,
        shock_withdrawal_fraction=shock_withdrawn / raw_depth,
        order_survival_fraction=Decimal(sum(order.survived_shock for order in orders))
        / Decimal(len(orders)),
        quantity_survival_fraction=surviving_quantity / raw_depth,
        replenishment_episodes=episodes,
        replenishment_count=len(episodes),
        replenishment_quantity=replenishment_quantity,
        replenishment_executed_fraction=replenishment_executed_fraction,
        replenishment_withdrawal_fraction=replenishment_withdrawal_fraction,
        absorption_cycle_count=cycles,
        replenishment_component=replenishment_component,
        cycle_component=cycle_component,
        credible_depth=credible_depth,
        credible_depth_ratio=credible_depth / raw_depth,
        credibility_score=score,
        observation_end_exchange_time=observation_end_reference.exchange_time,
        observation_end_process_time=observation_end_reference.process_time,
        observation_end_event_reference=observation_end_reference,
        observation_end_event_index=observation_end_index,
        available_at_process_time=observation_end_reference.process_time,
        configuration=config,
    )


def _replenishment_component(
    episodes: tuple[ReplenishmentEpisode, ...],
    shock_executed_quantity: Decimal,
    executed_fraction: Decimal | None,
    withdrawal_fraction: Decimal | None,
    config: LiquidityCredibilityConfig,
) -> Decimal | None:
    if not episodes:
        return Decimal(0)
    if executed_fraction is None or withdrawal_fraction is None:
        return None
    quantity = sum((episode.quantity_added for episode in episodes), Decimal(0))
    quantity_component = quantity / (quantity + shock_executed_quantity)
    speed_component = sum(
        (
            (-episode.exchange_delay_seconds / config.replenishment_delay_tau_seconds).exp()
            for episode in episodes
        ),
        Decimal(0),
    ) / Decimal(len(episodes))
    withdrawal_component = Decimal(1) - withdrawal_fraction
    return (
        quantity_component + speed_component + executed_fraction + withdrawal_component
    ) / Decimal(4)


def _state_before_event(
    lifecycle: OrderLifecycle,
    event_index: int,
) -> _BoundaryState | None:
    prior = tuple(
        transition
        for transition in lifecycle.transitions
        if _required_transition_index(transition) < event_index
    )
    if not prior:
        return None
    if (
        lifecycle.last_event_index is not None
        and lifecycle.last_event_index < event_index
        and lifecycle.terminal_reason
        in {
            OrderLifecycleTerminalReason.RESET,
            OrderLifecycleTerminalReason.OBSERVATION_END,
        }
    ):
        return None
    final = prior[-1]
    if final.post_remaining_quantity <= 0 or final.post_price is None:
        return None
    return _BoundaryState(
        lifecycle=lifecycle,
        price=final.post_price,
        remaining_quantity=final.post_remaining_quantity,
    )


def _has_quantity_increase_during_shock(
    lifecycle: OrderLifecycle,
    start_index: int,
    end_index: int,
) -> bool:
    return any(
        transition.added_quantity > 0
        for transition in _transitions_between(lifecycle, start_index, end_index)
    )


def _transitions_between(
    lifecycle: OrderLifecycle,
    start_index: int,
    end_index: int,
) -> tuple[OrderLifecycleTransition, ...]:
    return tuple(
        transition
        for transition in lifecycle.transitions
        if start_index <= _required_transition_index(transition) <= end_index
    )


def _required_transition_index(transition: OrderLifecycleTransition) -> int:
    index = transition.event.event_index
    if index is None:
        raise ValueError("liquidity credibility requires normalized event indices")
    return index


def _required_lifecycle_last_index(lifecycle: OrderLifecycle) -> int:
    index = lifecycle.last_event_index
    if index is None:
        raise ValueError("liquidity credibility requires lifecycle event indices")
    return index


def _original_attack_prices(
    snapshot: BookSnapshot,
    side: BookSide,
    depth_levels: int,
) -> tuple[Decimal, ...]:
    levels = snapshot.bid_levels if side is BookSide.BID else snapshot.ask_levels
    return tuple(level.price for level in levels[:depth_levels])


def _validate_boundary_depth_by_price(
    snapshot: BookSnapshot,
    side: BookSide,
    prices: tuple[Decimal, ...],
    states: tuple[_BoundaryState, ...],
) -> None:
    levels = snapshot.bid_levels if side is BookSide.BID else snapshot.ask_levels
    snapshot_by_price = {level.price: level.aggregate_quantity for level in levels}
    for price in prices:
        tracked = sum(
            (state.remaining_quantity for state in states if state.price == price),
            Decimal(0),
        )
        if tracked != snapshot_by_price[price]:
            raise ValueError("accepted lifecycle state disagrees with pre-shock MBO snapshot")


def _attacked_side(direction: ShockDirection) -> BookSide:
    return BookSide.ASK if direction is ShockDirection.BUY else BookSide.BID


def _validate_analysis_boundaries(
    shock: LiquidityShock,
    snapshot: BookSnapshot,
    shock_start_index: int,
    shock_end_index: int,
    observation_end_index: int,
    observation_end_reference: MarketEventReference,
    config: LiquidityCredibilityConfig,
) -> None:
    if shock_start_index < 0 or shock_end_index < shock_start_index:
        raise ValueError("shock event-index boundaries are invalid")
    if observation_end_index < shock_end_index:
        raise ValueError("observation end cannot precede shock end")
    if snapshot.instrument_id != shock.instrument_id:
        raise ValueError("pre-shock snapshot and shock must share instrument_id")
    if snapshot.exchange_time > shock.start_exchange_time:
        raise ValueError("pre-shock snapshot cannot follow shock start in market time")
    if snapshot.process_time > shock.start_process_time:
        raise ValueError("pre-shock snapshot cannot follow shock start in process time")
    if observation_end_reference.instrument_id != shock.instrument_id:
        raise ValueError("observation end and shock must share instrument_id")
    if observation_end_reference.exchange_time < shock.end_exchange_time:
        raise ValueError("observation end exchange time cannot precede shock end")
    if observation_end_reference.process_time < shock.end_process_time:
        raise ValueError("observation end process time cannot precede shock end")
    if config.attack_depth_levels <= 0:
        raise AssertionError("validated attack depth levels unexpectedly non-positive")


def _unavailable(
    shock: LiquidityShock,
    attacked_side: BookSide,
    reason: LiquidityCredibilityUnavailableReason,
    observation_end_reference: MarketEventReference | None,
    observation_end_index: int | None,
) -> LiquidityCredibilityUnavailable:
    return LiquidityCredibilityUnavailable(
        shock_id=shock.shock_id,
        instrument_id=shock.instrument_id,
        attacked_side=attacked_side,
        reasons=(reason,),
        observation_end_event_reference=observation_end_reference,
        observation_end_event_index=observation_end_index,
        available_at_process_time=(
            None if observation_end_reference is None else observation_end_reference.process_time
        ),
    )
