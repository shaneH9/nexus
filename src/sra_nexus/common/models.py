"""Validation primitives shared by SRA-Nexus data contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from types import MappingProxyType
from typing import Annotated, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

type ImmutableJsonObject = Mapping[str, object]


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("datetime must be timezone-aware")
    if offset != timedelta(0):
        raise ValueError("datetime must use UTC")
    return value.astimezone(UTC)


def _freeze_json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        frozen_items: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            frozen_items[key] = _freeze_json_value(item)
        return MappingProxyType(frozen_items)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    raise ValueError("metadata values must be JSON-compatible")


def freeze_json_object(value: object) -> ImmutableJsonObject:
    """Validate a JSON object and return a recursively immutable copy."""
    frozen = _freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        raise ValueError("metadata must be a JSON object")
    return cast("ImmutableJsonObject", frozen)


def thaw_json_object(value: ImmutableJsonObject) -> dict[str, object]:
    """Return a mutable JSON-compatible copy for serialization only."""

    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {key: thaw(nested) for key, nested in item.items()}
        if isinstance(item, tuple):
            return [thaw(nested) for nested in item]
        return item

    return {key: thaw(item) for key, item in value.items()}


NonBlankStr = Annotated[str, AfterValidator(_require_non_blank)]
UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
SignedUnitScore = Annotated[
    float,
    Field(
        ge=-1.0,
        le=1.0,
        description="Dimensionless score in the closed interval [-1, 1].",
    ),
]
UnitIntervalScore = Annotated[
    float,
    Field(
        ge=0.0,
        le=1.0,
        description="Dimensionless score in the closed interval [0, 1].",
    ),
]
NonNegativeFiniteFloat = Annotated[
    float,
    Field(ge=0.0, description="Finite dimensionless value greater than or equal to zero."),
]
CountryCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z]{2}$"),
]
CurrencyCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z]{3}$"),
]
LanguageTag = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-[A-Z]{2}|-[0-9]{3})?$"),
]
Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-fA-F]{64}$"),
]
EventSubtype = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$"),
]


class ContractModel(BaseModel):
    """Base for immutable, finite, closed-schema domain contracts."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class TimedEventModel(ContractModel):
    """Contract preserving source, receipt, and downstream availability times."""

    event_time: UtcDatetime = Field(description="UTC time assigned by the originating source.")
    receive_time: UtcDatetime = Field(description="UTC time SRA-Nexus received the event.")
    process_time: UtcDatetime = Field(
        description="UTC time the event became available to downstream consumers."
    )

    @model_validator(mode="after")
    def validate_timeline(self) -> TimedEventModel:
        """Reject event timelines that violate causal availability order."""
        if self.event_time > self.receive_time:
            raise ValueError("event_time must not be after receive_time")
        if self.receive_time > self.process_time:
            raise ValueError("receive_time must not be after process_time")
        return self
