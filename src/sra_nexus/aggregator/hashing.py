"""Deterministic content identity for raw news observations.

The version-one policy hashes normalized source, headline, body, URL, and source
event/publication time. Text is Unicode NFC-normalized, line endings are made
consistent, and surrounding whitespace is removed. The timestamp is normalized
to UTC with microsecond precision. Optional values remain JSON null.

The policy intentionally excludes internal IDs, provider item IDs, source type,
provider annotations, raw metadata, receive time, and process time. Provider ID
duplicates are handled separately, while receipt and processing latency must not
change the identity of otherwise identical source content.
"""

import json
from datetime import datetime
from hashlib import sha256
from unicodedata import normalize

from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common.models import normalize_utc_datetime

HASH_POLICY_VERSION = "sra-nexus.raw-news-content.v1"


def compute_raw_news_content_hash(item: RawNewsItem) -> str:
    """Return a stable lowercase SHA-256 digest for raw source content."""
    payload = {
        "body": _normalize_optional_text(item.body),
        "event_time": _serialize_datetime(item.event_time),
        "headline": _normalize_text(item.headline),
        "policy": HASH_POLICY_VERSION,
        "source": _normalize_text(item.source),
        "url": _normalize_optional_text(item.url),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _normalize_text(value: str) -> str:
    normalized_lines = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalize("NFC", normalized_lines.strip())


def _normalize_optional_text(value: str | None) -> str | None:
    return None if value is None else _normalize_text(value)


def _serialize_datetime(value: datetime) -> str:
    normalized = normalize_utc_datetime(value)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
