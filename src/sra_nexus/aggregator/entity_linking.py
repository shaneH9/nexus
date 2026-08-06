"""Deterministic entity extraction and reference-data linking."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from sra_nexus.aggregator.entity_links import (
    EntityLinkAmbiguity,
    EntityLinkingResult,
    EntityLinkResult,
    EventEntityLink,
    UnresolvedEntityLink,
)
from sra_nexus.aggregator.enums import EntityMatchMethod, EventEntityRole, LinkAmbiguityKind
from sra_nexus.aggregator.normalization import normalize_comparison_text
from sra_nexus.aggregator.revisions import CanonicalEventRevision
from sra_nexus.common.models import ContractModel, UnitIntervalScore
from sra_nexus.common.types import EntityId
from sra_nexus.reference.enums import EntityType, ReferenceResolutionStatus
from sra_nexus.reference.models import Entity
from sra_nexus.reference.repositories import EntityRepository, InstrumentRepository
from sra_nexus.storage.raw import RawNewsRepository


class MissingRevisionSourceError(RuntimeError):
    """Raised when a canonical revision references unavailable raw provenance."""


class EntityLinkingConfig(ContractModel):
    """Centralized initial engineering confidence and relevance values."""

    provider_ticker_confidence: UnitIntervalScore = 1.0
    provider_entity_confidence: UnitIntervalScore = 0.95
    canonical_name_confidence: UnitIntervalScore = 0.90
    alias_confidence: UnitIntervalScore = 0.85
    exact_phrase_confidence: UnitIntervalScore = 0.80
    primary_relevance: UnitIntervalScore = 1.0
    secondary_relevance: UnitIntervalScore = 0.80


class DeterministicEntityLinker:
    """Link explicit metadata and exact phrases without statistical NLP."""

    def __init__(
        self,
        entity_repository: EntityRepository,
        instrument_repository: InstrumentRepository,
        raw_repository: RawNewsRepository,
        config: EntityLinkingConfig | None = None,
    ) -> None:
        """Configure deterministic reference repositories and engineering priors."""
        self._entities = entity_repository
        self._instruments = instrument_repository
        self._raw = raw_repository
        self._config = EntityLinkingConfig() if config is None else config

    def link_revision(self, revision: CanonicalEventRevision) -> EntityLinkingResult:
        """Link only evidence available in this exact canonical revision."""
        raw_items = []
        for news_id in revision.event.source_news_ids:
            item = self._raw.get(news_id)
            if item is None:
                raise MissingRevisionSourceError(
                    f"canonical revision references missing raw NewsId {news_id}"
                )
            if item.process_time > revision.available_at:
                raise MissingRevisionSourceError(
                    f"raw NewsId {news_id} is unavailable at revision available_at"
                )
            raw_items.append(item)

        selected: dict[EntityId, tuple[int, EntityLinkResult]] = {}
        ambiguities: list[EntityLinkAmbiguity] = []
        unresolved: list[UnresolvedEntityLink] = []
        primary_exists = False

        provider_tickers = _ordered_unique(
            ticker for item in raw_items for ticker in item.provider_tickers
        )
        for ticker in provider_tickers:
            resolution = self._instruments.resolve_ticker(ticker)
            if resolution.status is ReferenceResolutionStatus.AMBIGUOUS:
                ambiguities.append(
                    EntityLinkAmbiguity(
                        kind=LinkAmbiguityKind.TICKER,
                        matched_text=ticker,
                        match_method=EntityMatchMethod.PROVIDER_TICKER,
                        candidate_instrument_ids=tuple(
                            instrument.instrument_id for instrument in resolution.candidates
                        ),
                        explanation="Provider ticker matches multiple local instruments.",
                    )
                )
                continue
            instrument = resolution.instrument
            if instrument is None:
                unresolved.append(
                    UnresolvedEntityLink(
                        matched_text=ticker,
                        explanation=(
                            "Provider ticker is absent from local instrument reference data."
                        ),
                    )
                )
                continue
            entity_links = self._instruments.list_entity_links_for_instrument(
                instrument.instrument_id,
                revision.available_at,
            )
            entity_ids = tuple(dict.fromkeys(link.entity_id for link in entity_links))
            if len(entity_ids) > 1:
                ambiguities.append(
                    EntityLinkAmbiguity(
                        kind=LinkAmbiguityKind.ENTITY,
                        matched_text=ticker,
                        match_method=EntityMatchMethod.PROVIDER_TICKER,
                        candidate_entity_ids=entity_ids,
                        explanation=("Resolved instrument has multiple valid entity associations."),
                    )
                )
                continue
            if not entity_ids:
                unresolved.append(
                    UnresolvedEntityLink(
                        matched_text=ticker,
                        explanation=(
                            "Resolved instrument has no valid entity association at available_at."
                        ),
                    )
                )
                continue
            result = EntityLinkResult(
                entity_id=entity_ids[0],
                matched_text=ticker,
                match_method=EntityMatchMethod.PROVIDER_TICKER,
                confidence=self._config.provider_ticker_confidence,
                is_primary=True,
                explanation="Resolved provider ticker through an explicit entity-instrument link.",
            )
            _select_link(selected, 0, result)
            primary_exists = True

        for provider_name in _ordered_unique(
            name for item in raw_items for name in item.provider_entities
        ):
            entity_candidates, resolution_basis = self._resolve_name_and_alias(provider_name)
            if len(entity_candidates) > 1:
                ambiguities.append(
                    EntityLinkAmbiguity(
                        kind=LinkAmbiguityKind.ENTITY,
                        matched_text=provider_name,
                        match_method=EntityMatchMethod.PROVIDER_ENTITY,
                        candidate_entity_ids=tuple(
                            entity.entity_id for entity in entity_candidates
                        ),
                        explanation=("Provider entity metadata matches multiple local entities."),
                    )
                )
                continue
            if not entity_candidates:
                unresolved.append(
                    UnresolvedEntityLink(
                        matched_text=provider_name,
                        explanation="Provider entity metadata is absent from local reference data.",
                    )
                )
                continue
            entity = entity_candidates[0]
            is_primary = not primary_exists
            result = EntityLinkResult(
                entity_id=entity.entity_id,
                matched_text=provider_name,
                match_method=EntityMatchMethod.PROVIDER_ENTITY,
                confidence=self._config.provider_entity_confidence,
                is_primary=is_primary,
                explanation=f"Resolved provider entity metadata by exact {resolution_basis}.",
            )
            _select_link(selected, 1, result)
            primary_exists = primary_exists or is_primary

        context_values = (
            *revision.event.sectors,
            *revision.event.industries,
            *revision.event.countries,
            *revision.event.commodities,
            *revision.event.macro_factors,
        )
        for context_value in _ordered_unique(context_values):
            context_candidates, _ = self._resolve_name_and_alias(context_value)
            if len(context_candidates) > 1:
                ambiguities.append(
                    EntityLinkAmbiguity(
                        kind=LinkAmbiguityKind.ENTITY,
                        matched_text=context_value,
                        match_method=EntityMatchMethod.EXACT_PHRASE,
                        candidate_entity_ids=tuple(
                            entity.entity_id for entity in context_candidates
                        ),
                        explanation="Canonical event context matches multiple local entities.",
                    )
                )
            elif context_candidates:
                _select_link(
                    selected,
                    2,
                    EntityLinkResult(
                        entity_id=context_candidates[0].entity_id,
                        matched_text=context_value,
                        match_method=EntityMatchMethod.EXACT_PHRASE,
                        confidence=self._config.exact_phrase_confidence,
                        is_primary=False,
                        explanation="Resolved explicit canonical event context by exact phrase.",
                    ),
                )

        content = revision.event.headline_summary
        if revision.event.event_summary is not None:
            content = f"{content} {revision.event.event_summary}"
        phrase_matches = _find_reference_phrases(content, self._entities.list_entities())
        for match in phrase_matches:
            if len(match.entity_ids) > 1:
                ambiguities.append(
                    EntityLinkAmbiguity(
                        kind=LinkAmbiguityKind.ENTITY,
                        matched_text=match.text,
                        match_method=EntityMatchMethod.EXACT_PHRASE,
                        candidate_entity_ids=match.entity_ids,
                        explanation="Canonical content phrase matches multiple local entities.",
                    )
                )
                continue
            entity_id = match.entity_ids[0]
            is_primary = not primary_exists
            method = (
                EntityMatchMethod.CANONICAL_NAME
                if match.is_canonical_name
                else EntityMatchMethod.ALIAS
            )
            confidence = (
                self._config.canonical_name_confidence
                if match.is_canonical_name
                else self._config.alias_confidence
            )
            _select_link(
                selected,
                3 if match.is_canonical_name else 4,
                EntityLinkResult(
                    entity_id=entity_id,
                    matched_text=match.text,
                    match_method=method,
                    confidence=confidence,
                    is_primary=is_primary,
                    explanation=(
                        f"Matched exact {method.value.lower()} phrase in canonical content."
                    ),
                ),
            )
            primary_exists = primary_exists or is_primary

        matched_text_keys = {
            normalize_comparison_text(value[1].matched_text).replace(" ", "")
            for value in selected.values()
        }
        provider_ticker_keys = {
            normalize_comparison_text(ticker).replace(" ", "") for ticker in provider_tickers
        }
        for ticker_anchor in revision.ticker_anchors:
            ticker_key = normalize_comparison_text(ticker_anchor).replace(" ", "")
            if ticker_key in provider_ticker_keys or ticker_key in matched_text_keys:
                continue
            resolution = self._instruments.resolve_ticker(ticker_anchor)
            if resolution.status is ReferenceResolutionStatus.AMBIGUOUS:
                ambiguities.append(
                    EntityLinkAmbiguity(
                        kind=LinkAmbiguityKind.TICKER,
                        matched_text=ticker_anchor,
                        match_method=EntityMatchMethod.EXACT_PHRASE,
                        candidate_instrument_ids=tuple(
                            instrument.instrument_id for instrument in resolution.candidates
                        ),
                        explanation="Fallback ticker token matches multiple local instruments.",
                    )
                )
                continue
            instrument = resolution.instrument
            if instrument is None:
                unresolved.append(
                    UnresolvedEntityLink(
                        matched_text=ticker_anchor,
                        explanation="Fallback ticker token is absent from local reference data.",
                    )
                )
                continue
            entity_links = self._instruments.list_entity_links_for_instrument(
                instrument.instrument_id,
                revision.available_at,
            )
            entity_ids = tuple(dict.fromkeys(link.entity_id for link in entity_links))
            if len(entity_ids) > 1:
                ambiguities.append(
                    EntityLinkAmbiguity(
                        kind=LinkAmbiguityKind.ENTITY,
                        matched_text=ticker_anchor,
                        match_method=EntityMatchMethod.EXACT_PHRASE,
                        candidate_entity_ids=entity_ids,
                        explanation="Fallback ticker token maps to multiple valid entities.",
                    )
                )
                continue
            if not entity_ids:
                unresolved.append(
                    UnresolvedEntityLink(
                        matched_text=ticker_anchor,
                        explanation="Fallback ticker token has no valid entity association.",
                    )
                )
                continue
            is_primary = not primary_exists
            _select_link(
                selected,
                5,
                EntityLinkResult(
                    entity_id=entity_ids[0],
                    matched_text=ticker_anchor,
                    match_method=EntityMatchMethod.EXACT_PHRASE,
                    confidence=self._config.exact_phrase_confidence,
                    is_primary=is_primary,
                    explanation="Resolved fallback ticker token against local reference data.",
                ),
            )
            primary_exists = primary_exists or is_primary

        ordered_links = tuple(
            value[1]
            for value in sorted(
                selected.values(),
                key=lambda item: (item[0], str(item[1].entity_id)),
            )
        )
        return EntityLinkingResult(
            links=ordered_links,
            ambiguities=tuple(_deduplicate_models(ambiguities)),
            unresolved=tuple(_deduplicate_models(unresolved)),
        )

    def _resolve_name_and_alias(self, value: str) -> tuple[tuple[Entity, ...], str]:
        canonical = self._entities.resolve_canonical_name(value).candidates
        aliases = self._entities.resolve_alias(value).candidates
        candidates = tuple(
            sorted(
                {entity.entity_id: entity for entity in (*canonical, *aliases)}.values(),
                key=lambda entity: str(entity.entity_id),
            )
        )
        if canonical and aliases:
            basis = "canonical name and alias"
        elif canonical:
            basis = "canonical name"
        else:
            basis = "alias"
        return candidates, basis

    def materialize_event_links(
        self,
        revision: CanonicalEventRevision,
        result: EntityLinkingResult,
    ) -> tuple[EventEntityLink, ...]:
        """Convert resolved evidence into immutable revision-specific entity links."""
        links: list[EventEntityLink] = []
        for match in result.links:
            entity = self._entities.get_entity(match.entity_id)
            if entity is None:
                raise MissingRevisionSourceError(f"resolved EntityId {match.entity_id} is missing")
            role = _event_role(entity, match.is_primary)
            links.append(
                EventEntityLink(
                    event_id=revision.event.event_id,
                    revision_id=revision.revision_id,
                    revision_number=revision.revision_number,
                    entity_id=match.entity_id,
                    role=role,
                    relevance=(
                        self._config.primary_relevance
                        if match.is_primary
                        else self._config.secondary_relevance
                    ),
                    confidence=match.confidence,
                    is_direct=True,
                    matched_text=match.matched_text,
                    match_method=match.match_method,
                    explanation=match.explanation,
                    available_at=revision.available_at,
                )
            )
        return tuple(sorted(links, key=lambda link: str(link.entity_id)))


class _PhraseMatch(ContractModel):
    text: str = Field(min_length=1)
    entity_ids: tuple[EntityId, ...] = Field(min_length=1)
    is_canonical_name: bool


def _find_reference_phrases(content: str, entities: tuple[Entity, ...]) -> tuple[_PhraseMatch, ...]:
    normalized_content = f" {normalize_comparison_text(content)} "
    candidates: dict[str, list[tuple[EntityId, bool]]] = {}
    display_text: dict[str, str] = {}
    for entity in entities:
        for text, is_canonical in (
            (entity.canonical_name, True),
            *((alias, False) for alias in entity.aliases),
        ):
            key = normalize_comparison_text(text)
            if key and f" {key} " in normalized_content:
                candidates.setdefault(key, []).append((entity.entity_id, is_canonical))
                display_text.setdefault(key, text)

    matches: list[_PhraseMatch] = []
    for key in sorted(candidates, key=lambda value: (-len(value.split()), value)):
        values = candidates[key]
        entity_ids = tuple(sorted({entity_id for entity_id, _ in values}, key=str))
        matches.append(
            _PhraseMatch(
                text=display_text[key],
                entity_ids=entity_ids,
                is_canonical_name=(len(entity_ids) == 1 and any(flag for _, flag in values)),
            )
        )
    return tuple(matches)


def _event_role(entity: Entity, is_primary: bool) -> EventEntityRole:
    if is_primary:
        return EventEntityRole.PRIMARY_SUBJECT
    roles = {
        EntityType.COUNTRY: EventEntityRole.COUNTRY_CONTEXT,
        EntityType.GEOGRAPHIC_REGION: EventEntityRole.COUNTRY_CONTEXT,
        EntityType.SECTOR: EventEntityRole.SECTOR_CONTEXT,
        EntityType.INDUSTRY: EventEntityRole.INDUSTRY_CONTEXT,
        EntityType.COMMODITY: EventEntityRole.COMMODITY_CONTEXT,
        EntityType.REGULATOR: EventEntityRole.REGULATOR_CONTEXT,
        EntityType.CENTRAL_BANK: EventEntityRole.MACRO_CONTEXT,
        EntityType.ECONOMIC_INDICATOR: EventEntityRole.MACRO_CONTEXT,
    }
    return roles.get(entity.entity_type, EventEntityRole.SECONDARY_SUBJECT)


def _select_link(
    selected: dict[EntityId, tuple[int, EntityLinkResult]],
    priority: int,
    result: EntityLinkResult,
) -> None:
    current = selected.get(result.entity_id)
    if current is None or (priority, -result.confidence) < (current[0], -current[1].confidence):
        selected[result.entity_id] = (priority, result)


def _ordered_unique[T](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _deduplicate_models[T: ContractModel](values: Iterable[T]) -> tuple[T, ...]:
    deduplicated: dict[str, T] = {}
    for value in values:
        key = value.model_dump_json()
        deduplicated.setdefault(key, value)
    return tuple(deduplicated.values())
