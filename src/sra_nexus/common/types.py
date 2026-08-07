"""Shared strongly typed identifiers for SRA-Nexus domain objects."""

from typing import Self
from uuid import UUID, uuid4

from pydantic import ConfigDict, RootModel, field_validator


class _UuidIdentifier(RootModel[UUID]):
    """Immutable UUID value object with type-specific equality."""

    model_config = ConfigDict(frozen=True)

    @classmethod
    def new(cls) -> Self:
        """Generate a new identifier of the concrete subtype."""
        return cls(uuid4())

    def __str__(self) -> str:
        """Return the canonical UUID string."""
        return str(self.root)


class InstrumentId(_UuidIdentifier):
    """Stable internal identifier for an instrument."""


class EntityId(_UuidIdentifier):
    """Stable internal identifier for a canonical entity."""


class NewsId(_UuidIdentifier):
    """Stable internal identifier for a raw news item."""


class CanonicalEventId(_UuidIdentifier):
    """Stable internal identifier for a canonical event."""


class CanonicalEventRevisionId(_UuidIdentifier):
    """Stable internal identifier for an immutable canonical-event revision."""


class EntityRelationshipId(_UuidIdentifier):
    """Stable internal identifier for a structural entity relationship."""


class EntityInstrumentLinkId(_UuidIdentifier):
    """Stable internal identifier for an entity-to-instrument relationship."""


class ExposurePathId(_UuidIdentifier):
    """Stable internal identifier for one auditable event-exposure path."""


class BookEventId(_UuidIdentifier):
    """Stable internal identifier for an immutable order-book event."""


class TradeEventId(_UuidIdentifier):
    """Stable internal identifier for an immutable trade observation."""


class QuoteEventId(_UuidIdentifier):
    """Stable internal identifier for an immutable top-of-book quote observation."""


class ShockId(_UuidIdentifier):
    """Stable internal identifier for an immutable detected liquidity shock."""


class ShockPairId(_UuidIdentifier):
    """Stable internal identifier for one ordered liquidity-shock comparison."""


class OrderLifecycleId(_UuidIdentifier):
    """Stable internal identifier for one observed provider-order lifecycle."""


class ReplenishmentEpisodeId(_UuidIdentifier):
    """Stable internal identifier for one price-level replenishment episode."""


class ResearchObservationId(_UuidIdentifier):
    """Stable internal identifier for one immutable historical research row."""


class ResearchSplitId(_UuidIdentifier):
    """Stable internal identifier for one chronological research split."""


class PermutationTestId(_UuidIdentifier):
    """Stable internal identifier for one reproducible permutation test."""


class ResearchRunId(_UuidIdentifier):
    """Deterministic identity for one experiment, dataset, and code revision."""


class _OpaqueStringIdentifier(RootModel[str]):
    """Immutable typed wrapper for a provider-defined opaque identifier."""

    model_config = ConfigDict(frozen=True)

    @field_validator("root", mode="before")
    @classmethod
    def normalize_identifier(cls, value: object) -> object:
        """Trim a non-empty string without coercing numeric provider IDs."""
        if not isinstance(value, str):
            raise ValueError("opaque identifier must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("opaque identifier must not be blank")
        return normalized

    def __str__(self) -> str:
        """Return the normalized provider identifier."""
        return self.root


class SequenceStreamId(_OpaqueStringIdentifier):
    """Provider-normalized identity for one sequence-number domain."""


class MarketOrderId(_OpaqueStringIdentifier):
    """Typed provider order identity within one market-data stream."""


class MarketTradeId(_OpaqueStringIdentifier):
    """Typed provider trade identity within one market-data stream."""


def new_news_id() -> NewsId:
    """Return a new internal news identifier."""
    return NewsId.new()


def new_canonical_event_id() -> CanonicalEventId:
    """Return a new internal canonical-event identifier."""
    return CanonicalEventId.new()


def new_canonical_event_revision_id() -> CanonicalEventRevisionId:
    """Return a new immutable canonical-event revision identifier."""
    return CanonicalEventRevisionId.new()


def new_instrument_id() -> InstrumentId:
    """Return a new internal instrument identifier."""
    return InstrumentId.new()


def new_entity_id() -> EntityId:
    """Return a new internal entity identifier."""
    return EntityId.new()


def new_entity_relationship_id() -> EntityRelationshipId:
    """Return a new structural relationship identifier."""
    return EntityRelationshipId.new()


def new_entity_instrument_link_id() -> EntityInstrumentLinkId:
    """Return a new entity-to-instrument link identifier."""
    return EntityInstrumentLinkId.new()


def new_exposure_path_id() -> ExposurePathId:
    """Return a new exposure-path identifier."""
    return ExposurePathId.new()


def new_book_event_id() -> BookEventId:
    """Return a new internal book-event identifier."""
    return BookEventId.new()


def new_trade_event_id() -> TradeEventId:
    """Return a new internal trade-event identifier."""
    return TradeEventId.new()


def new_quote_event_id() -> QuoteEventId:
    """Return a new internal quote-event identifier."""
    return QuoteEventId.new()


def new_shock_id() -> ShockId:
    """Return a new internal liquidity-shock identifier."""
    return ShockId.new()
