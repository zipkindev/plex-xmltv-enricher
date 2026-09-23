from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import tempfile


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
