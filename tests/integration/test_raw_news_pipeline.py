"""Integration tests for fixture source, ingestion service, and SQLite storage."""

from datetime import UTC, datetime
from pathlib import Path

from sra_nexus.aggregator import NewsSourceType
from sra_nexus.aggregator.ingestion import RawNewsIngestionService
from sra_nexus.aggregator.sources import MockNewsSource
from sra_nexus.storage import SQLiteRawNewsRepository

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "news"


def test_mock_to_sqlite_pipeline_is_immutable_and_process_time_gated(
    tmp_path: Path,
) -> None:
    """The complete raw path should preserve data, deduplicate, and gate replay."""
    repository = SQLiteRawNewsRepository(tmp_path / "pipeline.sqlite3")
    repository.initialize_schema()
    service = RawNewsIngestionService(
        MockNewsSource(FIXTURE_DIR / "representative.json"),
        repository,
    )

    first = service.ingest()
    second = service.ingest()

    before_first_process = repository.list_available_as_of(
        datetime(2026, 6, 1, 13, 0, 1, tzinfo=UTC)
    )
    after_speculative_process = repository.list_available_as_of(
        datetime(2026, 6, 1, 13, 2, 4, tzinfo=UTC)
    )
    all_items = repository.list_available_as_of(datetime(2026, 6, 1, 14, 0, tzinfo=UTC))

    assert (first.inserted_count, first.duplicate_count, first.failed_count) == (5, 0, 0)
    assert (second.inserted_count, second.duplicate_count, second.failed_count) == (0, 5, 0)
    assert before_first_process == ()
    assert len(after_speculative_process) == 3
    assert after_speculative_process[-1].source_type is NewsSourceType.SPECULATIVE
    assert len(all_items) == 5
    assert repository.get(all_items[0].news_id) == all_items[0]
