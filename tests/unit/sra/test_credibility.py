"""MBO-only attacked-liquidity credibility contract and service tests."""

from decimal import Decimal

from tests.support.market_data import (
    INSTRUMENT,
    SHARED_STREAM_ID,
    book_event,
    quote_event,
)
from tests.support.sra import liquidity_shock

from sra_nexus.market_data import (
    BookAction,
    BookDataMode,
    BookSnapshot,
    MarketEvent,
    OrderBook,
)
from sra_nexus.sra import (
    LIQUIDITY_CREDIBILITY_VERSION,
    SHOCK_PAIR_VERSION,
    LiquidityCredibilityConfig,
    LiquidityCredibilityResult,
    LiquidityCredibilityService,
    LiquidityCredibilityUnavailable,
    LiquidityCredibilityUnavailableReason,
    LiquidityShock,
    OrderLifecycle,
    OrderLifecycleTerminalReason,
    OrderLifecycleTracker,
    ShockDirection,
    ShockPair,
    calculate_credible_depth,
    calculate_delta_liquidity_credibility,
    calculate_quantity_weighted_order_credibility,
    compare_liquidity_credibility,
    derive_shock_pair_id,
    market_event_reference,
)


def _shock_between(start: MarketEvent, end: MarketEvent) -> LiquidityShock:
    base = liquidity_shock(direction=ShockDirection.SELL)
    return LiquidityShock(
        shock_id=base.shock_id,
        instrument_id=base.instrument_id,
        direction=base.direction,
        start_exchange_time=start.exchange_time,
        end_exchange_time=end.exchange_time,
        start_process_time=start.process_time,
        end_process_time=end.process_time,
        start_reference=market_event_reference(start),
        end_reference=market_event_reference(end),
        aggressive_volume=base.aggressive_volume,
        normalized_aggression=base.normalized_aggression,
        levels_touched=base.levels_touched,
        levels_consumed=base.levels_consumed,
        pre_spread=base.pre_spread,
        pre_depth=base.pre_depth,
        immediate_price_change=base.immediate_price_change,
        detection_method=base.detection_method,
        detection_version=base.detection_version,
    )


def _survival_scenario() -> tuple[
    LiquidityCredibilityResult,
    tuple[OrderLifecycle, ...],
    BookSnapshot,
]:
    events = (
        book_event(1, BookAction.ADD, quantity="100", order_id="survives"),
        book_event(2, BookAction.ADD, quantity="100", order_id="executes"),
        book_event(3, BookAction.ADD, quantity="100", order_id="cancels"),
        book_event(4, BookAction.EXECUTE, quantity="20", order_id="survives"),
        book_event(5, BookAction.EXECUTE, quantity="100", order_id="executes"),
        book_event(6, BookAction.CANCEL, quantity="100", order_id="cancels"),
        book_event(7, BookAction.ADD, quantity="100", order_id="replenishes"),
        book_event(8, BookAction.EXECUTE, quantity="60", order_id="replenishes"),
        book_event(9, BookAction.CANCEL, quantity="40", order_id="replenishes"),
    )
    book = OrderBook(INSTRUMENT)
    tracker = OrderLifecycleTracker()
    pre_snapshot: BookSnapshot | None = None
    for event_index, event in enumerate(events):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
        if event.sequence_number == 3:
            pre_snapshot = book.snapshot()
    lifecycles = tracker.close_observation(
        market_event_reference(events[-1]),
        event_index=8,
    )
    assert pre_snapshot is not None
    analysis = LiquidityCredibilityService(
        LiquidityCredibilityConfig(
            attack_depth_levels=1,
            post_shock_event_horizon=3,
        )
    ).analyze(
        shock=_shock_between(events[3], events[5]),
        book_mode=BookDataMode.MARKET_BY_ORDER,
        pre_shock_snapshot=pre_snapshot,
        lifecycles=lifecycles,
        reset_events=tracker.reset_events,
        shock_start_event_index=3,
        shock_end_event_index=5,
        observation_end_event_index=8,
        observation_end_event_reference=market_event_reference(events[-1]),
    )
    assert isinstance(analysis, LiquidityCredibilityResult)
    return analysis, lifecycles, pre_snapshot


def test_shock_survival_execution_withdrawal_and_replenishment_are_exact() -> None:
    """A surviving, executed, and cancelled order remain economically distinct."""
    result, _, _ = _survival_scenario()

    assert result.feature_version == LIQUIDITY_CREDIBILITY_VERSION
    assert result.raw_displayed_depth == Decimal("300")
    assert result.shock_executed_quantity == Decimal("120")
    assert result.shock_withdrawn_quantity == Decimal("100")
    assert result.surviving_quantity == Decimal("80")
    assert result.shock_executed_fraction == Decimal("0.4")
    assert result.shock_withdrawal_fraction == Decimal(1) / Decimal(3)
    assert result.order_survival_fraction == Decimal(1) / Decimal(3)
    assert result.quantity_survival_fraction == Decimal(4) / Decimal(15)
    assert result.replenishment_count == 1
    assert result.replenishment_quantity == Decimal("100")
    assert result.replenishment_executed_fraction == Decimal("0.6")
    assert result.replenishment_withdrawal_fraction == Decimal("0.4")
    assert result.absorption_cycle_count == 1
    assert result.available_at_process_time == result.observation_end_process_time
    assert result.observation_end_event_reference.process_time == result.available_at_process_time


