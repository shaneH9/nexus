"""Stable categorical values for normalized market-data contracts."""

from enum import StrEnum


class MarketEventKind(StrEnum):
    """Normalized market-event variants, independent of sequence scope."""

    BOOK = "BOOK"
    TRADE = "TRADE"
    QUOTE = "QUOTE"


class BookAction(StrEnum):
    """Supported deterministic order-book state transitions."""

    ADD = "ADD"
    MODIFY = "MODIFY"
    CANCEL = "CANCEL"
    EXECUTE = "EXECUTE"
    DELETE = "DELETE"
    RESET = "RESET"


class BookSide(StrEnum):
    """Displayed side of a limit order or aggregate price level."""

    BID = "BID"
    ASK = "ASK"


class AggressorSide(StrEnum):
    """Explicitly observed trade aggressor side."""

    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class BookDataMode(StrEnum):
    """Whether book events identify individual orders or only aggregate levels."""

    MARKET_BY_ORDER = "MARKET_BY_ORDER"
    MARKET_BY_PRICE = "MARKET_BY_PRICE"


class MarketEventFlag(StrEnum):
    """Small provider-neutral normalized market-event flag set."""

    REGULAR = "REGULAR"
    AUCTION = "AUCTION"
    CORRECTION = "CORRECTION"
    ODD_LOT = "ODD_LOT"


class TradeReconciliationStatus(StrEnum):
    """Relationship between book-mutation and trade-print observations."""

    BOOK_ONLY = "BOOK_ONLY"
    TRADE_ONLY = "TRADE_ONLY"
    MATCHED = "MATCHED"
    DISTINCT = "DISTINCT"
    UNRESOLVED = "UNRESOLVED"


class ExecutionVolumeOwner(StrEnum):
    """Observation permitted to own executed volume after reconciliation."""

    BOOK_EVENT = "BOOK_EVENT"
    TRADE_EVENT = "TRADE_EVENT"
