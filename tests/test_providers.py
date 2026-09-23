from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from plex_xmltv_enricher.config import ProviderConfig
from plex_xmltv_enricher.models import EpisodeCandidate, SeriesCandidate
from plex_xmltv_enricher.providers import JsonProvider


class StubProvider(JsonProvider):
    name = "stub"

    def search_series(
        self, title: str, language: str, country: str
    ) -> list[SeriesCandidate]:
        return []

    def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
        return []


class Response:
    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return b'{"ok":true}'


def test_json_provider_retries_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    headers = Message()
    headers["Retry-After"] = "0"

    def fake_urlopen(request: object, timeout: float) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError("https://example.invalid", 429, "rate limited", headers, BytesIO())
        return Response()

    monkeypatch.setattr("plex_xmltv_enricher.providers.urlopen", fake_urlopen)
    monkeypatch.setattr("plex_xmltv_enricher.providers.time.sleep", lambda _: None)
    provider = StubProvider(ProviderConfig("stub", True, base_url="https://example.invalid"), 1)
    assert provider._json("/test") == {"ok": True}
    assert calls == 2
