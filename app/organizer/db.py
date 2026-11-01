"""SQLite access: connections, explicit transactions, and migrations."""

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


def utc_now():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def connect(path):
    """Open a connection in autocommit mode; transactions are explicit (see tx())."""
    conn = sqlite3.connect(path, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    # SQLite's lower() only folds ASCII; search needs full Unicode case folding.
    conn.create_function("fold", 1, lambda v: v.casefold() if isinstance(v, str) else v, deterministic=True)
    return conn


@contextmanager
def tx(conn):
    """All-or-nothing write block. Takes the write lock up front."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _split_statements(script):
    statements, buf = [], ""
    for line in script.splitlines(keepends=True):
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise MigrationError("Migration ends with an incomplete SQL statement")
    return statements


def discover_migrations(directory):
    found = []
    for name in sorted(os.listdir(directory)):
        m = _MIGRATION_RE.match(name)
        if m:
            found.append((int(m.group(1)), m.group(2), os.path.join(directory, name)))
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Migration versions must be contiguous from 0001; found {versions}")
    return found


def _backup(db_path, tag):
    dest = f"{db_path}.bak-{tag}"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def migrate(db_path, migrations_dir=MIGRATIONS_DIR):
    """Bring the database to the latest schema version.

    Each migration runs in its own transaction together with its version record,
    so a failure rolls back completely. Before applying anything to an existing
    database a consistent backup copy is written next to it.
    """
    migrations = discover_migrations(migrations_dir)
    existed = os.path.exists(db_path) and os.path.getsize(db_path) > 0
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migration")}
        latest_known = migrations[-1][0] if migrations else 0
        if applied and max(applied) > latest_known:
            raise MigrationError(
                f"Database schema version {max(applied)} is newer than this application "
                f"supports ({latest_known}). Use a newer build or a different data directory."
            )
        pending = [m for m in migrations if m[0] not in applied]
        if not pending:
            return {"applied": [], "backup": None, "version": latest_known}

        backup = None
        if existed and applied:
            backup = _backup(db_path, f"pre-v{pending[0][0]:04d}-{int(time.time())}")

        done = []
        for version, name, path in pending:
            with open(path, encoding="utf-8") as fh:
                statements = _split_statements(fh.read())
            try:
                with tx(conn):
                    for stmt in statements:
                        conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO schema_migration(version, name, applied_at) VALUES (?, ?, ?)",
                        (version, name, utc_now()),
                    )
            except Exception as exc:
                where = f" The previous data was kept; a backup copy is at {backup}." if backup else ""
                raise MigrationError(
                    f"Migration {version:04d}_{name} failed and was rolled back: {exc}.{where}"
                ) from exc
            done.append(version)
        return {"applied": done, "backup": backup, "version": done[-1]}
    finally:
        conn.close()
