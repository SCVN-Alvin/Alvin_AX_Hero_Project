"""Tasks and subtasks: creation, editing, hierarchy, status/ordering, filters.

All writes run inside one transaction (db.tx). A validation failure or an error
part-way through leaves no partial records, and ticket numbers are only consumed
when the whole write commits.
"""

import json
import sqlite3
import uuid

from .. import priority as prio
from .. import validation as v
from ..db import tx, utc_now
from ..errors import Conflict, NotFound, ValidationError
from ..meta import (
    CATEGORY_KEYS,
    DISCIPLINE_KEYS,
    IMPACT_KEYS,
    LEVEL_KEYS,
    LEVEL_RANK,
    PRIORITY_KEYS,
    STATUS_KEYS,
    URGENCY_KEYS,
)
from .projects import get_project_row, new_id

RANK_GAP = 1024
MAX_TAGS = 30
ORIGINS = ("manual", "source_extraction", "rule", "template", "system")

# Fields whose changes are recorded in history.
TRACKED = (
    "title", "description", "level", "parent_id", "disciplines", "category", "tags",
    "impact", "urgency", "priority_override", "priority_override_reason",
    "status", "is_blocked", "blocked_reason", "assignee_id", "due_at",
)


# ---- helpers ----------------------------------------------------------------

