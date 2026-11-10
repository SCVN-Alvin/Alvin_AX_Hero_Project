"""AI settings kept in the application database. No credentials are ever stored here: Codex manages its own sign-in."""

import json
import os

from ..db import tx, utc_now
from ..errors import ValidationError

DEFAULTS = {
    "enabled": False,
    "provider": "codex_cli",
    "binary": "codex",
    "model": "",
    "timeout_seconds": 240,
    "max_blocks": 80,
    "max_chars": 24000,
    "batch_size": 3,
    "description_target": 600,
}
LIMITS = {"timeout_seconds": (20, 1800), "max_blocks": (1, 400), "max_chars": (500, 120000), "batch_size": (1, 8), "description_target": (100, 2000)}
PROVIDERS = ("codex_cli", "fake")


def get(conn):
    row = conn.execute("SELECT value FROM app_setting WHERE key = 'ai'").fetchone()
    out = dict(DEFAULTS)
    if row is not None:
        try:
            saved = json.loads(row["value"])
            if isinstance(saved, dict):
                out.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except ValueError:
            pass
    return out


def update(conn, data):
    cur = get(conn)
    new = dict(cur)
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise ValidationError("enabled must be true or false", field="enabled")
        new["enabled"] = data["enabled"]
    if "provider" in data:
        if data["provider"] not in PROVIDERS:
            raise ValidationError("Unknown provider", field="provider")
        new["provider"] = data["provider"]
    if "binary" in data:
        b = data["binary"]
        if not isinstance(b, str) or not b.strip() or len(b) > 500 or "\0" in b or "\n" in b:
            raise ValidationError("Enter the name or full path of the Codex command", field="binary")
        new["binary"] = b.strip()
    if "model" in data:
        m = data["model"] or ""
        if not isinstance(m, str) or len(m) > 100 or any(ch.isspace() for ch in m.strip()) or m.strip().startswith("-"):
            raise ValidationError("A model name has no spaces and does not start with a dash", field="model")
        new["model"] = m.strip()
    for k, (lo, hi) in LIMITS.items():
        if k in data:
            v = data[k]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValidationError(f"{k.replace('_', ' ')} must be a whole number from {lo} to {hi}", field=k)
            new[k] = v
    with tx(conn):
        conn.execute("INSERT INTO app_setting(key, value, updated_at) VALUES ('ai', ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (json.dumps(new), utc_now()))
    return new
