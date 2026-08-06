"""Validated construction of raw-news observations with computed identity."""

from collections.abc import Mapping

from sra_nexus.aggregator.hashing import compute_raw_news_content_hash
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common.models import ContractModel, NonBlankStr, UtcDatetime


class _RawNewsContentFields(ContractModel):
    """Validated and normalized inputs participating in raw content identity."""

    source: NonBlankStr
    headline: NonBlankStr
    body: str | None = None
    url: str | None = None
    event_time: UtcDatetime


def build_raw_news_item(item_data: Mapping[str, object]) -> RawNewsItem:
    """Validate hash inputs, compute the digest, and validate the complete item."""
    if "content_hash" in item_data:
        raise ValueError("content_hash is computed and must not be supplied")

    content_fields = _RawNewsContentFields.model_validate(
        {
            "source": item_data.get("source"),
            "headline": item_data.get("headline"),
            "body": item_data.get("body"),
            "url": item_data.get("url"),
            "event_time": item_data.get("event_time"),
        }
    )
    normalized_data = dict(item_data)
    normalized_data.update(content_fields.model_dump())
    normalized_data["content_hash"] = compute_raw_news_content_hash(content_fields)
    return RawNewsItem.model_validate(normalized_data)
