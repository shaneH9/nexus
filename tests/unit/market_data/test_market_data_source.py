"""Tests for the offline provider-shaped market-data fixture adapter."""

from datetime import UTC
from pathlib import Path

import pytest
from tests.support.market_data import INSTRUMENT

from sra_nexus.backtest import MarketReplay
from sra_nexus.market_data import (
    AggressorSide,
    BookEvent,
    DuplicateSequenceError,
    OrderBook,
    QuoteEvent,
    SequenceGapError,
    TradeEvent,
)
from sra_nexus.market_data.sources import (
    MarketDataFixtureError,
    MarketDataSource,
    MockMarketDataSource,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market_data"


def test_mock_source_satisfies_protocol_and_sorts_out_of_order_payload() -> None:
    """Downstream consumers should see stream/sequence order, not fixture insertion order."""
    source: MarketDataSource = MockMarketDataSource(FIXTURES / "full_replay.json")

    events = source.read()

    book_sequences = [event.sequence_number for event in events if isinstance(event, BookEvent)]
    trade_sequences = [event.sequence_number for event in events if isinstance(event, TradeEvent)]
    quote_sequences = [event.sequence_number for event in events if isinstance(event, QuoteEvent)]
    assert book_sequences == [100, 101, 102, 103]
    assert trade_sequences == [200, 201, 202]
    assert quote_sequences == [300]


def test_mock_source_reads_jsonl_provider_records() -> None:
    """JSONL input should use the same normalization path as JSON records."""
    events = MockMarketDataSource(FIXTURES / "single_trade.jsonl").read()

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, TradeEvent)
    assert event.trade_id.root == "jsonl-trade"
    assert event.aggressor_side is AggressorSide.UNKNOWN


def test_mock_source_preserves_trade_aggressors_and_normalizes_quote_timezone() -> None:
    """Fixture metadata should remain explicit rather than inferred or time-naive."""
    events = MockMarketDataSource(FIXTURES / "full_replay.json").read()
    trades = tuple(event for event in events if isinstance(event, TradeEvent))
    quote = next(event for event in events if isinstance(event, QuoteEvent))

    assert tuple(event.aggressor_side for event in trades) == (
        AggressorSide.BUY,
        AggressorSide.SELL,
        AggressorSide.UNKNOWN,
    )
    assert quote.exchange_time.tzinfo is UTC
    assert quote.exchange_time.hour == 14


def test_market_replay_can_consume_source_without_trade_or_quote_coupling() -> None:
    """Source replay should select only the target book stream from a mixed fixture."""
    replay = MarketReplay(OrderBook(INSTRUMENT))

    snapshots = replay.replay_source(MockMarketDataSource(FIXTURES / "full_replay.json"))

    assert tuple(snapshot.sequence_number for snapshot in snapshots) == (100, 101, 102, 103)


@pytest.mark.parametrize(
    "fixture_name",
    ["invalid_timestamp.json", "invalid_price.json", "invalid_quantity.json"],
)
def test_mock_source_rejects_malformed_records_without_repair(fixture_name: str) -> None:
    """Invalid provider records should identify their fixture position and stop."""
    with pytest.raises(MarketDataFixtureError, match="record at index 0"):
        MockMarketDataSource(FIXTURES / fixture_name).read()


def test_gap_fixture_stops_replay() -> None:
    """A validly shaped fixture may still contain explicit stream corruption."""
    events = MockMarketDataSource(FIXTURES / "sequence_gap.json").read()
    book_events = tuple(event for event in events if isinstance(event, BookEvent))

    with pytest.raises(SequenceGapError):
        MarketReplay(OrderBook(INSTRUMENT)).replay(book_events)


def test_duplicate_sequence_fixture_stops_replay() -> None:
    """Deterministic fallback ordering must not make duplicate sequences valid."""
    events = MockMarketDataSource(FIXTURES / "duplicate_sequence.json").read()
    book_events = tuple(event for event in events if isinstance(event, BookEvent))

    with pytest.raises(DuplicateSequenceError):
        MarketReplay(OrderBook(INSTRUMENT)).replay(book_events)


def test_reset_fixture_restarts_state_after_forward_gap() -> None:
    """A RESET event should clear the book and establish the later sequence baseline."""
    events = MockMarketDataSource(FIXTURES / "lifecycle.json").read()
    book_events = tuple(event for event in events if isinstance(event, BookEvent))

    snapshots = MarketReplay(OrderBook(INSTRUMENT)).replay(book_events)

    assert snapshots[-2].bid_levels == () and snapshots[-2].ask_levels == ()
    assert snapshots[-1].best_bid is not None
