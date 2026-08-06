"""Immutable raw-news observation contract."""

from __future__ import annotations

from pydantic import Field, field_serializer, field_validator

from sra_nexus.aggregator.enums import NewsSourceType
from sra_nexus.common.models import (
    ImmutableJsonObject,
    NonBlankStr,
    TimedEventModel,
    freeze_json_object,
    thaw_json_object,
)
from sra_nexus.common.types import NewsId, new_news_id


class RawNewsItem(TimedEventModel):
    """Immutable provider observation retained before normalization."""

    news_id: NewsId = Field(default_factory=new_news_id)
    source: NonBlankStr = Field(description="Provider name or source identifier.")
    source_type: NewsSourceType
    provider_item_id: NonBlankStr | None = None
    headline: NonBlankStr
    body: str | None = None
    url: str | None = None
    provider_tickers: tuple[NonBlankStr, ...] = ()
    provider_entities: tuple[NonBlankStr, ...] = ()
    language: str | None = None
    raw_metadata: ImmutableJsonObject = Field(
        default_factory=dict,
        description="Recursively immutable provider-specific JSON fields.",
    )
    content_hash: NonBlankStr = Field(description="Opaque deterministic content digest.")

    @field_validator("provider_tickers", mode="before")
    @classmethod
    def normalize_provider_tickers(cls, value: object) -> object:
        """Trim, uppercase, and deduplicate provider ticker metadata."""
        if not isinstance(value, (list, tuple)):
            raise ValueError("provider_tickers must be a collection of strings")

        normalized: list[str] = []
        seen: set[str] = set()
        for ticker in value:
            if not isinstance(ticker, str):
                raise ValueError("provider_tickers must contain only strings")
            uppercase_ticker = ticker.strip().upper()
            if not uppercase_ticker:
                raise ValueError("provider_tickers must not contain blank values")
            if uppercase_ticker not in seen:
                normalized.append(uppercase_ticker)
                seen.add(uppercase_ticker)
        return tuple(normalized)

    @field_validator("raw_metadata", mode="after")
    @classmethod
    def make_raw_metadata_immutable(cls, value: ImmutableJsonObject) -> ImmutableJsonObject:
        """Retain raw metadata as a recursively immutable copy."""
        return freeze_json_object(value)

    @field_serializer("raw_metadata", when_used="json")
    def serialize_raw_metadata(self, value: ImmutableJsonObject) -> dict[str, object]:
        """Emit JSON metadata without exposing the stored immutable mappings."""
        return thaw_json_object(value)
