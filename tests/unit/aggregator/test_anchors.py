"""Tests for lightweight deterministic event-anchor extraction."""

from datetime import UTC, datetime

from sra_nexus.aggregator import NewsSourceType, RawNewsItem
from sra_nexus.aggregator.anchors import extract_event_anchors
from sra_nexus.aggregator.factory import build_raw_news_item


def _item(**overrides: object) -> RawNewsItem:
    data: dict[str, object] = {
        "source": "Anchor Fixture",
        "source_type": NewsSourceType.WIRE,
        "provider_item_id": "anchor-1",
        "headline": "Acme posts an update",
        "event_time": datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        "receive_time": datetime(2026, 7, 1, 10, 0, 1, tzinfo=UTC),
        "process_time": datetime(2026, 7, 1, 10, 0, 2, tzinfo=UTC),
    }
    data.update(overrides)
    return build_raw_news_item(data)


def test_provider_tickers_are_preferred_and_normalized() -> None:
    """Provider ticker anchors should override less reliable text-like anchors."""
    anchors = extract_event_anchors(
        _item(
            provider_tickers=["ACME", "BETA"],
            provider_entities=["Acme Incorporated"],
            headline="ACME update from management",
        )
    )

    assert anchors.tickers == frozenset({"acme", "beta"})
    assert anchors.terms == frozenset()


def test_provider_entities_are_used_when_tickers_are_missing() -> None:
    """Provider entity phrases may anchor comparison without building an entity graph."""
    anchors = extract_event_anchors(_item(provider_entities=["Acme Corporation"]))

    assert "acme corporation" in anchors.terms


def test_obvious_uppercase_tokens_are_fallback_ticker_anchors() -> None:
    """Security-like uppercase tokens should provide a local deterministic fallback."""
    anchors = extract_event_anchors(_item(headline="ZXQ announces a product launch"))

    assert anchors.tickers == frozenset({"zxq"})


def test_common_macro_acronyms_are_terms_not_ticker_conflicts() -> None:
    """Known domain acronyms should help overlap without acting like company tickers."""
    anchors = extract_event_anchors(_item(headline="US CPI rises in June"))

    assert anchors.tickers == frozenset()
    assert {"us", "cpi"}.issubset(anchors.terms)
