"""End-to-end offline tests for raw ingestion through canonical event history."""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sra_nexus.aggregator import EventState, EventSubtype, NewsSourceType
from sra_nexus.aggregator.canonicalization import (
    CanonicalizationDecisionType,
    CanonicalizationService,
)
from sra_nexus.aggregator.classification import DeterministicEventClassifier
from sra_nexus.aggregator.ingestion import RawNewsIngestionService
from sra_nexus.aggregator.sources import MockNewsSource
from sra_nexus.storage import SQLiteCanonicalEventRepository, SQLiteRawNewsRepository

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "news"


def test_raw_to_canonical_pipeline_clusters_and_reconstructs_history(tmp_path: Path) -> None:
    """The full offline path should preserve identity, revisions, gating, and idempotency."""
    database_path = tmp_path / "nexus.sqlite3"
    raw_repository = SQLiteRawNewsRepository(database_path)
    canonical_repository = SQLiteCanonicalEventRepository(database_path)
    raw_repository.initialize_schema()
    canonical_repository.initialize_schema()

    for fixture_name in ("canonical_evolution.json", "canonical_clustering_cases.json"):
        summary = RawNewsIngestionService(
            MockNewsSource(FIXTURE_DIR / fixture_name),
            raw_repository,
        ).ingest()
        assert summary.failed_count == 0
        assert summary.duplicate_count == 0

    cutoff = datetime(2026, 7, 5, tzinfo=UTC)
    raw_items = raw_repository.list_available_as_of(cutoff)
    by_provider_id = {item.provider_item_id: item for item in raw_items}
    service = CanonicalizationService(
        canonical_repository,
        DeterministicEventClassifier(),
    )

    first_pass = service.canonicalize_available_raw_news(raw_repository, cutoff)
    second_pass = service.canonicalize_available_raw_news(raw_repository, cutoff)

    assert len(raw_items) == 14
    assert Counter(result.decision for result in first_pass) == {
        CanonicalizationDecisionType.NEW_EVENT: 10,
        CanonicalizationDecisionType.CLUSTERED: 4,
    }
    assert all(
        result.decision is CanonicalizationDecisionType.ALREADY_PROCESSED for result in second_pass
    )

    evolution_ids = [
        canonical_repository.get_event_id_for_news(
            by_provider_id[f"acme-evolution-{index}"].news_id
        )
        for index in (1, 2, 3)
    ]
    assert evolution_ids[0] is not None
    assert evolution_ids[0] == evolution_ids[1] == evolution_ids[2]
    event_id = evolution_ids[0]
    assert event_id is not None

    early = canonical_repository.get_event_as_of(
        event_id,
        datetime(2026, 7, 1, 10, 2, tzinfo=UTC),
    )
    middle = canonical_repository.get_event_as_of(
        event_id,
        datetime(2026, 7, 1, 10, 10, tzinfo=UTC),
    )
    late = canonical_repository.get_event_as_of(
        event_id,
        datetime(2026, 7, 1, 10, 30, tzinfo=UTC),
    )
    assert early is not None and middle is not None and late is not None
    assert early.source_news_ids == (by_provider_id["acme-evolution-1"].news_id,)
    assert early.event_state is EventState.NEW
    assert middle.source_news_ids == (
        by_provider_id["acme-evolution-1"].news_id,
        by_provider_id["acme-evolution-2"].news_id,
    )
    assert middle.event_state is EventState.DEVELOPING
    assert late.event_state is EventState.CONFIRMED

    assert canonical_repository.get_event_id_for_news(
        by_provider_id["nvda-earnings-1"].news_id
    ) == canonical_repository.get_event_id_for_news(by_provider_id["nvda-earnings-2"].news_id)
    assert canonical_repository.get_event_id_for_news(
        by_provider_id["apple-deal-1"].news_id
    ) != canonical_repository.get_event_id_for_news(by_provider_id["microsoft-deal-1"].news_id)
    assert canonical_repository.get_event_id_for_news(
        by_provider_id["nvda-earnings-1"].news_id
    ) != canonical_repository.get_event_id_for_news(by_provider_id["nvda-deal-1"].news_id)
    assert canonical_repository.get_event_id_for_news(
        by_provider_id["generic-1"].news_id
    ) != canonical_repository.get_event_id_for_news(by_provider_id["generic-2"].news_id)
    assert canonical_repository.get_event_id_for_news(
        by_provider_id["nvda-earnings-1"].news_id
    ) != canonical_repository.get_event_id_for_news(by_provider_id["nvda-earnings-far"].news_id)

    speculative_id = canonical_repository.get_event_id_for_news(
        by_provider_id["spec-guidance-1"].news_id
    )
    assert speculative_id is not None
    speculative = canonical_repository.get_current_event(speculative_id)
    assert speculative is not None
    assert by_provider_id["spec-guidance-1"].source_type is NewsSourceType.SPECULATIVE
    assert speculative.event_subtype is EventSubtype.COMPANY_GUIDANCE
    assert len(canonical_repository.list_events_available_as_of(cutoff)) == 10
