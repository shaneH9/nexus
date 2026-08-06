"""Tests for provider-normalized shared and independent sequence domains."""

import pytest
from tests.support.market_data import (
    BOOK_STREAM_ID,
    INSTRUMENT,
    SHARED_STREAM_ID,
    book_event,
    quote_event,
    trade_event,
)

from sra_nexus.backtest import MarketReplay
from sra_nexus.market_data import BookAction, BookSide, OrderBook, SequenceGapError


def test_independent_book_and_trade_sequence_domains_do_not_conflict() -> None:
    """Equal numbers in explicit independent domains should advance separately."""
    events = (
        trade_event(2, trade_id="independent-trade-2"),
        book_event(3, BookAction.ADD, price="99.99", order_id="independent-bid-2"),
        book_event(1, BookAction.ADD, order_id="independent-bid-1"),
        trade_event(1, trade_id="independent-trade-1"),
        book_event(
            2,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.01",
            order_id="independent-ask-1",
        ),
    )
    book = OrderBook(INSTRUMENT)

    snapshots = MarketReplay(book).replay(events)

    assert tuple(snapshot.sequence_number for snapshot in snapshots) == (1, 2, 3)
    assert book.sequence_stream_id == BOOK_STREAM_ID
    assert book.last_sequence == 3


def test_shared_book_trade_quote_sequence_has_no_false_book_gap() -> None:
    """Non-book messages must advance the shared sequence without changing book state."""
    events = (
        book_event(
            5,
            BookAction.ADD,
            price="99.99",
            order_id="shared-bid-2",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        quote_event(4, sequence_stream_id=SHARED_STREAM_ID),
        book_event(
            1,
            BookAction.ADD,
            order_id="shared-bid-1",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        book_event(
            3,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.01",
            order_id="shared-ask-1",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        trade_event(
            2,
            trade_id="shared-trade-2",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
    )
    book = OrderBook(INSTRUMENT)

    snapshots = MarketReplay(book).replay(events)

    assert tuple(snapshot.sequence_number for snapshot in snapshots) == (1, 3, 5)
    assert book.sequence_stream_id == SHARED_STREAM_ID
    assert book.last_sequence == 5
    assert snapshots[-1].best_bid is not None
    assert snapshots[-1].best_ask is not None


def test_gap_detection_uses_the_selected_shared_sequence_domain() -> None:
    """A genuinely missing shared sequence must still stop reconstruction."""
    events = (
        book_event(
            1,
            BookAction.ADD,
            order_id="gap-bid",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        trade_event(
            2,
            trade_id="gap-trade",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
        book_event(
            4,
            BookAction.ADD,
            side=BookSide.ASK,
            price="100.01",
            order_id="gap-ask",
            sequence_stream_id=SHARED_STREAM_ID,
        ),
    )

    with pytest.raises(SequenceGapError, match="expected sequence_number 3"):
        MarketReplay(OrderBook(INSTRUMENT)).replay(events)
