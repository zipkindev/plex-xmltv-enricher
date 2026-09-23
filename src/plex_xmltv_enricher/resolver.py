from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import re

from .config import Config, SeriesOverride
from .matching import normalize
from .models import EpisodeCandidate, ProgrammeFacts, Resolution, SeriesCandidate
from .providers import MetadataProvider, ProviderError
from .store import Store


class Resolver:
    def __init__(
        self,
        config: Config,
        store: Store,
        providers: list[MetadataProvider],
    ) -> None:
        self.config = config
        self.store = store
        self.providers = {provider.name: provider for provider in providers}
        self.overrides = {
            normalize(title): value for title, value in config.series_overrides
        }
        self._mapping_memory: dict[str, dict[str, object] | None] = {}
        self._catalog_memory: dict[tuple[str, str, str], list[EpisodeCandidate]] = {}
        self._series_lookups = 0

    def begin_refresh(self) -> None:
        self._series_lookups = 0

    @property
    def series_lookups(self) -> int:
        return self._series_lookups

    def resolve(self, facts: ProgrammeFacts) -> Resolution:
        fingerprint = _fingerprint(facts)
        title_key = normalize(facts.title) + "\x1f" + (facts.country or self.config.resolver_country)
        try:
            mapping = self._series_mapping(title_key, facts)
        except LookupBudgetExceeded:
            result = Resolution("unresolved", "series_lookup_budget_exhausted")
            self._record(fingerprint, result)
            return result
        if mapping is None:
            result = Resolution("unresolved", "series_not_resolved")
            self._record(fingerprint, result)
            return result
        provider_name = str(mapping["provider"])
        series_id = str(mapping["series_id"])
        provider = self.providers.get(provider_name)
        if provider is None:
            result = Resolution(
                "unresolved",
                "provider_unavailable",
                provider=provider_name,
                series_id=series_id,
            )
            self._record(fingerprint, result)
            return result
        episodes = self._episodes(
            provider,
            series_id,
            facts.language or self.config.resolver_language,
        )
        result = self._match_episode(facts, mapping, episodes)
        self._record(fingerprint, result)
        return result

    def _series_mapping(
        self, title_key: str, facts: ProgrammeFacts
    ) -> dict[str, object] | None:
        if title_key in self._mapping_memory:
            return self._mapping_memory[title_key]
        override = self.overrides.get(normalize(facts.title))
        if override is not None:
            mapping = self._override_mapping(title_key, facts.title, override)
            self._mapping_memory[title_key] = mapping
            return mapping
        stored = self.store.series_mapping(title_key)
        if stored is not None:
            self._mapping_memory[title_key] = stored
            return stored
        cached = self.store.series_search(title_key)
        if cached is None or not _fresh(
            cached[1],
            self.config.negative_ttl_hours if not cached[0] else self.config.catalog_ttl_hours,
        ):
            if self._series_lookups >= self.config.max_series_lookups_per_refresh:
                raise LookupBudgetExceeded
            self._series_lookups += 1
        candidates = self._series_candidates(title_key, facts)
        ranked = sorted(
            ((_series_score(facts, candidate, self.config), candidate) for candidate in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < self.config.minimum_series_confidence:
            return None
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        if ranked[0][0] - runner_up < self.config.ambiguity_margin:
            return None
        confidence, candidate = ranked[0]
        self.store.save_series_mapping(
            title_key,
            candidate.provider,
            candidate.series_id,
            candidate.name,
            "catalog",
            confidence,
            "provider-search",
        )
        discovered_mapping = self.store.series_mapping(title_key)
        self._mapping_memory[title_key] = discovered_mapping
        return discovered_mapping

    def _override_mapping(
        self, title_key: str, title: str, override: SeriesOverride
    ) -> dict[str, object]:
        self.store.save_series_mapping(
            title_key,
            override.provider,
            override.series_id,
            title,
            override.numbering,
            1.0,
            "configuration-override",
        )
        mapping = self.store.series_mapping(title_key)
        if mapping is None:
            raise RuntimeError("series override was not persisted")
        return mapping

    def _series_candidates(
        self, title_key: str, facts: ProgrammeFacts
    ) -> list[SeriesCandidate]:
        cached = self.store.series_search(title_key)
        ttl = self.config.negative_ttl_hours if cached and not cached[0] else self.config.catalog_ttl_hours
        if cached and _fresh(cached[1], ttl):
            return cached[0]
        candidates: list[SeriesCandidate] = []
        successful = 0
        for provider in self.providers.values():
            try:
                candidates.extend(
                    provider.search_series(
                        facts.title,
                        facts.language or self.config.resolver_language,
                        facts.country or self.config.resolver_country,
                    )
                )
                successful += 1
            except ProviderError:
                continue
        if successful:
            self.store.save_series_search(title_key, candidates)
            return candidates
        return cached[0] if cached else []

    def _episodes(
        self, provider: MetadataProvider, series_id: str, language: str
    ) -> list[EpisodeCandidate]:
        key = (provider.name, series_id, language)
        if key in self._catalog_memory:
            return self._catalog_memory[key]
        cached = self.store.episode_catalog(provider.name, series_id, language)
        if cached and _fresh(cached[1], self.config.catalog_ttl_hours):
            self._catalog_memory[key] = cached[0]
            return cached[0]
        try:
            episodes = provider.episodes(series_id, language)
        except ProviderError:
            episodes = cached[0] if cached else []
        else:
            self.store.save_episode_catalog(provider.name, series_id, language, episodes)
        self._catalog_memory[key] = episodes
        return episodes

    def _match_episode(
        self,
        facts: ProgrammeFacts,
        mapping: dict[str, object],
        episodes: list[EpisodeCandidate],
    ) -> Resolution:
        provider = str(mapping["provider"])
        series_id = str(mapping["series_id"])
        if not episodes:
            return Resolution(
                "unresolved", "episode_catalog_empty", provider=provider, series_id=series_id
            )
        scored = [
            (_episode_score(facts, episode, str(mapping["numbering"])), episode)
            for episode in episodes
        ]
        subtitle_key = normalize(facts.subtitle)
        exact_subtitles = [
            episode
            for episode in episodes
            if subtitle_key and normalize(episode.name) == subtitle_key
        ]
        exact_dates = [
            episode for episode in episodes if episode.airdate == facts.airing_date
        ]
        if len(exact_subtitles) == 1:
            scored = [
                (
                    max(score, 0.96) if episode == exact_subtitles[0] else score,
                    episode,
                )
                for score, episode in scored
            ]
        if len(exact_dates) == 1:
            scored = [
                (max(score, 0.96) if episode == exact_dates[0] else score, episode)
                for score, episode in scored
            ]
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if best_score < self.config.minimum_episode_confidence:
            return Resolution(
                "unresolved",
                "episode_below_confidence",
                best_score,
                provider,
                series_id,
            )
        if best_score - runner_up < self.config.ambiguity_margin:
            return Resolution(
                "ambiguous", "episode_tie", best_score, provider, series_id
            )
        if best.season is None or best.number is None or best.season < 0 or best.number <= 0:
            return Resolution(
                "unresolved", "episode_has_no_number", best_score, provider, series_id
            )
        return Resolution(
            "resolved",
            "provider-catalog",
            best_score,
            provider,
            series_id,
            best.episode_id,
            best.season,
            best.number,
            best.name or None,
            best.airdate,
        )

    def _record(self, fingerprint: str, result: Resolution) -> None:
        self.store.save_decision(
            fingerprint,
            result.status,
            result.reason,
            result.confidence,
            result.provider,
            result.series_id,
            result.episode_id,
            result.season,
            result.episode,
        )


def _series_score(facts: ProgrammeFacts, candidate: SeriesCandidate, config: Config) -> float:
    wanted = normalize(facts.title)
    names = [normalize(candidate.name), *(normalize(x) for x in candidate.aliases)]
    exact = wanted in names
    similarity = max((SequenceMatcher(None, wanted, name).ratio() for name in names), default=0.0)
    score = 0.78 if exact else 0.65 * similarity
    countries = {
        "DE": {"DE", "DEU", "GERMANY", "DEUTSCHLAND"},
        "US": {"US", "USA", "UNITED STATES"},
        "AU": {"AU", "AUS", "AUSTRALIA"},
        "CA": {"CA", "CAN", "CANADA"},
    }
    country = facts.country or config.resolver_country
    wanted_country = countries.get(country, {country})
    if candidate.country and candidate.country.upper() in wanted_country:
        score += 0.10
    language = normalize(candidate.language or "")
    wanted_language = normalize(facts.language or config.resolver_language)
    language_aliases = {
        "de": {"de", "deu", "ger", "german", "deutsch"},
        "en": {"en", "eng", "english"},
    }
    if language in language_aliases.get(wanted_language, {wanted_language}):
        score += 0.04
    network = normalize(candidate.network or "")
    if network and network in normalize(facts.channel_name):
        score += 0.08
    return min(score, 1.0)


def _episode_score(facts: ProgrammeFacts, episode: EpisodeCandidate, numbering: str) -> float:
    score = 0.0
    subtitle = normalize(facts.subtitle)
    episode_name = normalize(episode.name)
    if subtitle and episode_name:
        if subtitle == episode_name:
            score += 0.72
        else:
            score += 0.45 * SequenceMatcher(None, subtitle, episode_name).ratio()
    if episode.airdate and episode.airdate == facts.airing_date:
        score += 0.35
    if facts.production_year and episode.airdate and episode.airdate[:4].isdigit():
        if int(episode.airdate[:4]) == facts.production_year:
            score += 0.08
    season_match = re.fullmatch(
        r"S(\d{1,4})\s*E(\d{1,6})(?:/\d+)?", facts.onscreen_number, re.IGNORECASE
    )
    bare_match = re.fullmatch(r"E(\d{1,6})", facts.onscreen_number, re.IGNORECASE)
    if season_match and episode.season == int(season_match.group(1)) and episode.number == int(
        season_match.group(2)
    ):
        score += 1.0
    elif bare_match:
        number = int(bare_match.group(1))
        if (
            numbering == "year"
            and facts.production_year == episode.season
            and number == episode.number
        ):
            score += 1.0
        elif numbering == "absolute" and number == episode.absolute_number:
            score += 1.0
    return min(score, 1.0)


def _fingerprint(facts: ProgrammeFacts) -> str:
    value = "\x1f".join(
        (
            normalize(facts.title),
            normalize(facts.subtitle),
            facts.airing_date,
            facts.onscreen_number.casefold(),
            normalize(facts.channel_name),
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _fresh(timestamp: datetime, hours: int) -> bool:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timestamp <= timedelta(hours=hours)


class LookupBudgetExceeded(RuntimeError):
    pass
