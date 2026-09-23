from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgrammeFacts:
    title: str
    subtitle: str
    channel_name: str
    language: str
    country: str
    categories: tuple[str, ...]
    production_year: int | None
    airing_date: str
    onscreen_number: str


@dataclass(frozen=True)
class SeriesCandidate:
    provider: str
    series_id: str
    name: str
    aliases: tuple[str, ...] = ()
    language: str | None = None
    country: str | None = None
    network: str | None = None
    premiered: str | None = None


@dataclass(frozen=True)
class EpisodeCandidate:
    provider: str
    episode_id: str
    series_id: str
    name: str
    season: int | None
    number: int | None
    absolute_number: int | None = None
    airdate: str | None = None


@dataclass(frozen=True)
class Resolution:
    status: str
    reason: str
    confidence: float = 0.0
    provider: str | None = None
    series_id: str | None = None
    episode_id: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    original_air_date: str | None = None
