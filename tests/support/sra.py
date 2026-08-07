"""Deterministic exact-arithmetic helpers for SRA feature tests."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sra_nexus.market_data import (
    BookSnapshot,
    MarketEventKind,
    PriceLevel,
    calculate_microprice,
    calculate_midprice,
    calculate_spread,
)
from sra_nexus.sra import (
    LiquidityShock,
    MarketEventReference,
    MarketStateObservation,
    ShockDetectionMethod,
    ShockDirection,
)
from tests.support.market_data import BASE_TIME, INSTRUMENT, SHARED_STREAM_ID

SRA_BASE_TIME = BASE_TIME


def snapshot(
    sequence_number: int,
    *,
    bids: tuple[tuple[str, str], ...] = (("100.00", "100"),),
    asks: tuple[tuple[str, str], ...] = (("100.01", "100"),),
    exchange_time: datetime | None = None,
    receive_time: datetime | None = None,
    process_time: datetime | None = None,
) -> BookSnapshot:
    """Build one internally consistent reconstructed book snapshot."""
    exchange = (
        SRA_BASE_TIME + timedelta(milliseconds=sequence_number)
        if exchange_time is None
        else exchange_time
    )
    receive = exchange + timedelta(microseconds=1) if receive_time is None else receive_time
    process = receive + timedelta(microseconds=1) if process_time is None else process_time
    bid_levels = tuple(
        PriceLevel(price=Decimal(price), aggregate_quantity=Decimal(quantity), order_count=1)
        for price, quantity in bids
    )
    ask_levels = tuple(
        PriceLevel(price=Decimal(price), aggregate_quantity=Decimal(quantity), order_count=1)
        for price, quantity in asks
    )
    best_bid = None if not bid_levels else bid_levels[0].price
    best_ask = None if not ask_levels else ask_levels[0].price
    bid_quantity = None if not bid_levels else bid_levels[0].aggregate_quantity
    ask_quantity = None if not ask_levels else ask_levels[0].aggregate_quantity
    return BookSnapshot(
        instrument_id=INSTRUMENT.instrument_id,
        venue=INSTRUMENT.exchange,
        exchange_time=exchange,
        receive_time=receive,
        process_time=process,
        sequence_number=sequence_number,
        bid_levels=bid_levels,
        ask_levels=ask_levels,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=calculate_spread(best_bid, best_ask),
        midprice=calculate_midprice(best_bid, best_ask),
        microprice=calculate_microprice(
            best_bid,
            bid_quantity,
            best_ask,
            ask_quantity,
        ),
    )


def response_observation(
    sequence_number: int,
    current_snapshot: BookSnapshot,
    *,
    exchange_time: datetime | None = None,
    process_time: datetime | None = None,
) -> MarketStateObservation:
    """Wrap current book state as observed after one deterministic market event."""
    exchange = (
        SRA_BASE_TIME + timedelta(seconds=sequence_number)
        if exchange_time is None
        else exchange_time
    )
    receive = exchange + timedelta(microseconds=1)
    process = receive + timedelta(microseconds=1) if process_time is None else process_time
    reference = MarketEventReference(
        instrument_id=INSTRUMENT.instrument_id,
        venue=INSTRUMENT.exchange,
        sequence_stream_id=SHARED_STREAM_ID,
        sequence_number=sequence_number,
        event_kind=MarketEventKind.QUOTE,
        event_id=UUID(int=sequence_number + 1),
        exchange_time=exchange,
        receive_time=receive,
        process_time=process,
    )
    return MarketStateObservation(event_reference=reference, snapshot=current_snapshot)


def liquidity_shock(
    *,
    direction: ShockDirection = ShockDirection.SELL,
    aggressive_volume: str = "100",
    normalized_aggression: str = "0.5",
    end_time: datetime = SRA_BASE_TIME,
) -> LiquidityShock:
    """Build a valid deterministic shock for impact and resiliency unit tests."""
    reference = MarketEventReference(
        instrument_id=INSTRUMENT.instrument_id,
        venue=INSTRUMENT.exchange,
        sequence_stream_id=SHARED_STREAM_ID,
        sequence_number=0,
        event_kind=MarketEventKind.TRADE,
        event_id=UUID(int=1),
        exchange_time=end_time,
        receive_time=end_time,
        process_time=end_time,
    )
    return LiquidityShock(
        instrument_id=INSTRUMENT.instrument_id,
        direction=direction,
        start_exchange_time=end_time,
        end_exchange_time=end_time,
        start_process_time=end_time,
        end_process_time=end_time,
        start_reference=reference,
        end_reference=reference,
        aggressive_volume=Decimal(aggressive_volume),
        normalized_aggression=Decimal(normalized_aggression),
        levels_touched=1,
        levels_consumed=1,
        pre_spread=Decimal("0.01"),
        pre_depth=Decimal("200"),
        immediate_price_change=Decimal("-0.01"),
        detection_method=ShockDetectionMethod.DETERMINISTIC_THRESHOLDS,
        detection_version="shock-detection-v1",
    )
