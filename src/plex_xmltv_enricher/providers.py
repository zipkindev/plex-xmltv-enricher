from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import ProviderConfig
from .models import EpisodeCandidate, SeriesCandidate


class ProviderError(RuntimeError):
    pass


class MetadataProvider(ABC):
    name: str

    @abstractmethod
    def search_series(self, title: str, language: str, country: str) -> list[SeriesCandidate]:
        raise NotImplementedError

    @abstractmethod
    def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
        raise NotImplementedError


class JsonProvider(MetadataProvider):
    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        self.config = config
        self.base_url = str(config.base_url).rstrip("/")
        self.timeout = timeout

    def _json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> Any:
        query = "" if not params else "?" + urlencode(params)
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "plex-xmltv-enricher/0.3.0",
            **(headers or {}),
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path + query,
            headers=request_headers,
            method=method,
            data=data,
        )
        raw = b""
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                break
            except HTTPError as exc:
                if exc.code != 429 and not 500 <= exc.code < 600:
                    raise ProviderError(f"{self.name} request failed") from exc
                if attempt == 2:
                    raise ProviderError(f"{self.name} request failed after retries") from exc
                retry_after = exc.headers.get("Retry-After", "")
                delay = (
                    float(retry_after)
                    if retry_after.replace(".", "", 1).isdigit()
                    else 0.25 * (2 ** attempt)
                )
                time.sleep(min(max(delay, 0.0), 5.0))
            except (URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise ProviderError(f"{self.name} request failed after retries") from exc
                time.sleep(0.25 * (2 ** attempt))
        if len(raw) > 16 * 1024 * 1024:
            raise ProviderError(f"{self.name} response exceeded limit")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON") from exc


class TvMazeProvider(JsonProvider):
    name = "tvmaze"

    def search_series(self, title: str, language: str, country: str) -> list[SeriesCandidate]:
        rows = self._json("/search/shows", params={"q": title})
        result: list[SeriesCandidate] = []
        for row in rows if isinstance(rows, list) else []:
            show = row.get("show", {})
            network = show.get("network") or show.get("webChannel") or {}
            place = network.get("country") or {}
            result.append(
                SeriesCandidate(
                    provider=self.name,
                    series_id=str(show.get("id", "")),
                    name=str(show.get("name", "")),
                    language=show.get("language"),
                    country=place.get("code"),
                    network=network.get("name"),
                    premiered=show.get("premiered"),
                )
            )
        return [x for x in result if x.series_id and x.name]

    def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
        rows = self._json(f"/shows/{series_id}/episodes", params={"specials": "1"})
        result: list[EpisodeCandidate] = []
        for row in rows if isinstance(rows, list) else []:
            result.append(
                EpisodeCandidate(
                    provider=self.name,
                    episode_id=str(row.get("id", "")),
                    series_id=series_id,
                    name=str(row.get("name") or ""),
                    season=_integer(row.get("season")),
                    number=_integer(row.get("number")),
                    airdate=row.get("airdate"),
                )
            )
        return [x for x in result if x.episode_id]


class TmdbProvider(JsonProvider):
    name = "tmdb"

    @staticmethod
    def _locale(language: str, country: str | None = None) -> str:
        if "-" in language:
            return language
        region = country or {"de": "DE", "en": "US"}.get(language, language.upper())
        return f"{language}-{region}"

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key_env:
            raise ProviderError("tmdb api_key_env is not configured")
        token = os.environ.get(self.config.api_key_env, "").strip()
        if not token:
            raise ProviderError("tmdb credential environment variable is missing")
        return {"Authorization": f"Bearer {token}"}

    def search_series(self, title: str, language: str, country: str) -> list[SeriesCandidate]:
        data = self._json(
            "/search/tv",
            params={"query": title, "language": self._locale(language, country)},
            headers=self._headers(),
        )
        rows = data.get("results", []) if isinstance(data, dict) else []
        return [
            SeriesCandidate(
                provider=self.name,
                series_id=str(row.get("id", "")),
                name=str(row.get("name") or ""),
                aliases=(str(row.get("original_name")),) if row.get("original_name") else (),
                language=row.get("original_language"),
                country=(row.get("origin_country") or [None])[0],
                premiered=row.get("first_air_date"),
            )
            for row in rows
            if row.get("id") and row.get("name")
        ]

    def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
        headers = self._headers()
        details = self._json(
            f"/tv/{series_id}", params={"language": self._locale(language)}, headers=headers
        )
        result: list[EpisodeCandidate] = []
        for season in details.get("seasons", []) if isinstance(details, dict) else []:
            season_number = _integer(season.get("season_number"))
            if season_number is None:
                continue
            page = self._json(
                f"/tv/{series_id}/season/{season_number}",
                params={"language": self._locale(language)},
                headers=headers,
            )
            for row in page.get("episodes", []) if isinstance(page, dict) else []:
                result.append(
                    EpisodeCandidate(
                        provider=self.name,
                        episode_id=str(row.get("id", "")),
                        series_id=series_id,
                        name=str(row.get("name") or ""),
                        season=_integer(row.get("season_number")),
                        number=_integer(row.get("episode_number")),
                        airdate=row.get("air_date"),
                    )
                )
        return [x for x in result if x.episode_id]


class TheTvdbProvider(JsonProvider):
    name = "thetvdb"

    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        super().__init__(config, timeout)
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            if not self.config.api_key_env:
                raise ProviderError("thetvdb api_key_env is not configured")
            api_key = os.environ.get(self.config.api_key_env, "").strip()
            pin = os.environ.get(self.config.pin_env, "").strip() if self.config.pin_env else ""
            if not api_key:
                raise ProviderError("thetvdb credential environment variable is missing")
            body: dict[str, object] = {"apikey": api_key}
            if pin:
                body["pin"] = pin
            data = self._json("/login", method="POST", body=body)
            token = data.get("data", {}).get("token") if isinstance(data, dict) else None
            if not token:
                raise ProviderError("thetvdb login returned no token")
            self._token = str(token)
        return {"Authorization": f"Bearer {self._token}"}

    def search_series(self, title: str, language: str, country: str) -> list[SeriesCandidate]:
        data = self._json(
            "/search",
            params={"query": title, "type": "series", "country": country, "language": language},
            headers=self._headers(),
        )
        rows = data.get("data", []) if isinstance(data, dict) else []
        result: list[SeriesCandidate] = []
        for row in rows:
            aliases = row.get("aliases") or []
            result.append(
                SeriesCandidate(
                    provider=self.name,
                    series_id=str(row.get("tvdb_id") or row.get("id") or ""),
                    name=str(row.get("name") or ""),
                    aliases=tuple(str(x) for x in aliases if x),
                    language=row.get("primary_language"),
                    country=row.get("country"),
                    network=row.get("network"),
                    premiered=row.get("year"),
                )
            )
        return [x for x in result if x.series_id and x.name]

    def episodes(self, series_id: str, language: str) -> list[EpisodeCandidate]:
        result: list[EpisodeCandidate] = []
        page = 0
        while page < 100:
            data = self._json(
                f"/series/{series_id}/episodes/default/{language}",
                params={"page": str(page)},
                headers=self._headers(),
            )
            payload = data.get("data", {}) if isinstance(data, dict) else {}
            rows = payload.get("episodes", []) if isinstance(payload, dict) else []
            for row in rows:
                result.append(
                    EpisodeCandidate(
                        provider=self.name,
                        episode_id=str(row.get("id", "")),
                        series_id=series_id,
                        name=str(row.get("name") or ""),
                        season=_integer(row.get("seasonNumber")),
                        number=_integer(row.get("number")),
                        absolute_number=_integer(row.get("absoluteNumber")),
                        airdate=row.get("aired"),
                    )
                )
            links = data.get("links", {}) if isinstance(data, dict) else {}
            if not rows or not links.get("next"):
                break
            page += 1
        return [x for x in result if x.episode_id]


def _integer(value: object) -> int | None:
    try:
        return None if value is None else int(str(value))
    except (TypeError, ValueError):
        return None


def build_providers(configs: tuple[ProviderConfig, ...], timeout: float) -> list[MetadataProvider]:
    result: list[MetadataProvider] = []
    for row in configs:
        if not row.enabled:
            continue
        if row.name == "thetvdb":
            result.append(TheTvdbProvider(row, timeout))
        elif row.name == "tmdb":
            result.append(TmdbProvider(row, timeout))
        elif row.name == "tvmaze":
            result.append(TvMazeProvider(row, timeout))
    return result
