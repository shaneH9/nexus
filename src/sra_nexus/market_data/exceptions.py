"""Explicit market-stream and order-book corruption errors."""


class MarketDataError(RuntimeError):
    """Base failure for deterministic market-data processing."""


class SequenceError(MarketDataError):
    """Base failure for a corrupt sequence stream."""


class DuplicateSequenceError(SequenceError):
    """Raised when a stream repeats its last accepted sequence number."""


class SequenceRegressionError(SequenceError):
    """Raised when a stream moves to a lower sequence number."""


class SequenceGapError(SequenceError):
    """Raised when a non-RESET transition skips an expected sequence number."""


class OrderBookStateError(MarketDataError):
    """Base failure for an impossible or unsupported order-book transition."""


class UnsupportedBookModeError(OrderBookStateError):
    """Raised when reconstruction receives a mode it does not implement."""


class BookStreamMismatchError(OrderBookStateError):
    """Raised for another instrument, venue, or normalized sequence stream."""


class AmbiguousBookStreamError(OrderBookStateError):
    """Raised when replay cannot select one normalized book sequence stream."""


class TickAlignmentError(OrderBookStateError):
    """Raised when a price is not aligned with known instrument tick size."""


class DuplicateOrderError(OrderBookStateError):
    """Raised when ADD reuses an active order identity."""


class UnknownOrderError(OrderBookStateError):
    """Raised when a transition references an inactive order identity."""


class OrderAttributeMismatchError(OrderBookStateError):
    """Raised when event side or price conflicts with active order state."""


class QuantityExceedsRemainingError(OrderBookStateError):
    """Raised when cancellation or execution exceeds remaining order quantity."""


class NegativeDepthError(OrderBookStateError):
    """Raised when a transition would produce negative aggregate depth."""


class CrossedBookError(OrderBookStateError):
    """Raised when reconstruction would leave best bid above best ask."""


class BookNotInitializedError(OrderBookStateError):
    """Raised when snapshot creation precedes every accepted book event."""
