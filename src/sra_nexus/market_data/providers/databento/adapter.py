"""Strict streaming normalization for Databento historical MBO CSV exports."""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from sra_nexus.common.models import ContractModel, NonBlankStr
from sra_nexus.common.types import (
    BookEventId,
    MarketOrderId,
    MarketTradeId,
    SequenceStreamId,
    TradeEventId,
)
from sra_nexus.market_data.enums import (
    AggressorSide,
    BookAction,
    BookDataMode,
    BookSide,
    MarketEventFlag,
)
from sra_nexus.market_data.events import BookEvent, MarketEvent, TradeEvent
from sra_nexus.market_data.historical import (
    HistoricalBoundaryKind,
    HistoricalFileIdentity,
    HistoricalFileInspection,
    HistoricalInstrumentMapping,
    HistoricalNormalizedEvent,
    HistoricalProcessTimePolicy,
    HistoricalSessionPolicy,
    HistoricalSessionSegment,
)

DATABENTO_MBO_CSV_FORMAT_VERSION = "databento-mbo-csv-v1"
DATABENTO_MBO_NORMALIZATION_VERSION = "databento-mbo-normalization-v1"
DATABENTO_PROVIDER_NAME = "DATABENTO"
_EVENT_NAMESPACE = UUID("120bc0d8-0a50-5d09-a409-e86c4ab6d66f")
_REQUIRED_COLUMNS = frozenset(
    {
        "ts_recv",
        "ts_event",
        "rtype",
        "publisher_id",
        "instrument_id",
        "action",
        "side",
        "price",
        "size",
        "channel_id",
        "order_id",
        "flags",
        "ts_in_delta",
        "sequence",
        "symbol",
    }
)
_SUPPORTED_ACTIONS = frozenset({"A", "C", "M", "R", "T", "F", "N"})
_F_MAYBE_BAD_BOOK = 4
_F_BAD_TS_RECV = 8
_F_MBP = 16
_F_SNAPSHOT = 32
_F_TOB = 64
_F_LAST = 128


class HistoricalDataValidationError(ValueError):
    """Raised when historical bytes cannot be normalized without repair."""


class DatabentoPriceEncoding(StrEnum):
    """Supported text-export price representations."""

    PRETTY_DECIMAL = "PRETTY_DECIMAL"
    FIXED_1E9_INTEGER = "FIXED_1E9_INTEGER"


class DatabentoMboCsvConfig(ContractModel):
    """Explicit file, mapping, clock, sequence, and session normalization policy."""

    source_paths: tuple[NonBlankStr, ...]
    dataset: NonBlankStr
    provider_schema_version: NonBlankStr = "mbo"
    price_encoding: DatabentoPriceEncoding = DatabentoPriceEncoding.PRETTY_DECIMAL
    instrument_mappings: tuple[HistoricalInstrumentMapping, ...]
    session_policy: HistoricalSessionPolicy = Field(default_factory=HistoricalSessionPolicy)
    process_time_policy: HistoricalProcessTimePolicy = HistoricalProcessTimePolicy.RECEIVE_TIME
    synthetic_process_offset_microseconds: int = Field(default=0, ge=0)
    snapshot_session_date_offset_days: int = Field(default=0, ge=-2, le=2)
    allow_native_sequence_gaps: bool = False
    normalization_version: NonBlankStr = DATABENTO_MBO_NORMALIZATION_VERSION

    @model_validator(mode="after")
    def validate_config(self) -> DatabentoMboCsvConfig:
        """Require local files, MBO schema, unique mappings, and coherent clock policy."""
        if not self.source_paths:
            raise ValueError("Databento adapter requires at least one source path")
        if self.provider_schema_version.casefold() != "mbo":
            raise ValueError("Databento CSV adapter supports only the MBO schema")
        if not self.instrument_mappings:
            raise ValueError("Databento adapter requires explicit instrument mappings")
        identities = tuple(
            (item.publisher_id, item.provider_instrument_id, item.provider_symbol)
            for item in self.instrument_mappings
        )
        if len(set(identities)) != len(identities):
            raise ValueError("Databento instrument mappings must have unique provider identities")
        if (
            self.process_time_policy is HistoricalProcessTimePolicy.RECEIVE_TIME
            and self.synthetic_process_offset_microseconds != 0
        ):
            raise ValueError("receive-time process policy cannot add a synthetic offset")
        return self


