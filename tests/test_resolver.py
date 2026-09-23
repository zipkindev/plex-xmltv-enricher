from dataclasses import replace
from pathlib import Path

from plex_xmltv_enricher.config import Config, SeriesOverride
from plex_xmltv_enricher.models import EpisodeCandidate, ProgrammeFacts, SeriesCandidate
from plex_xmltv_enricher.providers import MetadataProvider
from plex_xmltv_enricher.resolver import Resolver
from plex_xmltv_enricher.store import Store


class FakeProvider(MetadataProvider):
    name = "thetvdb"

    def __init__(self) -> None:
        self.searches = 0
        self.catalogs = 0

    def search_series(
        self, title: str, language: str, country: str
    ) -> list[SeriesCandidate]:
        self.searches += 1
        return [
            SeriesCandidate(
                "thetvdb",
                "266543",
                "Das perfekte Dinner",
                language="de",
                country="DE",
                network="VOX",
            )
        ]

    def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
        self.catalogs += 1
        return [
            EpisodeCandidate(
                "thetvdb",
                "9001",
                series_id,
                "Tag 3: Noah, Berlin",
                2026,
                121,
                airdate="2026-09-23",
            ),
            EpisodeCandidate(
                "thetvdb",
                "9002",
                series_id,
                "Tag 4: Ada, Berlin",
                2026,
                122,
                airdate="2026-09-24",
            ),
        ]


def cfg(tmp_path: Path, overrides: bool = False) -> Config:
    return Config(
        "http://primary/xml",
        None,
        10,
        1024 * 1024,
        60,
        "127.0.0.1",
        9193,
        tmp_path,
        frozenset(),
        frozenset(),
        frozenset({"movie"}),
        False,
        resolver_enabled=True,
        providers=(),
        series_overrides=(
            (("Das perfekte Dinner", SeriesOverride("thetvdb", "266543", "year")),)
            if overrides
            else ()
        ),
    )


def facts(subtitle: str = "Tag 3: Noah, Berlin", number: str = "E121") -> ProgrammeFacts:
    return ProgrammeFacts(
        "Das perfekte Dinner",
        subtitle,
        "DE - VOX",
        "de",
        "DE",
        ("kochdokusoap",),
        2026,
        "2026-09-23",
        number,
    )


def test_override_and_year_numbering_resolve_exact_episode(tmp_path: Path) -> None:
    provider = FakeProvider()
    resolver = Resolver(cfg(tmp_path, True), Store(tmp_path), [provider])
    result = resolver.resolve(facts())
    assert result.status == "resolved"
    assert (result.season, result.episode, result.episode_id) == (2026, 121, "9001")
    assert provider.searches == 0 and provider.catalogs == 1


def test_ambiguous_episode_is_not_enriched(tmp_path: Path) -> None:
    class Ambiguous(FakeProvider):
        def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
            return [
                EpisodeCandidate(
                    "thetvdb", "1", series_id, "Same", 1, 1, airdate="2026-09-23"
                ),
                EpisodeCandidate(
                    "thetvdb", "2", series_id, "Same", 1, 2, airdate="2026-09-23"
                ),
            ]

    result = Resolver(cfg(tmp_path, True), Store(tmp_path), [Ambiguous()]).resolve(
        facts("Same", "")
    )
    assert result.status == "ambiguous"


def test_series_search_and_catalog_are_reused_from_sqlite(tmp_path: Path) -> None:
    provider = FakeProvider()
    first = Resolver(cfg(tmp_path), Store(tmp_path), [provider]).resolve(facts())
    second = Resolver(cfg(tmp_path), Store(tmp_path), [provider]).resolve(facts())
    assert first.status == second.status == "resolved"
    assert provider.searches == 1
    assert provider.catalogs == 1


def test_no_series_match_stays_unresolved(tmp_path: Path) -> None:
    class Empty(FakeProvider):
        def search_series(
            self, title: str, language: str, country: str
        ) -> list[SeriesCandidate]:
            return []

    result = Resolver(cfg(tmp_path), Store(tmp_path), [Empty()]).resolve(facts())
    assert result.status == "unresolved"
    assert result.reason == "series_not_resolved"


def test_series_lookup_budget_resets_each_refresh(tmp_path: Path) -> None:
    provider = FakeProvider()
    config = replace(cfg(tmp_path), max_series_lookups_per_refresh=1)
    resolver = Resolver(config, Store(tmp_path), [provider])
    first = facts("Unknown", "")
    second = ProgrammeFacts(
        "Another Show", "Unknown", "DE - VOX", "de", "DE", ("serie",),
        2026, "2026-09-23", "",
    )
    resolver.begin_refresh()
    resolver.resolve(first)
    blocked = resolver.resolve(second)
    assert blocked.reason == "series_lookup_budget_exhausted"
    resolver.begin_refresh()
    assert resolver.resolve(second).reason != "series_lookup_budget_exhausted"


def test_series_cache_is_scoped_by_country(tmp_path: Path) -> None:
    class Regional(FakeProvider):
        def search_series(
            self, title: str, language: str, country: str
        ) -> list[SeriesCandidate]:
            self.searches += 1
            return [
                SeriesCandidate(
                    "thetvdb",
                    f"series-{country}",
                    title,
                    language=language,
                    country=country,
                )
            ]

        def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
            self.catalogs += 1
            return [
                EpisodeCandidate(
                    "thetvdb",
                    f"episode-{series_id}",
                    series_id,
                    "Pilot",
                    1,
                    1,
                    airdate="2026-09-23",
                )
            ]

    provider = Regional()
    resolver = Resolver(cfg(tmp_path), Store(tmp_path), [provider])
    german = facts("Pilot", "S01E01")
    american = ProgrammeFacts(
        german.title,
        german.subtitle,
        "US - Example",
        "en",
        "US",
        german.categories,
        german.production_year,
        german.airing_date,
        german.onscreen_number,
    )
    assert resolver.resolve(german).series_id == "series-DE"
    assert resolver.resolve(american).series_id == "series-US"
    assert provider.searches == 2
