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


def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _find_project(conn, ref):
    rows = conn.execute("SELECT id, name, ticket_prefix FROM project").fetchall()
    for key in ("id", "ticket_prefix", "name"):
        for r in rows:
            if str(r[key]).casefold() == ref.casefold():
                return r
    names = ", ".join(f"{r['ticket_prefix']} ({r['name']})" for r in rows) or "none yet; create one in the interface first"
    raise SystemExit(f"No project matches '{ref}'. Projects: {names}")


def _cli_import(argv):
    """`python3 -m organizer preview|import`: the same pipeline as the interface, without a browser."""
    import json
    import threading
    import time
    from . import importer
    from .db import connect

    ap = argparse.ArgumentParser(prog="organizer " + argv[0], description="Preview or run a brief import from the command line")
    ap.add_argument("--project", required=True, help="project id, ticket prefix, or exact name")
    ap.add_argument("--html", required=True, help="path of the exported .html file")
    ap.add_argument("--assets", help="companion media folder (default: detected next to the HTML file)")
    ap.add_argument("--data-dir", default=os.environ.get("ORGANIZER_DATA_DIR", DEFAULT_DATA_DIR))
    ap.add_argument("--import-root", action="append", default=[], help="only allow importing from this folder (repeatable)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv[1:])
    storage = Storage(args.data_dir)
    try:
        storage.init()
        migrate(storage.db_path)
    except (MigrationError, OSError) as exc:
        print(f"Cannot open the data folder: {exc}", file=sys.stderr)
        return 2
    conn = connect(storage.db_path)
    project = _find_project(conn, args.project)
    try:
        if argv[0] == "preview":
            result = importer.preview(storage, conn, project["id"], args.html, args.assets, args.import_root)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 0
            a, st, ex = result["assets"], result["structure"], result["external"]
            print(f"Project:   {project['name']} ({project['ticket_prefix']})")
            print(f"HTML:      {result['html']['name']}  ({_fmt_bytes(result['html']['size'])}, sha256 {result['html']['sha256'][:12]}…)")
            print(f"Folder:    {result['companion']['path'] or 'none found'}" + (" (detected)" if result['companion']['auto_detected'] else ""))
            print(f"Structure: {st['blocks']} blocks, {st['sections']} sections, {st['tables']} tables, {st['scenarios']} scenarios {st['scenario_ids'][:3]}…")
            print(f"Historical sections (kept, excluded from extraction by default): {', '.join(st['historical_sections']) or 'none'}")
            print(f"Media:     {a['referenced']} referenced, {a['available']} found, {a['missing']} missing, {a['blocked']} blocked, {_fmt_bytes(a['total_bytes'])}")
            for p in a["problems"][:20]:
                print(f"           - {p['status']}: {p['path']}  ({p['problem']})")
            print(f"External:  {ex['links']} links (not fetched), {ex['images']} images (not loaded)")
            print(f"Earlier import: {result['prior']['status']}")
            for w in result["warnings"]:
                print(f"Warning:   {w}")
            return 0
        run_id = importer.create_run(conn, project["id"], os.path.basename(args.html))
        progress = importer.Progress()
        done = threading.Event()

        def monitor():
            last = None
            while not done.wait(0.4):
                snap = progress.snapshot()
                line = f"  {snap['stage']}" + (f"  {snap['files_done']}/{snap['files_total']} files, {_fmt_bytes(snap['bytes_done'])} of {_fmt_bytes(snap['bytes_total'])}" if snap["files_total"] else "")
                if line != last:
                    print(line, file=sys.stderr)
                    last = line

        t = threading.Thread(target=monitor, daemon=True)
        t.start()
        started = time.time()
        try:
            result = importer.execute_import(storage, conn, project["id"], run_id, args.html, args.assets, args.import_root, progress)
        finally:
            done.set()
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"{result['state'].upper()}  ({time.time() - started:.1f}s)  {result.get('note', '')}")
            if result.get("document_id"):
                print(f"Document id: {result['document_id']}")
        return 0
    except importer.AppError as exc:
        print(f"Import failed: {exc.message}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("import", "preview"):
        return _cli_import(argv)
    ap = argparse.ArgumentParser(prog="organizer", description="Offline Task Organizer (local service)")
    ap.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1, this computer only)")
    ap.add_argument("--port", type=int, default=8765, help="port to listen on (default: 8765; 0 picks a free port)")
    ap.add_argument("--data-dir", default=os.environ.get("ORGANIZER_DATA_DIR", DEFAULT_DATA_DIR),
                    help="folder holding the database and project files (default: ./data next to the app, or $ORGANIZER_DATA_DIR)")
    ap.add_argument("--import-root", action="append", default=[], metavar="FOLDER",
                    help="confine the file picker and imports to this folder (repeatable; default: any folder you can read)")
    ap.add_argument("--ai-fake", metavar="SCRIPT.json", help=argparse.SUPPRESS)   # testing only: scripted AI answers instead of Codex
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
        fake = None
        if args.ai_fake:
            from .ai.adapters import FakeAdapter
            fake = FakeAdapter(script_path=args.ai_fake)
        server = make_server(storage, args.host, args.port, import_roots=args.import_root, ai_fake=fake)
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
