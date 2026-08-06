"""Explainable deterministic classification of raw news into event taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from pydantic import Field, model_validator

from sra_nexus.aggregator.enums import EventSubtype, EventType, NewsSourceType
from sra_nexus.aggregator.normalization import normalize_comparison_text
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common.models import ContractModel, NonBlankStr, UnitIntervalScore


class EventClassification(ContractModel):
    """Auditable deterministic event classification result."""

    event_type: EventType
    event_subtype: EventSubtype
    confidence: UnitIntervalScore = Field(
        description="Initial engineering confidence in the matched deterministic rule."
    )
    matched_rules: tuple[NonBlankStr, ...] = Field(min_length=1)
    explanation: NonBlankStr

    @model_validator(mode="after")
    def validate_taxonomy_pair(self) -> EventClassification:
        """Require the namespaced subtype to belong to the selected event type."""
        if self.event_subtype.event_type is not self.event_type:
            raise ValueError("event_subtype must belong to event_type")
        return self


class EventClassifier(Protocol):
    """Provider-independent event-classification boundary."""

    def classify(self, item: RawNewsItem) -> EventClassification:
        """Classify one immutable raw-news item deterministically."""
        ...


@dataclass(frozen=True, slots=True)
class ClassificationRule:
    """Central rule definition for deterministic phrase classification."""

    name: str
    event_type: EventType
    event_subtype: EventSubtype
    keywords: tuple[str, ...]
    confidence: float
    required_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject internally inconsistent rule definitions."""
        if self.event_subtype.event_type is not self.event_type:
            raise ValueError("classification rule subtype must belong to its event type")


CLASSIFICATION_RULES: tuple[ClassificationRule, ...] = (
    ClassificationRule(
        "systemic.exchange_outage",
        EventType.SYSTEMIC,
        EventSubtype.SYSTEMIC_EXCHANGE_OUTAGE,
        ("exchange outage", "trading outage", "exchange unavailable"),
        0.95,
    ),
    ClassificationRule(
        "systemic.bank_failure",
        EventType.SYSTEMIC,
        EventSubtype.SYSTEMIC_BANK_FAILURE,
        ("bank failure", "bank collapse", "bank seized"),
        0.95,
    ),
    ClassificationRule(
        "systemic.market_disruption",
        EventType.SYSTEMIC,
        EventSubtype.SYSTEMIC_MARKET_DISRUPTION,
        ("market disruption", "marketwide halt", "market wide halt"),
        0.9,
    ),
    ClassificationRule(
        "geopolitical.sanction",
        EventType.GEOPOLITICAL,
        EventSubtype.GEOPOLITICAL_SANCTION,
        ("sanction", "sanctions", "sanctioned"),
        0.92,
    ),
    ClassificationRule(
        "geopolitical.trade_restriction",
        EventType.GEOPOLITICAL,
        EventSubtype.GEOPOLITICAL_TRADE_RESTRICTION,
        ("trade restriction", "trade ban", "import ban", "export ban", "tariff"),
        0.88,
    ),
    ClassificationRule(
        "geopolitical.conflict",
        EventType.GEOPOLITICAL,
        EventSubtype.GEOPOLITICAL_CONFLICT,
        ("armed conflict", "military strike", "invasion", "ceasefire"),
        0.9,
    ),
    ClassificationRule(
        "geopolitical.election",
        EventType.GEOPOLITICAL,
        EventSubtype.GEOPOLITICAL_ELECTION,
        ("election", "election result", "election results"),
        0.82,
    ),
    ClassificationRule(
        "regulatory.antitrust",
        EventType.REGULATORY,
        EventSubtype.REGULATORY_ANTITRUST,
        ("antitrust", "competition probe"),
        0.94,
    ),
    ClassificationRule(
        "regulatory.export_control",
        EventType.REGULATORY,
        EventSubtype.REGULATORY_EXPORT_CONTROL,
        ("export control", "export restriction", "export license restriction"),
        0.93,
    ),
    ClassificationRule(
        "regulatory.enforcement",
        EventType.REGULATORY,
        EventSubtype.REGULATORY_ENFORCEMENT,
        ("enforcement action", "regulatory fine", "regulator charges", "sec charges"),
        0.9,
    ),
    ClassificationRule(
        "regulatory.approval",
        EventType.REGULATORY,
        EventSubtype.REGULATORY_APPROVAL,
        ("regulatory approval", "fda approval", "regulator approves"),
        0.9,
    ),
    ClassificationRule(
        "rate.central_bank_decision",
        EventType.RATE,
        EventSubtype.RATE_CENTRAL_BANK_DECISION,
        ("interest rate decision", "rate decision", "raises interest rates", "cuts rates"),
        0.94,
        ("federal reserve", "fed", "central bank", "fomc", "ecb", "bank of england"),
    ),
    ClassificationRule(
        "rate.central_bank_speech",
        EventType.RATE,
        EventSubtype.RATE_CENTRAL_BANK_SPEECH,
        ("speech", "remarks", "testimony"),
        0.86,
        ("federal reserve", "fed", "central bank", "fomc", "ecb", "bank of england"),
    ),
    ClassificationRule(
        "macro.cpi",
        EventType.MACRO,
        EventSubtype.MACRO_CPI,
        ("cpi", "consumer price index"),
        0.96,
    ),
    ClassificationRule(
        "macro.jobs",
        EventType.MACRO,
        EventSubtype.MACRO_JOBS,
        ("jobs report", "nonfarm payroll", "non farm payroll", "unemployment report"),
        0.92,
    ),
    ClassificationRule(
        "macro.gdp",
        EventType.MACRO,
        EventSubtype.MACRO_GDP,
        ("gross domestic product", "gdp"),
        0.94,
    ),
    ClassificationRule(
        "macro.retail_sales",
        EventType.MACRO,
        EventSubtype.MACRO_RETAIL_SALES,
        ("retail sales",),
        0.93,
    ),
    ClassificationRule(
        "company.guidance",
        EventType.COMPANY,
        EventSubtype.COMPANY_GUIDANCE,
        ("guidance", "forecast", "outlook"),
        0.88,
    ),
    ClassificationRule(
        "company.earnings",
        EventType.COMPANY,
        EventSubtype.COMPANY_EARNINGS,
        ("earnings", "quarterly results", "financial results"),
        0.9,
    ),
    ClassificationRule(
        "company.merger_acquisition",
        EventType.COMPANY,
        EventSubtype.COMPANY_MERGER_ACQUISITION,
        (
            "acquire",
            "acquired",
            "acquires",
            "acquiring",
            "acquisition",
            "merger",
            "agrees to buy",
            "to acquire",
        ),
        0.92,
    ),
    ClassificationRule(
        "company.buyback",
        EventType.COMPANY,
        EventSubtype.COMPANY_BUYBACK,
        ("buyback", "share repurchase", "stock repurchase"),
        0.9,
    ),
    ClassificationRule(
        "company.dividend",
        EventType.COMPANY,
        EventSubtype.COMPANY_DIVIDEND,
        ("dividend",),
        0.86,
    ),
    ClassificationRule(
        "company.capital_raise",
        EventType.COMPANY,
        EventSubtype.COMPANY_CAPITAL_RAISE,
        ("capital raise", "stock offering", "share offering", "debt offering"),
        0.87,
    ),
    ClassificationRule(
        "company.management",
        EventType.COMPANY,
        EventSubtype.COMPANY_MANAGEMENT,
        ("chief executive", "ceo resigns", "ceo appointed", "management change"),
        0.85,
    ),
    ClassificationRule(
        "company.legal",
        EventType.COMPANY,
        EventSubtype.COMPANY_LEGAL,
        ("lawsuit", "litigation", "sued", "legal settlement"),
        0.84,
    ),
    ClassificationRule(
        "company.product",
        EventType.COMPANY,
        EventSubtype.COMPANY_PRODUCT,
        ("product launch", "launches product", "new product"),
        0.82,
    ),
    ClassificationRule(
        "company.sec_filing",
        EventType.COMPANY,
        EventSubtype.COMPANY_SEC_FILING,
        ("sec filing", "form 10 k", "form 10 q", "form 8 k"),
        0.9,
    ),
)


