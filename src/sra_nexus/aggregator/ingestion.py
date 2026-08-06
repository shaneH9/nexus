"""Provider-neutral orchestration for immutable raw-news ingestion."""

from dataclasses import dataclass

from sra_nexus.aggregator.sources.base import NewsSource, SourceRecordFailure
from sra_nexus.storage.raw import RawNewsInsertStatus, RawNewsRepository


@dataclass(frozen=True, slots=True)
class RawNewsIngestionSummary:
    """Counts and source-validation details for one ingestion batch."""

    received_count: int
    inserted_count: int
    duplicate_count: int
    failed_count: int
    failures: tuple[SourceRecordFailure, ...] = ()

    def __post_init__(self) -> None:
        """Require internally consistent batch accounting."""
        if self.failed_count != len(self.failures):
            raise ValueError("failed_count must match the number of failure details")
        accounted = self.inserted_count + self.duplicate_count + self.failed_count
        if accounted != self.received_count:
            raise ValueError("ingestion counts must account for every received record")


class RawNewsIngestionService:
    """Ingest validated raw items through source and repository abstractions."""

    def __init__(self, source: NewsSource, repository: RawNewsRepository) -> None:
        """Store the provider-neutral source and raw repository boundaries."""
        self._source = source
        self._repository = repository

    def ingest(self) -> RawNewsIngestionSummary:
        """Process records independently while propagating infrastructure errors."""
        batch = self._source.fetch()
        inserted_count = 0
        duplicate_count = 0

        for item in batch.items:
            result = self._repository.insert(item)
            if result.status is RawNewsInsertStatus.INSERTED:
                inserted_count += 1
            else:
                duplicate_count += 1

        return RawNewsIngestionSummary(
            received_count=batch.received_count,
            inserted_count=inserted_count,
            duplicate_count=duplicate_count,
            failed_count=len(batch.failures),
            failures=batch.failures,
        )
