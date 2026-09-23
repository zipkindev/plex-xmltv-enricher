from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import signal
import threading

from .config import load_config
from .httpd import Server
from .service import FeedService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "once", "doctor", "audit"))
    parser.add_argument(
        "--config", type=Path, default=Path("/config/config.toml")
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config(args.config)
    service = FeedService(config)

    if args.command in {"once", "doctor", "audit"}:
        result = service.refresh()
        report = {
            "status": "ok",
            "channels": result.channels,
            "programmes": result.programmes,
            "enriched": result.enriched,
            "source_normalized": result.source_normalized,
            "provider_resolved": result.provider_resolved,
            "ambiguous": result.ambiguous,
            "unresolved": result.unresolved,
            "non_episodic": result.non_episodic,
            "series_lookups": result.series_lookups,
            "identity_fallback": result.identity_fallback,
            "topology_hash": result.topology_hash,
        }
        if args.command == "audit":
            report["resolver"] = service.store.audit()
        print(json.dumps(report, sort_keys=True))
        return

    service.refresh()
    stop = threading.Event()
    thread = threading.Thread(
        target=service.run_refresh_loop, args=(stop,), daemon=True
    )
    thread.start()
    server = Server((config.listen_host, config.port), service)

    def halt(*_: object) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    try:
        server.serve_forever()
    finally:
        stop.set()
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
