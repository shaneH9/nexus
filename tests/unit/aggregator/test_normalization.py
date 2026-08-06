"""Tests for deterministic event-comparison text normalization."""

from sra_nexus.aggregator.normalization import (
    comparison_tokens,
    normalize_comparison_text,
)


def test_normalization_handles_case_punctuation_and_repeated_whitespace() -> None:
    """Equivalent formatting should produce one normalized comparison string."""
    first = normalize_comparison_text("  NVIDIA—Agrees   to ACQUIRE Acme! ")
    second = normalize_comparison_text("nvidia agrees to acquire acme")

    assert first == second == "nvidia agrees to acquire acme"


def test_normalization_canonicalizes_equivalent_unicode_forms() -> None:
    """Composed and decomposed Unicode should compare identically."""
    composed = normalize_comparison_text("Café results")
    decomposed = normalize_comparison_text("Cafe\u0301 results")

    assert composed == decomposed


def test_tokenization_is_deterministic_and_removes_small_stopword_set() -> None:
    """Token order and repeated words should not change the comparison set."""
    first = comparison_tokens("The Acme acquisition of Beta Beta")
    second = comparison_tokens("beta ACME acquires")

    assert first == second == frozenset({"acme", "acquire", "beta"})


def test_tokenization_applies_only_explicit_domain_aliases() -> None:
    """Small declared aliases should bridge common event wording differences."""
    assert comparison_tokens("quarterly results") == frozenset({"earnings"})
    assert comparison_tokens("consumer price index") == frozenset({"cpi"})


def test_empty_text_has_stable_empty_representation() -> None:
    """Empty and whitespace-only comparison text should remain harmless."""
    assert normalize_comparison_text("   ") == ""
    assert comparison_tokens("") == frozenset()
