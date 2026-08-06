"""Lightweight deterministic anchor extraction for event clustering."""

import re
from dataclasses import dataclass

from sra_nexus.aggregator.normalization import normalize_comparison_text
from sra_nexus.aggregator.raw import RawNewsItem

_UPPERCASE_TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z0-9.-]{1,7}\b")
_NON_TICKER_ACRONYMS = frozenset(
    {"CEO", "CFO", "CPI", "ECB", "EU", "FDA", "FED", "FOMC", "GDP", "SEC", "UK", "US", "USA"}
)


@dataclass(frozen=True, slots=True)
class EventAnchors:
    """Ticker and non-ticker anchor sets extracted without entity linking."""

    tickers: frozenset[str]
    terms: frozenset[str]

    @property
    def all(self) -> frozenset[str]:
        """Return every anchor participating in overlap similarity."""
        return self.tickers | self.terms


def extract_event_anchors(item: RawNewsItem) -> EventAnchors:
    """Prefer provider tickers, otherwise use provider names and uppercase terms."""
    provider_tickers = frozenset(_normalize_ticker(value) for value in item.provider_tickers)
    if provider_tickers:
        return EventAnchors(tickers=provider_tickers, terms=frozenset())

    terms = {
        normalized
        for value in item.provider_entities
        if (normalized := normalize_comparison_text(value))
    }
    text = item.headline if item.body is None else f"{item.headline} {item.body}"
    inferred_tickers: set[str] = set()
    for token in _UPPERCASE_TOKEN_PATTERN.findall(text):
        if token in _NON_TICKER_ACRONYMS:
            terms.add(normalize_comparison_text(token))
        else:
            inferred_tickers.add(_normalize_ticker(token))
    return EventAnchors(tickers=frozenset(inferred_tickers), terms=frozenset(terms))


def _normalize_ticker(value: str) -> str:
    return normalize_comparison_text(value).replace(" ", "")
