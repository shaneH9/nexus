"""Offline fixture adapter for deterministic normalized market events."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from sra_nexus.market_data.events import BookEvent, MarketEvent, QuoteEvent, TradeEvent
from sra_nexus.market_data.ordering import market_event_sort_key

MOCK_MARKET_DATA_SCHEMA_VERSION = "sra-nexus.mock-market-data.v1"


class MarketDataFixtureError(ValueError):
    """Raised when a local fixture cannot be normalized without repair."""


class MockMarketDataSource:
    """Map deterministic provider-shaped JSON or JSONL fixtures into contracts."""

    def __init__(self, fixture_path: str | Path) -> None:
        """Configure one local fixture path; no network capability is present."""
        self._fixture_path = Path(fixture_path)

    def read(self) -> tuple[MarketEvent, ...]:
        """Validate every fixture record and return canonical ordering."""
        records = self._load_records()
        events: list[MarketEvent] = []
        for index, record in enumerate(records):
            try:
                events.append(_normalize_record(record))
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise MarketDataFixtureError(
                    f"invalid market-data fixture record at index {index}: {error}"
                ) from error
        return tuple(sorted(events, key=market_event_sort_key))

    def _load_records(self) -> list[object]:
        try:
            text = self._fixture_path.read_text(encoding="utf-8")
            if self._fixture_path.suffix.casefold() == ".jsonl":
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            payload = json.loads(text)
        except (OSError, json.JSONDecodeError) as error:
            raise MarketDataFixtureError(f"cannot read market-data fixture: {error}") from error
        if not isinstance(payload, dict):
            raise MarketDataFixtureError("JSON fixture must be an object")
        if payload.get("schema_version") != MOCK_MARKET_DATA_SCHEMA_VERSION:
            raise MarketDataFixtureError(
                "unsupported or missing market-data fixture schema_version"
            )
        records = payload.get("events")
        if not isinstance(records, list):
            raise MarketDataFixtureError("market-data fixture events must be a list")
        return records


def _normalize_record(record: object) -> MarketEvent:
    if not isinstance(record, dict):
        raise TypeError("market-data record must be an object")
    kind = record["message_type"]
    common = {
        "instrument_id": record["instrument"],
        "venue": record["venue_code"],
        "exchange_time": record["exchange_timestamp"],
        "receive_time": record["received_timestamp"],
        "process_time": record["processed_timestamp"],
        "sequence_number": record["sequence"],
        "flags": record.get("normalized_flags", []),
    }
    if kind == "book_update":
        return BookEvent.model_validate(
            {
                **common,
                "event_id": record["message_id"],
                "action": record["operation"],
                "side": record.get("book_side"),
                "price": record.get("limit_price"),
                "quantity": record.get("size"),
                "order_id": record.get("provider_order_id"),
                "trade_id": record.get("provider_trade_id"),
                "book_mode": record.get("book_mode", "MARKET_BY_ORDER"),
            }
        )
    if kind == "trade_print":
        return TradeEvent.model_validate(
            {
                **common,
                "trade_event_id": record["message_id"],
                "trade_id": record["provider_trade_id"],
                "price": record["trade_price"],
                "quantity": record["trade_size"],
                "aggressor_side": record.get("aggressor", "UNKNOWN"),
            }
        )
    if kind == "top_quote":
        return QuoteEvent.model_validate(
            {
                **common,
                "quote_event_id": record["message_id"],
                "bid_price": record["best_bid_price"],
                "bid_quantity": record["best_bid_size"],
                "ask_price": record["best_ask_price"],
                "ask_quantity": record["best_ask_size"],
            }
        )
    raise ValueError(f"unsupported message_type {kind!r}")
