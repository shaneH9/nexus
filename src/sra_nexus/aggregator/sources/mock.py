"""Deterministic local-JSON implementation of the news source boundary."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid5

from pydantic import ValidationError

from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.sources.base import (
    NewsSourceBatch,
    SourceRecordFailure,
    SourceRecordFailureType,
)
from sra_nexus.common.types import NewsId

_MOCK_NEWS_NAMESPACE = UUID("d938d8ab-8e1f-5f98-a21e-a3f98a1f1b7a")


class MockNewsSourceFormatError(ValueError):
    """Raised when a fixture does not have the expected batch structure."""


class MockNewsSource:
    """Convert provider-shaped local JSON fixture records into RawNewsItem objects."""

    def __init__(self, fixture_path: str | Path) -> None:
        """Configure the source to read one local JSON fixture."""
        self._fixture_path = Path(fixture_path)

    def fetch(self) -> NewsSourceBatch:
        """Read a fixture and independently validate each provider record."""
        payload = self._read_fixture()
        records = payload.get("records")
        if not isinstance(records, list):
            raise MockNewsSourceFormatError("fixture must contain a records list")

        items: list[RawNewsItem] = []
        failures: list[SourceRecordFailure] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                failures.append(
                    SourceRecordFailure(
                        record_index=index,
                        provider_reference=None,
                        error_type=SourceRecordFailureType.RECORD_TYPE,
                        message="fixture record must be a JSON object",
                    )
                )
                continue
            try:
                items.append(self._to_raw_news_item(record, index))
            except ValidationError as error:
                failures.append(
                    SourceRecordFailure(
                        record_index=index,
                        provider_reference=_provider_reference(record),
                        error_type=SourceRecordFailureType.VALIDATION,
                        message=str(error),
                    )
                )

        return NewsSourceBatch(items=tuple(items), failures=tuple(failures))

    def _read_fixture(self) -> dict[str, object]:
        with self._fixture_path.open(encoding="utf-8") as fixture_file:
            payload: object = json.load(fixture_file)
        if not isinstance(payload, dict):
            raise MockNewsSourceFormatError("fixture root must be a JSON object")
        return payload

    def _to_raw_news_item(self, record: dict[object, object], index: int) -> RawNewsItem:
        item_data: dict[str, object] = {
            "news_id": NewsId(uuid5(_MOCK_NEWS_NAMESPACE, f"{self._fixture_path.name}:{index}")),
            "source": record.get("provider"),
            "source_type": record.get("source_category"),
            "provider_item_id": record.get("provider_record_id"),
            "headline": record.get("title"),
            "body": record.get("content"),
            "url": record.get("link"),
            "event_time": record.get("published_at"),
            "receive_time": record.get("received_at"),
            "process_time": record.get("processed_at"),
            "provider_tickers": record.get("symbols", []),
            "provider_entities": record.get("mentioned_entities", []),
            "language": record.get("language"),
            "raw_metadata": record.get("metadata", {}),
        }
        return build_raw_news_item(item_data)


def _provider_reference(record: dict[object, object]) -> str | None:
    reference = record.get("provider_record_id")
    return reference if isinstance(reference, str) else None
