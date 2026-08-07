"""Historical search for the nearest prior comparable same-direction shock."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sra_nexus.sra.comparison import FailedAggressionComparison, ShockPairService
from sra_nexus.sra.impact import ShockImpact
from sra_nexus.sra.resiliency import ResiliencyVector
from sra_nexus.sra.shock import LiquidityShock
from sra_nexus.sra.shock_pair import ShockPairSpan


@dataclass(frozen=True, slots=True)
class HistoricalShockCandidate:
    """One materialized historical shock plus its indexed response features."""

    shock: LiquidityShock
    impacts: tuple[ShockImpact, ...]
    resiliency: ResiliencyVector
    start_event_index: int
    end_event_index: int
    available_event_index: int
    segment: int

    def __post_init__(self) -> None:
        """Reject malformed normalized-event coordinates before pair search."""
        if (
            min(
                self.start_event_index,
                self.end_event_index,
                self.available_event_index,
                self.segment,
            )
            < 0
        ):
            raise ValueError("historical shock coordinates must be non-negative")
        if self.start_event_index > self.end_event_index:
            raise ValueError("historical shock event span cannot regress")
        if self.available_event_index < self.end_event_index:
            raise ValueError("historical shock availability cannot precede shock end")
        if any(item.shock_id != self.shock.shock_id for item in self.impacts):
            raise ValueError("historical shock impacts must belong to the candidate shock")
        if self.resiliency.shock_id != self.shock.shock_id:
            raise ValueError("historical shock resiliency must belong to the candidate shock")


def find_most_recent_prior_comparable_shock(
    current: HistoricalShockCandidate,
    prior_candidates: Sequence[HistoricalShockCandidate],
    service: ShockPairService,
) -> FailedAggressionComparison | None:
    """Return the first service-accepted candidate found in reverse chronology.

    Structural segments are a hard upstream boundary. Every other ordinary
    comparability rule—including direction, instrument, distance, aggression
    ratio, and feature availability—remains owned by ``ShockPairService``.
    Incompatible intervening candidates are skipped, and only one accepted pair
    is returned for the current shock.
    """
    for prior in reversed(tuple(prior_candidates)):
        if prior.segment != current.segment:
            continue
        event_distance = current.start_event_index - prior.end_event_index - 1
        if event_distance < 0:
            raise ValueError("prior shock must end before the current shock begins")
        comparison = service.compare(
            shock_1=prior.shock,
            shock_2=current.shock,
            span=ShockPairSpan(event_distance=event_distance),
            impacts_1=prior.impacts,
            impacts_2=current.impacts,
            resiliency_1=prior.resiliency,
            resiliency_2=current.resiliency,
        )
        if comparison.comparison_available:
            return comparison
    return None
