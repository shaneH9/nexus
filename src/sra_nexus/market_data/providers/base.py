"""Protocol boundary for streaming historical provider normalization."""

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from sra_nexus.market_data.historical import (
    HistoricalFileInspection,
    HistoricalNormalizedEvent,
)


class HistoricalMarketDataAdapter(Protocol):
    """Inspect and stream canonical events without leaking provider fields downstream."""

    @property
    def provider_name(self) -> str:
        """Return the stable provider name."""
        ...

    @property
    def format_version(self) -> str:
        """Return the supported provider schema/encoding version."""
        ...

    def discover(self) -> tuple[Path, ...]:
        """Return configured local source files in deterministic order."""
        ...

    def inspect(self) -> tuple[HistoricalFileInspection, ...]:
        """Return non-mutating pre-flight findings without repairing corruption."""
        ...

    def normalize(self) -> Iterator[HistoricalNormalizedEvent]:
        """Yield canonical events incrementally or raise on strict validation failure."""
        ...
