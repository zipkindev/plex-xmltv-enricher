from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import xml.etree.ElementTree as ET

from .config import Config
from .matching import normalize
from .models import ProgrammeFacts
from .resolver import Resolver
from .store import Store


class FeedError(ValueError):
    pass


@dataclass(frozen=True)
class Result:
    xml: bytes
    channels: int
    programmes: int
    enriched: int
    preserved: int
    refused_movies: int
    topology_hash: str
    source_normalized: int = 0
    provider_resolved: int = 0
    ambiguous: int = 0
    unresolved: int = 0
    non_episodic: int = 0
    series_lookups: int = 0
    identity_fallback: int = 0


def channel_topology(root: ET.Element) -> tuple[tuple[str, tuple[str, ...]], ...]:
    rows: list[tuple[str, tuple[str, ...]]] = []
    for channel in root.findall("channel"):
        identifier = channel.get("id")
        if not identifier:
            raise FeedError("channel lacks id")
        rows.append((identifier, tuple(x.text or "" for x in channel.findall("display-name"))))
    if not rows or len({x[0] for x in rows}) != len(rows):
        raise FeedError("channel topology is empty or duplicated")
    return tuple(rows)


def topology_digest(topology: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    return hashlib.sha256(repr(topology).encode()).hexdigest()


def _text(programme: ET.Element, tag: str) -> str:
    node = programme.find(tag)
    return "" if node is None or node.text is None else node.text.strip()


def _append(programme: ET.Element, tag: str, text: str, **attributes: str) -> None:
    node = ET.Element(tag, attributes)
    node.text = text
    order = {
        name: index
        for index, name in enumerate(
            (
                "title", "sub-title", "desc", "credits", "date", "category",
                "keyword", "language", "orig-language", "length", "icon", "url",
                "country", "episode-num", "video", "audio", "previously-shown",
                "premiere", "last-chance", "new", "subtitles", "rating",
                "star-rating", "review", "image",
            )
        )
    }
    rank = order.get(tag, len(order))
    for index, child in enumerate(programme):
        if order.get(child.tag, len(order)) > rank:
            programme.insert(index, node)
            return
    programme.append(node)


def _airing_date(programme: ET.Element) -> str:
    match = re.match(r"^(\d{8})", programme.get("start", ""))
    if not match:
        raise FeedError("programme lacks a valid start time")
    return datetime.strptime(match.group(1), "%Y%m%d").strftime("%Y-%m-%d")


def _airing_datetime(programme: ET.Element) -> str:
    match = re.match(r"^(\d{14})", programme.get("start", ""))
    if not match:
        raise FeedError("programme lacks a valid start time")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")


def _categories(programme: ET.Element) -> set[str]:
    return {
        (node.text or "").strip().casefold()
        for node in programme.findall("category")
        if (node.text or "").strip()
    }


def _contains(categories: set[str], words: tuple[str, ...]) -> bool:
    normalized = {normalize(category) for category in categories}
    return any(normalize(word) in category for word in words for category in normalized)


def _classification(programme: ET.Element, config: Config) -> str:
    categories = _categories(programme)
    title = normalize(_text(programme, "title"))
    if categories & config.movie_categories or _contains(
        categories, ("movie", "spielfilm", "kinofilm", "feature film")
    ):
        return "movie"
    if _contains(
        categories,
        (
            "news",
            "nachrichten",
            "weather",
            "wetter",
            "shopping",
            "werbesendung",
            "live sport",
            "live sports",
            "sports",
            "sport",
            "fußball",
            "football",
            "motorsport",
        ),
    ):
        return "non-episodic"
    if title in config.series_titles or bool(categories & config.series_categories):
        return "configured-series"
    return "unknown"


def _facts(
    programme: ET.Element,
    channel_names: dict[str, str],
    systems: dict[str, str],
) -> ProgrammeFacts:
    year_text = _text(programme, "date")
    production_year = int(year_text) if re.fullmatch(r"(?:19|20)\d{2}", year_text) else None
    channel_name = channel_names.get(programme.get("channel", ""), "")
    country_match = re.match(r"^([A-Z]{2})\s*[-:]", channel_name)
    country = country_match.group(1) if country_match else ""
    language = _text(programme, "language").casefold()
    if not language:
        language = {"DE": "de", "US": "en", "AU": "en", "CA": "en"}.get(country, "")
    return ProgrammeFacts(
        title=_text(programme, "title"),
        subtitle=_text(programme, "sub-title"),
        channel_name=channel_name,
        language=language,
        country=country,
        categories=tuple(sorted(_categories(programme))),
        production_year=production_year,
        airing_date=_airing_date(programme),
        onscreen_number=systems.get("onscreen", ""),
    )


def _add_series_category(programme: ET.Element) -> None:
    if not any(
        (node.text or "").strip().casefold() == "series"
        for node in programme.findall("category")
    ):
        _append(programme, "category", "Series", lang="en")


def _add_provider_resolution(programme: ET.Element, resolution: object) -> None:
    season = getattr(resolution, "season")
    episode = getattr(resolution, "episode")
    _append(programme, "episode-num", f"{season - 1}.{episode - 1}.", system="xmltv_ns")
    provider = getattr(resolution, "provider")
    episode_id = getattr(resolution, "episode_id")
    if provider and episode_id:
        system = {
            "thetvdb": "thetvdb.com",
            "tmdb": "themoviedb.org",
            "tvmaze": "tvmaze.com",
        }.get(provider, provider)
        _append(programme, "episode-num", f"episode/{episode_id}", system=system)
    if not _text(programme, "sub-title") and getattr(resolution, "episode_title"):
        _append(programme, "sub-title", getattr(resolution, "episode_title"))
    _add_series_category(programme)


def enrich(
    source: bytes,
    config: Config,
    store: Store,
    resolver: Resolver | None = None,
) -> Result:
    if len(source) > config.max_bytes:
        raise FeedError("source exceeds configured maximum")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise FeedError("source is not valid XML") from exc
    if root.tag != "tv":
        raise FeedError("source root is not tv")
    topology = channel_topology(root)
    channel_names = {
        identifier: (names[0] if names else "") for identifier, names in topology
    }
    programmes = root.findall("programme")
    enriched = preserved = refused_movies = 0
    source_normalized = provider_resolved = ambiguous = unresolved = non_episodic = 0
    identity_fallback = 0
    if resolver is not None:
        resolver.begin_refresh()
    for programme in programmes:
        if not programme.get("channel") or not _text(programme, "title"):
            raise FeedError("programme lacks channel or title")
        systems = {
            (node.get("system") or "onscreen").casefold(): (node.text or "").strip()
            for node in programme.findall("episode-num")
        }
        if "xmltv_ns" in systems or "original-air-date" in systems:
            preserved += 1
            continue
        classification = _classification(programme, config)
        if classification == "movie":
            refused_movies += 1
            continue
        if classification == "non-episodic":
            non_episodic += 1
            continue
        onscreen = systems.get("onscreen", "")
        structured = re.fullmatch(
            r"S(\d{1,4})\s*E(\d{1,6})(?:/\d+)?", onscreen, re.IGNORECASE
        )
        if structured:
            season, episode = map(int, structured.groups())
            if season > 0 and episode > 0:
                _append(
                    programme,
                    "episode-num",
                    f"{season - 1}.{episode - 1}.",
                    system="xmltv_ns",
                )
                _add_series_category(programme)
                enriched += 1
                source_normalized += 1
                continue
        provider_status = ""
        if (
            classification == "configured-series"
            and resolver is not None
            and config.resolver_enabled
        ):
            resolution = resolver.resolve(_facts(programme, channel_names, systems))
            if resolution.status == "resolved":
                _add_provider_resolution(programme, resolution)
                enriched += 1
                provider_resolved += 1
                continue
            provider_status = resolution.status
        bare = re.fullmatch(r"E(\d{1,6})", onscreen, re.IGNORECASE)
        year = _text(programme, "date")
        if classification == "configured-series" and bare and re.fullmatch(r"(?:19|20)\d{2}", year):
            episode = int(bare.group(1))
            if episode > 0:
                _append(
                    programme,
                    "episode-num",
                    f"{int(year) - 1}.{episode - 1}.",
                    system="xmltv_ns",
                )
                _add_series_category(programme)
                enriched += 1
                source_normalized += 1
                continue
        if classification == "configured-series" and _text(programme, "sub-title"):
            identity = normalize(_text(programme, "title")) + "\x1f" + normalize(
                _text(programme, "sub-title")
            )
            stable = store.stable_date(identity, _airing_datetime(programme))
            _append(programme, "episode-num", stable, system="original-air-date")
            _add_series_category(programme)
            enriched += 1
            identity_fallback += 1
            continue
        if provider_status == "ambiguous":
            ambiguous += 1
        else:
            unresolved += 1
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    reparsed = ET.fromstring(xml)
    if channel_topology(reparsed) != topology or len(reparsed.findall("programme")) != len(programmes):
        raise FeedError("serialization changed topology or programme count")
    return Result(
        xml=xml,
        channels=len(topology),
        programmes=len(programmes),
        enriched=enriched,
        preserved=preserved,
        refused_movies=refused_movies,
        topology_hash=topology_digest(topology),
        source_normalized=source_normalized,
        provider_resolved=provider_resolved,
        ambiguous=ambiguous,
        unresolved=unresolved,
        non_episodic=non_episodic,
        series_lookups=0 if resolver is None else resolver.series_lookups,
        identity_fallback=identity_fallback,
    )
