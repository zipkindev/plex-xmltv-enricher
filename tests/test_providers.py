from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from plex_xmltv_enricher.config import ProviderConfig
from plex_xmltv_enricher.models import EpisodeCandidate, SeriesCandidate
from plex_xmltv_enricher.providers import JsonProvider, TheTvdbProvider


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


def test_thetvdb_uses_iso_639_2_and_iso_3166_1_alpha_3(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = TheTvdbProvider(
        ProviderConfig(
            "thetvdb",
            True,
            api_key_env="TEST_TVDB_KEY",
            base_url="https://example.invalid",
        ),
        1,
    )
    provider._token = "test-token"
    requests: list[tuple[str, dict[str, str]]] = []

    def fake_json(path: str, **kwargs: object) -> object:
        params = kwargs.get("params")
        assert isinstance(params, dict)
        requests.append((path, params))
        if path == "/search":
            return {
                "data": [
                    {
                        "tvdb_id": "266543",
                        "name": "Das perfekte Dinner",
                        "primary_language": "deu",
                        "country": "deu",
                    }
                ]
            }
        return {"data": {"episodes": []}, "links": {}}

    monkeypatch.setattr(provider, "_json", fake_json)
    assert provider.search_series("Das perfekte Dinner", "de", "DE")[0].series_id == "266543"
    provider.episodes("266543", "de")
    assert requests[0][1]["language"] == "deu"
    assert requests[0][1]["country"] == "deu"
    assert requests[1][0].endswith("/default/deu")
