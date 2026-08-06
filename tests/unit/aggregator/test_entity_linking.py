"""Tests for deterministic revision-aware entity and ticker linking."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.support.reference_data import load_reference_fixture

from sra_nexus.aggregator import (
    CanonicalEvent,
    CanonicalEventRevision,
    DeterministicEntityLinker,
    EntityMatchMethod,
    EventState,
    EventSubtype,
    EventType,
    LinkAmbiguityKind,
    NewsSourceType,
)
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.reference import AssetType, Entity, EntityType, Instrument
from sra_nexus.storage import SQLiteRawNewsRepository, SQLiteReferenceRepository

NOW = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def _raw(
    *,
    headline: str = "NVIDIA reports quarterly results",
    tickers: tuple[str, ...] = (),
    entities: tuple[str, ...] = (),
    source_type: NewsSourceType = NewsSourceType.WIRE,
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "source": "Offline Fixture",
            "source_type": source_type,
            "provider_item_id": f"{source_type.value}:{headline}",
            "headline": headline,
            "event_time": NOW - timedelta(seconds=2),
            "receive_time": NOW - timedelta(seconds=1),
            "process_time": NOW,
            "provider_tickers": tickers,
            "provider_entities": entities,
        }
    )


def _revision(
    item: RawNewsItem,
    *,
    ticker_anchors: tuple[str, ...] = (),
) -> CanonicalEventRevision:
    event = CanonicalEvent(
        first_event_time=item.event_time,
        first_receive_time=item.receive_time,
        last_update_time=item.process_time,
        event_type=EventType.COMPANY,
        event_subtype=EventSubtype.COMPANY_EARNINGS,
        headline_summary=item.headline,
        source_news_ids=(item.news_id,),
        event_state=EventState.NEW,
    )
    return CanonicalEventRevision(
        revision_number=1,
        available_at=item.process_time,
        event=event,
        headline_tokens=("earnings",),
        anchors=ticker_anchors,
        ticker_anchors=ticker_anchors,
        source_names=(item.source,),
        source_types=(item.source_type,),
    )


def _linker(
    tmp_path: Path,
    item: RawNewsItem,
) -> tuple[DeterministicEntityLinker, SQLiteReferenceRepository]:
    database_path = tmp_path / "linking.sqlite3"
    raw_repository = SQLiteRawNewsRepository(database_path)
    reference_repository = SQLiteReferenceRepository(database_path)
    raw_repository.initialize_schema()
    reference_repository.initialize_schema()
    load_reference_fixture(reference_repository, reference_repository, reference_repository)
    raw_repository.insert(item)
    return (
        DeterministicEntityLinker(
            reference_repository,
            reference_repository,
            raw_repository,
        ),
        reference_repository,
    )


def test_provider_ticker_resolves_entity_through_explicit_instrument_link(tmp_path: Path) -> None:
    """Authoritative ticker metadata should have highest deterministic precedence."""
    item = _raw(headline="Quarterly results announced", tickers=("NVDA",))
    linker, repository = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item))

    expected = repository.resolve_canonical_name("NVIDIA Corporation").candidates[0]
    assert len(result.links) == 1
    assert result.links[0].entity_id == expected.entity_id
    assert result.links[0].match_method is EntityMatchMethod.PROVIDER_TICKER
    assert result.links[0].is_primary


def test_provider_entity_alias_resolves_without_fuzzy_matching(tmp_path: Path) -> None:
    """Provider entity strings may resolve through exact normalized aliases."""
    item = _raw(headline="Quarterly results announced", entities=("NVIDIA",))
    linker, repository = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item))

    assert result.links[0].entity_id == repository.resolve_alias("NVIDIA").candidates[0].entity_id
    assert result.links[0].match_method is EntityMatchMethod.PROVIDER_ENTITY


def test_exact_content_alias_is_auditable(tmp_path: Path) -> None:
    """Canonical content should use exact phrase matching after provider metadata."""
    item = _raw(headline="NVIDIA reports quarterly results")
    linker, _ = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item))

    assert len(result.links) == 1
    assert result.links[0].match_method is EntityMatchMethod.ALIAS
    assert result.links[0].matched_text == "NVIDIA"


def test_unknown_provider_entity_remains_explicitly_unresolved(tmp_path: Path) -> None:
    """Unknown provider metadata must not become a fabricated entity."""
    item = _raw(headline="Quarterly results announced", entities=("Unknown Holdings",))
    linker, _ = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item))

    assert result.links == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].matched_text == "Unknown Holdings"
    assert result.unresolved[0].match_method is EntityMatchMethod.UNRESOLVED


def test_ambiguous_ticker_does_not_select_an_instrument(tmp_path: Path) -> None:
    """A ticker collision across exchanges should return typed ticker ambiguity."""
    item = _raw(headline="Quarterly results announced", tickers=("NVDA",))
    linker, repository = _linker(tmp_path, item)
    repository.insert_instrument(
        Instrument(
            ticker="NVDA",
            exchange="SYNTH",
            asset_type=AssetType.EQUITY,
            currency="USD",
        )
    )

    result = linker.link_revision(_revision(item))

    assert result.links == ()
    assert result.ambiguities[0].kind is LinkAmbiguityKind.TICKER
    assert len(result.ambiguities[0].candidate_instrument_ids) == 2


def test_ambiguous_alias_does_not_select_an_entity(tmp_path: Path) -> None:
    """A cross-entity alias collision should remain explicit regardless of insertion order."""
    item = _raw(headline="Quarterly results announced", entities=("NVIDIA",))
    linker, repository = _linker(tmp_path, item)
    repository.insert_entity(
        Entity(
            entity_type=EntityType.OTHER,
            canonical_name="NVIDIA",
        )
    )

    result = linker.link_revision(_revision(item))

    assert result.links == ()
    assert result.ambiguities[0].kind is LinkAmbiguityKind.ENTITY
    assert len(result.ambiguities[0].candidate_entity_ids) == 2


def test_provider_ticker_prevents_duplicate_lower_priority_phrase_link(tmp_path: Path) -> None:
    """One entity should retain the highest-priority evidence rather than duplicate links."""
    item = _raw(tickers=("NVDA",), entities=("NVIDIA",))
    linker, _ = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item))

    assert len(result.links) == 1
    assert result.links[0].match_method is EntityMatchMethod.PROVIDER_TICKER


def test_speculative_source_uses_normal_linking_path(tmp_path: Path) -> None:
    """SPECULATIVE is a source category, not a special event or graph taxonomy."""
    item = _raw(tickers=("NVDA",), source_type=NewsSourceType.SPECULATIVE)
    linker, _ = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item))

    assert len(result.links) == 1
    assert result.links[0].match_method is EntityMatchMethod.PROVIDER_TICKER


def test_fallback_ticker_anchor_resolves_without_provider_metadata(tmp_path: Path) -> None:
    """A canonical ticker token may resolve last, after authoritative explicit evidence."""
    item = _raw(headline="NVDA reports quarterly results")
    linker, repository = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item, ticker_anchors=("nvda",)))

    nvda_entity = repository.resolve_alias("NVIDIA").candidates[0]
    assert len(result.links) == 1
    assert result.links[0].entity_id == nvda_entity.entity_id
    assert result.links[0].match_method is EntityMatchMethod.EXACT_PHRASE


def test_unknown_fallback_ticker_anchor_remains_unresolved(tmp_path: Path) -> None:
    """An inferred token absent from local instruments must remain auditable and unresolved."""
    item = _raw(headline="ZZZZ reports quarterly results")
    linker, _ = _linker(tmp_path, item)

    result = linker.link_revision(_revision(item, ticker_anchors=("zzzz",)))

    assert result.links == ()
    assert result.unresolved[0].matched_text == "zzzz"
