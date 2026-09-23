from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sqlite3
import tempfile

from .models import EpisodeCandidate, SeriesCandidate


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(mode=0o750, parents=True, exist_ok=True)
        self.database = root / "identity.sqlite3"
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS episode_identity ("
                "identity_key TEXT PRIMARY KEY, original_air_date TEXT NOT NULL)"
            )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS series_mapping (
                    title_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    series_id TEXT NOT NULL,
                    canonical_title TEXT NOT NULL,
                    numbering TEXT NOT NULL DEFAULT 'catalog',
                    confidence REAL NOT NULL,
                    provenance TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS episode_catalog (
                    provider TEXT NOT NULL,
                    series_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY(provider, series_id)
                );
                CREATE TABLE IF NOT EXISTS episode_catalog_v2 (
                    provider TEXT NOT NULL,
                    series_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY(provider, series_id, language)
                );
                CREATE TABLE IF NOT EXISTS series_search_cache (
                    title_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resolution_decision (
                    fingerprint TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    provider TEXT,
                    series_id TEXT,
                    episode_id TEXT,
                    season INTEGER,
                    episode INTEGER,
                    confidence REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def stable_date(self, identity_key: str, proposed: str) -> str:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO episode_identity(identity_key, original_air_date) VALUES (?, ?)",
                (identity_key, proposed),
            )
            row = connection.execute(
                "SELECT original_air_date FROM episode_identity WHERE identity_key = ?",
                (identity_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("identity transaction did not persist")
        return str(row[0])

    def series_mapping(self, title_key: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT provider, series_id, canonical_title, numbering, confidence, "
                "provenance, updated_at FROM series_mapping WHERE title_key = ?",
                (title_key,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "provider",
            "series_id",
            "canonical_title",
            "numbering",
            "confidence",
            "provenance",
            "updated_at",
        )
        return dict(zip(keys, row, strict=True))

    def save_series_mapping(
        self,
        title_key: str,
        provider: str,
        series_id: str,
        canonical_title: str,
        numbering: str,
        confidence: float,
        provenance: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO series_mapping VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(title_key) DO UPDATE SET provider=excluded.provider, "
                "series_id=excluded.series_id, canonical_title=excluded.canonical_title, "
                "numbering=excluded.numbering, confidence=excluded.confidence, "
                "provenance=excluded.provenance, updated_at=excluded.updated_at",
                (
                    title_key,
                    provider,
                    series_id,
                    canonical_title,
                    numbering,
                    confidence,
                    provenance,
                    _now(),
                ),
            )

    def series_search(
        self, title_key: str
    ) -> tuple[list[SeriesCandidate], datetime] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, fetched_at FROM series_search_cache WHERE title_key = ?",
                (title_key,),
            ).fetchone()
        if row is None:
            return None
        return (
            [SeriesCandidate(**item) for item in json.loads(str(row[0]))],
            datetime.fromisoformat(str(row[1])),
        )

    def save_series_search(
        self, title_key: str, candidates: list[SeriesCandidate]
    ) -> None:
        payload = json.dumps(
            [candidate.__dict__ for candidate in candidates],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO series_search_cache VALUES (?, ?, ?) "
                "ON CONFLICT(title_key) DO UPDATE SET payload=excluded.payload, "
                "fetched_at=excluded.fetched_at",
                (title_key, payload, _now()),
            )

    def episode_catalog(
        self, provider: str, series_id: str, language: str
    ) -> tuple[list[EpisodeCandidate], datetime] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, fetched_at FROM episode_catalog_v2 "
                "WHERE provider = ? AND series_id = ? AND language = ?",
                (provider, series_id, language),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        return (
            [EpisodeCandidate(**item) for item in payload],
            datetime.fromisoformat(str(row[1])),
        )

    def save_episode_catalog(
        self,
        provider: str,
        series_id: str,
        language: str,
        episodes: list[EpisodeCandidate],
    ) -> None:
        payload = json.dumps(
            [episode.__dict__ for episode in episodes],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO episode_catalog_v2 VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, series_id, language) DO UPDATE SET "
                "payload=excluded.payload, fetched_at=excluded.fetched_at",
                (provider, series_id, language, payload, _now()),
            )

    def save_decision(
        self,
        fingerprint: str,
        status: str,
        reason: str,
        confidence: float,
        provider: str | None = None,
        series_id: str | None = None,
        episode_id: str | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO resolution_decision VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status, "
                "reason=excluded.reason, provider=excluded.provider, "
                "series_id=excluded.series_id, episode_id=excluded.episode_id, "
                "season=excluded.season, episode=excluded.episode, "
                "confidence=excluded.confidence, updated_at=excluded.updated_at",
                (
                    fingerprint,
                    status,
                    reason,
                    provider,
                    series_id,
                    episode_id,
                    season,
                    episode,
                    confidence,
                    _now(),
                ),
            )

    def audit(self) -> dict[str, object]:
        with self._connect() as connection:
            decisions = {
                str(status): int(count)
                for status, count in connection.execute(
                    "SELECT status, COUNT(*) FROM resolution_decision GROUP BY status"
                )
            }
            mappings = int(connection.execute("SELECT COUNT(*) FROM series_mapping").fetchone()[0])
            catalogs = int(
                connection.execute("SELECT COUNT(*) FROM episode_catalog_v2").fetchone()[0]
            )
            searches = int(connection.execute("SELECT COUNT(*) FROM series_search_cache").fetchone()[0])
        return {
            "decisions": decisions,
            "series_mappings": mappings,
            "episode_catalogs": catalogs,
            "series_searches": searches,
        }

    def install_output(self, data: bytes) -> Path:
        target = self.root / "enriched.xml"
        descriptor, temporary = tempfile.mkstemp(prefix=".enriched-", suffix=".xml", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory = os.open(self.root, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
