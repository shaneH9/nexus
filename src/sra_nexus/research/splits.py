"""Chronological expanding and rolling splits with purging and embargo."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from sra_nexus.common.models import (
    ContractModel,
    NonBlankStr,
    NonNegativeDecimal,
    UtcDatetime,
)
from sra_nexus.common.types import ResearchObservationId, ResearchSplitId
from sra_nexus.research.dataset import ResearchObservation
from sra_nexus.research.enums import WalkForwardMode
from sra_nexus.research.models import WALK_FORWARD_SPLIT_VERSION
from sra_nexus.sra.state import elapsed_decimal_seconds

_RESEARCH_SPLIT_NAMESPACE = UUID("2f737261-2d73-706c-6974-726368763100")


class WalkForwardConfig(ContractModel):
    """Explicit chronological window, purge horizon, and pre-test embargo policy."""

    mode: WalkForwardMode = WalkForwardMode.EXPANDING
    minimum_train_observations: int = Field(default=100, gt=0)
    rolling_train_observations: int | None = Field(default=None, gt=0)
    test_observations: int = Field(default=50, gt=0)
    step_observations: int | None = Field(default=None, gt=0)
    maximum_label_horizon_events: int = Field(default=250, gt=0)
    embargo_event_count: int = Field(default=0, ge=0)
    embargo_exchange_seconds: NonNegativeDecimal | None = None
    split_version: NonBlankStr = WALK_FORWARD_SPLIT_VERSION

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        """Require a trailing-window length only for rolling evaluation."""
        if self.mode is WalkForwardMode.ROLLING:
            if self.rolling_train_observations is None:
                raise ValueError("rolling mode requires rolling_train_observations")
            if self.rolling_train_observations < self.minimum_train_observations:
                raise ValueError("rolling train window cannot be shorter than minimum train")
        elif self.rolling_train_observations is not None:
            raise ValueError("expanding mode cannot configure a rolling train window")
        return self


class WalkForwardSplit(ContractModel):
    """One typed chronological fold with explicit excluded observations."""

    split_id: ResearchSplitId
    mode: WalkForwardMode
    train_start: UtcDatetime
    train_end: UtcDatetime
    test_start: UtcDatetime
    test_end: UtcDatetime
    train_observation_ids: tuple[ResearchObservationId, ...]
    test_observation_ids: tuple[ResearchObservationId, ...]
    purged_observation_ids: tuple[ResearchObservationId, ...]
    embargoed_observation_ids: tuple[ResearchObservationId, ...]
    maximum_label_horizon_events: int = Field(gt=0)
    embargo_event_count: int = Field(ge=0)
    embargo_exchange_seconds: NonNegativeDecimal | None
    split_version: NonBlankStr = WALK_FORWARD_SPLIT_VERSION

    @model_validator(mode="after")
    def validate_split(self) -> Self:
        """Require nonempty disjoint regions and chronological boundaries."""
        if not self.train_observation_ids or not self.test_observation_ids:
            raise ValueError("walk-forward split requires nonempty train and test regions")
        if self.train_start > self.train_end or self.test_start > self.test_end:
            raise ValueError("walk-forward region boundaries cannot regress")
        if self.train_end > self.test_start:
            raise ValueError("training boundary cannot follow test boundary")
        groups = (
            set(self.train_observation_ids),
            set(self.test_observation_ids),
            set(self.purged_observation_ids),
            set(self.embargoed_observation_ids),
        )
        if any(left & right for index, left in enumerate(groups) for right in groups[index + 1 :]):
            raise ValueError("train, test, purge, and embargo identities must be disjoint")
        return self


class WalkForwardSplitter:
    """Create chronological folds without any random train/test helper."""

    def __init__(self, config: WalkForwardConfig | None = None) -> None:
        """Configure expanding or rolling folds and leakage controls."""
        self._config = WalkForwardConfig() if config is None else config

    @property
    def config(self) -> WalkForwardConfig:
        """Return the immutable split policy."""
        return self._config

    def split(self, observations: Sequence[ResearchObservation]) -> tuple[WalkForwardSplit, ...]:
        """Return every complete chronological fold after purge and embargo."""
        ordered = tuple(sorted(observations, key=_observation_key))
        if len({item.observation_id for item in ordered}) != len(ordered):
            raise ValueError("walk-forward observations must have unique identities")
        _validate_market_chronology(ordered)
        if any(
            item.maximum_label_horizon_events != self._config.maximum_label_horizon_events
            for item in ordered
        ):
            raise ValueError("split maximum label horizon must match every observation")
        step = self._config.step_observations or self._config.test_observations
        cursor = self._config.minimum_train_observations
        folds: list[WalkForwardSplit] = []
        fold_number = 0
        while cursor < len(ordered):
            raw_test = ordered[cursor : cursor + self._config.test_observations]
            if not raw_test:
                break
            raw_train = _training_window(ordered, cursor, self._config)
            retained_test, embargoed = _apply_embargo(raw_train, raw_test, self._config)
            if retained_test:
                retained_train, purged = _purge_overlapping_labels(raw_train, retained_test)
                if retained_train:
                    folds.append(
                        _materialize_split(
                            retained_train,
                            retained_test,
                            purged,
                            embargoed,
                            raw_train,
                            self._config,
                            fold_number,
                        )
                    )
                    fold_number += 1
            cursor += step
        return tuple(folds)


def _training_window(
    ordered: tuple[ResearchObservation, ...],
    cursor: int,
    config: WalkForwardConfig,
) -> tuple[ResearchObservation, ...]:
    if config.mode is WalkForwardMode.EXPANDING:
        return ordered[:cursor]
    width = _required(config.rolling_train_observations)
    return ordered[max(0, cursor - width) : cursor]


def _apply_embargo(
    train: tuple[ResearchObservation, ...],
    test: tuple[ResearchObservation, ...],
    config: WalkForwardConfig,
) -> tuple[tuple[ResearchObservation, ...], tuple[ResearchObservation, ...]]:
    last_train = _latest_by_market(train)
    retained: list[ResearchObservation] = []
    excluded: list[ResearchObservation] = []
    for observation in test:
        prior = last_train.get(_market_key(observation))
        if prior is None:
            retained.append(observation)
            continue
        event_distance = (
            observation.prediction_anchor_event_index - prior.prediction_anchor_event_index
        )
        within_event_embargo = 0 < event_distance <= config.embargo_event_count
        within_time_embargo = False
        if config.embargo_exchange_seconds is not None:
            elapsed = elapsed_decimal_seconds(
                prior.prediction_anchor_exchange_time,
                observation.prediction_anchor_exchange_time,
            )
            within_time_embargo = elapsed <= config.embargo_exchange_seconds
        if within_event_embargo or within_time_embargo:
            excluded.append(observation)
        else:
            retained.append(observation)
    return tuple(retained), tuple(excluded)


def _purge_overlapping_labels(
    train: tuple[ResearchObservation, ...],
    test: tuple[ResearchObservation, ...],
) -> tuple[tuple[ResearchObservation, ...], tuple[ResearchObservation, ...]]:
    first_test_index: dict[tuple[str, str], int] = {}
    for observation in test:
        key = _market_key(observation)
        first_test_index[key] = min(
            first_test_index.get(key, observation.prediction_anchor_event_index),
            observation.prediction_anchor_event_index,
        )
    retained: list[ResearchObservation] = []
    purged: list[ResearchObservation] = []
    for observation in train:
        boundary = first_test_index.get(_market_key(observation))
        if boundary is not None and observation.label_window_end_event_index >= boundary:
            purged.append(observation)
        else:
            retained.append(observation)
    return tuple(retained), tuple(purged)


def _materialize_split(
    train: tuple[ResearchObservation, ...],
    test: tuple[ResearchObservation, ...],
    purged: tuple[ResearchObservation, ...],
    embargoed: tuple[ResearchObservation, ...],
    raw_train: tuple[ResearchObservation, ...],
    config: WalkForwardConfig,
    fold_number: int,
) -> WalkForwardSplit:
    identity = "|".join(
        (
            config.split_version,
            str(fold_number),
            *(str(item.observation_id) for item in train),
            "TEST",
            *(str(item.observation_id) for item in test),
        )
    )
    return WalkForwardSplit(
        split_id=ResearchSplitId(uuid5(_RESEARCH_SPLIT_NAMESPACE, identity)),
        mode=config.mode,
        train_start=raw_train[0].prediction_anchor_process_time,
        train_end=raw_train[-1].prediction_anchor_process_time,
        test_start=test[0].prediction_anchor_process_time,
        test_end=test[-1].prediction_anchor_process_time,
        train_observation_ids=tuple(item.observation_id for item in train),
        test_observation_ids=tuple(item.observation_id for item in test),
        purged_observation_ids=tuple(item.observation_id for item in purged),
        embargoed_observation_ids=tuple(item.observation_id for item in embargoed),
        maximum_label_horizon_events=config.maximum_label_horizon_events,
        embargo_event_count=config.embargo_event_count,
        embargo_exchange_seconds=config.embargo_exchange_seconds,
        split_version=config.split_version,
    )


def _latest_by_market(
    observations: tuple[ResearchObservation, ...],
) -> dict[tuple[str, str], ResearchObservation]:
    result: dict[tuple[str, str], ResearchObservation] = {}
    for observation in observations:
        key = _market_key(observation)
        current = result.get(key)
        if current is None or observation.prediction_anchor_event_index > (
            current.prediction_anchor_event_index
        ):
            result[key] = observation
    return result


def _market_key(observation: ResearchObservation) -> tuple[str, str]:
    return str(observation.instrument_id), observation.venue


def _observation_key(observation: ResearchObservation) -> tuple[object, str, int, str]:
    return (
        observation.prediction_anchor_process_time,
        str(observation.instrument_id),
        observation.prediction_anchor_event_index,
        str(observation.observation_id),
    )


def _validate_market_chronology(
    observations: tuple[ResearchObservation, ...],
) -> None:
    previous_by_market: dict[tuple[str, str], ResearchObservation] = {}
    for observation in observations:
        key = _market_key(observation)
        previous = previous_by_market.get(key)
        if previous is not None and observation.prediction_anchor_event_index <= (
            previous.prediction_anchor_event_index
        ):
            raise ValueError("walk-forward event indices must increase within each market")
        previous_by_market[key] = observation


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("required walk-forward configuration is unavailable")
    return value
