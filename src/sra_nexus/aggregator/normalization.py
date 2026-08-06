"""Small deterministic text normalization for event comparison."""

from types import MappingProxyType
from unicodedata import category, normalize

_STOPWORDS = frozenset({"a", "an", "and", "for", "in", "of", "on", "the", "to"})

_PHRASE_ALIASES = (
    ("consumer price index", "cpi"),
    ("quarterly results", "earnings"),
    ("share repurchase", "buyback"),
    ("stock repurchase", "buyback"),
)

_TOKEN_ALIASES = MappingProxyType(
    {
        "acquired": "acquire",
        "acquires": "acquire",
        "acquiring": "acquire",
        "acquisition": "acquire",
        "acquisitions": "acquire",
        "agreed": "agree",
        "agrees": "agree",
        "announced": "announce",
        "announces": "announce",
        "increased": "increase",
        "increases": "increase",
        "merger": "acquire",
        "mergers": "acquire",
        "results": "earnings",
        "rises": "increase",
        "rose": "increase",
    }
)


def normalize_comparison_text(value: str) -> str:
    """Return NFKC-casefolded text with punctuation and whitespace normalized."""
    compatible = normalize("NFKC", value).casefold()
    characters = (
        character if character.isalnum() or character.isspace() else " "
        for character in compatible
        if category(character) != "Cf"
    )
    normalized = " ".join("".join(characters).split())
    padded = f" {normalized} "
    for source, replacement in _PHRASE_ALIASES:
        padded = padded.replace(f" {source} ", f" {replacement} ")
    return " ".join(padded.split())


def comparison_tokens(value: str) -> frozenset[str]:
    """Return a deterministic set of normalized non-stopword comparison tokens."""
    normalized = normalize_comparison_text(value)
    return frozenset(
        _TOKEN_ALIASES.get(token, token) for token in normalized.split() if token not in _STOPWORDS
    )
