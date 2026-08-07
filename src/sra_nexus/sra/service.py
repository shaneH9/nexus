"""Focused orchestration for deterministic Milestone G research primitives."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel
from sra_nexus.market_data.snapshots import BookSnapshot
from sra_nexus.sra.enums import ShockDirection, ShockResearchStatus
from sra_nexus.sra.impact import ImpactConfig, ShockImpact, calculate_shock_impacts
from sra_nexus.sra.resiliency import (
    ResiliencyConfig,
    ResiliencyVector,
    calculate_resiliency_vector,
)
from sra_nexus.sra.shock import (
    BookExecutionState,
    LevelPenetration,
    LiquidityShock,
    NormalizedAggression,
    ShockClassification,
    ShockDetectionConfig,
    ShockFeatures,
    build_shock_features,
    calculate_level_penetration,
    calculate_normalized_aggression,
    classify_shock,
    materialize_liquidity_shock,
)
from sra_nexus.sra.state import MarketStateObservation
from sra_nexus.sra.windows import (
    AggressiveFlowWindow,
    AggressiveTradeObservation,
    build_aggressive_flow_window,
    directional_aggressive_volume,
)


class ShockResearchConfig(ContractModel):
    """Central immutable Milestone G configuration bundle."""

    shock_detection: ShockDetectionConfig = Field(default_factory=ShockDetectionConfig)
    impact: ImpactConfig = Field(default_factory=ImpactConfig)
    resiliency: ResiliencyConfig = Field(default_factory=ResiliencyConfig)


class ShockResearchResult(ContractModel):
    """Atomic result for one explicit episode, including non-candidate outcomes."""

    status: ShockResearchStatus
    direction: ShockDirection
    flow_window: AggressiveFlowWindow
    normalized_aggression: NormalizedAggression | None
    level_penetration: LevelPenetration | None
    shock_features: ShockFeatures | None
    classification: ShockClassification | None
    liquidity_shock: LiquidityShock | None
    impacts: tuple[ShockImpact, ...]
    resiliency: ResiliencyVector | None

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        """Prevent incomplete candidate results from appearing valid."""
        derived = (
            self.normalized_aggression,
            self.level_penetration,
            self.shock_features,
            self.classification,
        )
        if self.status is ShockResearchStatus.NO_DIRECTIONAL_AGGRESSION:
            if any(value is not None for value in derived):
                raise ValueError("no-direction result cannot contain directional features")
            if self.liquidity_shock is not None or self.impacts or self.resiliency is not None:
                raise ValueError("no-direction result cannot contain shock response features")
        elif any(value is None for value in derived):
            raise ValueError("directional result requires all transparent shock features")
        if self.status is ShockResearchStatus.SHOCK_CANDIDATE:
            if self.liquidity_shock is None or self.resiliency is None:
                raise ValueError("shock candidate requires shock and resiliency outputs")
            if not self.impacts:
                raise ValueError("shock candidate requires configured impact outputs")
        elif self.liquidity_shock is not None or self.impacts or self.resiliency is not None:
            raise ValueError("non-candidate cannot contain materialized response features")
        return self


class ShockResearchService:
    """Compose pure shock, impact, and resiliency calculations without persistence."""

    def __init__(self, config: ShockResearchConfig | None = None) -> None:
        """Configure explicit initial engineering thresholds and event horizons."""
        self._config = ShockResearchConfig() if config is None else config

    @property
    def config(self) -> ShockResearchConfig:
        """Return the immutable configuration used for reproducible output."""
        return self._config

    def analyze_episode(
        self,
        *,
        direction: ShockDirection,
        aggressive_observations: Sequence[AggressiveTradeObservation],
        pre_snapshot: BookSnapshot,
        end_snapshot: BookSnapshot,
        book_executions: Sequence[BookExecutionState],
        depletion_snapshots: Sequence[BookSnapshot],
        response_observations: Sequence[MarketStateObservation],
    ) -> ShockResearchResult:
        """Analyze one caller-bounded event episode atomically.

        ``pre_snapshot`` must represent state immediately before the supplied
        window. Callers can use ``build_aggressive_flow_window(...,
        last_event_count=N)`` to select last-N episodes, then supply the matching
        baseline. This service rejects oversized ambiguous windows rather than
        silently changing their baseline.
        """
        observations = tuple(aggressive_observations)
        if len(observations) > self._config.shock_detection.aggression_window_event_count:
            raise ValueError("episode exceeds configured aggression-window event count")
        window = build_aggressive_flow_window(observations)
        if directional_aggressive_volume(window, direction) == 0:
            return ShockResearchResult(
                status=ShockResearchStatus.NO_DIRECTIONAL_AGGRESSION,
                direction=direction,
                flow_window=window,
                normalized_aggression=None,
                level_penetration=None,
                shock_features=None,
                classification=None,
                liquidity_shock=None,
                impacts=(),
                resiliency=None,
            )
        normalized = calculate_normalized_aggression(
            direction,
            window,
            pre_snapshot,
            end_snapshot,
            self._config.shock_detection.weighted_depth_config,
        )
        if normalized is None:
            raise AssertionError("positive directional volume unexpectedly lacked normalization")
        penetration = calculate_level_penetration(direction, tuple(book_executions))
        features = build_shock_features(
            window,
            normalized,
            penetration,
            pre_snapshot,
            end_snapshot,
        )
        classification = classify_shock(features, self._config.shock_detection)
        if not classification.is_candidate:
            return ShockResearchResult(
                status=ShockResearchStatus.BELOW_THRESHOLDS,
                direction=direction,
                flow_window=window,
                normalized_aggression=normalized,
                level_penetration=penetration,
                shock_features=features,
                classification=classification,
                liquidity_shock=None,
                impacts=(),
                resiliency=None,
            )
        shock = materialize_liquidity_shock(features, classification)
        responses = tuple(response_observations)
        impacts = calculate_shock_impacts(
            shock,
            features.pre_midprice,
            responses,
            self._config.impact,
        )
        resiliency = calculate_resiliency_vector(
            shock,
            pre_snapshot,
            tuple(depletion_snapshots),
            responses,
            self._config.resiliency,
        )
        return ShockResearchResult(
            status=ShockResearchStatus.SHOCK_CANDIDATE,
            direction=direction,
            flow_window=window,
            normalized_aggression=normalized,
            level_penetration=penetration,
            shock_features=features,
            classification=classification,
            liquidity_shock=shock,
            impacts=impacts,
            resiliency=resiliency,
        )