_SOURCE_FALLBACKS = MappingProxyType(
    {
        NewsSourceType.SEC: (EventType.COMPANY, EventSubtype.COMPANY_SEC_FILING, 0.65),
        NewsSourceType.MACRO_CALENDAR: (EventType.MACRO, EventSubtype.MACRO_OTHER, 0.35),
        NewsSourceType.CENTRAL_BANK: (EventType.RATE, EventSubtype.RATE_OTHER, 0.35),
        NewsSourceType.GOVERNMENT: (
            EventType.REGULATORY,
            EventSubtype.REGULATORY_OTHER,
            0.25,
        ),
        NewsSourceType.GLOBAL_NEWS: (
            EventType.GEOPOLITICAL,
            EventSubtype.GEOPOLITICAL_OTHER,
            0.2,
        ),
    }
)


class DeterministicEventClassifier:
    """Apply the central ordered rule table and a documented source fallback."""

    def classify(self, item: RawNewsItem) -> EventClassification:
        """Classify normalized headline and body without changing the raw item."""
        combined = item.headline if item.body is None else f"{item.headline} {item.body}"
        normalized = normalize_comparison_text(combined)

        for rule in CLASSIFICATION_RULES:
            keyword = _first_match(normalized, rule.keywords)
            context = _first_match(normalized, rule.required_context)
            if keyword is not None and (not rule.required_context or context is not None):
                matched = [f"{rule.name}:keyword={keyword}"]
                if context is not None:
                    matched.append(f"{rule.name}:context={context}")
                return EventClassification(
                    event_type=rule.event_type,
                    event_subtype=rule.event_subtype,
                    confidence=rule.confidence,
                    matched_rules=tuple(matched),
                    explanation=f"Matched deterministic rule {rule.name}.",
                )

        event_type, event_subtype, confidence = _SOURCE_FALLBACKS.get(
            item.source_type,
            (EventType.COMPANY, EventSubtype.COMPANY_OTHER, 0.15),
        )
        fallback = f"fallback:source_type={item.source_type.value}"
        return EventClassification(
            event_type=event_type,
            event_subtype=event_subtype,
            confidence=confidence,
            matched_rules=(fallback,),
            explanation="No phrase rule matched; applied the conservative source-type fallback.",
        )


def _first_match(normalized_text: str, phrases: tuple[str, ...]) -> str | None:
    padded = f" {normalized_text} "
    for phrase in phrases:
        normalized_phrase = normalize_comparison_text(phrase)
        if f" {normalized_phrase} " in padded:
            return phrase
    return None
