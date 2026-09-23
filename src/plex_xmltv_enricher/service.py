from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import threading
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .config import Config
from .enrich import FeedError, Result, channel_topology, enrich, topology_digest
from .store import Store

LOG = logging.getLogger("plex_xmltv_enricher")


class FeedService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = Store(config.state_dir)
        self.lock = threading.Lock()
        self.result: Result | None = None
        self.updated_at: datetime | None = None
        self.active_source: str | None = None

    def _fetch(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": "plex-xmltv-enricher/0.1"})
        with urlopen(request, timeout=self.config.timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"application/xml", "text/xml", "application/octet-stream"}:
                LOG.warning(json.dumps({"event":"source_content_type_unexpected","content_type":content_type}))
            data = bytes(response.read(self.config.max_bytes + 1))
        if len(data) > self.config.max_bytes:
            raise FeedError("download exceeds configured maximum")
        return data

    def _topology(self, data: bytes) -> str:
        try:
            return topology_digest(channel_topology(ET.fromstring(data)))
        except ET.ParseError as exc:
            raise FeedError("fallback is invalid XML") from exc

    def refresh(self) -> Result:
        with self.lock:
            source_name = "primary"
            try:
                source = self._fetch(self.config.primary_url)
            except Exception:
                if not self.config.fallback_url:
                    raise
                source_name = "fallback"
                source = self._fetch(self.config.fallback_url)
                baseline = self.result.topology_hash if self.result else None
                if baseline is None:
                    raise FeedError("fallback refused before a primary topology is established")
                if self._topology(source) != baseline:
                    raise FeedError("fallback channel topology differs from primary")
            result = enrich(source, self.config, self.store)
            self.store.install_output(result.xml)
            self.result = result
            self.updated_at = datetime.now(timezone.utc)
            self.active_source = source_name
            LOG.info(json.dumps({"event":"feed_refreshed","source":source_name,**asdict(result)|{"xml":"redacted"}}))
            return result

    def output(self) -> bytes:
        with self.lock:
            path = self.config.state_dir / "enriched.xml"
            if not path.is_file():
                raise FeedError("no last-known-good output")
            return path.read_bytes()

    def health(self) -> dict[str, object]:
        now=datetime.now(timezone.utc)
        age=None if self.updated_at is None else (now-self.updated_at).total_seconds()
        result=self.result
        return {"status":"ok" if result else "starting","updated_at":None if self.updated_at is None else self.updated_at.isoformat(),"age_seconds":age,"source":self.active_source,"channels":None if result is None else result.channels,"programmes":None if result is None else result.programmes,"enriched":None if result is None else result.enriched}

    def run_refresh_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.refresh()
            except Exception as exc:
                LOG.error(json.dumps({"event":"refresh_failed","error_type":type(exc).__name__}))
            stop.wait(self.config.refresh_seconds)
