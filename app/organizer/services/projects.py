"""Projects and their local people."""

import re
import sqlite3
import uuid

from .. import validation as v
from ..db import tx, utc_now
from ..errors import Conflict, NotFound, ValidationError

_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def new_id():
    return uuid.uuid4().hex


def project_dict(row, task_count=None):
    d = {
        "id": row["id"],
        "name": row["name"],
        "ticket_prefix": row["ticket_prefix"],
        "description": row["description"],
        "next_ticket_number": row["next_ticket_number"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if task_count is not None:
        d["task_count"] = task_count
    return d


def get_project_row(conn, project_id):
    row = conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise NotFound("Project not found")
    return row


def list_projects(conn):
    rows = conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM task t WHERE t.project_id = p.id) AS task_count "
        "FROM project p ORDER BY lower(p.name), p.created_at"
    ).fetchall()
    return [project_dict(r, r["task_count"]) for r in rows]


def get_project(conn, project_id):
    row = get_project_row(conn, project_id)
    count = conn.execute("SELECT COUNT(*) FROM task WHERE project_id = ?", (project_id,)).fetchone()[0]
    return project_dict(row, count)


def create_project(conn, storage, data):
    name = v.text(data, "name", max_len=120, required=True)
    prefix = v.text(data, "ticket_prefix", max_len=10, required=True).upper()
    if not _PREFIX_RE.match(prefix):
        raise ValidationError(
            "Ticket prefix must be 2-10 characters: letters and digits, starting with a letter",
            field="ticket_prefix",
        )
    description = v.text(data, "description", max_len=5000, default="")
    pid, now = new_id(), utc_now()
    made_dirs = False
    try:
        with tx(conn):
            try:
                conn.execute(
                    "INSERT INTO project(id, name, ticket_prefix, description, next_ticket_number, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (pid, name, prefix, description, now, now),
                )
            except sqlite3.IntegrityError:
                raise Conflict(f"Ticket prefix {prefix} is already used by another project", code="prefix_taken")
            # Directory creation is inside the transaction: if it fails the row is rolled back.
            storage.create_project_dirs(pid)
            made_dirs = True
    except BaseException:
        if made_dirs:
            storage.remove_project_dirs(pid)
        raise
    return get_project(conn, pid)


def update_project(conn, project_id, data):
    row = get_project_row(conn, project_id)
    if "ticket_prefix" in data and str(data["ticket_prefix"]).upper() != row["ticket_prefix"]:
        raise ValidationError("The ticket prefix cannot be changed after creation", field="ticket_prefix")
    name = v.text(data, "name", max_len=120, default=row["name"])
    if not name:
        raise ValidationError("name cannot be empty", field="name")
    description = v.text(data, "description", max_len=5000, default=row["description"])
    with tx(conn):
        conn.execute(
            "UPDATE project SET name = ?, description = ?, updated_at = ? WHERE id = ?",
            (name, description, utc_now(), project_id),
        )
    return get_project(conn, project_id)


def delete_project(conn, storage, project_id, confirm_prefix):
    row = get_project_row(conn, project_id)
    if confirm_prefix != row["ticket_prefix"]:
        raise ValidationError("Type the project's ticket prefix to confirm deletion", field="confirm")
    with tx(conn):
        # Cascades remove tasks, people, tags, criteria, dependencies, and history.
        conn.execute("DELETE FROM project WHERE id = ?", (project_id,))
    trashed = None
    try:
        trashed = storage.trash_project_dirs(project_id)
    except OSError:
        pass  # Database rows are gone; leaving the folder behind is harmless.
    return {"deleted": project_id, "files_moved_to": trashed}


# ---- people -----------------------------------------------------------------

def person_dict(row):
    return {"id": row["id"], "project_id": row["project_id"], "name": row["name"]}


def list_people(conn, project_id):
    get_project_row(conn, project_id)
    rows = conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM task t WHERE t.assignee_id = p.id) AS assigned_count "
        "FROM person p WHERE p.project_id = ? ORDER BY lower(p.name)",
        (project_id,),
    ).fetchall()
    return [dict(person_dict(r), assigned_count=r["assigned_count"]) for r in rows]


def create_person(conn, project_id, data):
    get_project_row(conn, project_id)
    name = " ".join(v.text(data, "name", max_len=80, required=True).split())
    pid = new_id()
    try:
        with tx(conn):
            conn.execute(
                "INSERT INTO person(id, project_id, name, created_at) VALUES (?, ?, ?, ?)",
                (pid, project_id, name, utc_now()),
            )
    except sqlite3.IntegrityError:
        raise Conflict(f"A person named '{name}' already exists in this project", code="person_exists")
    return {"id": pid, "project_id": project_id, "name": name, "assigned_count": 0}


def _person_row(conn, person_id):
    row = conn.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        raise NotFound("Person not found")
    return row


def rename_person(conn, person_id, data):
    row = _person_row(conn, person_id)
    name = " ".join(v.text(data, "name", max_len=80, required=True).split())
    try:
        with tx(conn):
            conn.execute("UPDATE person SET name = ? WHERE id = ?", (name, person_id))
    except sqlite3.IntegrityError:
        raise Conflict(f"A person named '{name}' already exists in this project", code="person_exists")
    return {"id": row["id"], "project_id": row["project_id"], "name": name}


def delete_person(conn, person_id):
    row = _person_row(conn, person_id)
    n = conn.execute("SELECT COUNT(*) FROM task WHERE assignee_id = ?", (person_id,)).fetchone()[0]
    if n:
        raise Conflict(
            f"{row['name']} is assigned to {n} task{'s' if n != 1 else ''}. Reassign them first.",
            code="person_in_use",
        )
    with tx(conn):
        conn.execute("DELETE FROM person WHERE id = ?", (person_id,))
    return {"deleted": person_id}
