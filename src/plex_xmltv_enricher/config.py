from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    enabled: bool
    api_key_env: str | None = None
    pin_env: str | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class SeriesOverride:
    provider: str
    series_id: str
    numbering: str = "catalog"


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
    resolver_enabled: bool = False
    resolver_language: str = "de"
    resolver_country: str = "DE"
    minimum_series_confidence: float = 0.92
    minimum_episode_confidence: float = 0.95
    ambiguity_margin: float = 0.08
    catalog_ttl_hours: int = 168
    negative_ttl_hours: int = 24
    providers: tuple[ProviderConfig, ...] = ()
    series_overrides: tuple[tuple[str, SeriesOverride], ...] = ()


def _url(value: object, name: str, *, required: bool) -> str | None:
    text = str(value or "").strip()
    if not text and not required:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ConfigError(f"{name} must be an http(s) URL without embedded credentials")
    return text


def _probability(value: object, name: str, default: float) -> float:
    result = default if value is None else float(str(value))
    if not 0.0 <= result <= 1.0:
        raise ConfigError(f"{name} must be between 0 and 1")
    return result


def _provider_configs(data: dict[str, object]) -> tuple[ProviderConfig, ...]:
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("providers must be a TOML table")
    rows: list[ProviderConfig] = []
    defaults = {
        "thetvdb": "https://api4.thetvdb.com/v4",
        "tmdb": "https://api.themoviedb.org/3",
        "tvmaze": "https://api.tvmaze.com",
    }
    for name in ("thetvdb", "tmdb", "tvmaze"):
        raw = providers.get(name, {})
        if not isinstance(raw, dict):
            raise ConfigError(f"providers.{name} must be a TOML table")
        base_url = _url(
            raw.get("base_url", defaults[name]),
            f"providers.{name}.base_url",
            required=True,
        )
        rows.append(
            ProviderConfig(
                name=name,
                enabled=bool(raw.get("enabled", False)),
                api_key_env=str(raw.get("api_key_env", "")).strip() or None,
                pin_env=str(raw.get("pin_env", "")).strip() or None,
                base_url=base_url,
            )
        )
    return tuple(rows)


def _series_overrides(data: dict[str, object]) -> tuple[tuple[str, SeriesOverride], ...]:
    raw = data.get("series_overrides", {})
    if not isinstance(raw, dict):
        raise ConfigError("series_overrides must be a TOML table")
    rows: list[tuple[str, SeriesOverride]] = []
    for title, value in raw.items():
        if not isinstance(value, dict):
            raise ConfigError(f"series_overrides.{title} must be a TOML table")
        provider = str(value.get("provider", "")).strip().casefold()
        series_id = str(value.get("series_id", "")).strip()
        numbering = str(value.get("numbering", "catalog")).strip().casefold()
        if provider not in {"thetvdb", "tmdb", "tvmaze"} or not series_id:
            raise ConfigError(f"series override is incomplete: {title}")
        if numbering not in {"catalog", "year", "absolute"}:
            raise ConfigError(f"unsupported numbering model for {title}")
        rows.append((str(title).casefold(), SeriesOverride(provider, series_id, numbering)))
    return tuple(rows)


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError("configuration file is missing")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    source = data.get("source", {})
    server = data.get("server", {})
    storage = data.get("storage", {})
    enrichment = data.get("enrichment", {})
    resolver = data.get("resolver", {})
    test_tuner = data.get("test_tuner", {})
    sections = (source, server, storage, enrichment, resolver, test_tuner)
    if not all(isinstance(section, dict) for section in sections):
        raise ConfigError("configuration sections must be TOML tables")
    primary = _url(source.get("primary_url"), "primary_url", required=True)
    fallback = _url(source.get("fallback_url"), "fallback_url", required=False)
    timeout = float(source.get("timeout_seconds", 30))
    max_bytes = int(source.get("max_bytes", 32 * 1024 * 1024))
    refresh = int(source.get("refresh_seconds", 900))
    port = int(server.get("port", 9193))
    state_dir = Path(str(storage.get("state_dir", "/state"))).resolve()
    catalog_ttl = int(resolver.get("catalog_ttl_hours", 168))
    negative_ttl = int(resolver.get("negative_ttl_hours", 24))
    if not 1 <= timeout <= 120:
        raise ConfigError("timeout_seconds must be between 1 and 120")
    if not 1024 <= max_bytes <= 256 * 1024 * 1024:
        raise ConfigError("max_bytes is outside the safe range")
    if not 30 <= refresh <= 86400:
        raise ConfigError("refresh_seconds must be between 30 and 86400")
    if not 1 <= port <= 65535:
        raise ConfigError("port is invalid")
    if not 1 <= negative_ttl <= catalog_ttl <= 24 * 365:
        raise ConfigError("resolver cache TTL values are invalid")
    if primary == fallback:
        raise ConfigError("primary and fallback URLs must differ")
    language = str(resolver.get("language", "de")).strip().casefold()
    country = str(resolver.get("country", "DE")).strip().upper()
    if not (2 <= len(language) <= 8 and len(country) == 2):
        raise ConfigError("resolver language or country is invalid")
    return Config(
        primary_url=str(primary),
        fallback_url=fallback,
        timeout_seconds=timeout,
        max_bytes=max_bytes,
        refresh_seconds=refresh,
        listen_host=str(server.get("listen_host", "0.0.0.0")),
        port=port,
        state_dir=state_dir,
        series_categories=frozenset(
            str(x).casefold() for x in enrichment.get("series_categories", [])
        ),
        series_titles=frozenset(
            str(x).casefold() for x in enrichment.get("series_titles", [])
        ),
        movie_categories=frozenset(
            str(x).casefold() for x in enrichment.get("movie_categories", [])
        ),
        test_tuner_enabled=bool(test_tuner.get("enabled", False)),
        resolver_enabled=bool(resolver.get("enabled", False)),
        resolver_language=language,
        resolver_country=country,
        minimum_series_confidence=_probability(
            resolver.get("minimum_series_confidence"),
            "minimum_series_confidence",
            0.92,
        ),
        minimum_episode_confidence=_probability(
            resolver.get("minimum_episode_confidence"),
            "minimum_episode_confidence",
            0.95,
        ),
        ambiguity_margin=_probability(
            resolver.get("ambiguity_margin"), "ambiguity_margin", 0.08
        ),
        catalog_ttl_hours=catalog_ttl,
        negative_ttl_hours=negative_ttl,
        providers=_provider_configs(data),
        series_overrides=_series_overrides(data),
    )
