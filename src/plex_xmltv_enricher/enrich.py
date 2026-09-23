from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET

from .config import Config
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


def normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", folded, flags=re.UNICODE).split())


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
    programme.append(node)


def _airing_date(programme: ET.Element) -> str:
    start = programme.get("start", "")
    match = re.match(r"^(\d{14})", start)
    if not match:
        raise FeedError("episodic programme lacks a valid start time")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")


def _eligible(programme: ET.Element, config: Config) -> tuple[bool, bool]:
    title = _text(programme, "title").casefold()
    categories = {(_text_node.text or "").strip().casefold() for _text_node in programme.findall("category")}
    is_movie = bool(categories & config.movie_categories)
    is_series = title in config.series_titles or bool(categories & config.series_categories)
    return is_series, is_movie


def enrich(source: bytes, config: Config, store: Store) -> Result:
    if len(source) > config.max_bytes:
        raise FeedError("source exceeds configured maximum")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise FeedError("source is not valid XML") from exc
    if root.tag != "tv":
        raise FeedError("source root is not tv")
    topology = channel_topology(root)
    enriched = preserved = refused_movies = 0
    programmes = root.findall("programme")
    for programme in programmes:
        if not programme.get("channel") or not _text(programme, "title"):
            raise FeedError("programme lacks channel or title")
        systems = {(x.get("system") or "").casefold(): (x.text or "").strip() for x in programme.findall("episode-num")}
        if "xmltv_ns" in systems or "original-air-date" in systems:
            preserved += 1
            continue
        eligible, movie = _eligible(programme, config)
        if movie:
            refused_movies += 1
            continue
        onscreen = systems.get("onscreen", "")
        season_episode = re.fullmatch(r"S(\d{1,4})\s*E(\d{1,6})", onscreen, re.IGNORECASE)
        bare_episode = re.fullmatch(r"E(\d{1,6})", onscreen, re.IGNORECASE)
        added = False
        if season_episode and eligible:
            season, episode = map(int, season_episode.groups())
            if season > 0 and episode > 0:
                _append(programme, "episode-num", f"{season-1}.{episode-1}.", system="xmltv_ns")
                added = True
        elif bare_episode and eligible:
            year = _text(programme, "date")
            episode = int(bare_episode.group(1))
            if re.fullmatch(r"(?:19|20)\d{2}", year) and episode > 0:
                _append(programme, "episode-num", f"{int(year)-1}.{episode-1}.", system="xmltv_ns")
                added = True
        elif eligible and _text(programme, "sub-title"):
            identity = normalize(_text(programme, "title"))+"\x1f"+normalize(_text(programme, "sub-title"))
            stable = store.stable_date(identity, _airing_date(programme))
            _append(programme, "episode-num", stable, system="original-air-date")
            added = True
        if added:
            if not any((x.text or "").strip().casefold() == "series" for x in programme.findall("category")):
                _append(programme, "category", "Series", lang="en")
            enriched += 1
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    reparsed = ET.fromstring(xml)
    if channel_topology(reparsed) != topology or len(reparsed.findall("programme")) != len(programmes):
        raise FeedError("serialization changed topology or programme count")
    return Result(xml, len(topology), len(programmes), enriched, preserved, refused_movies, topology_digest(topology))
