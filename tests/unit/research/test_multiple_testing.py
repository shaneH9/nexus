"""Tests for explicit family-wise and false-discovery-rate corrections."""

from decimal import Decimal
from uuid import UUID

from sra_nexus.common.types import PermutationTestId
from sra_nexus.research import ResearchPValue, adjust_research_p_values


def test_bonferroni_and_benjamini_hochberg_known_values() -> None:
    """Both corrections are exact, monotone, and restored to caller order."""
    p_values = (
        ResearchPValue(test_id=PermutationTestId(UUID(int=1)), p_value=Decimal("0.01")),
        ResearchPValue(test_id=PermutationTestId(UUID(int=2)), p_value=Decimal("0.04")),
        ResearchPValue(test_id=PermutationTestId(UUID(int=3)), p_value=Decimal("0.03")),
    )

    adjusted = adjust_research_p_values(p_values)

    assert tuple(item.bonferroni_p_value for item in adjusted) == (
        Decimal("0.03"),
        Decimal("0.12"),
        Decimal("0.09"),
    )
    assert tuple(item.benjamini_hochberg_p_value for item in adjusted) == (
        Decimal("0.03"),
        Decimal("0.04"),
        Decimal("0.04"),
    )
    assert tuple(item.raw_p_value for item in adjusted) == tuple(item.p_value for item in p_values)


def test_adjustments_are_capped_at_one() -> None:
    """Large family-wise multipliers remain valid p-values."""
    values = tuple(
        ResearchPValue(
            test_id=PermutationTestId(UUID(int=index + 1)),
            p_value=Decimal("0.9"),
        )
        for index in range(3)
    )
    adjusted = adjust_research_p_values(values)
    assert all(item.bonferroni_p_value == 1 for item in adjusted)
    assert all(item.benjamini_hochberg_p_value == Decimal("0.9") for item in adjusted)
