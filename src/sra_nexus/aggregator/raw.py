"""Immutable raw-news observation contract."""

from __future__ import annotations

from pydantic import Field, HttpUrl, field_serializer, field_validator, model_validator

from sra_nexus.aggregator.enums import NewsSourceType
from sra_nexus.common.models import (
    ImmutableJsonObject,
    LanguageTag,
    NonBlankStr,
    Sha256Hex,
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
    provider_item_id: NonBlankStr
    headline: NonBlankStr
    body: str | None = None
    url: HttpUrl | None = None
    provider_tickers: tuple[NonBlankStr, ...] = ()
    provider_entities: tuple[NonBlankStr, ...] = ()
    language: LanguageTag
    raw_metadata: ImmutableJsonObject = Field(
        default_factory=dict,
        description="Recursively immutable provider-specific JSON fields.",
    )
    content_hash: Sha256Hex = Field(description="SHA-256 content digest as 64 hexadecimal digits.")

    @field_validator("raw_metadata", mode="after")
    @classmethod
    def make_raw_metadata_immutable(cls, value: ImmutableJsonObject) -> ImmutableJsonObject:
        """Retain raw metadata as a recursively immutable copy."""
        return freeze_json_object(value)

    @field_serializer("raw_metadata", when_used="json")
    def serialize_raw_metadata(self, value: ImmutableJsonObject) -> dict[str, object]:
        """Emit JSON metadata without exposing the stored immutable mappings."""
        return thaw_json_object(value)

    @model_validator(mode="after")
    def validate_provider_references(self) -> RawNewsItem:
        """Reject duplicate provider symbols or entity labels."""
        if len(self.provider_tickers) != len(set(self.provider_tickers)):
            raise ValueError("provider_tickers must be unique")
        if len(self.provider_entities) != len(set(self.provider_entities)):
            raise ValueError("provider_entities must be unique")
        return self
