"""Tests for bounded, explainable, revision-aware event exposure propagation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support.reference_data import load_reference_fixture

from sra_nexus.aggregator import (
    CanonicalEvent,
    CanonicalEventRevision,
    DeterministicEntityLinker,
    DirectionPropagation,
    EventExposureService,
    EventState,
    EventSubtype,
    EventType,
    ExposureGenerationStatus,
    ExposureGraphConfig,
    ExposureRelationType,
    NewsSourceType,
    RelationshipTraversal,
)
from sra_nexus.aggregator.exposures import (
    RelationshipPropagationPolicy,
    calculate_propagated_confidence,
    calculate_propagated_magnitude,
    calculate_relevance,
    combine_bounded,
    deterministic_event_direction,
)
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common.types import CanonicalEventId, InstrumentId
from sra_nexus.reference import (
    AssetType,
    Entity,
    EntityInstrumentLink,
    EntityInstrumentRelationType,
    EntityRelationship,
    EntityRelationshipType,
    EntityType,
    Instrument,
)
from sra_nexus.storage import (
    SQLiteCanonicalEventRepository,
    SQLiteEventGraphRepository,
    SQLiteRawNewsRepository,
    SQLiteReferenceRepository,
)

NOW = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


class _Stack:
    def __init__(self, tmp_path: Path) -> None:
        path = tmp_path / "exposure.sqlite3"
        self.raw = SQLiteRawNewsRepository(path)
        self.canonical = SQLiteCanonicalEventRepository(path)
        self.reference = SQLiteReferenceRepository(path)
        self.graph = SQLiteEventGraphRepository(path)
        self.raw.initialize_schema()
        self.canonical.initialize_schema()
        self.reference.initialize_schema()
        self.graph.initialize_schema()
        load_reference_fixture(self.reference, self.reference, self.reference)

    def service(
        self,
        *,
        config: ExposureGraphConfig | None = None,
        policies: dict[EntityRelationshipType, RelationshipPropagationPolicy] | None = None,
    ) -> EventExposureService:
        linker = DeterministicEntityLinker(self.reference, self.reference, self.raw)
        return EventExposureService(
            self.canonical,
            self.reference,
            self.reference,
            self.reference,
            self.graph,
            self.graph,
            linker,
            config=config,
            relationship_policies=policies,
        )


def _raw(
    headline: str,
    *,
    at: datetime = NOW,
    tickers: tuple[str, ...] = (),
    entities: tuple[str, ...] = (),
    source_type: NewsSourceType = NewsSourceType.WIRE,
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "source": f"Offline {at.isoformat()}",
            "source_type": source_type,
            "provider_item_id": f"{at.isoformat()}:{headline}",
            "headline": headline,
            "event_time": at - timedelta(seconds=2),
            "receive_time": at - timedelta(seconds=1),
            "process_time": at,
            "provider_tickers": tickers,
            "provider_entities": entities,
        }
    )


def _revision(
    items: tuple[RawNewsItem, ...],
    *,
    event_id: CanonicalEventId | None = None,
    revision_number: int = 1,
    event_type: EventType = EventType.COMPANY,
    event_subtype: EventSubtype = EventSubtype.COMPANY_EARNINGS,
    headline: str | None = None,
) -> CanonicalEventRevision:
    event = CanonicalEvent(
        event_id=CanonicalEventId.new() if event_id is None else event_id,
        first_event_time=items[0].event_time,
        first_receive_time=items[0].receive_time,
        last_update_time=items[-1].process_time,
        event_type=event_type,
        event_subtype=event_subtype,
        headline_summary=items[-1].headline if headline is None else headline,
        source_news_ids=tuple(item.news_id for item in items),
        event_state=EventState.NEW if revision_number == 1 else EventState.UPDATED,
    )
    return CanonicalEventRevision(
        revision_number=revision_number,
        available_at=items[-1].process_time,
        event=event,
        headline_tokens=("fixture",),
        source_names=tuple(item.source for item in items),
        source_types=tuple(item.source_type for item in items),
    )


def _store_revision(stack: _Stack, revision: CanonicalEventRevision, *items: RawNewsItem) -> None:
    for item in items:
        stack.raw.insert(item)
    if revision.revision_number == 1:
        stack.canonical.create_event(revision)
    else:
        stack.canonical.append_revision(revision)


def _ticker(stack: _Stack, instrument_id: InstrumentId) -> str:
    instrument = stack.reference.get_instrument(instrument_id)
    if instrument is None:
        raise AssertionError(f"missing fixture instrument {instrument_id}")
    return instrument.ticker


def test_propagation_formulas_are_exact_and_bounded() -> None:
    """Mathematical engineering priors should be explicit rather than fixture-shaped."""
    assert calculate_propagated_magnitude(1.0, (0.8,), 1, 0.75) == pytest.approx(0.6)
    assert calculate_propagated_confidence(0.9, (0.8, 0.5), 0.75) == pytest.approx(0.27)
    assert calculate_relevance(1.0, 0.7, 2) == pytest.approx(0.49)
    assert combine_bounded((0.6, 0.25)) == pytest.approx(0.7)


@pytest.mark.parametrize(
    ("event_type", "event_subtype", "headline", "expected"),
    [
        (EventType.COMPANY, EventSubtype.COMPANY_BUYBACK, "Issuer authorizes buyback", 1.0),
        (
            EventType.COMPANY,
            EventSubtype.COMPANY_DIVIDEND,
            "Issuer suspends dividend",
            -1.0,
        ),
        (
            EventType.REGULATORY,
            EventSubtype.REGULATORY_APPROVAL,
            "Regulator approves treatment",
            1.0,
        ),
        (
            EventType.REGULATORY,
            EventSubtype.REGULATORY_OTHER,
            "Regulator rejects application",
            -1.0,
        ),
        (
            EventType.GEOPOLITICAL,
            EventSubtype.GEOPOLITICAL_SANCTION,
            "Government announces sanctions",
            -1.0,
        ),
        (EventType.COMPANY, EventSubtype.COMPANY_EARNINGS, "Issuer reports results", 0.0),
    ],
)
def test_event_direction_uses_only_explicit_semantics(
    event_type: EventType,
    event_subtype: EventSubtype,
    headline: str,
    expected: float,
) -> None:
    """The limited rule table should preserve unknown direction as zero."""
    assert deterministic_event_direction(event_type, event_subtype, headline, None) == expected


def test_propagation_formulas_reject_invalid_units_and_shapes() -> None:
    """Formula helpers should reject impossible ranges and inconsistent depth."""
    with pytest.raises(ValueError, match="equal relationship count"):
        calculate_propagated_magnitude(1.0, (0.8,), 2, 0.75)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        calculate_propagated_confidence(1.1, (), 1.0)
    with pytest.raises(ValueError, match="at least one"):
        combine_bounded(())
    with pytest.raises(ValueError, match="finite"):
        combine_bounded((float("nan"),))


def test_direct_and_indirect_exposure_preserve_auditable_path(tmp_path: Path) -> None:
    """A TSMC event should map directly to TSM and indirectly to NVDA."""
    stack = _Stack(tmp_path)
    item = _raw("TSMC reports quarterly results", tickers=("TSM",))
    revision = _revision((item,))
    _store_revision(stack, revision, item)

    result = stack.service().process_revision(revision.event.event_id, 1)
    by_ticker = {
        _ticker(stack, record.exposure.instrument_id): record for record in result.exposures
    }

    assert by_ticker["TSM"].exposure.is_direct
    assert by_ticker["TSM"].exposure.relation_type is ExposureRelationType.DIRECT_COMPANY
    assert not by_ticker["NVDA"].exposure.is_direct
    assert by_ticker["NVDA"].exposure.relation_type is ExposureRelationType.SUPPLIER
    indirect_path = next(
        path
        for path in result.paths
        if path.target_instrument_id == by_ticker["NVDA"].exposure.instrument_id
    )
    assert indirect_path.depth == 1
    assert indirect_path.magnitude == pytest.approx(0.6)


def test_maximum_depth_and_cycle_guard(tmp_path: Path) -> None:
    """Depth two may reach B and C but not D, and a cycle must terminate."""
    stack = _Stack(tmp_path)
    entities = [Entity(entity_type=EntityType.COMPANY, canonical_name=name) for name in "ABCD"]
    instruments = [
        Instrument(
            ticker=f"X{name}",
            exchange="SYNTH",
            asset_type=AssetType.EQUITY,
            currency="USD",
        )
        for name in "BCD"
    ]
    for entity in entities:
        stack.reference.insert_entity(entity)
    for entity, instrument in zip(entities[1:], instruments, strict=True):
        stack.reference.insert_instrument(instrument)
        stack.reference.insert_entity_instrument_link(
            EntityInstrumentLink(
                entity_id=entity.entity_id,
                instrument_id=instrument.instrument_id,
                relationship_type=EntityInstrumentRelationType.PRIMARY_EQUITY,
                confidence=1.0,
            )
        )
    edges = ((0, 1), (1, 2), (2, 3), (1, 0))
    for source, target in edges:
        stack.reference.insert_relationship(
            EntityRelationship(
                source_entity_id=entities[source].entity_id,
                target_entity_id=entities[target].entity_id,
                relation_type=EntityRelationshipType.SUPPLIER_TO,
                magnitude=0.8,
                confidence=0.9,
            )
        )
    item = _raw("A reports quarterly results", entities=("A",))
    revision = _revision((item,))
    _store_revision(stack, revision, item)

    result = stack.service().process_revision(revision.event.event_id, 1)
    exposed_ids = {record.exposure.instrument_id for record in result.exposures}

    assert instruments[0].instrument_id in exposed_ids
    assert instruments[1].instrument_id in exposed_ids
    assert instruments[2].instrument_id not in exposed_ids
    assert all(len(set(path.entity_ids)) == len(path.entity_ids) for path in result.paths)


def test_multiple_paths_combine_once_and_preserve_each_path(tmp_path: Path) -> None:
    """Two paths should yield one indirect NVDA exposure with bounded union values."""
    stack = _Stack(tmp_path)
    tsmc = stack.reference.resolve_alias("TSMC").candidates[0]
    amd = stack.reference.resolve_alias("AMD").candidates[0]
    nvidia = stack.reference.resolve_alias("NVIDIA").candidates[0]
    stack.reference.insert_relationship(
        EntityRelationship(
            source_entity_id=tsmc.entity_id,
            target_entity_id=amd.entity_id,
            relation_type=EntityRelationshipType.SUPPLIER_TO,
            magnitude=0.7,
            confidence=0.8,
        )
    )
    stack.reference.insert_relationship(
        EntityRelationship(
            source_entity_id=amd.entity_id,
            target_entity_id=nvidia.entity_id,
            relation_type=EntityRelationshipType.SUPPLIER_TO,
            magnitude=0.6,
            confidence=0.7,
        )
    )
    item = _raw("TSMC reports quarterly results", tickers=("TSM",))
    revision = _revision((item,))
    _store_revision(stack, revision, item)

    result = stack.service().process_revision(revision.event.event_id, 1)
    nvda = stack.reference.resolve_ticker("NVDA").candidates[0]
    record = next(
        exposure
        for exposure in result.exposures
        if exposure.exposure.instrument_id == nvda.instrument_id
    )
    paths = [path for path in result.paths if path.target_instrument_id == nvda.instrument_id]

    assert len(paths) == 2
    assert len(record.path_ids) == 2
    assert record.exposure.magnitude == pytest.approx(combine_bounded((0.6, 0.23625)))
    assert record.exposure.confidence == pytest.approx(combine_bounded((0.9, 0.56)))
    assert record.exposure.magnitude <= 1.0


def test_conflicting_deterministic_path_directions_become_zero(tmp_path: Path) -> None:
    """Conflicting paths must set a conflict flag rather than use processing order."""
    stack = _Stack(tmp_path)
    tsmc = stack.reference.resolve_alias("TSMC").candidates[0]
    nvidia = stack.reference.resolve_alias("NVIDIA").candidates[0]
    stack.reference.insert_relationship(
        EntityRelationship(
            source_entity_id=tsmc.entity_id,
            target_entity_id=nvidia.entity_id,
            relation_type=EntityRelationshipType.CUSTOMER_OF,
            magnitude=0.7,
            confidence=0.8,
        )
    )
    policies = {
        EntityRelationshipType.SUPPLIER_TO: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.FORWARD,
            direction=DirectionPropagation.PRESERVE,
        ),
        EntityRelationshipType.CUSTOMER_OF: RelationshipPropagationPolicy(
            traversal=RelationshipTraversal.FORWARD,
            direction=DirectionPropagation.REVERSE,
        ),
    }
    item = _raw("TSMC authorizes share buyback", tickers=("TSM",))
    revision = _revision(
        (item,),
        event_subtype=EventSubtype.COMPANY_BUYBACK,
    )
    _store_revision(stack, revision, item)

    result = stack.service(policies=policies).process_revision(revision.event.event_id, 1)
    nvda = stack.reference.resolve_ticker("NVDA").candidates[0]
    record = next(
        exposure
        for exposure in result.exposures
        if exposure.exposure.instrument_id == nvda.instrument_id
    )

    assert record.direction_conflict
    assert record.exposure.direction == 0.0


def test_revision_history_never_exposes_later_entity_knowledge(tmp_path: Path) -> None:
    """Revision two entity metadata must not appear in revision one historical queries."""
    stack = _Stack(tmp_path)
    first_item = _raw("Apple reports quarterly results", tickers=("AAPL",))
    second_item = _raw(
        "Apple update identifies TSMC",
        at=NOW + timedelta(minutes=15),
        tickers=("TSM",),
    )
    first = _revision((first_item,))
    second = _revision(
        (first_item, second_item),
        event_id=first.event.event_id,
        revision_number=2,
    )
    _store_revision(stack, first, first_item)
    _store_revision(stack, second, second_item)
    service = stack.service()
    service.process_revision(first.event.event_id, 1)
    service.process_revision(first.event.event_id, 2)

    early = stack.graph.get_event_exposures_as_of(
        first.event.event_id,
        NOW + timedelta(minutes=5),
    )
    late = stack.graph.get_event_exposures_as_of(
        first.event.event_id,
        NOW + timedelta(minutes=20),
    )
    early_tickers = {_ticker(stack, record.exposure.instrument_id) for record in early}
    late_tickers = {_ticker(stack, record.exposure.instrument_id) for record in late}
    nvda = stack.reference.resolve_ticker("NVDA").candidates[0]
    nvda_early = stack.graph.list_instrument_exposures_as_of(
        nvda.instrument_id,
        NOW + timedelta(minutes=5),
    )
    nvda_late = stack.graph.list_instrument_exposures_as_of(
        nvda.instrument_id,
        NOW + timedelta(minutes=20),
    )

    assert early_tickers == {"AAPL"}
    assert {"AAPL", "TSM", "NVDA"} <= late_tickers
    assert nvda_early == ()
    assert len(nvda_late) == 1 and nvda_late[0].revision_number == 2


def test_relationship_validity_changes_propagation_by_revision(tmp_path: Path) -> None:
    """A relationship beginning in 2026 must not affect a 2025 event revision."""
    stack = _Stack(tmp_path)
    apple = stack.reference.resolve_alias("Apple").candidates[0]
    microsoft = stack.reference.resolve_alias("Microsoft").candidates[0]
    valid_from = datetime(2026, 1, 1, tzinfo=UTC)
    stack.reference.insert_relationship(
        EntityRelationship(
            source_entity_id=apple.entity_id,
            target_entity_id=microsoft.entity_id,
            relation_type=EntityRelationshipType.SUPPLIER_TO,
            magnitude=0.7,
            confidence=0.8,
            valid_from=valid_from,
        )
    )
    old_item = _raw(
        "Apple reports quarterly results",
        at=datetime(2025, 12, 31, 10, 0, tzinfo=UTC),
        tickers=("AAPL",),
    )
    new_item = _raw(
        "Apple updates quarterly results",
        at=datetime(2026, 1, 2, 10, 0, tzinfo=UTC),
        tickers=("AAPL",),
    )
    first = _revision((old_item,))
    second = _revision((old_item, new_item), event_id=first.event.event_id, revision_number=2)
    _store_revision(stack, first, old_item)
    _store_revision(stack, second, new_item)
    service = stack.service()

    before = service.process_revision(first.event.event_id, 1)
    after = service.process_revision(first.event.event_id, 2)
    msft = stack.reference.resolve_ticker("MSFT").candidates[0]

    assert all(record.exposure.instrument_id != msft.instrument_id for record in before.exposures)
    assert any(record.exposure.instrument_id == msft.instrument_id for record in after.exposures)


def test_processing_same_revision_is_idempotent(tmp_path: Path) -> None:
    """A rerun should return stored immutable results and create no duplicate paths."""
    stack = _Stack(tmp_path)
    item = _raw("TSMC reports quarterly results", tickers=("TSM",))
    revision = _revision((item,))
    _store_revision(stack, revision, item)
    service = stack.service()

    first = service.process_revision(revision.event.event_id, 1)
    second = service.process_revision(revision.event.event_id, 1)

    assert first.status is ExposureGenerationStatus.PROCESSED
    assert second.status is ExposureGenerationStatus.ALREADY_PROCESSED
    assert second.entity_links == first.entity_links
    assert second.exposures == first.exposures
    assert second.paths == first.paths


def test_unknown_direction_remains_zero_with_nonzero_economic_magnitude(tmp_path: Path) -> None:
    """Generic earnings must not fabricate sign even when economic connectivity exists."""
    stack = _Stack(tmp_path)
    item = _raw("TSMC reports quarterly results", tickers=("TSM",))
    revision = _revision((item,))
    _store_revision(stack, revision, item)

    result = stack.service().process_revision(revision.event.event_id, 1)

    assert result.exposures
    assert all(record.exposure.direction == 0.0 for record in result.exposures)
    assert all(record.exposure.magnitude > 0.0 for record in result.exposures)
