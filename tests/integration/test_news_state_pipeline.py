"""Offline integration tests for revision-safe on-demand NewsState aggregation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support.reference_data import load_reference_fixture

from sra_nexus.aggregator import (
    CanonicalEvent,
    CanonicalEventRevision,
    DeterministicEntityLinker,
    EventExposureService,
    EventScoringService,
    EventState,
    EventSubtype,
    EventType,
    NewsSourceType,
    NewsStateService,
)
from sra_nexus.aggregator.classification import DeterministicEventClassifier
from sra_nexus.aggregator.factory import build_raw_news_item
from sra_nexus.aggregator.normalization import comparison_tokens
from sra_nexus.aggregator.raw import RawNewsItem
from sra_nexus.common import CanonicalEventId, InstrumentId
from sra_nexus.reference import ReferenceDataPolicy
from sra_nexus.storage import (
    SQLiteCanonicalEventRepository,
    SQLiteEventGraphRepository,
    SQLiteRawNewsRepository,
    SQLiteReferenceRepository,
)

START = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


class _Stack:
    def __init__(self, tmp_path: Path) -> None:
        path = tmp_path / "news-state.sqlite3"
        self.raw = SQLiteRawNewsRepository(path)
        self.canonical = SQLiteCanonicalEventRepository(path)
        self.reference = SQLiteReferenceRepository(path)
        self.graph = SQLiteEventGraphRepository(path)
        self.raw.initialize_schema()
        self.canonical.initialize_schema()
        self.reference.initialize_schema()
        self.graph.initialize_schema()
        load_reference_fixture(self.reference, self.reference, self.reference)
        linker = DeterministicEntityLinker(self.reference, self.reference, self.raw)
        self.exposure_service = EventExposureService(
            self.canonical,
            self.reference,
            self.reference,
            self.reference,
            self.graph,
            self.graph,
            linker,
        )
        scoring = EventScoringService(DeterministicEventClassifier())
        self.state_service = NewsStateService(
            self.canonical,
            self.raw,
            self.graph,
            self.graph,
            scoring,
        )

    @property
    def nvda_id(self) -> InstrumentId:
        instrument = self.reference.resolve_ticker("NVDA").instrument
        if instrument is None:
            raise AssertionError("NVDA fixture instrument is missing")
        return instrument.instrument_id


def _raw(
    *,
    at: datetime,
    source: str,
    source_type: NewsSourceType,
    headline: str,
) -> RawNewsItem:
    return build_raw_news_item(
        {
            "source": source,
            "source_type": source_type,
            "provider_item_id": f"{source}:{at.isoformat()}:{headline}",
            "headline": headline,
            "event_time": at - timedelta(seconds=2),
            "receive_time": at - timedelta(seconds=1),
            "process_time": at,
            "provider_tickers": ["NVDA"],
        }
    )


def _store_event(
    stack: _Stack,
    items: tuple[RawNewsItem, ...],
    states: tuple[EventState, ...],
    *,
    event_type: EventType,
    event_subtype: EventSubtype,
) -> CanonicalEventId:
    if len(items) != len(states):
        raise AssertionError("each test revision requires one lifecycle state")
    event_id = CanonicalEventId.new()
    for item in items:
        result = stack.raw.insert(item)
        if not result.inserted:
            raise AssertionError(f"fixture raw item was not unique: {result.status}")
    for index, state in enumerate(states, start=1):
        current_items = items[:index]
        event = CanonicalEvent(
            event_id=event_id,
            first_event_time=items[0].event_time,
            first_receive_time=items[0].receive_time,
            last_update_time=current_items[-1].process_time,
            event_type=event_type,
            event_subtype=event_subtype,
            headline_summary=current_items[-1].headline,
            source_news_ids=tuple(item.news_id for item in current_items),
            event_state=state,
        )
        revision = CanonicalEventRevision(
            revision_number=index,
            available_at=current_items[-1].process_time,
            event=event,
            headline_tokens=tuple(sorted(comparison_tokens(current_items[-1].headline))),
            source_names=tuple(dict.fromkeys(item.source for item in current_items)),
            source_types=tuple(dict.fromkeys(item.source_type for item in current_items)),
        )
        if index == 1:
            stack.canonical.create_event(revision)
        else:
            stack.canonical.append_revision(revision)
        stack.exposure_service.process_revision(event_id, index)
    return event_id


def test_evolving_event_uses_only_revision_sources_available_as_of(tmp_path: Path) -> None:
    """10:02, 10:10, and 10:30 states must select revisions one, two, and three."""
    stack = _Stack(tmp_path)
    headline = "NVIDIA authorizes share buyback"
    first = _raw(
        at=START,
        source="Rumor Desk",
        source_type=NewsSourceType.FINANCIAL_NEWS,
        headline=headline,
    )
    second = _raw(
        at=START + timedelta(minutes=5),
        source="Independent Wire",
        source_type=NewsSourceType.WIRE,
        headline=headline,
    )
    official = _raw(
        at=START + timedelta(minutes=20),
        source="NVIDIA IR",
        source_type=NewsSourceType.COMPANY_RELEASE,
        headline=headline,
    )
    event_id = _store_event(
        stack,
        (first, second, official),
        (EventState.NEW, EventState.DEVELOPING, EventState.CONFIRMED),
        event_type=EventType.COMPANY,
        event_subtype=EventSubtype.COMPANY_BUYBACK,
    )

    early = stack.state_service.get_news_state(
        stack.nvda_id,
        START + timedelta(minutes=2),
    )
    middle = stack.state_service.get_news_state(
        stack.nvda_id,
        START + timedelta(minutes=10),
    )
    confirmed = stack.state_service.get_news_state(
        stack.nvda_id,
        START + timedelta(minutes=30),
    )

    assert early.active_event_ids == (event_id,)
    assert middle.active_event_ids == (event_id,)
    assert confirmed.active_event_ids == (event_id,)
    assert (early.news_volume, middle.news_volume, confirmed.news_volume) == (1, 2, 3)
    assert early.confidence < middle.confidence < confirmed.confidence
    assert confirmed.uncertainty < middle.uncertainty < early.uncertainty
    assert confirmed.news_acceleration == pytest.approx(2.0)
    assert confirmed.reference_data_policy is ReferenceDataPolicy.CURRENT_REFERENCE_DATA


def test_multiple_events_retain_positive_negative_and_neutral_risk(tmp_path: Path) -> None:
    """Independent directional and risk dimensions must coexist in one state."""
    stack = _Stack(tmp_path)
    company = _raw(
        at=START,
        source="Company Event Wire",
        source_type=NewsSourceType.WIRE,
        headline="NVIDIA authorizes share buyback",
    )
    regulatory = _raw(
        at=START + timedelta(minutes=1),
        source="Regulatory Event Wire",
        source_type=NewsSourceType.WIRE,
        headline="Regulator rejects NVIDIA application",
    )
    geopolitical = _raw(
        at=START + timedelta(minutes=2),
        source="Geopolitical Event Wire",
        source_type=NewsSourceType.GLOBAL_NEWS,
        headline="Military conflict affects NVIDIA operations",
    )
    _store_event(
        stack,
        (company,),
        (EventState.NEW,),
        event_type=EventType.COMPANY,
        event_subtype=EventSubtype.COMPANY_BUYBACK,
    )
    _store_event(
        stack,
        (regulatory,),
        (EventState.NEW,),
        event_type=EventType.REGULATORY,
        event_subtype=EventSubtype.REGULATORY_OTHER,
    )
    _store_event(
        stack,
        (geopolitical,),
        (EventState.NEW,),
        event_type=EventType.GEOPOLITICAL,
        event_subtype=EventSubtype.GEOPOLITICAL_CONFLICT,
    )

    state = stack.state_service.get_news_state(
        stack.nvda_id,
        START + timedelta(minutes=5),
    )

    assert state.positive_event_intensity > 0.0
    assert state.negative_event_intensity > 0.0
    assert state.company_event_risk > 0.0
    assert state.regulatory_event_risk > 0.0
    assert state.geopolitical_event_risk > 0.0
    assert len(state.active_event_ids) == 3


def test_unknown_direction_still_contributes_risk(tmp_path: Path) -> None:
    """Generic earnings carry event risk without fabricated directional intensity."""
    stack = _Stack(tmp_path)
    item = _raw(
        at=START,
        source="Earnings Wire",
        source_type=NewsSourceType.WIRE,
        headline="NVIDIA reports quarterly results",
    )
    _store_event(
        stack,
        (item,),
        (EventState.NEW,),
        event_type=EventType.COMPANY,
        event_subtype=EventSubtype.COMPANY_EARNINGS,
    )

    state = stack.state_service.get_news_state(stack.nvda_id, START + timedelta(minutes=1))

    assert state.positive_event_intensity == 0.0
    assert state.negative_event_intensity == 0.0
    assert state.company_event_risk > 0.0


def test_speculative_event_participates_without_automatic_direction(tmp_path: Path) -> None:
    """SPECULATIVE provenance remains active, lower-prior, uncertain context."""
    stack = _Stack(tmp_path)
    item = _raw(
        at=START,
        source="Alternative Data Desk",
        source_type=NewsSourceType.SPECULATIVE,
        headline="NVIDIA reports a corporate update",
    )
    _store_event(
        stack,
        (item,),
        (EventState.NEW,),
        event_type=EventType.COMPANY,
        event_subtype=EventSubtype.COMPANY_OTHER,
    )

    state = stack.state_service.get_news_state(stack.nvda_id, START + timedelta(minutes=1))

    assert state.active_event_ids
    assert state.news_volume == 1
    assert state.positive_event_intensity == 0.0
    assert state.negative_event_intensity == 0.0
    assert state.company_event_risk > 0.0
    assert state.uncertainty > 0.0


def test_retraction_stops_future_contribution_without_rewriting_history(tmp_path: Path) -> None:
    """A retracted latest revision replaces, but never mutates, earlier visible state."""
    stack = _Stack(tmp_path)
    reported = _raw(
        at=START,
        source="Initial Wire",
        source_type=NewsSourceType.WIRE,
        headline="NVIDIA authorizes share buyback",
    )
    retracted = _raw(
        at=START + timedelta(minutes=30),
        source="Correction Wire",
        source_type=NewsSourceType.WIRE,
        headline="NVIDIA buyback report retracted",
    )
    _store_event(
        stack,
        (reported, retracted),
        (EventState.NEW, EventState.RETRACTED),
        event_type=EventType.COMPANY,
        event_subtype=EventSubtype.COMPANY_BUYBACK,
    )

    before = stack.state_service.get_news_state(
        stack.nvda_id,
        START + timedelta(minutes=20),
    )
    after = stack.state_service.get_news_state(
        stack.nvda_id,
        START + timedelta(minutes=40),
    )

    assert before.positive_event_intensity > 0.0
    assert before.active_event_ids
    assert after.positive_event_intensity == 0.0
    assert after.negative_event_intensity == 0.0
    assert after.active_event_ids == ()
    assert after.news_volume == 0


def test_no_event_state_is_deterministic_zero_state(tmp_path: Path) -> None:
    """No relevant exposure yields zero values and no false information confidence."""
    stack = _Stack(tmp_path)

    state = stack.state_service.get_news_state(InstrumentId.new(), START)

    assert state.positive_event_intensity == 0.0
    assert state.negative_event_intensity == 0.0
    assert state.company_event_risk == 0.0
    assert state.sector_event_risk == 0.0
    assert state.macro_event_risk == 0.0
    assert state.geopolitical_event_risk == 0.0
    assert state.regulatory_event_risk == 0.0
    assert state.systemic_event_risk == 0.0
    assert state.news_volume == 0
    assert state.news_acceleration == 0.0
    assert state.novelty_intensity == 0.0
    assert state.uncertainty == 0.0
    assert state.confidence == 0.0
    assert state.active_event_ids == ()
    assert state.direct_event_exposures == ()
    assert state.indirect_event_exposures == ()
