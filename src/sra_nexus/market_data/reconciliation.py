"""Provider-neutral ownership policy for execution observations."""

from __future__ import annotations

from typing import Protocol, Self

from pydantic import model_validator

from sra_nexus.common.models import ContractModel
from sra_nexus.common.types import (
    BookEventId,
    MarketTradeId,
    TradeEventId,
)
from sra_nexus.market_data.enums import (
    BookAction,
    ExecutionVolumeOwner,
    TradeReconciliationStatus,
)
from sra_nexus.market_data.events import BookEvent, TradeEvent


class TradeReconciliationResult(ContractModel):
    """Auditable decision about economic-execution volume ownership."""

    status: TradeReconciliationStatus
    book_event_id: BookEventId | None = None
    trade_event_id: TradeEventId | None = None
    common_trade_id: MarketTradeId | None = None
    volume_owners: tuple[ExecutionVolumeOwner, ...]

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        """Keep ownership and observation identities consistent with status."""
        expected: dict[
            TradeReconciliationStatus,
            tuple[bool, bool, bool, tuple[ExecutionVolumeOwner, ...]],
        ] = {
            TradeReconciliationStatus.BOOK_ONLY: (
                True,
                False,
                False,
                (ExecutionVolumeOwner.BOOK_EVENT,),
            ),
            TradeReconciliationStatus.TRADE_ONLY: (
                False,
                True,
                False,
                (ExecutionVolumeOwner.TRADE_EVENT,),
            ),
            TradeReconciliationStatus.MATCHED: (
                True,
                True,
                True,
                (ExecutionVolumeOwner.TRADE_EVENT,),
            ),
            TradeReconciliationStatus.DISTINCT: (
                True,
                True,
                False,
                (
                    ExecutionVolumeOwner.BOOK_EVENT,
                    ExecutionVolumeOwner.TRADE_EVENT,
                ),
            ),
            TradeReconciliationStatus.UNRESOLVED: (True, True, False, ()),
        }
        book_required, trade_required, common_required, owners = expected[self.status]
        if (self.book_event_id is not None) is not book_required:
            raise ValueError("book_event_id is inconsistent with reconciliation status")
        if (self.trade_event_id is not None) is not trade_required:
            raise ValueError("trade_event_id is inconsistent with reconciliation status")
        if (self.common_trade_id is not None) is not common_required:
            raise ValueError("common_trade_id is inconsistent with reconciliation status")
        if self.volume_owners != owners:
            raise ValueError("volume_owners are inconsistent with reconciliation status")
        return self

    @property
    def economic_execution_count(self) -> int | None:
        """Return countable executions, or ``None`` when ownership is unresolved."""
        if self.status is TradeReconciliationStatus.UNRESOLVED:
            return None
        return len(self.volume_owners)


class ExecutionReconciliationPolicy(Protocol):
    """Boundary for reconciling state mutation and trade-flow observations."""

    def reconcile(
        self,
        book_event: BookEvent | None,
        trade_event: TradeEvent | None,
    ) -> TradeReconciliationResult:
        """Assign executed-volume ownership without inferring aggressor side."""
        ...


class TradeIdExecutionReconciler:
    """Conservative deterministic reconciliation using normalized common trade IDs."""

    def reconcile(
        self,
        book_event: BookEvent | None,
        trade_event: TradeEvent | None,
    ) -> TradeReconciliationResult:
        """Recognize shared IDs and withhold ownership when identity is unavailable."""
        if book_event is None and trade_event is None:
            raise ValueError("at least one execution observation is required")
        if book_event is not None and book_event.action is not BookAction.EXECUTE:
            raise ValueError("book_event must have action=EXECUTE")
        if book_event is None:
            if trade_event is None:
                raise AssertionError("validated observation unexpectedly missing")
            return TradeReconciliationResult(
                status=TradeReconciliationStatus.TRADE_ONLY,
                trade_event_id=trade_event.trade_event_id,
                volume_owners=(ExecutionVolumeOwner.TRADE_EVENT,),
            )
        if trade_event is None:
            return TradeReconciliationResult(
                status=TradeReconciliationStatus.BOOK_ONLY,
                book_event_id=book_event.event_id,
                volume_owners=(ExecutionVolumeOwner.BOOK_EVENT,),
            )
        if (
            book_event.instrument_id != trade_event.instrument_id
            or book_event.venue != trade_event.venue
        ):
            raise ValueError("execution observations must share instrument and venue")
        if book_event.trade_id is None or trade_event.trade_id is None:
            return TradeReconciliationResult(
                status=TradeReconciliationStatus.UNRESOLVED,
                book_event_id=book_event.event_id,
                trade_event_id=trade_event.trade_event_id,
                volume_owners=(),
            )
        if book_event.trade_id == trade_event.trade_id:
            return TradeReconciliationResult(
                status=TradeReconciliationStatus.MATCHED,
                book_event_id=book_event.event_id,
                trade_event_id=trade_event.trade_event_id,
                common_trade_id=book_event.trade_id,
                volume_owners=(ExecutionVolumeOwner.TRADE_EVENT,),
            )
        return TradeReconciliationResult(
            status=TradeReconciliationStatus.DISTINCT,
            book_event_id=book_event.event_id,
            trade_event_id=trade_event.trade_event_id,
            volume_owners=(
                ExecutionVolumeOwner.BOOK_EVENT,
                ExecutionVolumeOwner.TRADE_EVENT,
            ),
        )
