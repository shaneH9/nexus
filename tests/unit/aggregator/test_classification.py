"""Tests for explainable deterministic raw-news event classification."""

from datetime import UTC, datetime

import pytest

from sra_nexus.aggregator import EventSubtype, EventType, NewsSourceType, RawNewsItem
from sra_nexus.aggregator.classification import DeterministicEventClassifier
from sra_nexus.aggregator.factory import build_raw_news_item


def _item(
    headline: str,
    *,
    source_type: NewsSourceType = NewsSourceType.WIRE,
    body: str | None = None,
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "source": "Classification Fixture",
            "source_type": source_type,
            "provider_item_id": headline,
            "headline": headline,
            "body": body,
            "event_time": datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            "receive_time": datetime(2026, 7, 1, 10, 0, 1, tzinfo=UTC),
            "process_time": datetime(2026, 7, 1, 10, 0, 2, tzinfo=UTC),
        }
    )


@pytest.mark.parametrize(
    ("headline", "expected_type", "expected_subtype", "rule_fragment"),
    [
        (
            "Acme reports quarterly earnings",
            EventType.COMPANY,
            EventSubtype.COMPANY_EARNINGS,
            "company.earnings",
        ),
        (
            "Acme raises full-year guidance",
            EventType.COMPANY,
            EventSubtype.COMPANY_GUIDANCE,
            "company.guidance",
        ),
        (
            "Acme agrees to acquire Beta",
            EventType.COMPANY,
            EventSubtype.COMPANY_MERGER_ACQUISITION,
            "company.merger_acquisition",
        ),
        (
            "Consumer price index rises in June",
            EventType.MACRO,
            EventSubtype.MACRO_CPI,
            "macro.cpi",
        ),
        (
            "Federal Reserve issues interest rate decision",
            EventType.RATE,
            EventSubtype.RATE_CENTRAL_BANK_DECISION,
            "rate.central_bank_decision",
        ),
        (
            "SEC launches enforcement action against Acme",
            EventType.REGULATORY,
            EventSubtype.REGULATORY_ENFORCEMENT,
            "regulatory.enforcement",
        ),
        (
            "Government announces new sanctions",
            EventType.GEOPOLITICAL,
            EventSubtype.GEOPOLITICAL_SANCTION,
            "geopolitical.sanction",
        ),
        (
            "Local organization posts an operational update",
            EventType.COMPANY,
            EventSubtype.COMPANY_OTHER,
            "fallback:source_type=WIRE",
        ),
    ],
)
def test_classifier_rules_are_deterministic_and_auditable(
    headline: str,
    expected_type: EventType,
    expected_subtype: EventSubtype,
    rule_fragment: str,
) -> None:
    """Each required class should expose its chosen rule and explanation."""
    result = DeterministicEventClassifier().classify(_item(headline))

    assert result.event_type is expected_type
    assert result.event_subtype is expected_subtype
    assert any(rule_fragment in rule for rule in result.matched_rules)
    assert result.explanation


def test_speculative_source_uses_event_content_not_a_speculative_event_type() -> None:
    """Alternative source classification should use the ordinary event taxonomy."""
    result = DeterministicEventClassifier().classify(
        _item(
            "Acme agrees to acquire Beta",
            source_type=NewsSourceType.SPECULATIVE,
        )
    )

    assert result.event_type is EventType.COMPANY
    assert result.event_subtype is EventSubtype.COMPANY_MERGER_ACQUISITION


def test_classification_does_not_mutate_original_raw_text() -> None:
    """Normalized comparison text must remain outside immutable raw records."""
    item = _item("  Acme raises GUIDANCE!  ")

    DeterministicEventClassifier().classify(item)

    assert item.headline == "Acme raises GUIDANCE!"
