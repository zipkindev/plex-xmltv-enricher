from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit

from .service import FeedService


TEST_CHANNELS = (("1029", "RTL"), ("1035", "SAT.1"), ("1049", "VOX"))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: FeedService):
        self.service = service
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            body = json.dumps(
                self.server.service.health(), sort_keys=True
            ).encode()
            self._send(200, body, "application/json")
            return

        if path == "/xmltv/plex.xml":
            try:
                body = self.server.service.output()
            except Exception:
                self._send(
                    503, b'{"error":"feed unavailable"}', "application/json"
                )
                return
            self._send(200, body, "application/xml")
            return

        if (
            self.server.service.config.test_tuner_enabled
            and path == "/test-tuner/discover.json"
        ):
            host = self.headers.get(
                "Host", f"127.0.0.1:{self.server.server_port}"
            )
            body = json.dumps(
                {
                    "FriendlyName": "Plex XMLTV Metadata Test",
                    "Manufacturer": "plex-xmltv-enricher",
                    "ModelNumber": "metadata-only",
                    "FirmwareName": "metadata-only",
                    "TunerCount": 1,
                    "FirmwareVersion": "0.1.0",
                    "DeviceID": "plexxmltvenrichertest01",
                    "DeviceAuth": "",
                    "BaseURL": f"http://{host}/test-tuner",
                    "LineupURL": f"http://{host}/test-tuner/lineup.json",
                }
            ).encode()
            self._send(200, body, "application/json")
            return

        if (
            self.server.service.config.test_tuner_enabled
            and path == "/test-tuner/lineup.json"
        ):
            host = self.headers.get(
                "Host", f"127.0.0.1:{self.server.server_port}"
            )
            body = json.dumps(
                [
                    {
                        "GuideNumber": number,
                        "GuideName": name,
                        "URL": (
                            f"http://{host}/test-tuner/unavailable/{number}"
                        ),
                    }
                    for number, name in TEST_CHANNELS
                ]
            ).encode()
            self._send(200, body, "application/json")
            return

        if path.startswith("/test-tuner/unavailable/"):
            self._send(
                503, b'{"error":"metadata-only test tuner"}', "application/json"
            )
            return

        self._send(404, b'{"error":"not found"}', "application/json")
