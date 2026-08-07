"""Deterministic price-level replenishment-episode tests."""

from decimal import Decimal

from tests.support.market_data import INSTRUMENT, book_event
from tests.support.sra import liquidity_shock

from sra_nexus.market_data import BookAction, BookEvent, BookSide, OrderBook
from sra_nexus.sra import (
    REPLENISHMENT_EPISODE_VERSION,
    OrderLifecycle,
    OrderLifecycleTracker,
    absorption_cycle_count,
    identify_replenishment_episodes,
    market_event_reference,
)


def _lifecycles(*events: BookEvent) -> tuple[OrderLifecycle, ...]:
    book = OrderBook(INSTRUMENT)
    tracker = OrderLifecycleTracker()
    for event_index, event in enumerate(events):
        book.apply(event)
        tracker.observe_accepted(event, event_index=event_index)
    boundary = book_event(len(events) + 1, BookAction.RESET)
    return tracker.close_observation(
        market_event_reference(boundary),
        event_index=len(events),
    )


def test_replenishment_episode_has_exact_delay_identity_and_execution() -> None:
    """A new same-price order after execution forms one attributable episode."""
    lifecycles = _lifecycles(
        book_event(1, BookAction.ADD, quantity="100", order_id="depleted"),
        book_event(2, BookAction.EXECUTE, quantity="100", order_id="depleted"),
        book_event(3, BookAction.ADD, quantity="100", order_id="replenished"),
        book_event(4, BookAction.EXECUTE, quantity="60", order_id="replenished"),
        book_event(5, BookAction.CANCEL, quantity="40", order_id="replenished"),
    )

    episodes = identify_replenishment_episodes(
        shock=liquidity_shock(),
        attacked_side=BookSide.BID,
        original_prices=(Decimal("100.00"),),
        lifecycles=lifecycles,
        shock_start_event_index=1,
        observation_end_event_index=4,
        episode_event_gap=2,
    )

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.feature_version == REPLENISHMENT_EPISODE_VERSION
    assert episode.quantity_added == Decimal("100")
    assert tuple(str(order_id) for order_id in episode.contributing_order_ids) == ("replenished",)
    assert episode.exchange_delay_seconds == Decimal("0.001")
    assert episode.process_delay_seconds == Decimal("0.001")
    assert episode.subsequent_executed_quantity == Decimal("60")
    assert episode.subsequent_withdrawn_quantity == Decimal("40")
    assert episode.executed_fraction == Decimal("0.60")
    assert episode.withdrawn_fraction == Decimal("0.40")
    assert episode.attribution_complete


def test_replenishment_withdrawal_fraction_uses_attributable_quantity() -> None:
    """Withdrawing 80 of a 100-unit new order produces exact fraction .80."""
    lifecycles = _lifecycles(
        book_event(1, BookAction.ADD, quantity="100", order_id="base"),
        book_event(2, BookAction.EXECUTE, quantity="100", order_id="base"),
        book_event(3, BookAction.ADD, quantity="100", order_id="new"),
        book_event(4, BookAction.CANCEL, quantity="80", order_id="new"),
    )

    episode = identify_replenishment_episodes(
        shock=liquidity_shock(),
        attacked_side=BookSide.BID,
        original_prices=(Decimal("100.00"),),
        lifecycles=lifecycles,
        shock_start_event_index=1,
        observation_end_event_index=3,
        episode_event_gap=2,
    )[0]

    assert episode.subsequent_withdrawn_quantity == Decimal("80")
    assert episode.withdrawn_fraction == Decimal("0.80")
    assert episode.executed_fraction == Decimal(0)


def test_repeated_execute_replenish_execute_cycles_are_deterministic() -> None:
    """Two replenish-then-execute episodes count as two price-level cycles."""
    lifecycles = _lifecycles(
        book_event(1, BookAction.ADD, quantity="100", order_id="base"),
        book_event(2, BookAction.EXECUTE, quantity="100", order_id="base"),
        book_event(3, BookAction.ADD, quantity="100", order_id="cycle-1"),
        book_event(4, BookAction.EXECUTE, quantity="100", order_id="cycle-1"),
        book_event(5, BookAction.ADD, quantity="100", order_id="cycle-2"),
        book_event(6, BookAction.EXECUTE, quantity="100", order_id="cycle-2"),
    )

    episodes = identify_replenishment_episodes(
        shock=liquidity_shock(),
        attacked_side=BookSide.BID,
        original_prices=(Decimal("100.00"),),
        lifecycles=lifecycles,
        shock_start_event_index=1,
        observation_end_event_index=5,
        episode_event_gap=2,
    )

    assert len(episodes) == 2
    assert tuple(episode.quantity_added for episode in episodes) == (
        Decimal("100"),
        Decimal("100"),
    )
    assert absorption_cycle_count(episodes) == 2


def test_older_episode_keeps_later_withdrawal_attribution() -> None:
    """Starting a later burst must not orphan an earlier order's remainder."""
    lifecycles = _lifecycles(
        book_event(1, BookAction.ADD, quantity="100", order_id="base"),
        book_event(2, BookAction.EXECUTE, quantity="100", order_id="base"),
        book_event(3, BookAction.ADD, quantity="100", order_id="older"),
        book_event(4, BookAction.EXECUTE, quantity="60", order_id="older"),
        book_event(5, BookAction.ADD, quantity="100", order_id="newer"),
        book_event(6, BookAction.CANCEL, quantity="40", order_id="older"),
        book_event(7, BookAction.EXECUTE, quantity="100", order_id="newer"),
    )

    episodes = identify_replenishment_episodes(
        shock=liquidity_shock(),
        attacked_side=BookSide.BID,
        original_prices=(Decimal("100.00"),),
        lifecycles=lifecycles,
        shock_start_event_index=1,
        observation_end_event_index=6,
        episode_event_gap=2,
    )

    assert len(episodes) == 2
    assert episodes[0].executed_fraction == Decimal("0.6")
    assert episodes[0].withdrawn_fraction == Decimal("0.4")
    assert episodes[1].executed_fraction == Decimal(1)


def test_modify_up_creates_episode_but_withholds_ambiguous_fractions() -> None:
    """A MODIFY increase is replenishment without false within-order attribution."""
    lifecycles = _lifecycles(
        book_event(1, BookAction.ADD, quantity="100", order_id="base"),
        book_event(2, BookAction.ADD, quantity="50", order_id="standing"),
        book_event(3, BookAction.EXECUTE, quantity="100", order_id="base"),
        book_event(4, BookAction.MODIFY, quantity="100", order_id="standing"),
    )

    episode = identify_replenishment_episodes(
        shock=liquidity_shock(),
        attacked_side=BookSide.BID,
        original_prices=(Decimal("100.00"),),
        lifecycles=lifecycles,
        shock_start_event_index=2,
        observation_end_event_index=3,
        episode_event_gap=2,
    )[0]

    assert episode.quantity_added == Decimal("50")
    assert not episode.attribution_complete
    assert episode.executed_fraction is None
    assert episode.withdrawn_fraction is None
