"""Strict Databento historical adapter and source-identity tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from sra_nexus.market_data import BookAction, BookEvent, TradeEvent
from sra_nexus.market_data.providers.databento import (
    DatabentoMboCsvAdapter,
    DatabentoMboCsvConfig,
)
from sra_nexus.market_data.providers.databento.adapter import (
    HistoricalDataValidationError,
    sha256_file,
)
from sra_nexus.research.run import load_experiment

EXPERIMENT = Path("examples/historical/fixture_experiment.json")
FIXTURE = Path("tests/fixtures/historical/databento_mbo_fixture.csv")


def test_inspection_and_streaming_normalization_are_strict_and_deterministic() -> None:
    """Inspect exact bytes and stream contiguous provider-neutral MBO/trade events."""
    source = load_experiment(EXPERIMENT).sources[0]
    adapter = DatabentoMboCsvAdapter(source.adapter)

    inspection = adapter.inspect()[0]
    stream = adapter.normalize()

    assert inspection.file_identity == source.expected_files[0]
    assert inspection.raw_record_count == 49
    assert inspection.normalized_event_estimate == 41
    assert inspection.detected_schema == "databento-mbo-csv-v1"
    assert inspection.book_mode == "MARKET_BY_ORDER"
    assert not inspection.has_fatal_issues
    assert iter(stream) is stream

    events = tuple(stream)
    assert len(events) == 41
    assert tuple(item.event.sequence_number for item in events) == tuple(range(41))
    assert all(item.process_time_synthetic for item in events)
    assert all(item.event.process_time == item.event.receive_time for item in events)
    assert events[0].session_segment.value == "PREMARKET"
    assert events[0].is_recovery_snapshot
    assert all(item.session_segment.value == "REGULAR" for item in events[1:])
    assert (
        sum(
            isinstance(item.event, BookEvent) and item.event.action is BookAction.EXECUTE
            for item in events
        )
        == 8
    )
    assert sum(isinstance(item.event, TradeEvent) for item in events) == 8


def test_synthetic_exchange_offset_process_time_is_explicit_and_deterministic() -> None:
    """Never substitute file-read wall time for unavailable historical process time."""
    source = load_experiment(EXPERIMENT).sources[0]
    payload = source.adapter.model_dump(mode="python")
    payload.update(
        {
            "process_time_policy": "EXCHANGE_TIME_PLUS_OFFSET",
            "synthetic_process_offset_microseconds": 2,
        }
    )
    adapter = DatabentoMboCsvAdapter(DatabentoMboCsvConfig.model_validate(payload))

    first = next(adapter.normalize())

    assert first.process_time_synthetic
    assert first.event.process_time > first.event.receive_time
    assert (first.event.process_time - first.event.exchange_time).microseconds == 2


def test_source_sha256_changes_when_one_byte_changes(tmp_path: Path) -> None:
    """Bind research identity to exact source bytes rather than a filename."""
    original = sha256_file(FIXTURE)
    modified = tmp_path / FIXTURE.name
    payload = bytearray(FIXTURE.read_bytes())
    payload[-2] = ord("3") if payload[-2] != ord("3") else ord("4")
    modified.write_bytes(payload)

    changed = sha256_file(modified)

    assert changed.sha256 != original.sha256
    assert changed.byte_count == original.byte_count


def test_official_snapshot_bad_receive_flag_is_accepted_only_with_snapshot(
    tmp_path: Path,
) -> None:
    """Accept Databento's documented snapshot clock marker without hiding real corruption."""
    snapshot = tmp_path / "snapshot.csv"
    snapshot.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(",0,0,32,1000,0,SRA", ",0,0,40,1000,0,SRA", 1),
        encoding="utf-8",
    )
    source = load_experiment(EXPERIMENT).sources[0]
    payload = source.adapter.model_dump(mode="python")
    payload["source_paths"] = (str(snapshot),)
    adapter = DatabentoMboCsvAdapter(DatabentoMboCsvConfig.model_validate(payload))

    assert not adapter.inspect()[0].unsupported_records
    assert next(adapter.normalize()).is_recovery_snapshot


def test_unresolved_sequence_corruption_is_not_repaired(tmp_path: Path) -> None:
    """Report a native gap/regression and refuse canonical normalization by default."""
    corrupt = tmp_path / "corrupt.csv"
    corrupt.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(",6,SRA\n", ",8,SRA\n", 1),
        encoding="utf-8",
    )
    source = load_experiment(EXPERIMENT).sources[0]
    payload = source.adapter.model_dump(mode="python")
    payload["source_paths"] = (str(corrupt),)
    adapter = DatabentoMboCsvAdapter(DatabentoMboCsvConfig.model_validate(payload))

    inspection = adapter.inspect()[0]

    assert inspection.sequence_gaps
    assert inspection.sequence_regressions
    with pytest.raises(HistoricalDataValidationError):
        tuple(adapter.normalize())
