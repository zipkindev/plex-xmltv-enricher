from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    primary_url: str
    fallback_url: str | None
    timeout_seconds: float
    max_bytes: int
    refresh_seconds: int
    listen_host: str
    port: int
    state_dir: Path
    series_categories: frozenset[str]
    series_titles: frozenset[str]
    movie_categories: frozenset[str]
    test_tuner_enabled: bool


def _url(value: object, name: str, *, required: bool) -> str | None:
    text = str(value or "").strip()
    if not text and not required:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ConfigError(f"{name} must be an http(s) URL without embedded credentials")
    return text


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError("configuration file is missing")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    source = data.get("source", {})
    server = data.get("server", {})
    storage = data.get("storage", {})
    enrichment = data.get("enrichment", {})
    test_tuner = data.get("test_tuner", {})
    primary = _url(source.get("primary_url"), "primary_url", required=True)
    fallback = _url(source.get("fallback_url"), "fallback_url", required=False)
    timeout = float(source.get("timeout_seconds", 30))
    max_bytes = int(source.get("max_bytes", 32 * 1024 * 1024))
    refresh = int(source.get("refresh_seconds", 900))
    port = int(server.get("port", 9193))
    state_dir = Path(str(storage.get("state_dir", "/state"))).resolve()
    if not 1 <= timeout <= 120:
        raise ConfigError("timeout_seconds must be between 1 and 120")
    if not 1024 <= max_bytes <= 256 * 1024 * 1024:
        raise ConfigError("max_bytes is outside the safe range")
    if not 30 <= refresh <= 86400:
        raise ConfigError("refresh_seconds must be between 30 and 86400")
    if not 1 <= port <= 65535:
        raise ConfigError("port is invalid")
    if primary == fallback:
        raise ConfigError("primary and fallback URLs must differ")
    return Config(
        primary_url=str(primary), fallback_url=fallback, timeout_seconds=timeout,
        max_bytes=max_bytes, refresh_seconds=refresh,
        listen_host=str(server.get("listen_host", "0.0.0.0")), port=port,
        state_dir=state_dir,
        series_categories=frozenset(str(x).casefold() for x in enrichment.get("series_categories", [])),
        series_titles=frozenset(str(x).casefold() for x in enrichment.get("series_titles", [])),
        movie_categories=frozenset(str(x).casefold() for x in enrichment.get("movie_categories", [])),
        test_tuner_enabled=bool(test_tuner.get("enabled", False)),
    )
