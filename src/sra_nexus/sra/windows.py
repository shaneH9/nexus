"""Deterministic aggressive-flow observations and event windows."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Self

from pydantic import model_validator

from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import BookEventId, InstrumentId, TradeEventId
from sra_nexus.market_data.enums import (
    AggressorSide,
    ExecutionVolumeOwner,
    TradeReconciliationStatus,
)
from sra_nexus.market_data.events import BookEvent, TradeEvent
from sra_nexus.market_data.reconciliation import (
    ExecutionReconciliationPolicy,
    TradeIdExecutionReconciler,
    TradeReconciliationResult,
)
from sra_nexus.sra.enums import ShockDirection
from sra_nexus.sra.state import MarketEventReference, market_event_reference


class AggressiveTradeObservation(ContractModel):
    """One volume-owning economic execution after explicit reconciliation."""

    instrument_id: InstrumentId
    venue: NonBlankStr
    reference: MarketEventReference
    exchange_time: UtcDatetime
    receive_time: UtcDatetime
    process_time: UtcDatetime
    price: PositiveDecimal
    quantity: PositiveDecimal
    aggressor_side: AggressorSide
    volume_owner: ExecutionVolumeOwner
    reconciliation_status: TradeReconciliationStatus
    book_event_id: BookEventId | None = None
    trade_event_id: TradeEventId | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        """Require copied identity and clocks to describe the volume-owning event."""
        if self.instrument_id != self.reference.instrument_id or self.venue != self.reference.venue:
            raise ValueError("aggressive observation and event reference must share a stream")
        if (
            self.exchange_time != self.reference.exchange_time
            or self.receive_time != self.reference.receive_time
            or self.process_time != self.reference.process_time
        ):
            raise ValueError("aggressive observation clocks must match its event reference")
        if self.volume_owner is ExecutionVolumeOwner.BOOK_EVENT:
            if self.book_event_id is None or self.trade_event_id is not None:
                raise ValueError("book-owned volume requires only book_event_id")
            if self.aggressor_side is not AggressorSide.UNKNOWN:
                raise ValueError("book-owned volume must preserve UNKNOWN aggressor side")
        elif self.trade_event_id is None:
            raise ValueError("trade-owned volume requires trade_event_id")
        return self


class ReconciledTradeBatch(ContractModel):
    """Auditable reconciliation decision and its zero, one, or two volume owners."""

    reconciliation: TradeReconciliationResult
    observations: tuple[AggressiveTradeObservation, ...]

    @model_validator(mode="after")
    def validate_owner_count(self) -> Self:
        """Keep materialized observations aligned with the ownership decision."""
        if len(self.observations) != len(self.reconciliation.volume_owners):
            raise ValueError("observation count must match reconciled volume owners")
        if tuple(item.volume_owner for item in self.observations) != (
            self.reconciliation.volume_owners
        ):
            raise ValueError("observation order must match reconciled volume owners")
        return self


class AggressiveFlowWindow(ContractModel):
    """Exact event-bounded BUY, SELL, and UNKNOWN executed flow.

    Volumes use instrument quantity units. Signed order flow excludes UNKNOWN.
    The supplied observation order is the explicit event order; wall-clock
    duration is metadata rather than the window definition.
    """

    instrument_id: InstrumentId
    start_reference: MarketEventReference
    end_reference: MarketEventReference
    start_exchange_time: UtcDatetime
    end_exchange_time: UtcDatetime
    start_process_time: UtcDatetime
    end_process_time: UtcDatetime
    buy_volume: NonNegativeDecimal
    sell_volume: NonNegativeDecimal
    unknown_volume: NonNegativeDecimal
    buy_trade_count: int
    sell_trade_count: int
    unknown_trade_count: int
    observations: tuple[AggressiveTradeObservation, ...]

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        """Require nonempty, ordered observations and exact stored aggregates."""
        if not self.observations:
            raise ValueError("aggressive flow window requires at least one observation")
        if min(self.buy_trade_count, self.sell_trade_count, self.unknown_trade_count) < 0:
            raise ValueError("trade counts must be non-negative")
        if self.start_exchange_time > self.end_exchange_time:
            raise ValueError("market-time window duration cannot be negative")
        if self.start_process_time > self.end_process_time:
            raise ValueError("observable-time window duration cannot be negative")
        if self.start_reference != self.observations[0].reference:
            raise ValueError("start_reference must identify the first observation")
        if self.end_reference != self.observations[-1].reference:
            raise ValueError("end_reference must identify the last observation")
        if any(item.instrument_id != self.instrument_id for item in self.observations):
            raise ValueError("all window observations must share instrument_id")
        if len({item.venue for item in self.observations}) != 1:
            raise ValueError("all window observations must share venue")
        expected = _aggregate_flow(self.observations)
        stored = (
            self.buy_volume,
            self.sell_volume,
            self.unknown_volume,
            self.buy_trade_count,
            self.sell_trade_count,
            self.unknown_trade_count,
        )
        if stored != expected:
            raise ValueError("stored flow aggregates must equal the observation values")
        return self

    @property
    def relevant_event_count(self) -> int:
        """Return the number of volume-owning trade observations in the window."""
        return self.buy_trade_count + self.sell_trade_count + self.unknown_trade_count


def reconcile_aggressive_trade_observations(
    book_event: BookEvent | None,
    trade_event: TradeEvent | None,
    policy: ExecutionReconciliationPolicy | None = None,
) -> ReconciledTradeBatch:
    """Materialize exactly the volume owners selected by reconciliation.

    A book-owned execution has no authoritative aggressor field, so its side
    remains UNKNOWN. A trade-owned execution preserves the normalized
    ``TradeEvent.aggressor_side``. MATCHED observations therefore own volume
    only once through the trade event.
    """
    reconciler = TradeIdExecutionReconciler() if policy is None else policy
    result = reconciler.reconcile(book_event, trade_event)
    observations: list[AggressiveTradeObservation] = []
    for owner in result.volume_owners:
        if owner is ExecutionVolumeOwner.BOOK_EVENT:
            if book_event is None or book_event.price is None or book_event.quantity is None:
                raise ValueError("reconciled book owner is missing exact execution values")
            observations.append(
                AggressiveTradeObservation(
                    instrument_id=book_event.instrument_id,
                    venue=book_event.venue,
                    reference=market_event_reference(book_event),
                    exchange_time=book_event.exchange_time,
                    receive_time=book_event.receive_time,
                    process_time=book_event.process_time,
                    price=book_event.price,
                    quantity=book_event.quantity,
                    aggressor_side=AggressorSide.UNKNOWN,
                    volume_owner=owner,
                    reconciliation_status=result.status,
                    book_event_id=book_event.event_id,
                )
            )
        else:
            if trade_event is None:
                raise ValueError("reconciled trade owner is missing its TradeEvent")
            observations.append(
                AggressiveTradeObservation(
                    instrument_id=trade_event.instrument_id,
                    venue=trade_event.venue,
                    reference=market_event_reference(trade_event),
                    exchange_time=trade_event.exchange_time,
                    receive_time=trade_event.receive_time,
                    process_time=trade_event.process_time,
                    price=trade_event.price,
                    quantity=trade_event.quantity,
                    aggressor_side=trade_event.aggressor_side,
                    volume_owner=owner,
                    reconciliation_status=result.status,
                    trade_event_id=trade_event.trade_event_id,
                )
            )
    return ReconciledTradeBatch(reconciliation=result, observations=tuple(observations))


def build_aggressive_flow_window(
    observations: Sequence[AggressiveTradeObservation],
    *,
    last_event_count: int | None = None,
) -> AggressiveFlowWindow:
    """Aggregate an explicit event order, optionally selecting its last N events."""
    if last_event_count is not None and last_event_count <= 0:
        raise ValueError("last_event_count must be positive")
    selected = tuple(observations)
    if last_event_count is not None:
        selected = selected[-last_event_count:]
    if not selected:
        raise ValueError("aggressive flow window requires at least one observation")
    for earlier, later in zip(selected, selected[1:], strict=False):
        if earlier.exchange_time > later.exchange_time:
            raise ValueError("observations must be in nondecreasing exchange-time order")
        if earlier.process_time > later.process_time:
            raise ValueError("observations must be in nondecreasing process-time order")
    instrument_id = selected[0].instrument_id
    if any(item.instrument_id != instrument_id for item in selected):
        raise ValueError("all window observations must share instrument_id")
    aggregates = _aggregate_flow(selected)
    return AggressiveFlowWindow(
        instrument_id=instrument_id,
        start_reference=selected[0].reference,
        end_reference=selected[-1].reference,
        start_exchange_time=selected[0].exchange_time,
        end_exchange_time=selected[-1].exchange_time,
        start_process_time=selected[0].process_time,
        end_process_time=selected[-1].process_time,
        buy_volume=aggregates[0],
        sell_volume=aggregates[1],
        unknown_volume=aggregates[2],
        buy_trade_count=aggregates[3],
        sell_trade_count=aggregates[4],
        unknown_trade_count=aggregates[5],
        observations=selected,
    )


def signed_aggressive_flow(window: AggressiveFlowWindow) -> Decimal:
    """Return exact ``V_buy(W) - V_sell(W)``; UNKNOWN volume is excluded."""
    return window.buy_volume - window.sell_volume


def directional_aggressive_volume(
    window: AggressiveFlowWindow,
    direction: ShockDirection,
) -> Decimal:
    """Return exact volume owned by the requested observed aggressor direction."""
    return window.buy_volume if direction is ShockDirection.BUY else window.sell_volume


def directional_trade_count(window: AggressiveFlowWindow, direction: ShockDirection) -> int:
    """Return count of volume-owning observations in the requested direction."""
    return window.buy_trade_count if direction is ShockDirection.BUY else window.sell_trade_count


def _aggregate_flow(
    observations: Sequence[AggressiveTradeObservation],
) -> tuple[Decimal, Decimal, Decimal, int, int, int]:
    buy_volume = Decimal(0)
    sell_volume = Decimal(0)
    unknown_volume = Decimal(0)
    buy_count = 0
    sell_count = 0
    unknown_count = 0
    for observation in observations:
        if observation.aggressor_side is AggressorSide.BUY:
            buy_volume += observation.quantity
            buy_count += 1
        elif observation.aggressor_side is AggressorSide.SELL:
            sell_volume += observation.quantity
            sell_count += 1
        else:
            unknown_volume += observation.quantity
            unknown_count += 1
    return buy_volume, sell_volume, unknown_volume, buy_count, sell_count, unknown_count
