"""Provider-independent persistence contract for immutable raw news."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common.types import NewsId


class RawNewsInsertStatus(StrEnum):
    """Outcome of an immutable raw-news insertion attempt."""

    INSERTED = "INSERTED"
    DUPLICATE_PROVIDER_ITEM = "DUPLICATE_PROVIDER_ITEM"
    DUPLICATE_CONTENT_HASH = "DUPLICATE_CONTENT_HASH"
    DUPLICATE_NEWS_ID = "DUPLICATE_NEWS_ID"


@dataclass(frozen=True, slots=True)
class RawNewsInsertResult:
    """Typed insert outcome identifying an existing duplicate when present."""

    status: RawNewsInsertStatus
    incoming_news_id: NewsId
    existing_news_id: NewsId | None = None

    @property
    def inserted(self) -> bool:
        """Return whether the incoming immutable record was stored."""
        return self.status is RawNewsInsertStatus.INSERTED


class RawNewsRepository(Protocol):
    """Storage boundary for immutable raw provider observations."""

    def insert(self, item: RawNewsItem) -> RawNewsInsertResult:
        """Insert without overwriting and return a typed conflict outcome."""
        ...

    def get(self, news_id: NewsId) -> RawNewsItem | None:
        """Return one raw record by internal identifier when present."""
        ...

    def get_many_available_as_of(
        self,
        news_ids: tuple[NewsId, ...],
        as_of: datetime,
    ) -> tuple[RawNewsItem, ...]:
        """Return requested records whose process_time permits historical visibility."""
        ...

    def exists_provider_item(self, source: str, provider_item_id: str) -> bool:
        """Return whether a source/provider identifier pair already exists."""
        ...

    def exists_content_hash(self, content_hash: str) -> bool:
        """Return whether a deterministic raw-content digest already exists."""
        ...

    def list_available_as_of(self, as_of: datetime) -> tuple[RawNewsItem, ...]:
        """Return records with process_time no later than the UTC cutoff."""
        ...