@dataclass(frozen=True, slots=True)
class _NativeRecord:
    line_number: int
    exchange_time: datetime
    receive_time: datetime
    publisher_id: int
    instrument_id: int
    symbol: str
    action: str
    side: str
    price: Decimal | None
    size: Decimal | None
    channel_id: int
    order_id: str
    flags: int
    sequence: int
    raw_fingerprint: str

    @property
    def group_key(self) -> tuple[int, int, int, int]:
        """Return one provider-native event-group identity."""
        return self.publisher_id, self.instrument_id, self.channel_id, self.sequence


class DatabentoMboCsvAdapter:
    """Inspect CSV exports and yield provider-neutral MBO/trade events incrementally."""

    def __init__(self, config: DatabentoMboCsvConfig) -> None:
        """Configure one local, credential-free historical source."""
        self._config = config
        try:
            self._exchange_timezone = ZoneInfo(config.session_policy.exchange_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown exchange timezone in historical session policy") from error

    @property
    def provider_name(self) -> str:
        """Return the stable provider name."""
        return DATABENTO_PROVIDER_NAME

    @property
    def format_version(self) -> str:
        """Return the supported text encoding contract version."""
        return DATABENTO_MBO_CSV_FORMAT_VERSION

    @property
    def config(self) -> DatabentoMboCsvConfig:
        """Return the immutable normalization policy."""
        return self._config

    def discover(self) -> tuple[Path, ...]:
        """Return explicitly configured files in deterministic lexical order."""
        return tuple(
            sorted(
                (Path(value).expanduser().resolve() for value in self._config.source_paths),
                key=str,
            )
        )

    def inspect(self) -> tuple[HistoricalFileInspection, ...]:
        """Inspect every source without repairing or emitting canonical events."""
        return tuple(self._inspect_file(path) for path in self.discover())

    def normalize(self) -> Iterator[HistoricalNormalizedEvent]:
        """Yield canonical events incrementally after strict pre-flight validation."""
        inspections = self.inspect()
        for inspection in inspections:
            if inspection.has_fatal_issues:
                raise HistoricalDataValidationError(
                    f"historical file {inspection.file_identity.source_filename} has fatal "
                    "inspection findings"
                )
            if inspection.sequence_gaps and not self._config.allow_native_sequence_gaps:
                raise HistoricalDataValidationError(
                    "native sequence gaps require explicit allow_native_sequence_gaps=true"
                )

        previous_session_by_market: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}
        for path, inspection in zip(self.discover(), inspections, strict=True):
            source_hash = inspection.file_identity.sha256
            records = self._iter_native_records(path)
            for group in _consecutive_groups(records):
                mapping = self._resolve_mapping(group[0])
                segment, session_date, session_id = self._session(group[0], mapping)
                market_key = (str(mapping.instrument.instrument_id), mapping.venue)
                previous_session = previous_session_by_market.get(market_key)
                boundary = (
                    HistoricalBoundaryKind.SESSION
                    if previous_session is not None and previous_session != session_id
                    else None
                )
                previous_session_by_market[market_key] = session_id
                stream_text = (
                    f"databento:{source_hash[:16]}:{group[0].publisher_id}:"
                    f"{group[0].instrument_id}:{group[0].channel_id}:{session_id}"
                )
                counter = counters.get(stream_text, 0)
                events = self._normalize_group(
                    group,
                    mapping,
                    source_hash,
                    stream_text,
                    counter,
                    segment,
                )
                counters[stream_text] = counter + len(events)
                for event_index, event in enumerate(events):
                    event_boundary = boundary if event_index == 0 else None
                    if isinstance(event, BookEvent) and event.action is BookAction.RESET:
                        event_boundary = HistoricalBoundaryKind.RESET
                    yield HistoricalNormalizedEvent(
                        event=event,
                        source_sha256=source_hash,
                        source_line_numbers=tuple(item.line_number for item in group),
                        provider_sequence=group[0].sequence,
                        session_date=session_date,
                        session_segment=segment,
                        session_id=session_id,
                        boundary_before=event_boundary,
                        process_time_synthetic=True,
                        is_recovery_snapshot=bool(group[0].flags & _F_SNAPSHOT),
                    )

    def _inspect_file(self, path: Path) -> HistoricalFileInspection:
        identity = sha256_file(path)
        missing_fields: list[str] = []
        malformed: list[str] = []
        timestamp_problems: list[str] = []
        gaps: list[str] = []
        regressions: list[str] = []
        duplicates: list[str] = []
        unsupported: list[str] = []
        provider_instruments: set[int] = set()
        symbols: set[str] = set()
        venues: set[str] = set()
        actions: set[str] = set()
        fingerprints: set[str] = set()
        previous_sequence: dict[tuple[int, int, int], int] = {}
        times: list[datetime] = []
        sequences: list[int] = []
        count = 0
        event_estimate = 0
        detected_schema: str | None = None
        try:
            handle = path.open("r", encoding="utf-8", newline="")
        except OSError as error:
            message = f"cannot open historical source {path}: {error}"
            raise HistoricalDataValidationError(message) from error
        with handle:
            reader = csv.DictReader(handle)
            headers = set(reader.fieldnames or ())
            absent = tuple(sorted(_REQUIRED_COLUMNS - headers))
            if absent:
                missing_fields.extend(f"missing CSV column {name}" for name in absent)
            else:
                detected_schema = DATABENTO_MBO_CSV_FORMAT_VERSION
            for line_number, row in enumerate(reader, start=2):
                count += 1
                try:
                    record = self._parse_record(row, line_number)
                except (HistoricalDataValidationError, ValueError) as error:
                    malformed.append(f"line {line_number}: {error}")
                    continue
                provider_instruments.add(record.instrument_id)
                symbols.add(record.symbol)
                actions.add(record.action)
                times.append(record.exchange_time)
                sequences.append(record.sequence)
                event_estimate += record.action in {"A", "C", "M", "R", "T"}
                mapping = self._mapping_or_none(record)
                if mapping is None:
                    unsupported.append(f"line {line_number}: no unambiguous instrument mapping")
                else:
                    venues.add(mapping.venue)
                if record.exchange_time > record.receive_time:
                    timestamp_problems.append(f"line {line_number}: ts_event is after ts_recv")
                if record.flags & (_F_MBP | _F_TOB):
                    unsupported.append(
                        f"line {line_number}: MBP/top-of-book record is not true MBO"
                    )
                bad_receive_clock = bool(record.flags & _F_BAD_TS_RECV)
                is_snapshot = bool(record.flags & _F_SNAPSHOT)
                if record.flags & _F_MAYBE_BAD_BOOK or (bad_receive_clock and not is_snapshot):
                    unsupported.append(
                        f"line {line_number}: provider flags mark bad clock/book quality"
                    )
                if record.raw_fingerprint in fingerprints:
                    duplicates.append(f"line {line_number}: duplicate provider record")
                fingerprints.add(record.raw_fingerprint)
                stream = (record.publisher_id, record.instrument_id, record.channel_id)
                if is_snapshot:
                    if record.flags & _F_LAST:
                        previous_sequence[stream] = record.sequence
                else:
                    previous = previous_sequence.get(stream)
                    if previous is not None and record.sequence != previous:
                        if record.sequence < previous:
                            regressions.append(
                                f"line {line_number}: sequence regressed "
                                f"{previous}->{record.sequence}"
                            )
                        elif record.sequence > previous + 1:
                            gaps.append(
                                f"line {line_number}: sequence gap {previous}->{record.sequence}"
                            )
                    previous_sequence[stream] = record.sequence
        return HistoricalFileInspection(
            file_identity=identity,
            provider=self.provider_name,
            detected_schema=detected_schema,
            raw_record_count=count,
            normalized_event_estimate=event_estimate,
            provider_instrument_ids=tuple(sorted(provider_instruments)),
            raw_symbols=tuple(sorted(symbols)),
            venues=tuple(sorted(venues)),
            start_exchange_time=None if not times else min(times),
            end_exchange_time=None if not times else max(times),
            minimum_provider_sequence=None if not sequences else min(sequences),
            maximum_provider_sequence=None if not sequences else max(sequences),
            event_actions=tuple(sorted(actions)),
            book_mode=BookDataMode.MARKET_BY_ORDER.value,
            missing_fields=tuple(missing_fields),
            malformed_records=tuple(malformed),
            timestamp_ordering_problems=tuple(timestamp_problems),
            sequence_gaps=tuple(gaps),
            sequence_regressions=tuple(regressions),
            duplicate_records=tuple(duplicates),
            unsupported_records=tuple(unsupported),
        )

    def _iter_native_records(self, path: Path) -> Iterator[_NativeRecord]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for line_number, row in enumerate(reader, start=2):
                yield self._parse_record(row, line_number)

    def _parse_record(self, row: Mapping[str, str | None], line_number: int) -> _NativeRecord:
        try:
            if int(_required_text(row, "rtype")) != 160:
                raise HistoricalDataValidationError("rtype must be 160 for MBO")
            action = _required_text(row, "action").upper()
            if action not in _SUPPORTED_ACTIONS:
                raise HistoricalDataValidationError(f"unsupported Databento action {action!r}")
            exchange_time = _parse_timestamp(_required_text(row, "ts_event"))
            receive_time = _parse_timestamp(_required_text(row, "ts_recv"))
            side = _required_text(row, "side").upper()
            if side not in {"A", "B", "N"}:
                raise HistoricalDataValidationError(f"unsupported Databento side {side!r}")
            price = _optional_price(row.get("price"), self._config.price_encoding)
            size = _optional_decimal(row.get("size"))
            values = tuple((key, row.get(key)) for key in sorted(row))
            fingerprint = hashlib.sha256(repr(values).encode()).hexdigest()
            record = _NativeRecord(
                line_number=line_number,
                exchange_time=exchange_time,
                receive_time=receive_time,
                publisher_id=int(_required_text(row, "publisher_id")),
                instrument_id=int(_required_text(row, "instrument_id")),
                symbol=_required_text(row, "symbol"),
                action=action,
                side=side,
                price=price,
                size=size,
                channel_id=int(_required_text(row, "channel_id")),
                order_id=_required_text(row, "order_id"),
                flags=int(_required_text(row, "flags")),
                sequence=int(_required_text(row, "sequence")),
                raw_fingerprint=fingerprint,
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            raise HistoricalDataValidationError(f"invalid typed field: {error}") from error
        _validate_native_shape(record)
        return record

    def _mapping_or_none(self, record: _NativeRecord) -> HistoricalInstrumentMapping | None:
        effective_time = record.receive_time if record.flags & _F_SNAPSHOT else record.exchange_time
        candidates = tuple(
            item
            for item in self._config.instrument_mappings
            if item.publisher_id == record.publisher_id
            and item.provider_instrument_id == record.instrument_id
            and item.provider_symbol == record.symbol
            and item.is_valid_at(effective_time)
        )
        return candidates[0] if len(candidates) == 1 else None

    def _resolve_mapping(self, record: _NativeRecord) -> HistoricalInstrumentMapping:
        mapping = self._mapping_or_none(record)
        if mapping is None:
            raise HistoricalDataValidationError(
                f"line {record.line_number}: no unique effective instrument mapping"
            )
        return mapping

    def _session(
        self,
        record: _NativeRecord,
        mapping: HistoricalInstrumentMapping,
    ) -> tuple[HistoricalSessionSegment, date, str]:
        if record.flags & _F_SNAPSHOT:
            local = record.receive_time.astimezone(self._exchange_timezone)
            session_date = local.date() + timedelta(
                days=self._config.snapshot_session_date_offset_days
            )
            return (
                HistoricalSessionSegment.PREMARKET,
                session_date,
                f"{mapping.venue}:{session_date.isoformat()}",
            )
        local = record.exchange_time.astimezone(self._exchange_timezone)
        policy = self._config.session_policy
        local_time = local.timetz().replace(tzinfo=None)
        if policy.premarket_start <= local_time < policy.regular_start:
            segment = HistoricalSessionSegment.PREMARKET
        elif policy.regular_start <= local_time < policy.regular_end:
            segment = HistoricalSessionSegment.REGULAR
        elif policy.regular_end <= local_time < policy.after_hours_end:
            segment = HistoricalSessionSegment.AFTER_HOURS
        else:
            segment = HistoricalSessionSegment.CLOSED
        session_date = local.date()
        session_id = f"{mapping.venue}:{session_date.isoformat()}"
        return segment, session_date, session_id

    def _normalize_group(
        self,
        group: tuple[_NativeRecord, ...],
        mapping: HistoricalInstrumentMapping,
        source_hash: str,
        stream_text: str,
        first_sequence: int,
        segment: HistoricalSessionSegment,
    ) -> tuple[MarketEvent, ...]:
        if any(item.flags & (_F_MBP | _F_TOB | _F_MAYBE_BAD_BOOK) for item in group):
            raise HistoricalDataValidationError("unsupported or corrupt Databento group flags")
        if any(item.flags & _F_BAD_TS_RECV and not item.flags & _F_SNAPSHOT for item in group):
            raise HistoricalDataValidationError(
                "bad receive clock is accepted only on provider recovery snapshots"
            )
        trades = tuple(item for item in group if item.action == "T")
        fills = tuple(item for item in group if item.action == "F")
        trade_identity = (
            None
            if not trades
            else MarketTradeId(
                f"databento:{group[0].publisher_id}:{group[0].instrument_id}:"
                f"{group[0].channel_id}:{group[0].sequence}"
            )
        )
        events: list[MarketEvent] = []
        current_sequence = first_sequence
        for record in group:
            if record.action in {"T", "F", "N"}:
                continue
            book_action = _book_action(record, fills, bool(trades))
            common = self._common_fields(
                record,
                mapping,
                stream_text,
                current_sequence,
                segment,
            )
            event_name = f"{source_hash}|{record.line_number}|BOOK|{book_action.value}"
            if book_action is BookAction.RESET:
                event = BookEvent.model_validate(
                    {
                        **common,
                        "event_id": BookEventId(uuid5(_EVENT_NAMESPACE, event_name)),
                        "action": book_action,
                    }
                )
            else:
                event = BookEvent.model_validate(
                    {
                        **common,
                        "event_id": BookEventId(uuid5(_EVENT_NAMESPACE, event_name)),
                        "action": book_action,
                        "side": _book_side(record.side),
                        "price": _required(record.price, "book price"),
                        "quantity": _required(record.size, "book size"),
                        "order_id": MarketOrderId(
                            f"databento:{record.publisher_id}:"
                            f"{record.instrument_id}:{record.order_id}"
                        ),
                        "trade_id": (trade_identity if book_action is BookAction.EXECUTE else None),
                        "book_mode": BookDataMode.MARKET_BY_ORDER,
                    }
                )
            events.append(event)
            current_sequence += 1
        for record in trades:
            common = self._common_fields(
                record,
                mapping,
                stream_text,
                current_sequence,
                segment,
            )
            event_name = f"{source_hash}|{record.line_number}|TRADE"
            events.append(
                TradeEvent.model_validate(
                    {
                        **common,
                        "trade_event_id": TradeEventId(uuid5(_EVENT_NAMESPACE, event_name)),
                        "trade_id": trade_identity,
                        "price": _required(record.price, "trade price"),
                        "quantity": _required(record.size, "trade size"),
                        "aggressor_side": _aggressor_side(record.side),
                    }
                )
            )
            current_sequence += 1
        return tuple(events)

    def _common_fields(
        self,
        record: _NativeRecord,
        mapping: HistoricalInstrumentMapping,
        stream_text: str,
        sequence: int,
        segment: HistoricalSessionSegment,
    ) -> dict[str, object]:
        process_time = _historical_process_time(record, self._config)
        flags = (MarketEventFlag.REGULAR,) if segment is HistoricalSessionSegment.REGULAR else ()
        return {
            "instrument_id": mapping.instrument.instrument_id,
            "venue": mapping.venue,
            "sequence_stream_id": SequenceStreamId(stream_text),
            "exchange_time": record.exchange_time,
            "receive_time": record.receive_time,
            "process_time": process_time,
            "sequence_number": sequence,
            "flags": flags,
        }


def sha256_file(path: Path) -> HistoricalFileIdentity:
    """Hash exact source bytes incrementally using standard-library SHA-256."""
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_count += len(chunk)
    except OSError as error:
        message = f"cannot hash historical source {path}: {error}"
        raise HistoricalDataValidationError(message) from error
    return HistoricalFileIdentity(
        source_filename=path.name,
        sha256=digest.hexdigest(),
        byte_count=byte_count,
    )


def _consecutive_groups(records: Iterator[_NativeRecord]) -> Iterator[tuple[_NativeRecord, ...]]:
    current: list[_NativeRecord] = []
    current_key: tuple[int, int, int, int] | None = None
    for record in records:
        if current_key is not None and record.group_key != current_key:
            yield tuple(current)
            current = []
        current.append(record)
        current_key = record.group_key
    if current:
        yield tuple(current)


def _validate_native_shape(record: _NativeRecord) -> None:
    if min(record.publisher_id, record.instrument_id, record.channel_id, record.sequence) < 0:
        raise HistoricalDataValidationError("provider numeric identifiers must be non-negative")
    if record.exchange_time > record.receive_time:
        raise HistoricalDataValidationError("ts_event must not be after ts_recv")
    if record.action in {"A", "C", "M", "T", "F"}:
        if record.price is None or record.price <= 0:
            raise HistoricalDataValidationError("priced MBO action requires positive price")
        if record.size is None or record.size <= 0:
            raise HistoricalDataValidationError(
                "quantity-bearing MBO action requires positive size"
            )
    if record.action in {"A", "C", "M"} and record.side not in {"A", "B"}:
        raise HistoricalDataValidationError("resting-order action requires Ask or Bid side")
    if record.action == "R" and record.side != "N":
        raise HistoricalDataValidationError("clear-book action requires side N")


def _book_action(
    record: _NativeRecord,
    fills: Sequence[_NativeRecord],
    has_trade: bool,
) -> BookAction:
    if record.action == "A":
        return BookAction.ADD
    if record.action == "M":
        return BookAction.MODIFY
    if record.action == "R":
        return BookAction.RESET
    if record.action != "C":
        raise HistoricalDataValidationError(f"record action {record.action} is not book-mutating")
    matched_fill = any(item.order_id == record.order_id for item in fills)
    if has_trade and matched_fill:
        return BookAction.EXECUTE
    return BookAction.CANCEL


def _parse_timestamp(value: str) -> datetime:
    stripped = value.strip()
    if stripped.isdigit():
        nanoseconds = int(stripped)
        seconds, remainder = divmod(nanoseconds, 1_000_000_000)
        return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=remainder // 1000)
    normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    parsed = datetime.fromisoformat(normalized)
    if parsed.utcoffset() is None:
        raise HistoricalDataValidationError("Databento timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _optional_price(value: str | None, encoding: DatabentoPriceEncoding) -> Decimal | None:
    parsed = _optional_decimal(value)
    if parsed is None:
        return None
    if encoding is DatabentoPriceEncoding.FIXED_1E9_INTEGER:
        return parsed / Decimal(1_000_000_000)
    return parsed


def _optional_decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    return Decimal(value.strip())


def _required_text(row: Mapping[str, str | None], key: str) -> str:
    value = row.get(key)
    if value is None or not value.strip():
        raise HistoricalDataValidationError(f"missing required field {key}")
    return value.strip()


def _book_side(value: str) -> BookSide:
    if value == "A":
        return BookSide.ASK
    if value == "B":
        return BookSide.BID
    raise HistoricalDataValidationError("book action has unknown mandatory side")


def _aggressor_side(value: str) -> AggressorSide:
    if value == "A":
        return AggressorSide.SELL
    if value == "B":
        return AggressorSide.BUY
    return AggressorSide.UNKNOWN


def _historical_process_time(
    record: _NativeRecord,
    config: DatabentoMboCsvConfig,
) -> datetime:
    if config.process_time_policy is HistoricalProcessTimePolicy.RECEIVE_TIME:
        return record.receive_time
    result = record.exchange_time + timedelta(
        microseconds=config.synthetic_process_offset_microseconds
    )
    if result < record.receive_time:
        raise HistoricalDataValidationError(
            "synthetic process offset produces process_time before receive_time"
        )
    return result


def _required[T](value: T | None, label: str) -> T:
    if value is None:
        raise HistoricalDataValidationError(f"required {label} is missing")
    return value
