from dataclasses import replace
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from plex_xmltv_enricher.config import Config
from plex_xmltv_enricher.enrich import FeedError, enrich
from plex_xmltv_enricher.models import Resolution
from plex_xmltv_enricher.store import Store


def config(tmp_path: Path) -> Config:
    return Config(
        primary_url="http://primary/guide.xml", fallback_url="http://fallback/guide.xml",
        timeout_seconds=10, max_bytes=1024 * 1024, refresh_seconds=60,
        listen_host="127.0.0.1", port=9193, state_dir=tmp_path,
        series_categories=frozenset({"kochdokusoap", "serie"}),
        series_titles=frozenset({"first dates ein tisch für zwei"}),
        movie_categories=frozenset({"spielfilm", "movie"}), test_tuner_enabled=False,
    )


def feed(programmes: str) -> bytes:
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<tv><channel id="1049"><display-name>DE - VOX</display-name></channel>
<channel id="1029"><display-name>DE - RTL</display-name></channel>""" + programmes + "</tv>").encode()


def programme(extra: str, *, title: str = "Das perfekte Dinner", subtitle: str = "Tag 3: Noah") -> str:
    return f"""<programme channel="1049" start="20260923170000 +0000" stop="20260923180000 +0000">
<title>{title}</title><sub-title>{subtitle}</sub-title><desc>Original description</desc>
<category>Kochdokusoap</category>{extra}</programme>"""


def parsed(result: bytes) -> ET.Element:
    return ET.fromstring(result).find("programme")  # type: ignore[return-value]


def test_bare_episode_uses_year_as_season_without_e121_guess(tmp_path: Path) -> None:
    source = feed(programme("<date>2026</date><episode-num system=\"onscreen\">E121</episode-num>"))
    result = enrich(source, config(tmp_path), Store(tmp_path))
    node = parsed(result.xml)
    values = {(x.get("system"), x.text) for x in node.findall("episode-num")}
    assert ("onscreen", "E121") in values
    assert ("xmltv_ns", "2025.120.") in values
    assert all(value != "0.20." for _, value in values)
    assert result.enriched == 1


def test_sxxexx_is_converted_to_zero_based_xmltv_ns(tmp_path: Path) -> None:
    source = feed(programme('<episode-num system="onscreen">S03E08</episode-num>'))
    node = parsed(enrich(source, config(tmp_path), Store(tmp_path)).xml)
    assert node.find("episode-num[@system='xmltv_ns']").text == "2.7."  # type: ignore[union-attr]


def test_sxx_exx_with_total_is_normalized_without_category_guess(tmp_path: Path) -> None:
    source = feed(
        programme(
            '<episode-num system="onscreen">S10 E7/16</episode-num>',
            title="Hubert ohne Staller",
        ).replace("<category>Kochdokusoap</category>", "<category>Krimiserie</category>")
    )
    node = parsed(enrich(source, config(tmp_path), Store(tmp_path)).xml)
    assert node.find("episode-num[@system='xmltv_ns']").text == "9.6."  # type: ignore[union-attr]


def test_subtitle_without_episode_evidence_uses_stable_identity(tmp_path: Path) -> None:
    first = feed(programme(""))
    second = feed(programme("").replace("20260923170000", "20260924170000"))
    store = Store(tmp_path)
    one = enrich(first, config(tmp_path), store)
    two = enrich(second, config(tmp_path), store)
    first_num = parsed(one.xml).find("episode-num[@system='original-air-date']")
    second_num = parsed(two.xml).find("episode-num[@system='original-air-date']")
    assert first_num is not None and second_num is not None and first_num.text == second_num.text
    assert one.identity_fallback == 1 and two.identity_fallback == 1


def test_movie_category_refuses_enrichment(tmp_path: Path) -> None:
    source = feed(programme("<category>Spielfilm</category><date>2026</date><episode-num system=\"onscreen\">E121</episode-num>"))
    result = enrich(source, config(tmp_path), Store(tmp_path))
    node = parsed(result.xml)
    assert node.find("episode-num[@system='xmltv_ns']") is None
    assert result.refused_movies == 1


def test_existing_recognized_marker_is_preserved(tmp_path: Path) -> None:
    source = feed(programme('<episode-num system="xmltv_ns">1.2.</episode-num>'))
    result = enrich(source, config(tmp_path), Store(tmp_path))
    assert result.enriched == 0 and result.preserved == 1
    assert parsed(result.xml).find("episode-num").text == "1.2."  # type: ignore[union-attr]


def test_channel_topology_and_protected_fields_are_preserved(tmp_path: Path) -> None:
    source = feed(programme("<date>2026</date><episode-num system=\"onscreen\">E121</episode-num>"))
    before = ET.fromstring(source)
    result = enrich(source, config(tmp_path), Store(tmp_path))
    after = ET.fromstring(result.xml)
    assert [(x.get("id"), [n.text for n in x.findall("display-name")]) for x in before.findall("channel")] == [(x.get("id"), [n.text for n in x.findall("display-name")]) for x in after.findall("channel")]
    fields = ("title", "sub-title", "desc", "date")
    assert {f: before.findtext("programme/"+f) for f in fields} == {f: after.findtext("programme/"+f) for f in fields}
    assert before.find("programme").attrib == after.find("programme").attrib  # type: ignore[union-attr]


def test_inserted_elements_follow_xmltv_dtd_order(tmp_path: Path) -> None:
    source = feed(
        programme(
            '<date>2026</date><episode-num system="onscreen">E121</episode-num>'
            "<video><present>yes</present></video><rating><value>12</value></rating>"
        )
    )
    node = parsed(enrich(source, config(tmp_path), Store(tmp_path)).xml)
    tags = [child.tag for child in node]
    assert tags.index("category") < tags.index("episode-num") < tags.index("video")


def test_provider_resolution_is_append_only(tmp_path: Path) -> None:
    class StubResolver:
        series_lookups = 0

        def begin_refresh(self) -> None:
            pass

        def resolve(self, facts: object) -> Resolution:
            return Resolution(
                "resolved",
                "provider-catalog",
                1.0,
                "tvmaze",
                "58601",
                "3705532",
                11,
                11,
                "Das Wiedersehen",
                "2026-11-03",
            )

    source = feed(programme("", title="Das Sommerhaus der Stars", subtitle="")).replace(
        b"<sub-title></sub-title>", b""
    )
    cfg = replace(config(tmp_path), resolver_enabled=True)
    node = parsed(enrich(source, cfg, Store(tmp_path), StubResolver()).xml)  # type: ignore[arg-type]
    values = {(x.get("system"), x.text) for x in node.findall("episode-num")}
    assert ("xmltv_ns", "10.10.") in values
    assert ("tvmaze.com", "episode/3705532") in values
    assert node.findtext("sub-title") == "Das Wiedersehen"


def test_unknown_classification_does_not_call_provider(tmp_path: Path) -> None:
    class StubResolver:
        calls = 0
        series_lookups = 0

        def begin_refresh(self) -> None:
            pass

        def resolve(self, facts: object) -> Resolution:
            self.calls += 1
            raise AssertionError("unclassified programme reached provider resolver")

    source = feed(
        programme("", title="Unclassified Programme").replace(
            "<category>Kochdokusoap</category>", ""
        )
    )
    cfg = replace(config(tmp_path), resolver_enabled=True)
    resolver = StubResolver()
    result = enrich(source, cfg, Store(tmp_path), resolver)  # type: ignore[arg-type]
    assert resolver.calls == 0
    assert result.unresolved == 1
    assert result.series_lookups == 0


def test_configured_series_uses_stable_identity_after_provider_miss(tmp_path: Path) -> None:
    class StubResolver:
        series_lookups = 1

        def begin_refresh(self) -> None:
            pass

        def resolve(self, facts: object) -> Resolution:
            return Resolution("unresolved", "episode_below_confidence")

    cfg = replace(config(tmp_path), resolver_enabled=True)
    source = feed(
        programme(
            "",
            title="First Dates – Ein Tisch für zwei",
            subtitle="U. a. mit: Steffi und Frank",
        )
    )
    store = Store(tmp_path)
    first = enrich(source, cfg, store, StubResolver())  # type: ignore[arg-type]
    second_source = source.replace(b"20260923160000", b"20260924160000")
    second = enrich(second_source, cfg, store, StubResolver())  # type: ignore[arg-type]
    first_node = ET.fromstring(first.xml).find("programme/episode-num")
    second_node = ET.fromstring(second.xml).find("programme/episode-num")
    assert first.identity_fallback == 1
    assert first_node is not None and first_node.get("system") == "original-air-date"
    assert second_node is not None and second_node.text == first_node.text


def test_previously_shown_date_is_passed_to_resolver(tmp_path: Path) -> None:
    class StubResolver:
        calls: list[object] = []
        series_lookups = 0

        def begin_refresh(self) -> None:
            pass

        def resolve(self, facts: object) -> Resolution:
            self.calls.append(facts)
            return Resolution("unresolved", "episode_below_confidence")

    source = feed(programme('<previously-shown start="20240221000000 +0100"/>'))
    cfg = replace(config(tmp_path), resolver_enabled=True)
    resolver = StubResolver()
    enrich(source, cfg, Store(tmp_path), resolver)  # type: ignore[arg-type]
    assert len(resolver.calls) == 1
    assert getattr(resolver.calls[0], "original_air_date") == "2024-02-21"


@pytest.mark.parametrize("source", [b"", b"<tv>", b"<not-tv />", b"<tv><channel /></tv>"])
def test_malformed_or_incomplete_feed_is_rejected(tmp_path: Path, source: bytes) -> None:
    with pytest.raises(FeedError):
        enrich(source, config(tmp_path), Store(tmp_path))
