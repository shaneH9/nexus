"""External event observation and aggregation interfaces."""

from sra_nexus.aggregator.enums import (
    EventState,
    EventType,
    ExposureRelationType,
    NewsSourceType,
)
from sra_nexus.aggregator.events import CanonicalEvent, EventExposure
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.aggregator.state import NewsState

__all__ = [
    "CanonicalEvent",
    "EventExposure",
    "EventState",
    "EventType",
    "ExposureRelationType",
    "NewsSourceType",
    "NewsState",
    "RawNewsItem",
]
