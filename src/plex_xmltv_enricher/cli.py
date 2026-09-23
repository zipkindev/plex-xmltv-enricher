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
    parser.add_argument("command", choices=("serve", "once", "doctor"))
    parser.add_argument(
        "--config", type=Path, default=Path("/config/config.toml")
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config(args.config)
    service = FeedService(config)

    if args.command in {"once", "doctor"}:
        result = service.refresh()
        print(
            json.dumps(
                {
                    "status": "ok",
                    "channels": result.channels,
                    "programmes": result.programmes,
                    "enriched": result.enriched,
                    "topology_hash": result.topology_hash,
                },
                sort_keys=True,
            )
        )
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
