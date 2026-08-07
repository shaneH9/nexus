"""Provider-independent construction of historical directional aggression episodes."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    NonNegativeDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import InstrumentId
from sra_nexus.market_data.enums import AggressorSide
from sra_nexus.market_data.snapshots import BookSnapshot
from sra_nexus.sra.enums import ShockDirection
from sra_nexus.sra.service import ShockResearchResult, ShockResearchService
from sra_nexus.sra.shock import BookExecutionState
from sra_nexus.sra.state import MarketStateObservation, elapsed_decimal_seconds
from sra_nexus.sra.windows import AggressiveTradeObservation


class AggressionEpisodeConfig(ContractModel):
    """Frozen initial engineering policy for continuing one directional burst.

    Event counts refer to all normalized market events. Clock values are exact
    exchange-time seconds. These defaults are preregistered starting values, not
    values fitted to forward returns.
    """

    maximum_market_event_gap_between_executions: int = Field(default=4, ge=0)
    maximum_exchange_time_gap_between_executions: NonNegativeDecimal = Decimal("0.050")
    maximum_episode_market_events: int = Field(default=20, gt=0)
    maximum_episode_exchange_seconds: NonNegativeDecimal = Decimal("0.250")


class ReconciledAggressiveExecution(ContractModel):
    """One reconciled economic execution with its normalized event coordinates."""

    observation: AggressiveTradeObservation
    execution: BookExecutionState
    execution_event_index: int = Field(ge=0)
    observation_event_index: int = Field(ge=0)
    segment: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        """Require one causal, same-stream execution/observation boundary."""
        if self.execution_event_index > self.observation_event_index:
            raise ValueError("execution event index must not follow its observation index")
        event = self.execution.event
        observation = self.observation
        if event.instrument_id != observation.instrument_id or event.venue != observation.venue:
            raise ValueError("execution and aggressive observation must share a market stream")
        if event.price != observation.price or event.quantity != observation.quantity:
            raise ValueError("reconciled execution and observation must share price and quantity")
        if event.exchange_time > observation.exchange_time:
            raise ValueError("execution exchange time must not follow its observation")
        if event.process_time > observation.process_time:
            raise ValueError("execution process time must not follow its observation")
        return self


class AggressionEpisode(ContractModel):
    """One completed same-direction historical aggressive-flow episode.

    ``pre_snapshot`` is immediately before the first execution. ``end_snapshot``
    is immediately after the final included book execution; the paired trade
    observation is non-mutating under the existing normalization contract.
    ``end_process_time`` is the earliest time the completed episode itself is
    available and therefore never precedes the final included observation.
    """

    instrument_id: InstrumentId
    venue: NonBlankStr
    direction: ShockDirection
    observations: tuple[AggressiveTradeObservation, ...]
    executions: tuple[BookExecutionState, ...]
    start_event_index: int = Field(ge=0)
    end_event_index: int = Field(ge=0)
    start_exchange_time: UtcDatetime
    end_exchange_time: UtcDatetime
    start_process_time: UtcDatetime
    end_process_time: UtcDatetime
    segment: int = Field(ge=0)
    pre_snapshot: BookSnapshot
    end_snapshot: BookSnapshot

    @model_validator(mode="after")
    def validate_episode(self) -> Self:
        """Keep stored boundaries aligned with every included execution."""
        if not self.observations or len(self.observations) != len(self.executions):
            raise ValueError("aggression episode requires paired observations and executions")
        if self.start_event_index > self.end_event_index:
            raise ValueError("aggression episode event span cannot regress")
        expected_side = (
            AggressorSide.BUY if self.direction is ShockDirection.BUY else AggressorSide.SELL
        )
        if any(item.aggressor_side is not expected_side for item in self.observations):
            raise ValueError("aggression episode observations must share its direction")
        if any(
            item.instrument_id != self.instrument_id or item.venue != self.venue
            for item in self.observations
        ):
            raise ValueError("aggression episode observations must share one market stream")
        if any(
            item.event.instrument_id != self.instrument_id or item.event.venue != self.venue
            for item in self.executions
        ):
            raise ValueError("aggression episode executions must share one market stream")
        if self.start_exchange_time != self.observations[0].exchange_time:
            raise ValueError("episode start exchange time must match its first observation")
        if self.end_exchange_time != self.observations[-1].exchange_time:
            raise ValueError("episode end exchange time must match its final observation")
        if self.start_process_time != self.observations[0].process_time:
            raise ValueError("episode start process time must match its first observation")
        if self.end_process_time != self.observations[-1].process_time:
            raise ValueError("episode end process time must match its final observation")
        if self.start_exchange_time > self.end_exchange_time:
            raise ValueError("aggression episode exchange time cannot regress")
        if self.start_process_time > self.end_process_time:
            raise ValueError("aggression episode process time cannot regress")
        if self.pre_snapshot != self.executions[0].pre_snapshot:
            raise ValueError("episode pre_snapshot must precede its first execution")
        if self.end_snapshot != self.executions[-1].post_snapshot:
            raise ValueError("episode end_snapshot must follow its final execution")
        return self


class AggressionEpisodeBuilder:
    """Group chronological reconciled executions under one frozen burst policy."""

    def __init__(
        self,
        config: AggressionEpisodeConfig | None = None,
        *,
        maximum_observations: int | None = None,
    ) -> None:
        """Configure continuation bounds and the existing SRA observation cap."""
        if maximum_observations is not None and maximum_observations <= 0:
            raise ValueError("maximum observations must be positive")
        self._config = AggressionEpisodeConfig() if config is None else config
        self._maximum_observations = maximum_observations

    @property
    def config(self) -> AggressionEpisodeConfig:
        """Return the immutable grouping policy."""
        return self._config

    def build(
        self,
        records: Sequence[ReconciledAggressiveExecution],
    ) -> tuple[AggressionEpisode, ...]:
        """Return completed directional episodes in normalized event order.

        UNKNOWN is deliberately excluded and terminates an open directional
        episode. Direction is never inferred from the resting book side.
        """
        ordered = tuple(records)
        _validate_record_order(ordered)
        episodes: list[AggressionEpisode] = []
        current: list[ReconciledAggressiveExecution] = []
        for record in ordered:
            direction = _direction(record.observation.aggressor_side)
            if direction is None:
                _finish_episode(current, episodes)
                current = []
                continue
            if current and not self._continues(current, record, direction):
                _finish_episode(current, episodes)
                current = []
            current.append(record)
        _finish_episode(current, episodes)
        return tuple(episodes)

    def _continues(
        self,
        current: Sequence[ReconciledAggressiveExecution],
        candidate: ReconciledAggressiveExecution,
        direction: ShockDirection,
    ) -> bool:
        first = current[0]
        previous = current[-1]
        if _direction(previous.observation.aggressor_side) is not direction:
            return False
        if (
            previous.segment != candidate.segment
            or previous.observation.instrument_id != candidate.observation.instrument_id
            or previous.observation.venue != candidate.observation.venue
        ):
            return False
        market_event_gap = candidate.execution_event_index - previous.observation_event_index - 1
        exchange_gap = elapsed_decimal_seconds(
            previous.execution.event.exchange_time,
            candidate.execution.event.exchange_time,
        )
        episode_market_events = candidate.observation_event_index - first.execution_event_index + 1
        episode_exchange_seconds = elapsed_decimal_seconds(
            first.observation.exchange_time,
            candidate.observation.exchange_time,
        )
        return (
            (self._maximum_observations is None or len(current) + 1 <= self._maximum_observations)
            and market_event_gap <= self._config.maximum_market_event_gap_between_executions
            and exchange_gap <= self._config.maximum_exchange_time_gap_between_executions
            and episode_market_events <= self._config.maximum_episode_market_events
            and episode_exchange_seconds <= self._config.maximum_episode_exchange_seconds
        )


def analyze_historical_aggression_episode(
    episode: AggressionEpisode,
    response_observations: Sequence[MarketStateObservation],
    service: ShockResearchService,
) -> ShockResearchResult:
    """Delegate one completed historical episode to the existing SRA service."""
    return service.analyze_episode(
        direction=episode.direction,
        aggressive_observations=episode.observations,
        pre_snapshot=episode.pre_snapshot,
        end_snapshot=episode.end_snapshot,
        book_executions=episode.executions,
        depletion_snapshots=tuple(item.post_snapshot for item in episode.executions),
        response_observations=response_observations,
    )


def _validate_record_order(records: Sequence[ReconciledAggressiveExecution]) -> None:
    for previous, current in zip(records, records[1:], strict=False):
        if current.execution_event_index <= previous.observation_event_index:
            raise ValueError("reconciled aggressive executions must be chronological and disjoint")
        if current.observation.exchange_time < previous.observation.exchange_time:
            raise ValueError("aggressive observations must not regress in exchange time")
        if current.observation.process_time < previous.observation.process_time:
            raise ValueError("aggressive observations must not regress in process time")


def _finish_episode(
    records: Sequence[ReconciledAggressiveExecution],
    episodes: list[AggressionEpisode],
) -> None:
    if not records:
        return
    first = records[0]
    last = records[-1]
    direction = _direction(first.observation.aggressor_side)
    if direction is None:
        raise AssertionError("UNKNOWN record unexpectedly reached episode materialization")
    episodes.append(
        AggressionEpisode(
            instrument_id=first.observation.instrument_id,
            venue=first.observation.venue,
            direction=direction,
            observations=tuple(item.observation for item in records),
            executions=tuple(item.execution for item in records),
            start_event_index=first.execution_event_index,
            end_event_index=last.observation_event_index,
            start_exchange_time=first.observation.exchange_time,
            end_exchange_time=last.observation.exchange_time,
            start_process_time=first.observation.process_time,
            end_process_time=last.observation.process_time,
            segment=first.segment,
            pre_snapshot=first.execution.pre_snapshot,
            end_snapshot=last.execution.post_snapshot,
        )
    )


def _direction(side: AggressorSide) -> ShockDirection | None:
    if side is AggressorSide.BUY:
        return ShockDirection.BUY
    if side is AggressorSide.SELL:
        return ShockDirection.SELL
    return None
