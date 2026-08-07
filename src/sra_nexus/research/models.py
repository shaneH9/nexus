"""Immutable feature and configuration contracts for historical SRA research."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    ExactDecimal,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDatetime,
)
from sra_nexus.market_data.features import WeightedDepthConfig
from sra_nexus.sra.enums import ShockDirection

RESEARCH_DATASET_VERSION = "sra-research-dataset-v1"
FEATURE_SNAPSHOT_VERSION = "sra-feature-snapshot-v1"
LABEL_VERSION = "forward-market-response-label-v1"
WALK_FORWARD_SPLIT_VERSION = "walk-forward-split-v1"
PERMUTATION_TEST_VERSION = "block-label-permutation-v1"

UnitIntervalDecimal = Annotated[
    ExactDecimal,
    Field(ge=0, le=1, description="Exact dimensionless value in [0, 1]."),
]
SignedUnitDecimal = Annotated[
    ExactDecimal,
    Field(ge=-1, le=1, description="Exact dimensionless value in [-1, 1]."),
]


class FeatureVersion(ContractModel):
    """Stable feature-family name and version pair."""

    feature_name: NonBlankStr
    version: NonBlankStr


class FeatureAvailability(ContractModel):
    """Causal availability and source identity for one feature family."""

    feature_name: NonBlankStr
    available_at_process_time: UtcDatetime
    source_data_identifier: NonBlankStr


class EffectivenessFeature(ContractModel):
    """Aggressor-effectiveness values at one normalized-event horizon."""

    horizon_events: int = Field(gt=0)
    ae_1: ExactDecimal
    ae_2: ExactDecimal
    delta_ae: ExactDecimal
    relative_ae_change: ExactDecimal | None

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        """Preserve the exact absolute effectiveness change."""
        if self.delta_ae != self.ae_2 - self.ae_1:
            raise ValueError("delta_ae must equal AE_2 - AE_1")
        return self


class ResiliencyFeature(ContractModel):
    """Replenishment values at one normalized-event horizon."""

    horizon_events: int = Field(gt=0)
    rr_1: ExactDecimal
    rr_2: ExactDecimal
    delta_rr: ExactDecimal

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        """Preserve the exact replenishment change."""
        if self.delta_rr != self.rr_2 - self.rr_1:
            raise ValueError("delta_rr must equal RR_2 - RR_1")
        return self


class RecoveryTimeFeature(ContractModel):
    """Available recovery-time changes at one exact RR threshold."""

    threshold: PositiveDecimal
    delta_events: int | None
    delta_exchange_seconds: ExactDecimal | None
    delta_process_seconds: ExactDecimal | None
    available: bool

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        """Keep unavailable recovery values as missing rather than sentinels."""
        values = (
            self.delta_events,
            self.delta_exchange_seconds,
            self.delta_process_seconds,
        )
        if self.available != all(value is not None for value in values):
            raise ValueError("recovery availability must agree with all three deltas")
        return self


class AbsorptionFeature(ContractModel):
    """Magnitude-normalized absorption values at one event horizon."""

    horizon_events: int = Field(gt=0)
    absorption_efficiency_1: ExactDecimal
    absorption_efficiency_2: ExactDecimal
    delta_absorption_efficiency: ExactDecimal

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        """Preserve the exact absorption-efficiency change."""
        expected = self.absorption_efficiency_2 - self.absorption_efficiency_1
        if self.delta_absorption_efficiency != expected:
            raise ValueError("absorption delta must equal AbsEff_2 - AbsEff_1")
        return self


class CredibilityRawComponents(ContractModel):
    """Inspectable raw side-credibility components for one shock."""

    quantity_weighted_order_credibility: UnitIntervalDecimal
    shock_executed_fraction: UnitIntervalDecimal
    shock_withdrawal_fraction: UnitIntervalDecimal
    order_survival_fraction: UnitIntervalDecimal
    quantity_survival_fraction: UnitIntervalDecimal
    replenishment_component: UnitIntervalDecimal | None
    cycle_component: UnitIntervalDecimal
    credible_depth: NonNegativeDecimal
    credible_depth_ratio: UnitIntervalDecimal


class LiquidityCredibilityFeature(ContractModel):
    """Pair credibility score and practical raw components without imputation."""

    liquidity_credibility_1: UnitIntervalDecimal
    liquidity_credibility_2: UnitIntervalDecimal
    delta_liquidity_credibility: ExactDecimal
    raw_components_1: CredibilityRawComponents
    raw_components_2: CredibilityRawComponents

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        """Require exact pair credibility change."""
        expected = self.liquidity_credibility_2 - self.liquidity_credibility_1
        if self.delta_liquidity_credibility != expected:
            raise ValueError("liquidity credibility delta must equal LC_2 - LC_1")
        return self


class ToxicityFeature(ContractModel):
    """Inspectable market-side toxicity components for the second shock."""

    flow_persistence: UnitIntervalDecimal
    shock_persistence: UnitIntervalDecimal
    directional_flow_coverage: UnitIntervalDecimal
    unknown_flow_share: UnitIntervalDecimal
    raw_replenishment_failure: ExactDecimal
    bounded_replenishment_failure: UnitIntervalDecimal
    attacked_nnlp: SignedUnitDecimal
    opposite_nnlp: SignedUnitDecimal
    withdrawal_pressure: UnitIntervalDecimal
    spread_expansion_ratio: NonNegativeDecimal
    bounded_spread_expansion: UnitIntervalDecimal
    volatility_jump_ratio: NonNegativeDecimal
    bounded_volatility_jump: UnitIntervalDecimal
    toxicity_score: UnitIntervalDecimal
    delta_toxicity: ExactDecimal | None


class BackwardMarketFeature(ContractModel):
    """Past-only return and volatility at one backward event horizon."""

    horizon_events: int = Field(gt=0)
    recent_return: ExactDecimal | None
    recent_volatility: NonNegativeDecimal | None


class BaselineFeatureSnapshot(ContractModel):
    """Ordinary microstructure state available at the prediction anchor."""

    depth_levels: int = Field(gt=0)
    spread: NonNegativeDecimal | None
    midprice: PositiveDecimal | None
    microprice: PositiveDecimal | None
    microprice_offset: ExactDecimal | None
    order_book_imbalance: SignedUnitDecimal
    raw_bid_depth: NonNegativeDecimal
    raw_ask_depth: NonNegativeDecimal
    weighted_bid_depth: NonNegativeDecimal
    weighted_ask_depth: NonNegativeDecimal
    weighted_depth_weights: tuple[PositiveDecimal, ...]
    backward_features: tuple[BackwardMarketFeature, ...]

    @model_validator(mode="after")
    def validate_horizons(self) -> Self:
        """Require deterministic unique ordering for backward horizons."""
        horizons = tuple(item.horizon_events for item in self.backward_features)
        if horizons != tuple(sorted(set(horizons))):
            raise ValueError("backward feature horizons must be unique and sorted")
        expected_offset = (
            None
            if self.midprice is None or self.microprice is None
            else (self.microprice - self.midprice) / self.midprice
        )
        if self.microprice_offset != expected_offset:
            raise ValueError("microprice_offset must be (microprice - midprice) / midprice")
        return self


class SRAFeatureSnapshot(ContractModel):
    """Typed, component-preserving SRA feature state at one causal anchor."""

    direction: ShockDirection
    normalized_aggression_1: PositiveDecimal
    normalized_aggression_2: PositiveDecimal
    aggression_ratio: PositiveDecimal
    effectiveness_by_horizon: tuple[EffectivenessFeature, ...]
    resiliency_by_horizon: tuple[ResiliencyFeature, ...]
    recovery_time_deltas: tuple[RecoveryTimeFeature, ...]
    absorption_by_horizon: tuple[AbsorptionFeature, ...]
    liquidity_credibility: LiquidityCredibilityFeature | None
    toxicity: ToxicityFeature | None
    baseline: BaselineFeatureSnapshot
    feature_availability: tuple[FeatureAvailability, ...]
    feature_available_at_process_time: UtcDatetime
    feature_version: NonBlankStr = FEATURE_SNAPSHOT_VERSION

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """Require exact ratios, unique horizons, and maximum feature availability."""
        if self.aggression_ratio != self.normalized_aggression_2 / self.normalized_aggression_1:
            raise ValueError("aggression_ratio must equal normalized aggression 2 / 1")
        for values, label in (
            (self.effectiveness_by_horizon, "effectiveness"),
            (self.resiliency_by_horizon, "resiliency"),
            (self.absorption_by_horizon, "absorption"),
        ):
            horizons = tuple(item.horizon_events for item in values)
            if horizons != tuple(sorted(set(horizons))):
                raise ValueError(f"{label} horizons must be unique and sorted")
        thresholds = tuple(item.threshold for item in self.recovery_time_deltas)
        if thresholds != tuple(sorted(set(thresholds))):
            raise ValueError("recovery thresholds must be unique and sorted")
        if not self.feature_availability:
            raise ValueError("feature snapshot requires availability provenance")
        names = tuple(item.feature_name for item in self.feature_availability)
        if len(set(names)) != len(names):
            raise ValueError("feature availability names must be unique")
        latest = max(item.available_at_process_time for item in self.feature_availability)
        if self.feature_available_at_process_time != latest:
            raise ValueError("feature availability must equal latest included evidence")
        return self


class FeatureSnapshotConfig(ContractModel):
    """Transparent baseline depths and backward horizons for feature construction."""

    depth_levels: int = Field(default=5, gt=0)
    weighted_depth_config: WeightedDepthConfig = Field(default_factory=WeightedDepthConfig)
    backward_horizons_events: tuple[int, ...] = (10, 25, 50)
    feature_version: NonBlankStr = FEATURE_SNAPSHOT_VERSION

    @model_validator(mode="after")
    def validate_horizons(self) -> Self:
        """Require positive unique sorted past-only horizons."""
        if self.backward_horizons_events != tuple(
            sorted(set(self.backward_horizons_events))
        ) or any(horizon <= 0 for horizon in self.backward_horizons_events):
            raise ValueError("backward horizons must be positive, unique, and sorted")
        return self
