"""Tests for provider-neutral raw-news ingestion orchestration."""

from datetime import datetime
from pathlib import Path

import pytest

from sra_nexus.aggregator import RawNewsItem
from sra_nexus.aggregator.ingestion import RawNewsIngestionService
from sra_nexus.aggregator.sources import MockNewsSource, SourceRecordFailureType
from sra_nexus.common.types import NewsId
from sra_nexus.storage import (
    RawNewsInsertResult,
    SQLiteRawNewsRepository,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "news"


def _repository(tmp_path: Path) -> SQLiteRawNewsRepository:
    repository = SQLiteRawNewsRepository(tmp_path / "ingestion.sqlite3")
    repository.initialize_schema()
    return repository


def test_ingestion_summary_counts_new_records(tmp_path: Path) -> None:
    """A clean fixture should report every valid item as inserted."""
    repository = _repository(tmp_path)
    service = RawNewsIngestionService(
        MockNewsSource(FIXTURE_DIR / "representative.json"),
        repository,
    )

    summary = service.ingest()

    assert summary.received_count == 5
    assert summary.inserted_count == 5
    assert summary.duplicate_count == 0
    assert summary.failed_count == 0
    assert summary.failures == ()


def test_repeated_ingestion_reports_duplicates_without_overwrite(tmp_path: Path) -> None:
    """Replaying the same batch should retain the first records and count duplicates."""
    repository = _repository(tmp_path)
    service = RawNewsIngestionService(
        MockNewsSource(FIXTURE_DIR / "representative.json"),
        repository,
    )
    first = service.ingest()

    second = service.ingest()

    assert first.inserted_count == 5
    assert second.received_count == 5
    assert second.inserted_count == 0
    assert second.duplicate_count == 5
    assert second.failed_count == 0


def test_mixed_batch_retains_valid_records_and_reports_validation_failure(
    tmp_path: Path,
) -> None:
    """One malformed record should not discard a new item or a valid duplicate."""
    repository = _repository(tmp_path)
    RawNewsIngestionService(
        MockNewsSource(FIXTURE_DIR / "representative.json"),
        repository,
    ).ingest()
    service = RawNewsIngestionService(
        MockNewsSource(FIXTURE_DIR / "mixed.json"),
        repository,
    )

    summary = service.ingest()

    assert summary.received_count == 3
    assert summary.inserted_count == 1
    assert summary.duplicate_count == 1
    assert summary.failed_count == 1
    assert summary.failures[0].provider_reference == "bad-700"
    assert summary.failures[0].error_type is SourceRecordFailureType.VALIDATION


class _FailingRepository:
    """Test repository that models a database infrastructure failure."""

    def insert(self, item: RawNewsItem) -> RawNewsInsertResult:
        """Raise the simulated infrastructure error."""
        raise RuntimeError("database unavailable")

    def get(self, news_id: NewsId) -> RawNewsItem | None:
        """Return no record; this method is unused in the test."""
        return None

    def exists_provider_item(self, source: str, provider_item_id: str) -> bool:
        """Return false; this method is unused in the test."""
        return False

    def exists_content_hash(self, content_hash: str) -> bool:
        """Return false; this method is unused in the test."""
        return False

    def list_available_as_of(self, as_of: datetime) -> tuple[RawNewsItem, ...]:
        """Return no records; this method is unused in the test."""
        return ()


def test_repository_infrastructure_failure_propagates() -> None:
    """The ingestion service must not disguise a batch-level storage outage."""
    service = RawNewsIngestionService(
        MockNewsSource(FIXTURE_DIR / "representative.json"),
        _FailingRepository(),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.ingest()