def _chunks(items, n=400):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _get_row(conn, task_id):
    row = conn.execute(
        "SELECT t.*, p.ticket_prefix FROM task t JOIN project p ON p.id = t.project_id WHERE t.id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise NotFound("Task not found")
    return row


def effective_priority(row):
    """(value, source) where source is overridden | suggested | not_assessed."""
    if row["priority_override"]:
        return row["priority_override"], "overridden"
    if row["suggested_priority"]:
        return row["suggested_priority"], "suggested"
    return None, "not_assessed"


def _hydrate(conn, rows):
    """Turn task rows into dicts with disciplines, tags, assignee name and child progress."""
    ids = [r["id"] for r in rows]
    disciplines = {i: [] for i in ids}
    tags = {i: [] for i in ids}
    progress = {i: [0, 0] for i in ids}
    project_ids = list({r["project_id"] for r in rows})
    people = {}
    if project_ids:
        marks = ",".join("?" * len(project_ids))
        people = {r["id"]: r["name"] for r in conn.execute(
            f"SELECT id, name FROM person WHERE project_id IN ({marks})", project_ids)}
    for chunk in _chunks(ids):
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT task_id, discipline FROM task_discipline WHERE task_id IN ({q}) ORDER BY discipline", chunk):
            disciplines[r["task_id"]].append(r["discipline"])
        for r in conn.execute(
            f"SELECT tt.task_id, g.name FROM task_tag tt JOIN tag g ON g.id = tt.tag_id "
            f"WHERE tt.task_id IN ({q}) ORDER BY lower(g.name)", chunk
        ):
            tags[r["task_id"]].append(r["name"])
        for r in conn.execute(
            f"SELECT parent_id, COUNT(*) AS total, SUM(status = 'done') AS done FROM task "
            f"WHERE parent_id IN ({q}) AND review_state = 'approved' GROUP BY parent_id", chunk
        ):
            progress[r["parent_id"]] = [r["done"] or 0, r["total"]]
    out = []
    for r in rows:
        eff, src = effective_priority(r)
        out.append({
            "id": r["id"],
            "project_id": r["project_id"],
            "key": f"{r['ticket_prefix']}-{r['ticket_number']}",
            "ticket_number": r["ticket_number"],
            "parent_id": r["parent_id"],
            "level": r["level"],
            "title": r["title"],
            "description": r["description"],
            "disciplines": disciplines[r["id"]],
            "category": r["category"],
            "tags": tags[r["id"]],
            "impact": r["impact"],
            "urgency": r["urgency"],
            "suggested_priority": r["suggested_priority"],
            "priority_reason": r["priority_reason"],
            "priority_override": r["priority_override"],
            "priority_override_reason": r["priority_override_reason"],
            "priority": eff,
            "priority_source": src,
            "status": r["status"],
            "is_blocked": bool(r["is_blocked"]),
            "blocked_reason": r["blocked_reason"],
            "review_state": r["review_state"],
            "assignee_id": r["assignee_id"],
            "assignee_name": people.get(r["assignee_id"]) if r["assignee_id"] else None,
            "due_at": r["due_at"],
            "board_rank": r["board_rank"],
            "provenance": r["provenance"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "children_done": progress[r["id"]][0],
            "children_total": progress[r["id"]][1],
        })
    return out


_SELECT = "SELECT t.*, p.ticket_prefix FROM task t JOIN project p ON p.id = t.project_id"


def _record(conn, task_id, field, old, new, origin, now):
    if old == new:
        return
    conn.execute(
        "INSERT INTO task_field_change(task_id, field, old_value, new_value, origin, changed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, field, _s(old), _s(new), origin, now),
    )


def _s(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return ", ".join(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _next_rank_end(conn, project_id, status):
    row = conn.execute(
        "SELECT MAX(board_rank) FROM task WHERE project_id = ? AND status = ?", (project_id, status)
    ).fetchone()
    return (row[0] if row[0] is not None else 0) + RANK_GAP


def _next_rank_top(conn, project_id, status, exclude_id=None):
    row = conn.execute(
        "SELECT MIN(board_rank) FROM task WHERE project_id = ? AND status = ? AND id IS NOT ?",
        (project_id, status, exclude_id),
    ).fetchone()
    return (row[0] if row[0] is not None else RANK_GAP) - RANK_GAP


def _rebalance(conn, project_id, status):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM task WHERE project_id = ? AND status = ? ORDER BY board_rank, ticket_number",
        (project_id, status))]
    for i, tid in enumerate(ids, start=1):
        conn.execute("UPDATE task SET board_rank = ? WHERE id = ?", (i * RANK_GAP, tid))


def _rank_before(conn, project_id, status, before_id, exclude_id):
    """Rank that places a card immediately before `before_id` (None = end of column)."""
    for _ in range(2):
        if before_id is None:
            return _next_rank_end_excluding(conn, project_id, status, exclude_id)
        nxt = conn.execute(
            "SELECT board_rank FROM task WHERE id = ? AND project_id = ? AND status = ?",
            (before_id, project_id, status),
        ).fetchone()
        if nxt is None:
            raise ValidationError("before_id is not a card in the target column", field="before_id")
        prev = conn.execute(
            "SELECT board_rank FROM task WHERE project_id = ? AND status = ? AND board_rank < ? AND id IS NOT ? "
            "ORDER BY board_rank DESC LIMIT 1",
            (project_id, status, nxt["board_rank"], exclude_id),
        ).fetchone()
        if prev is None:
            return nxt["board_rank"] - RANK_GAP
        mid = (prev["board_rank"] + nxt["board_rank"]) // 2
        if prev["board_rank"] < mid < nxt["board_rank"]:
            return mid
        _rebalance(conn, project_id, status)
    raise Conflict("Could not compute a card position")  # pragma: no cover


def _next_rank_end_excluding(conn, project_id, status, exclude_id):
    row = conn.execute(
        "SELECT MAX(board_rank) FROM task WHERE project_id = ? AND status = ? AND id IS NOT ?",
        (project_id, status, exclude_id),
    ).fetchone()
    return (row[0] if row[0] is not None else 0) + RANK_GAP


# ---- hierarchy validation ---------------------------------------------------

def _validate_hierarchy(conn, task_id, project_id, level, parent_id):
    """Friendly pre-checks; the database triggers repeat them as a safety net."""
    if parent_id is None:
        if level == "subtask":
            raise ValidationError("A subtask needs a parent task or epic", field="parent_id")
    else:
        if parent_id == task_id:
            raise ValidationError("A task cannot be its own parent", field="parent_id")
        parent = conn.execute("SELECT id, project_id, level FROM task WHERE id = ?", (parent_id,)).fetchone()
        if parent is None:
            raise ValidationError("Parent task does not exist", field="parent_id")
        if parent["project_id"] != project_id:
            raise ValidationError("Parent belongs to a different project", field="parent_id")
        if LEVEL_RANK[parent["level"]] >= LEVEL_RANK[level]:
            raise ValidationError(
                f"A {level} cannot be placed under a {parent['level']}. "
                "Allowed: epic > task > subtask.", field="parent_id")
        # Walk up to be certain no cycle exists (defence in depth; levels already forbid it).
        seen, cur = {task_id} if task_id else set(), parent_id
        while cur:
            if cur in seen:
                raise ValidationError("This change would create a hierarchy cycle", field="parent_id")
            seen.add(cur)
            r = conn.execute("SELECT parent_id FROM task WHERE id = ?", (cur,)).fetchone()
            cur = r["parent_id"] if r else None
    if task_id:
        bad = conn.execute(
            "SELECT t.title, t.level FROM task t WHERE t.parent_id = ?", (task_id,)
        ).fetchall()
        for child in bad:
            if LEVEL_RANK[child["level"]] <= LEVEL_RANK[level]:
                raise ValidationError(
                    f"Cannot make this a {level}: it contains {child['level']} “{child['title']}”. "
                    "Move or re-level its children first.", field="level")


def _check_assignee(conn, project_id, assignee_id):
    if assignee_id is None:
        return
    row = conn.execute("SELECT project_id FROM person WHERE id = ?", (assignee_id,)).fetchone()
    if row is None or row["project_id"] != project_id:
        raise ValidationError("Assignee is not a person in this project", field="assignee_id")


def _sync_tags(conn, project_id, task_id, names):
    existing = {r["name"].casefold(): r["id"] for r in conn.execute(
        "SELECT id, name FROM tag WHERE project_id = ?", (project_id,))}
    wanted = []
    for name in names:
        tid = existing.get(name.casefold())
        if tid is None:
            tid = new_id()
            conn.execute("INSERT INTO tag(id, project_id, name) VALUES (?, ?, ?)", (tid, project_id, name))
            existing[name.casefold()] = tid
        wanted.append(tid)
    conn.execute("DELETE FROM task_tag WHERE task_id = ?", (task_id,))
    for tid in wanted:
        conn.execute("INSERT INTO task_tag(project_id, task_id, tag_id) VALUES (?, ?, ?)", (project_id, task_id, tid))
    # Tidy labels that nothing uses any more.
    conn.execute(
        "DELETE FROM tag WHERE project_id = ? AND id NOT IN (SELECT tag_id FROM task_tag)", (project_id,))


def _sync_disciplines(conn, task_id, values):
    conn.execute("DELETE FROM task_discipline WHERE task_id = ?", (task_id,))
    for d in values:
        conn.execute("INSERT INTO task_discipline(task_id, discipline) VALUES (?, ?)", (task_id, d))


def _parse_disciplines(data):
    if "disciplines" not in data:
        return None
    raw = data["disciplines"]
    if raw is None:
        return []
    if not isinstance(raw, list) or any(d not in DISCIPLINE_KEYS for d in raw):
        raise ValidationError(f"disciplines must be a list drawn from: {', '.join(DISCIPLINE_KEYS)}", field="disciplines")
    return sorted(set(raw))


def _unfinished_descendants(conn, task_id):
    rows = conn.execute(
        "WITH RECURSIVE d(id) AS (SELECT id FROM task WHERE parent_id = ? "
        " UNION ALL SELECT t.id FROM task t JOIN d ON t.parent_id = d.id) "
        "SELECT t.id, t.title, t.status, t.level, p.ticket_prefix || '-' || t.ticket_number AS key "
        "FROM task t JOIN project p ON p.id = t.project_id "
        "WHERE t.id IN (SELECT id FROM d) AND t.status <> 'done' ORDER BY t.ticket_number",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _guard_done(conn, task_id, new_status, old_status, confirm):
    if new_status != "done" or old_status == "done" or confirm:
        return
    remaining = _unfinished_descendants(conn, task_id)
    if remaining:
        raise Conflict(
            f"{len(remaining)} unfinished child task{'s' if len(remaining) != 1 else ''} remain. "
            "Children are never completed automatically; confirm to mark the parent Done anyway.",
            code="unfinished_children",
            details={"unfinished": remaining},
        )


# ---- create / update / delete -----------------------------------------------

def create_task_in_tx(conn, project_id, data, *, origin="manual", provenance="manual"):
    """Create a task inside the caller's transaction (used by approval, which creates several at once). Returns the task id."""
    project = get_project_row(conn, project_id)
    title = v.text(data, "title", max_len=300, required=True)
    # Collapse hard line breaks in titles; they are single-line values.
    title = " ".join(title.split())
    description = v.text(data, "description", max_len=20000, default="", strip=False) or ""
    parent_id = data.get("parent_id") or None
    level = v.choice(data, "level", LEVEL_KEYS, default=None)
    if level is None:
        level = "subtask" if parent_id else "task"
    disciplines = _parse_disciplines(data) or []
    category = v.choice(data, "category", CATEGORY_KEYS, default="unassigned")
    impact = v.choice(data, "impact", IMPACT_KEYS, default="unassessed")
    urgency = v.choice(data, "urgency", URGENCY_KEYS, default="unspecified")
    override = v.choice(data, "priority_override", PRIORITY_KEYS, nullable=True, default=None)
    override_reason = v.text(data, "priority_override_reason", max_len=1000, default="")
    status = v.choice(data, "status", STATUS_KEYS, default="backlog")
    blocked = v.boolean(data, "is_blocked", False)
    blocked_reason = v.text(data, "blocked_reason", max_len=1000, default="")
    assignee_id = data.get("assignee_id") or None
    due = v.date_or_datetime(data, "due_at")
    due = None if due is ... else due
    tags = v.string_list(data, "tags", max_items=MAX_TAGS, max_len=40) or []
    if override is None and override_reason:
        raise ValidationError("An override reason needs an override priority", field="priority_override_reason")

    task_id, now = new_id(), utc_now()
    _validate_hierarchy(conn, None, project_id, level, parent_id)
    _check_assignee(conn, project_id, assignee_id)
    number = conn.execute("SELECT next_ticket_number FROM project WHERE id = ?", (project_id,)).fetchone()[0]
    conn.execute("UPDATE project SET next_ticket_number = ?, updated_at = ? WHERE id = ?", (number + 1, now, project_id))
    suggested, reason = prio.suggest(impact, urgency)
    rank = _next_rank_end(conn, project_id, status)
    conn.execute(
        "INSERT INTO task(id, project_id, ticket_number, parent_id, level, title, description, category, impact,"
        " urgency, suggested_priority, priority_reason, priority_override, priority_override_reason, status,"
        " is_blocked, blocked_reason, review_state, assignee_id, due_at, board_rank, provenance, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'approved',?,?,?,?,?,?)",
        (task_id, project_id, number, parent_id, level, title, description, category, impact, urgency,
         suggested, reason, override, override_reason, status, int(blocked), blocked_reason,
         assignee_id, due, rank, provenance, now, now),
    )
    _sync_disciplines(conn, task_id, disciplines)
    _sync_tags(conn, project_id, task_id, tags)
    _record(conn, task_id, "created", None, f"{project['ticket_prefix']}-{number}", origin, now)
    initial = {
        "disciplines": (disciplines, []), "category": (category, "unassigned"), "impact": (impact, "unassessed"),
        "urgency": (urgency, "unspecified"), "priority_override": (override, None), "status": (status, "backlog"),
        "is_blocked": (blocked, False), "assignee_id": (assignee_id, None), "due_at": (due, None),
        "tags": (tags, []), "parent_id": (parent_id, None),
    }
    for field, (value, default) in initial.items():
        if value != default:
            _record(conn, task_id, field, None, value, origin, now)
    return task_id




def create_task(conn, project_id, data, *, origin="manual", provenance="manual"):
    with tx(conn):
        task_id = create_task_in_tx(conn, project_id, data, origin=origin, provenance=provenance)
    return get_task(conn, task_id)


def set_field_origin(conn, task_id, field, origin):
    """Relabel who set a field in the task's history (for example a rule suggestion a person accepted)."""
    conn.execute("UPDATE task_field_change SET origin = ? WHERE task_id = ? AND field = ? AND origin <> ?", (origin, task_id, field, origin))


def update_task(conn, task_id, data, *, origin="manual"):
    row = _get_row(conn, task_id)
    pid = row["project_id"]
    changes = {}  # column -> new value (scalars)
    new_disc = _parse_disciplines(data)
    new_tags = v.string_list(data, "tags", max_items=MAX_TAGS, max_len=40)

    if "title" in data:
        t = v.text(data, "title", max_len=300, required=True)
        changes["title"] = " ".join(t.split())
    if "description" in data:
        changes["description"] = v.text(data, "description", max_len=20000, default="", strip=False) or ""
    if "category" in data:
        changes["category"] = v.choice(data, "category", CATEGORY_KEYS)
    if "impact" in data:
        changes["impact"] = v.choice(data, "impact", IMPACT_KEYS)
    if "urgency" in data:
        changes["urgency"] = v.choice(data, "urgency", URGENCY_KEYS)
    if "priority_override" in data:
        changes["priority_override"] = v.choice(data, "priority_override", PRIORITY_KEYS, nullable=True)
        if changes["priority_override"] is None:
            changes["priority_override_reason"] = ""
    if "priority_override_reason" in data and changes.get("priority_override", row["priority_override"]) is not None:
        changes["priority_override_reason"] = v.text(data, "priority_override_reason", max_len=1000, default="")
    if "is_blocked" in data:
        changes["is_blocked"] = int(v.boolean(data, "is_blocked"))
    if "blocked_reason" in data:
        changes["blocked_reason"] = v.text(data, "blocked_reason", max_len=1000, default="")
    if "assignee_id" in data:
        changes["assignee_id"] = data["assignee_id"] or None
    if "due_at" in data:
        changes["due_at"] = v.date_or_datetime(data, "due_at")
    level = v.choice(data, "level", LEVEL_KEYS, default=row["level"])
    parent_id = (data["parent_id"] or None) if "parent_id" in data else row["parent_id"]
    new_status = v.choice(data, "status", STATUS_KEYS, default=row["status"])
    confirm = bool(data.get("confirm_unfinished"))

    now = utc_now()
    with tx(conn):
        if "level" in data or "parent_id" in data:
            _validate_hierarchy(conn, task_id, pid, level, parent_id)
            changes["level"], changes["parent_id"] = level, parent_id
        if "assignee_id" in changes:
            _check_assignee(conn, pid, changes["assignee_id"])
        if "impact" in changes or "urgency" in changes:
            suggested, reason = prio.suggest(changes.get("impact", row["impact"]), changes.get("urgency", row["urgency"]))
            changes["suggested_priority"], changes["priority_reason"] = suggested, reason
        if new_status != row["status"]:
            _guard_done(conn, task_id, new_status, row["status"], confirm)
            changes["status"] = new_status
            changes["board_rank"] = _next_rank_top(conn, pid, new_status, exclude_id=task_id)

        old = {k: row[k] for k in changes if k in row.keys()}
        changes = {k: val for k, val in changes.items() if row[k] != val}
        disc_changed = new_disc is not None and new_disc != sorted(
            r["discipline"] for r in conn.execute("SELECT discipline FROM task_discipline WHERE task_id = ?", (task_id,)))
        old_tags = sorted((r["name"] for r in conn.execute(
            "SELECT g.name FROM task_tag tt JOIN tag g ON g.id = tt.tag_id WHERE tt.task_id = ?", (task_id,))), key=str.casefold)
        tags_changed = new_tags is not None and [t.casefold() for t in sorted(new_tags, key=str.casefold)] != [t.casefold() for t in old_tags]

        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE task SET {sets}, updated_at = ? WHERE id = ?", (*changes.values(), now, task_id))
            for k, val in changes.items():
                if k in TRACKED:
                    before = bool(old[k]) if k == "is_blocked" else old[k]
                    after = bool(val) if k == "is_blocked" else val
                    _record(conn, task_id, k, before, after, origin, now)
        if disc_changed:
            before = sorted(r["discipline"] for r in conn.execute("SELECT discipline FROM task_discipline WHERE task_id = ?", (task_id,)))
            _sync_disciplines(conn, task_id, new_disc)
            _record(conn, task_id, "disciplines", before, new_disc, origin, now)
            conn.execute("UPDATE task SET updated_at = ? WHERE id = ?", (now, task_id))
        if tags_changed:
            _sync_tags(conn, pid, task_id, new_tags)
            _record(conn, task_id, "tags", old_tags, new_tags, origin, now)
            conn.execute("UPDATE task SET updated_at = ? WHERE id = ?", (now, task_id))
    return get_task(conn, task_id)


def move_task(conn, task_id, status, before_id=None, confirm=False, *, origin="manual"):
    """Move a card to a column and position. before_id=None appends to the column."""
    if status not in STATUS_KEYS:
        raise ValidationError("Unknown status", field="status")
    row = _get_row(conn, task_id)
    pid = row["project_id"]
    if before_id == task_id:
        raise ValidationError("A card cannot be placed before itself", field="before_id")
    now = utc_now()
    with tx(conn):
        _guard_done(conn, task_id, status, row["status"], confirm)
        rank = _rank_before(conn, pid, status, before_id, task_id)
        conn.execute("UPDATE task SET status = ?, board_rank = ?, updated_at = ? WHERE id = ?", (status, rank, now, task_id))
        _record(conn, task_id, "status", row["status"], status, origin, now)
    return get_task(conn, task_id)


def delete_task(conn, task_id):
    row = _get_row(conn, task_id)
    kids = conn.execute(
        "SELECT t.title, p.ticket_prefix || '-' || t.ticket_number AS key FROM task t "
        "JOIN project p ON p.id = t.project_id WHERE t.parent_id = ?", (task_id,)).fetchall()
    if kids:
        raise Conflict(
            f"This task contains {len(kids)} child task{'s' if len(kids) != 1 else ''}. "
            "Move or delete them first.",
            code="has_children", details={"children": [dict(k) for k in kids]})
    with tx(conn):
        # Evidence that belonged to this task goes with it. Its candidates return to review as dismissed
        # (so regenerating does not recreate them unasked, and they can be restored and approved again).
        reqs = [r["requirement_id"] for r in conn.execute("SELECT requirement_id FROM task_requirement WHERE task_id = ?", (task_id,))]
        for rid in reqs:
            conn.execute("UPDATE candidate SET state = 'dismissed', requirement_id = NULL WHERE requirement_id = ?", (rid,))
            conn.execute("DELETE FROM requirement WHERE id = ?", (rid,))
        conn.execute("UPDATE candidate SET state = 'dismissed', task_id = NULL WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM task WHERE id = ?", (task_id,))
        conn.execute("DELETE FROM tag WHERE project_id = ? AND id NOT IN (SELECT tag_id FROM task_tag)", (row["project_id"],))
    # The ticket number is deliberately not returned to the project counter.
    return {"deleted": task_id, "key": f"{row['ticket_prefix']}-{row['ticket_number']}"}


# ---- reading ----------------------------------------------------------------

def _brief(row):
    return {
        "id": row["id"], "key": row["key"], "title": row["title"], "level": row["level"],
        "status": row["status"], "is_blocked": bool(row["is_blocked"]), "parent_id": row["parent_id"],
    }


_BRIEF_SQL = ("SELECT t.id, t.title, t.level, t.status, t.is_blocked, t.parent_id, "
              "p.ticket_prefix || '-' || t.ticket_number AS key FROM task t JOIN project p ON p.id = t.project_id")


def _field_origins(conn, task_id):
    origins = {}
    for r in conn.execute("SELECT field, origin FROM task_field_change WHERE task_id = ? ORDER BY id", (task_id,)):
        origins[r["field"]] = r["origin"]
    return origins


def get_task(conn, task_id):
    row = _get_row(conn, task_id)
    task = _hydrate(conn, [row])[0]
    task["parent"] = None
    if row["parent_id"]:
        p = conn.execute(_BRIEF_SQL + " WHERE t.id = ?", (row["parent_id"],)).fetchone()
        task["parent"] = _brief(p)
    kids = conn.execute(_BRIEF_SQL + " WHERE t.parent_id = ? ORDER BY t.ticket_number", (task_id,)).fetchall()
    task["children"] = [_brief(k) for k in kids]
    task["criteria"] = [
        {"id": c["id"], "text": c["text"], "checked": bool(c["checked"]), "position": c["position"]}
        for c in conn.execute("SELECT * FROM acceptance_criterion WHERE task_id = ? ORDER BY position, created_at", (task_id,))
    ]
    task["depends_on"] = [_brief(r) for r in conn.execute(
        _BRIEF_SQL + " JOIN task_dependency d ON d.depends_on_task_id = t.id WHERE d.task_id = ? ORDER BY t.ticket_number", (task_id,))]
    task["dependents"] = [_brief(r) for r in conn.execute(
        _BRIEF_SQL + " JOIN task_dependency d ON d.task_id = t.id WHERE d.depends_on_task_id = ? ORDER BY t.ticket_number", (task_id,))]
    task["history"] = [dict(r) for r in conn.execute(
        "SELECT id, field, old_value, new_value, origin, changed_at FROM task_field_change "
        "WHERE task_id = ? ORDER BY id DESC LIMIT 200", (task_id,))]
    task["field_origins"] = _field_origins(conn, task_id)
    return task


# ---- filtering --------------------------------------------------------------

def _csv(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_csv(item))
        return out
    return [s for s in (p.strip() for p in str(value).split(",")) if s]


def parse_filters(params):
    """Normalise query-string filters. `params` maps name -> list of values (parse_qs shape)."""
    def first(name):
        val = params.get(name)
        if isinstance(val, list):
            val = val[0] if val else ""
        return (val or "").strip()

    f = {
        "q": first("q"),
        "discipline": _csv(params.get("discipline")),
        "category": _csv(params.get("category")),
        "impact": _csv(params.get("impact")),
        "urgency": _csv(params.get("urgency")),
        "priority": _csv(params.get("priority")),
        "assignee": _csv(params.get("assignee")),
        "status": _csv(params.get("status")),
    }
    for key, allowed in (
        ("discipline", DISCIPLINE_KEYS + ["both", "none"]),
        ("category", CATEGORY_KEYS), ("impact", IMPACT_KEYS), ("urgency", URGENCY_KEYS),
        ("priority", PRIORITY_KEYS + ["not_assessed"]), ("status", STATUS_KEYS),
    ):
        bad = [x for x in f[key] if x not in allowed]
        if bad:
            raise ValidationError(f"Unknown {key} filter value: {', '.join(bad)}", field=key)
    return f


def filters_active(f, ignore=("status",)):
    return any(bool(val) for k, val in f.items() if k not in ignore)


def _like(term):
    return "%" + term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _where(f, args):
    """Build WHERE fragments over alias t (task) and p (project). Dimensions AND; values within OR."""
    clauses = []
    for term in f["q"].split():
        like = _like(term)
        clauses.append(
            "(fold(t.title) LIKE ? ESCAPE '\\' OR fold(t.description) LIKE ? ESCAPE '\\' "
            "OR fold(p.ticket_prefix || '-' || t.ticket_number) LIKE ? ESCAPE '\\')")
        args += [like, like, like]
    if f["discipline"]:
        parts = []
        sel = set(f["discipline"])
        has = "EXISTS (SELECT 1 FROM task_discipline d WHERE d.task_id = t.id AND d.discipline = '%s')"
        if "art" in sel:
            parts.append(has % "art")
        if "dev" in sel:
            parts.append(has % "dev")
        if "both" in sel:
            parts.append("(" + has % "art" + " AND " + has % "dev" + ")")
        if "none" in sel:
            parts.append("NOT EXISTS (SELECT 1 FROM task_discipline d WHERE d.task_id = t.id)")
        clauses.append("(" + " OR ".join(parts) + ")")
    for key, col in (("category", "t.category"), ("impact", "t.impact"), ("urgency", "t.urgency"), ("status", "t.status")):
        if f[key]:
            clauses.append(f"{col} IN ({','.join('?' * len(f[key]))})")
            args += f[key]
    if f["priority"]:
        parts = []
        real = [p for p in f["priority"] if p != "not_assessed"]
        if real:
            parts.append(f"COALESCE(t.priority_override, t.suggested_priority) IN ({','.join('?' * len(real))})")
            args += real
        if "not_assessed" in f["priority"]:
            parts.append("COALESCE(t.priority_override, t.suggested_priority) IS NULL")
        clauses.append("(" + " OR ".join(parts) + ")")
    if f["assignee"]:
        parts = []
        real = [a for a in f["assignee"] if a != "unassigned"]
        if real:
            parts.append(f"t.assignee_id IN ({','.join('?' * len(real))})")
            args += real
        if "unassigned" in f["assignee"]:
            parts.append("t.assignee_id IS NULL")
        clauses.append("(" + " OR ".join(parts) + ")")
    return clauses


def _matching_ids(conn, project_id, f):
    args = [project_id]
    clauses = ["t.project_id = ?", "t.review_state = 'approved'"] + _where(f, args)
    rows = conn.execute(
        "SELECT t.id FROM task t JOIN project p ON p.id = t.project_id WHERE " + " AND ".join(clauses), args)
    return {r["id"] for r in rows}


def list_tasks(conn, project_id, f):
    """Flat list of approved tasks (all levels) matching the filters."""
    get_project_row(conn, project_id)
    args = [project_id]
    clauses = ["t.project_id = ?", "t.review_state = 'approved'"] + _where(f, args)
    order = "CASE t.status " + " ".join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(STATUS_KEYS)) + " END"
    rows = conn.execute(f"{_SELECT} WHERE " + " AND ".join(clauses) + f" ORDER BY {order}, t.board_rank, t.ticket_number", args).fetchall()
    tasks = _hydrate(conn, rows)
    parents = {r["id"]: f"{r['ticket_prefix']}-{r['ticket_number']}" for r in conn.execute(
        _SELECT + " WHERE t.project_id = ?", (project_id,))}
    for t in tasks:
        t["parent_key"] = parents.get(t["parent_id"])
    return tasks


def board(conn, project_id, f):
    """Top-level approved tasks grouped by status, honouring filters.

    A top-level card is shown when it matches itself or when any descendant matches;
    in the latter case `matched_via_children` explains why it appears. Each card is
    listed once, however many descendants match.
    """
    get_project_row(conn, project_id)
    board_filters = dict(f, status=[])  # columns already express status
    active = filters_active(board_filters)
    matches = _matching_ids(conn, project_id, board_filters)
    parent_of = {r["id"]: r["parent_id"] for r in conn.execute(
        "SELECT id, parent_id FROM task WHERE project_id = ? AND review_state = 'approved'", (project_id,))}
    descendants_matching = {}
    for tid in matches:
        cur = parent_of.get(tid)
        while cur:
            descendants_matching[cur] = descendants_matching.get(cur, 0) + 1
            cur = parent_of.get(cur)
    top_ids = {tid for tid, pid in parent_of.items() if pid is None}
    visible = [tid for tid in top_ids if tid in matches or descendants_matching.get(tid)]
    rows = []
    for chunk in _chunks(visible):
        rows += conn.execute(f"{_SELECT} WHERE t.id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    rows.sort(key=lambda r: (r["board_rank"], r["ticket_number"]))
    cards = _hydrate(conn, rows)
    child_rows = {}
    if cards:
        for chunk in _chunks([c["id"] for c in cards]):
            for r in conn.execute(f"{_BRIEF_SQL} WHERE t.review_state = 'approved' AND t.parent_id IN ({','.join('?' * len(chunk))}) "
                                  "ORDER BY t.ticket_number", chunk):
                child_rows.setdefault(r["parent_id"], []).append(r)
    columns = {k: [] for k in STATUS_KEYS}
    for c in cards:
        c["self_match"] = c["id"] in matches
        c["matched_via_children"] = descendants_matching.get(c["id"], 0) if active else 0
        kids = []
        for r in child_rows.get(c["id"], []):
            b = _brief(r)
            b["matches"] = (r["id"] in matches) if active else True
            kids.append(b)
        c["children"] = kids
        c["children_hidden_by_filter"] = sum(1 for k in kids if not k["matches"]) if active else 0
        columns[c["status"]].append(c)
    return {
        "columns": columns,
        "counts": {k: len(v_) for k, v_ in columns.items()},
        "total_visible": len(cards),
        "total_top_level": len(top_ids),
        "filters_active": active,
    }


# ---- acceptance criteria ----------------------------------------------------

def add_criterion(conn, task_id, data):
    row = _get_row(conn, task_id)
    text = v.text(data, "text", max_len=1000, required=True)
    cid, now = new_id(), utc_now()
    with tx(conn):
        pos = (conn.execute("SELECT MAX(position) FROM acceptance_criterion WHERE task_id = ?", (task_id,)).fetchone()[0] or 0) + 1
        conn.execute(
            "INSERT INTO acceptance_criterion(id, project_id, task_id, text, checked, position, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 0, ?, ?, ?)", (cid, row["project_id"], task_id, text, pos, now, now))
        conn.execute("UPDATE task SET updated_at = ? WHERE id = ?", (now, task_id))
    return {"id": cid, "text": text, "checked": False, "position": pos}


def _criterion(conn, cid):
    r = conn.execute("SELECT * FROM acceptance_criterion WHERE id = ?", (cid,)).fetchone()
    if r is None:
        raise NotFound("Acceptance criterion not found")
    return r


def update_criterion(conn, cid, data):
    r = _criterion(conn, cid)
    text = v.text(data, "text", max_len=1000, default=r["text"])
    if not text:
        raise ValidationError("text cannot be empty", field="text")
    checked = v.boolean(data, "checked", bool(r["checked"]))
    now = utc_now()
    with tx(conn):
        conn.execute("UPDATE acceptance_criterion SET text = ?, checked = ?, updated_at = ? WHERE id = ?",
                     (text, int(checked), now, cid))
        conn.execute("UPDATE task SET updated_at = ? WHERE id = ?", (now, r["task_id"]))
    return {"id": cid, "text": text, "checked": checked, "position": r["position"]}


def delete_criterion(conn, cid):
    r = _criterion(conn, cid)
    with tx(conn):
        conn.execute("DELETE FROM acceptance_criterion WHERE id = ?", (cid,))
        conn.execute("UPDATE task SET updated_at = ? WHERE id = ?", (utc_now(), r["task_id"]))
    return {"deleted": cid}


# ---- dependencies -----------------------------------------------------------

def add_dependency(conn, task_id, depends_on_id, *, origin="manual"):
    row = _get_row(conn, task_id)
    if not depends_on_id or not isinstance(depends_on_id, str):
        raise ValidationError("depends_on_id is required", field="depends_on_id")
    target = conn.execute("SELECT id, project_id FROM task WHERE id = ?", (depends_on_id,)).fetchone()
    if target is None:
        raise ValidationError("The task to depend on does not exist", field="depends_on_id")
    if target["project_id"] != row["project_id"]:
        raise ValidationError("Dependencies cannot cross projects", field="depends_on_id")
    if depends_on_id == task_id:
        raise ValidationError("A task cannot depend on itself", field="depends_on_id")
    now = utc_now()
    with tx(conn):
        # Cycle check: does depends_on_id already (transitively) depend on task_id?
        cyc = conn.execute(
            "WITH RECURSIVE up(id) AS (SELECT depends_on_task_id FROM task_dependency WHERE task_id = ? "
            " UNION SELECT d.depends_on_task_id FROM task_dependency d JOIN up ON d.task_id = up.id) "
            "SELECT 1 FROM up WHERE id = ?", (depends_on_id, task_id)).fetchone()
        if cyc:
            raise ValidationError("This dependency would create a cycle", field="depends_on_id")
        try:
            conn.execute(
                "INSERT INTO task_dependency(project_id, task_id, depends_on_task_id, created_at) VALUES (?, ?, ?, ?)",
                (row["project_id"], task_id, depends_on_id, now))
        except sqlite3.IntegrityError:
            raise Conflict("That dependency already exists", code="duplicate_dependency")
        _record(conn, task_id, "dependency_added", None, depends_on_id, origin, now)
    return get_task(conn, task_id)


def remove_dependency(conn, task_id, depends_on_id, *, origin="manual"):
    _get_row(conn, task_id)
    now = utc_now()
    with tx(conn):
        cur = conn.execute("DELETE FROM task_dependency WHERE task_id = ? AND depends_on_task_id = ?", (task_id, depends_on_id))
        if cur.rowcount == 0:
            raise NotFound("Dependency not found")
        _record(conn, task_id, "dependency_removed", depends_on_id, None, origin, now)
    return get_task(conn, task_id)
