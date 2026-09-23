from pathlib import Path

import pytest

from plex_xmltv_enricher.config import Config
from plex_xmltv_enricher.enrich import FeedError
from plex_xmltv_enricher.service import FeedService


def cfg(tmp_path: Path) -> Config:
    return Config(
        "http://primary/xml",
        "http://fallback/xml",
        10,
        1024 * 1024,
        60,
        "127.0.0.1",
        9193,
        tmp_path,
        frozenset({"serie"}),
        frozenset(),
        frozenset({"movie"}),
        False,
    )


def xml(channel: str = "1") -> bytes:
    return (
        f'<tv><channel id="{channel}"><display-name>One</display-name></channel>'
        f'<programme channel="{channel}" start="20260923000000 +0000" '
        f'stop="20260923010000 +0000"><title>News</title></programme></tv>'
    ).encode()


def test_fallback_requires_established_primary_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = FeedService(cfg(tmp_path))

    def fetch(url: str) -> bytes:
        if "primary" in url:
            raise OSError("offline")
        return xml()

    monkeypatch.setattr(service, "_fetch", fetch)
    with pytest.raises(FeedError, match="before a primary topology"):
        service.refresh()


def test_fallback_rejects_changed_topology_and_keeps_last_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = FeedService(cfg(tmp_path))
    monkeypatch.setattr(service, "_fetch", lambda _: xml())
    first = service.refresh()
    assert service.output() == first.xml

    def fetch(url: str) -> bytes:
        if "primary" in url:
            raise OSError("offline")
        return xml("2")

    monkeypatch.setattr(service, "_fetch", fetch)
    with pytest.raises(FeedError, match="topology differs"):
        service.refresh()
    assert service.output() == first.xml


def test_fallback_with_same_topology_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = FeedService(cfg(tmp_path))
    monkeypatch.setattr(service, "_fetch", lambda _: xml())
    service.refresh()

    def fetch(url: str) -> bytes:
        if "primary" in url:
            raise OSError("offline")
        return xml()

    monkeypatch.setattr(service, "_fetch", fetch)
    result = service.refresh()
    assert result.channels == 1
    assert service.active_source == "fallback"
