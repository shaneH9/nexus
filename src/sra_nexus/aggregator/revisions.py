"""Immutable canonical-event revision and candidate-query contracts."""

from __future__ import annotations

from pydantic import Field, PositiveInt, field_validator, model_validator

from sra_nexus.aggregator.enums import EventSubtype, EventType, NewsSourceType
from sra_nexus.aggregator.events import CanonicalEvent
from sra_nexus.common.models import ContractModel, NonBlankStr, UtcDatetime
from sra_nexus.common.types import (
    CanonicalEventRevisionId,
    new_canonical_event_revision_id,
)


class CanonicalEventRevision(ContractModel):
    """One immutable canonical-event state available from a specific UTC time."""

    revision_id: CanonicalEventRevisionId = Field(default_factory=new_canonical_event_revision_id)
    revision_number: PositiveInt
    available_at: UtcDatetime
    event: CanonicalEvent
    headline_tokens: tuple[NonBlankStr, ...] = ()
    anchors: tuple[NonBlankStr, ...] = ()
    ticker_anchors: tuple[NonBlankStr, ...] = ()
    source_names: tuple[NonBlankStr, ...] = Field(min_length=1)
    source_types: tuple[NewsSourceType, ...] = Field(min_length=1)

    @field_validator(
        "headline_tokens",
        "anchors",
        "ticker_anchors",
        "source_names",
        "source_types",
        mode="after",
    )
    @classmethod
    def deduplicate_metadata(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        """Remove repeated revision metadata while preserving deterministic order."""
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_revision(self) -> CanonicalEventRevision:
        """Require availability-safe, fully classified revision state."""
        if self.event.event_subtype is None:
            raise ValueError("canonical event revisions require an event_subtype")
        if self.event.last_update_time > self.available_at:
            raise ValueError("event last_update_time must not be after revision available_at")
        if not set(self.ticker_anchors).issubset(self.anchors):
            raise ValueError("ticker_anchors must be a subset of anchors")
        return self


class CanonicalEventCandidateQuery(ContractModel):
    """Indexed constraints for retrieving historical clustering candidates."""

    event_type: EventType
    event_subtype: EventSubtype
    as_of: UtcDatetime
    not_before: UtcDatetime
    anchors: tuple[NonBlankStr, ...] = ()

    @field_validator("anchors", mode="after")
    @classmethod
    def deduplicate_anchors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Retain one copy of each deterministic candidate anchor."""
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_window(self) -> CanonicalEventCandidateQuery:
        """Reject a candidate window whose lower bound follows its cutoff."""
        if self.not_before > self.as_of:
            raise ValueError("not_before must not be after as_of")
        if self.event_subtype.event_type is not self.event_type:
            raise ValueError("event_subtype must belong to event_type")
        return self
