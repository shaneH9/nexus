"""Offline integration tests from raw news through revision-aware event exposure."""

from datetime import UTC, datetime
from pathlib import Path

from tests.support.reference_data import load_reference_fixture

from sra_nexus.aggregator import (
    DeterministicEntityLinker,
    EventExposureService,
    EventType,
    NewsSourceType,
)
from sra_nexus.aggregator.canonicalization import CanonicalizationService
from sra_nexus.aggregator.classification import DeterministicEventClassifier
from sra_nexus.aggregator.ingestion import RawNewsIngestionService
from sra_nexus.aggregator.sources import MockNewsSource
from sra_nexus.storage import (
    SQLiteCanonicalEventRepository,
    SQLiteEventGraphRepository,
    SQLiteRawNewsRepository,
    SQLiteReferenceRepository,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "news" / "speculative_entity_linking.json"
)


def test_speculative_raw_item_follows_normal_entity_and_exposure_pipeline(tmp_path: Path) -> None:
    """SPECULATIVE provenance should not create a special event type or graph path."""
    database_path = tmp_path / "milestone_d.sqlite3"
    raw = SQLiteRawNewsRepository(database_path)
    canonical = SQLiteCanonicalEventRepository(database_path)
    reference = SQLiteReferenceRepository(database_path)
    graph = SQLiteEventGraphRepository(database_path)
    raw.initialize_schema()
    canonical.initialize_schema()
    reference.initialize_schema()
    graph.initialize_schema()
    load_reference_fixture(reference, reference, reference)

    ingestion = RawNewsIngestionService(MockNewsSource(FIXTURE), raw).ingest()
    assert ingestion.inserted_count == 1
    item = raw.list_available_as_of(datetime(2026, 7, 1, 13, 1, tzinfo=UTC))[0]
    canonical_result = CanonicalizationService(
        canonical,
        DeterministicEventClassifier(),
    ).canonicalize(item)
    assert canonical_result.event_id is not None

    exposure_result = EventExposureService(
        canonical,
        reference,
        reference,
        reference,
        graph,
        graph,
        DeterministicEntityLinker(reference, reference, raw),
    ).process_revision(canonical_result.event_id, 1)

    event = canonical.get_current_event(canonical_result.event_id)
    nvda = reference.resolve_ticker("NVDA").instrument
    assert item.source_type is NewsSourceType.SPECULATIVE
    assert event is not None and event.event_type is EventType.COMPANY
    assert nvda is not None
    assert any(
        record.exposure.instrument_id == nvda.instrument_id and record.exposure.is_direct
        for record in exposure_result.exposures
    )
