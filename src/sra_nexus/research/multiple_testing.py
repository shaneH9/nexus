"""Transparent Bonferroni and Benjamini-Hochberg p-value corrections."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sra_nexus.common.models import ContractModel
from sra_nexus.common.types import PermutationTestId
from sra_nexus.research.models import UnitIntervalDecimal


class ResearchPValue(ContractModel):
    """Raw p-value identified with one declared research test."""

    test_id: PermutationTestId
    p_value: UnitIntervalDecimal


class AdjustedResearchPValue(ContractModel):
    """Raw and separately reported family-wise and FDR adjustments."""

    test_id: PermutationTestId
    raw_p_value: UnitIntervalDecimal
    bonferroni_p_value: UnitIntervalDecimal
    benjamini_hochberg_p_value: UnitIntervalDecimal


def adjust_research_p_values(
    p_values: Sequence[ResearchPValue],
) -> tuple[AdjustedResearchPValue, ...]:
    """Return Bonferroni and BH-FDR values in the caller's original order."""
    values = tuple(p_values)
    if not values:
        raise ValueError("multiple-testing correction requires p-values")
    if len({item.test_id for item in values}) != len(values):
        raise ValueError("multiple-testing p-values require unique test IDs")
    count = len(values)
    bonferroni = {item.test_id: min(item.p_value * Decimal(count), Decimal(1)) for item in values}
    ordered = tuple(sorted(values, key=lambda item: (item.p_value, str(item.test_id))))
    bh: dict[PermutationTestId, Decimal] = {}
    running = Decimal(1)
    for reverse_index in range(count - 1, -1, -1):
        rank = reverse_index + 1
        item = ordered[reverse_index]
        candidate = item.p_value * Decimal(count) / Decimal(rank)
        running = min(running, candidate, Decimal(1))
        bh[item.test_id] = running
    return tuple(
        AdjustedResearchPValue(
            test_id=item.test_id,
            raw_p_value=item.p_value,
            bonferroni_p_value=bonferroni[item.test_id],
            benjamini_hochberg_p_value=bh[item.test_id],
        )
        for item in values
    )
