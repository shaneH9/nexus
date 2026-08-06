"""Offline fixture-to-storage-to-reconstruction integration coverage."""

from decimal import Decimal
from pathlib import Path

from tests.support.market_data import INSTRUMENT

from sra_nexus.backtest import MarketReplay
from sra_nexus.market_data import OrderBook
from sra_nexus.market_data.sources import MockMarketDataSource
from sra_nexus.storage import SQLiteRawMarketEventRepository

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "market_data" / "full_replay.json"


def test_offline_fixture_storage_replay_produces_exact_book_features(tmp_path: Path) -> None:
    """The complete Milestone F path should be deterministic and exact without network access."""
    source = MockMarketDataSource(FIXTURE)
    repository = SQLiteRawMarketEventRepository(tmp_path / "market-pipeline.sqlite3")
    repository.initialize_schema()
    events = source.read()
    for event in events:
        assert repository.insert(event).inserted

    snapshots = MarketReplay(OrderBook(INSTRUMENT)).replay_repository(repository)
    final = snapshots[-1]
    expected_microprice = (
        Decimal("100.01") * Decimal("200") + Decimal("100.00") * Decimal("150")
    ) / Decimal("350")

    assert len(events) == 8
    assert len(repository.list_for_instrument(INSTRUMENT.instrument_id)) == 8
    assert len(snapshots) == 4
    assert final.sequence_number == 103
    assert tuple(level.price for level in final.bid_levels) == (
        Decimal("100.00"),
        Decimal("99.99"),
    )
    assert tuple(level.price for level in final.ask_levels) == (
        Decimal("100.01"),
        Decimal("100.02"),
    )
    assert final.best_bid == Decimal("100.00")
    assert final.best_ask == Decimal("100.01")
    assert final.spread == Decimal("0.01")
    assert final.midprice == Decimal("100.005")
    assert final.microprice == expected_microprice
    assert final.order_book_imbalance(2) == Decimal("100") / Decimal("900")
    assert final.weighted_bid_depth() == Decimal("350")
    assert final.weighted_ask_depth() == Decimal("275")
