"""Command line entry point: python3 -m organizer"""

import argparse
import logging
import os
import sys

from . import __version__
from .db import MigrationError, migrate
from .server import make_server
from .storage import Storage

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="organizer", description="Offline Task Organizer (local service)")
    ap.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1, this computer only)")
    ap.add_argument("--port", type=int, default=8765, help="port to listen on (default: 8765; 0 picks a free port)")
    ap.add_argument("--data-dir", default=os.environ.get("ORGANIZER_DATA_DIR", DEFAULT_DATA_DIR),
                    help="folder holding the database and project files (default: ./data next to the app, or $ORGANIZER_DATA_DIR)")
    ap.add_argument("--open", action="store_true", help="open the interface in the default browser")
    ap.add_argument("--version", action="version", version=f"organizer {__version__}")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    storage = Storage(args.data_dir)
    try:
        storage.init()
        result = migrate(storage.db_path)
    except (MigrationError, OSError) as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 2
    if result["applied"]:
        logging.info("Applied migrations %s (backup: %s)", result["applied"], result["backup"])

    try:
        server = make_server(storage, args.host, args.port)
    except OSError as exc:
        print(f"Cannot listen on {args.host}:{args.port}: {exc}. Try --port 0 or another port.", file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    shown = "localhost" if host in ("127.0.0.1", "::1") else host
    url = f"http://{shown}:{port}/"
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: bound to {args.host}. The service has no authentication; anyone who can reach this address can edit your data.")
    print(f"Offline Task Organizer {__version__}")
    print(f"  Data folder: {storage.data_dir}")
    print(f"  Open:        {url}")
    print("  Press Ctrl+C to stop.")
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