def test_full_execution_scores_above_fast_full_cancellation() -> None:
    """The engineering prior rewards execution without calling withdrawal intent."""
    result, _, _ = _survival_scenario()
    by_order = {str(order.order_id): order for order in result.attacked_orders}
    executed = by_order["executes"]
    cancelled = by_order["cancels"]

    assert executed.shock_executed_fraction == Decimal(1)
    assert not executed.survived_shock
    assert executed.execution_component == Decimal(1)
    assert executed.survival_component == Decimal(1)
    assert cancelled.shock_withdrawal_fraction == Decimal(1)
    assert cancelled.execution_component == Decimal(0)
    assert cancelled.cancellation_component == Decimal(0)
    assert executed.order_credibility_score > cancelled.order_credibility_score


def test_qwoc_and_credible_depth_use_exact_quantity_weighting() -> None:
    """100 at .8 and 300 at .4 produce QWOC .5, not simple mean .6."""
    quantities = (Decimal("100"), Decimal("300"))
    scores = (Decimal("0.8"), Decimal("0.4"))

    credible_depth = calculate_credible_depth(quantities, scores)

    assert credible_depth == Decimal("200")
    assert calculate_quantity_weighted_order_credibility(quantities, scores) == Decimal("0.5")
    assert credible_depth / sum(quantities, Decimal(0)) == Decimal("0.5")


def test_mbp_returns_explicit_unavailable_result() -> None:
    """Aggregate data cannot fabricate order-lifecycle credibility."""
    shock = liquidity_shock()
    observation = shock.end_reference
    analysis = LiquidityCredibilityService().analyze(
        shock=shock,
        book_mode=BookDataMode.MARKET_BY_PRICE,
        pre_shock_snapshot=BookSnapshot(
            instrument_id=INSTRUMENT.instrument_id,
            venue=INSTRUMENT.exchange,
            exchange_time=shock.start_exchange_time,
            receive_time=shock.start_exchange_time,
            process_time=shock.start_process_time,
            sequence_number=0,
        ),
        lifecycles=(),
        reset_events=(),
        shock_start_event_index=0,
        shock_end_event_index=0,
        observation_end_event_index=0,
        observation_end_event_reference=observation,
    )

    assert isinstance(analysis, LiquidityCredibilityUnavailable)
    assert analysis.reasons == (LiquidityCredibilityUnavailableReason.MBO_REQUIRED,)


def test_post_horizon_feature_does_not_see_later_cancellation() -> None:
    """A cancellation at event 99 is invisible at the configured event-26 cutoff."""
    add = book_event(
        1,
        BookAction.ADD,
        quantity="100",
        order_id="future-cancel",
        sequence_stream_id=SHARED_STREAM_ID,
    )
    cancel = book_event(
        100,
        BookAction.CANCEL,
        quantity="100",
        order_id="future-cancel",
        sequence_stream_id=SHARED_STREAM_ID,
    )
    book = OrderBook(INSTRUMENT, sequence_stream_id=SHARED_STREAM_ID)
    tracker = OrderLifecycleTracker()
    book.apply(add)
    tracker.observe_accepted(add, event_index=0)
    pre_snapshot = book.snapshot()
    quotes = tuple(
        quote_event(sequence, sequence_stream_id=SHARED_STREAM_ID) for sequence in range(2, 100)
    )
    for quote in quotes:
        book.observe_non_book_event(quote)
    book.apply(cancel)
    tracker.observe_accepted(cancel, event_index=99)
    shock_reference = quotes[0]
    observation = quotes[25]
    shock = _shock_between(shock_reference, shock_reference)
    analysis = LiquidityCredibilityService(
        LiquidityCredibilityConfig(
            attack_depth_levels=1,
            post_shock_event_horizon=25,
        )
    ).analyze(
        shock=shock,
        book_mode=BookDataMode.MARKET_BY_ORDER,
        pre_shock_snapshot=pre_snapshot,
        lifecycles=tracker.completed_lifecycles,
        reset_events=tracker.reset_events,
        shock_start_event_index=1,
        shock_end_event_index=1,
        observation_end_event_index=26,
        observation_end_event_reference=market_event_reference(observation),
    )

    assert isinstance(analysis, LiquidityCredibilityResult)
    behavior = analysis.attacked_orders[0].behavior
    assert behavior.withdrawn_quantity == Decimal(0)
    assert behavior.unresolved_quantity == Decimal("100")
    assert behavior.terminal_reason is OrderLifecycleTerminalReason.OBSERVATION_END
    assert analysis.available_at_process_time == observation.process_time


def test_liquidity_credibility_delta_is_exact_and_non_directional() -> None:
    """LC .40 followed by .70 produces the descriptive change +.30."""
    assert calculate_delta_liquidity_credibility(
        Decimal("0.40"),
        Decimal("0.70"),
    ) == Decimal("0.30")


def test_valid_shock_pair_compares_only_matching_credibility_results() -> None:
    """The pair helper adds DeltaLC without changing failed-aggression contracts."""
    first, _, _ = _survival_scenario()
    second, _, _ = _survival_scenario()
    pair = ShockPair(
        pair_id=derive_shock_pair_id(
            first.shock_id,
            second.shock_id,
            SHOCK_PAIR_VERSION,
        ),
        instrument_id=INSTRUMENT.instrument_id,
        direction=ShockDirection.SELL,
        shock_1_id=first.shock_id,
        shock_2_id=second.shock_id,
        event_distance=1,
        exchange_seconds_distance=Decimal("0.1"),
        process_seconds_distance=Decimal("0.1"),
        aggression_ratio=Decimal(1),
        comparison_version=SHOCK_PAIR_VERSION,
    )

    comparison = compare_liquidity_credibility(pair, first, second)

    assert comparison.pair_id == pair.pair_id
    assert comparison.delta_liquidity_credibility == Decimal(0)
