"""Provider-independent source boundary for normalized market events."""

from collections.abc import Iterable
from typing import Protocol

from sra_nexus.market_data.events import MarketEvent


class MarketDataSource(Protocol):
    """A source of immutable normalized market events with no storage coupling."""

    def read(self) -> Iterable[MarketEvent]:
        """Return events in canonical deterministic stream/sequence order."""
        ...
