"""Managed import of an HTML brief and its companion media folder.

Pipeline (see AIWorkFlow/Architecture/offline-processing.md):
  select -> inventory -> parse -> copy and hash -> compare with earlier imports -> commit

Safety properties this module is responsible for:
  * Reference paths are decoded and validated before any file is touched. Absolute paths,
    parent-directory segments, backslashes, control characters, file: addresses, symlinks and
    anything that resolves outside the selected companion folder are refused and reported.
  * Files are copied and hashed in one streaming pass into content-addressed storage.
  * Database rows for a document are written in ONE transaction at the very end, so a failed or
    interrupted import leaves either the previous state or nothing (plus a visible failed run).
  * Nothing here opens a network connection.
"""

import hashlib
import json
import os
import shutil
import stat
import threading
import unicodedata
import uuid
from collections import Counter

from . import scenarios as scenario_mod
from .db import connect, tx, utc_now
from .errors import AppError, Conflict, NotFound, ValidationError
from .htmlparse import TooLarge
from .sourceparse import PARSER_VERSION, kind_for_name, parse_document

MAX_HTML_BYTES = 64 * 1024 * 1024
CHUNK = 1024 * 1024
MAX_WALK_FILES = 50_000
SAFETY_MARGIN = 64 * 1024 * 1024

EXT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".bmp": "image/bmp", ".avif": "image/avif", ".svg": "image/svg+xml", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".webm": "video/webm", ".mov": "video/quicktime", ".ogv": "video/ogg", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".pdf": "application/pdf",
}
# Types the browser may be asked to display inline. Everything else (including SVG, which can
# carry script) is only ever offered as a download.
INLINE_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif",
    "video/mp4", "video/webm", "video/quicktime", "video/ogg", "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4",
}


class ImportProblem(AppError):
    status = 400
    code = "import_problem"


class ImportCancelled(Exception):
    pass


# ---- selection and path safety ----------------------------------------------------

def _under(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def check_allowed(path, roots):
    if not roots:
        return
    real = os.path.realpath(path)
    if not any(_under(real, os.path.realpath(r)) for r in roots):
        raise ImportProblem("That location is outside the folders this service was started with (--import-root).",
                            code="outside_import_root")


def _expand(path):
    p = os.path.expanduser((path or "").strip())
    if not p:
        raise ImportProblem("Choose an HTML file to import.", code="no_file")
    if "\x00" in p:
        raise ImportProblem("That path is not valid.", code="bad_path")
    if not os.path.isabs(p):
        raise ImportProblem("Enter the full path of the file (it should start with / or ~).", code="relative_path")
    return os.path.normpath(p)


class Selection:
    def __init__(self, html_path, html_dir, companion, auto_detected):
        self.media_prefix = None  # the export's own media folder name, learned from its references
        self.html_path = html_path
        self.html_dir = html_dir
        self.companion = companion
        self.auto_detected = auto_detected
        self.name = os.path.basename(html_path)


def _detect_companion(html_dir, filename):
    stem = os.path.splitext(filename)[0]
    base = stem
    parts = stem.rsplit(" ", 1)
    if len(parts) == 2 and len(parts[1]) == 32 and all(c in "0123456789abcdef" for c in parts[1].lower()):
        base = parts[0]
    for candidate in (base, stem):
        d = os.path.join(html_dir, candidate)
        if os.path.isdir(d) and not os.path.islink(d):
            return d
    return None


def resolve_selection(html_path, companion_dir=None, roots=()):
    p = _expand(html_path)
    if not os.path.exists(p):
        raise ImportProblem(f"No file exists at {p}.", code="file_not_found")
    real = os.path.realpath(p)
    if not os.path.isfile(real):
        raise ImportProblem("That path is a folder, not an HTML file.", code="not_a_file")
    if os.path.splitext(real)[1].lower() not in (".html", ".htm", ".xhtml"):
        raise ImportProblem("Choose an .html file.", code="not_html")
    check_allowed(real, roots)
    try:
        size = os.path.getsize(real)
    except OSError as exc:
        raise ImportProblem(f"The file cannot be read: {exc.strerror or exc}.", code="unreadable")
    if size > MAX_HTML_BYTES:
        raise ImportProblem(f"The HTML file is {size // (1024 * 1024)} MB; the limit is {MAX_HTML_BYTES // (1024 * 1024)} MB.", code="too_large")
    html_dir = os.path.dirname(real)
    comp, auto = None, False
    if companion_dir and companion_dir.strip():
        c = _expand(companion_dir)
        if not os.path.exists(c):
            raise ImportProblem(f"The companion folder does not exist: {c}.", code="companion_not_found")
        creal = os.path.realpath(c)
        if not os.path.isdir(creal):
            raise ImportProblem("The companion path is a file, not a folder.", code="companion_not_dir")
        check_allowed(creal, roots)
        comp = creal
    else:
        found = _detect_companion(html_dir, os.path.basename(real))
        if found:
            comp, auto = os.path.realpath(found), True
    return Selection(real, html_dir, comp, auto)


def _nfc(s):
    return unicodedata.normalize("NFC", s)


def _nfd(s):
    return unicodedata.normalize("NFD", s)


class AssetInfo:
    def __init__(self, source_path):
        self.source_path = source_path
        self.name = source_path.rsplit("/", 1)[-1]
        self.status = "missing"  # available | missing | blocked | unreadable
        self.problem = ""
        self.abs_path = None
        self.size = None
        # filled in when copied
        self.relative_path = None
        self.content_hash = None
        self.media_type = ""
        self.created_file = None
        self.warning = ""


def locate_asset(source_path, selection, syntax_problem=None):
    """Find the file a reference names. Never reads outside the companion folder.

    The export names its media folder in every path (`<original folder>/<file>`), but the folder a
    person selects may have been renamed or moved. Candidates are tried in order: the selected
    folder's own name, the path relative to the HTML file, and the export's original media prefix
    (the first segment its references share). Each candidate must stay inside the selected folder.
    """
    info = AssetInfo(source_path)
    if syntax_problem:
        info.status, info.problem = "blocked", f"Not read: {syntax_problem}."
        return info
    comp = selection.companion
    if comp is None:
        info.problem = "No companion folder was selected, so this file could not be looked up."
        return info
    segs = source_path.split("/")
    comp_name = os.path.basename(comp)
    first = _nfc(segs[0]).casefold()
    prefix = getattr(selection, "media_prefix", None)
    candidates = []
    if first == _nfc(comp_name).casefold():
        candidates.append((comp, segs[1:]))
    candidates.append((selection.html_dir, segs))
    if prefix and first == _nfc(prefix).casefold() and len(segs) > 1:
        candidates.append((comp, segs[1:]))
    outside = False
    for base, rest in candidates:
        if not rest:
            info.status, info.problem = "blocked", "Not read: the reference names a folder."
            return info
        for variant in (rest, [_nfc(s) for s in rest], [_nfd(s) for s in rest]):
            target = os.path.normpath(os.path.join(base, *variant))
            if not _under(target, os.path.normpath(comp)):
                if os.path.lexists(target):
                    outside = True  # a real file, but not inside the folder that was chosen
                break
            # No symlink anywhere between the companion folder and the file.
            cur = comp
            linked = False
            for part in os.path.relpath(target, comp).split(os.sep):
                cur = os.path.join(cur, part)
                if os.path.islink(cur):
                    linked = True
                    break
                if not os.path.lexists(cur):
                    break
            if linked:
                info.status, info.problem = "blocked", "Not read: the path goes through a symbolic link."
                return info
            try:
                st = os.lstat(target)
            except FileNotFoundError:
                continue
            except OSError as exc:
                info.status, info.problem = "unreadable", f"The file cannot be read: {exc.strerror or exc}."
                return info
            if not stat.S_ISREG(st.st_mode):
                info.status, info.problem = "blocked", "Not read: the path is not a regular file."
                return info
            if not _under(os.path.realpath(target), os.path.realpath(comp)):
                info.status, info.problem = "blocked", "Not read: the file resolves outside the companion folder."
                return info
            info.status, info.abs_path, info.size = "available", target, st.st_size
            return info
    if outside:
        info.status, info.problem = "blocked", "Not read: the path points outside the selected companion folder."
        return info
    info.problem = "The referenced file was not found in the companion folder."
    return info


# ---- type sniffing -----------------------------------------------------------------

_COMPATIBLE_TYPES = [
    {"video/mp4", "audio/mp4", "video/quicktime"},
    {"audio/ogg", "video/ogg"},
    {"video/webm", "video/x-matroska"},
]


def sniff_media_type(head, name):
    """(media_type, warning). Content decides; the extension is only a fallback and a cross-check."""
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    by_ext = EXT_TYPES.get(ext)
    found = None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        found = "image/png"
    elif head.startswith(b"\xff\xd8\xff"):
        found = "image/jpeg"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        found = "image/gif"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        found = "image/webp"
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        found = "audio/wav"
    elif head[:2] == b"BM" and ext == ".bmp":
        found = "image/bmp"
    elif head[4:8] == b"ftyp":
        brand = head[8:12]
        found = ("image/avif" if brand in (b"avif", b"avis") else "video/quicktime" if brand == b"qt  "
                 else "audio/mp4" if brand in (b"M4A ", b"M4B ") else "video/mp4")
    elif head.startswith(b"\x1a\x45\xdf\xa3"):
        found = "video/webm" if b"webm" in head[:64] else "video/x-matroska"
    elif head.startswith(b"OggS"):
        found = "video/ogg" if ext == ".ogv" else "audio/ogg"
    elif head.startswith(b"%PDF"):
        found = "application/pdf"
    elif head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        found = "audio/mpeg"
    else:
        lead = head[:256].lstrip().lower()
        if lead.startswith(b"<svg") or (lead.startswith(b"<?xml") and b"<svg" in head[:512].lower()):
            found = "image/svg+xml"
    if found and by_ext and found != by_ext and not any({found, by_ext} <= g for g in _COMPATIBLE_TYPES):
        return found, f"The file extension suggests {by_ext} but the content looks like {found}."
    if found:
        return found, ""
    if by_ext:
        if by_ext.split("/")[0] in ("image", "video", "audio") and head:
            return by_ext, f"The content could not be verified as {by_ext}; it is shown by its file extension."
        return by_ext, ""
    return "application/octet-stream", ""


def kind_from_media_type(media_type, fallback):
    top = media_type.split("/")[0]
    return top if top in ("image", "video", "audio") else "attachment"


# ---- copying -----------------------------------------------------------------------

class Progress:
    def __init__(self):
        self.stage = "queued"
        self.files_total = 0
        self.files_done = 0
        self.bytes_total = 0
        self.bytes_done = 0
        self.current = ""
        self.cancelled = False

    def snapshot(self):
        return {"stage": self.stage, "files_total": self.files_total, "files_done": self.files_done,
                "bytes_total": self.bytes_total, "bytes_done": self.bytes_done, "current": self.current}

    def check(self):
        if self.cancelled:
            raise ImportCancelled()


def store_file(storage, project_id, info, progress):
    """Copy one file into content-addressed storage while hashing it. Returns True if a new file was written."""
    incoming = storage.incoming_dir(project_id)
    os.makedirs(incoming, exist_ok=True)
    tmp = os.path.join(incoming, uuid.uuid4().hex + ".part")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    sha = hashlib.sha256()
    size = 0
    head = b""
    try:
        fd = os.open(info.abs_path, flags)
    except OSError as exc:
        info.status, info.problem = "unreadable", f"The file cannot be opened: {exc.strerror or exc}."
        return False
    try:
        with os.fdopen(fd, "rb") as src:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                info.status, info.problem = "blocked", "Not read: the path is not a regular file."
                return False
            try:
                with open(tmp, "wb") as dst:
                    while True:
                        progress.check()
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        if not head:
                            head = chunk[:512]
                        sha.update(chunk)
                        dst.write(chunk)
                        size += len(chunk)
                        progress.bytes_done += len(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
            except OSError as exc:
                info.status, info.problem = "unreadable", f"The file could not be copied: {exc.strerror or exc}."
                _silent_remove(tmp)
                return False
        digest = sha.hexdigest()
        ext = os.path.splitext(info.name)[1]
        rel = storage.asset_relative_path(digest, ext)
        final = storage.resolve_in_project(project_id, rel)
        os.makedirs(os.path.dirname(final), exist_ok=True)
        created = False
        if os.path.exists(final) and os.path.getsize(final) == size:
            _silent_remove(tmp)
        else:
            os.replace(tmp, final)
            created = True
        media_type, warning = sniff_media_type(head, info.name)
        info.relative_path, info.content_hash, info.size = rel, digest, size
        info.media_type, info.warning = media_type, warning
        info.created_file = final if created else None
        return created
    except BaseException:
        _silent_remove(tmp)
        raise


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def manifest_hash(entries):
    """entries: iterable of (source_path, content_hash or status). Stable across runs."""
    lines = sorted(f"{p}\t{h}" for p, h in entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def asset_manifest_entry(info):
    return (info.source_path, info.content_hash if info.status == "available" else info.status.upper())


# ---- inspection (no writes) ---------------------------------------------------------

def read_html(selection):
    try:
        with open(selection.html_path, "rb") as fh:
            raw = fh.read(MAX_HTML_BYTES + 1)
    except OSError as exc:
        raise ImportProblem(f"The HTML file cannot be read: {exc.strerror or exc}.", code="unreadable")
    if len(raw) > MAX_HTML_BYTES:
        raise ImportProblem("The HTML file is too large to import.", code="too_large")
    try:
        text, note = raw.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        text, note = raw.decode("utf-8", errors="replace"), "The file is not valid UTF-8; unreadable characters were replaced."
    return raw, text, note


class Analysis:
    """Everything learned about a selection without writing anything."""

    def __init__(self, selection, raw, doc, html_note):
        self.selection = selection
        self.raw = raw
        self.html_hash = hashlib.sha256(raw).hexdigest()
        self.doc = doc
        self.html_note = html_note
        self.assets = {}  # source_path -> AssetInfo (first-appearance order)
        self.scenarios = []  # (table_block, extraction)
        self.skipped_scenario_tables = []


def analyse(selection):
    raw, text, note = read_html(selection)
    try:
        doc = parse_document(text, selection.name)
    except TooLarge as exc:
        raise ImportProblem(str(exc), code="too_large")
    a = Analysis(selection, raw, doc, note)
    selection.media_prefix = _common_prefix(doc)
    if selection.companion is None:
        guess = _guess_companion(selection, doc)
        if guess:
            selection.companion, selection.auto_detected = guess, True
    for ref in doc.refs:
        if ref.source_path and ref.source_path not in a.assets:
            a.assets[ref.source_path] = locate_asset(ref.source_path, selection, ref.problem)
    for block in doc.tables():
        result = scenario_mod.extract_from_table(block)
        if result is None:
            continue
        role = doc.sections[block.section_index].role
        if role == "historical":
            a.skipped_scenario_tables.append(block)
        else:
            a.scenarios.append((block, result))
    return a


def _common_prefix(doc):
    """The first path segment most of the document's local references share (its media folder name)."""
    firsts = Counter(r.source_path.split("/")[0] for r in doc.refs if r.source_path and "/" in r.source_path and not r.problem)
    if not firsts:
        return None
    name, n = firsts.most_common(1)[0]
    return name if n * 2 >= sum(firsts.values()) else None


def _guess_companion(selection, doc):
    firsts = Counter(r.source_path.split("/")[0] for r in doc.refs if r.source_path and "/" in r.source_path and not r.problem)
    for name, _ in firsts.most_common():
        d = os.path.join(selection.html_dir, name)
        if os.path.isdir(d) and not os.path.islink(d):
            return os.path.realpath(d)
    return None


def list_companion_files(companion):
    """Relative paths (NFC, casefolded) of regular files under the companion folder, without following links."""
    files, truncated = {}, False
    stack = [companion]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        if len(files) >= MAX_WALK_FILES:
                            truncated = True
                            return files, truncated
                        try:
                            size = e.stat(follow_symlinks=False).st_size
                        except OSError:
                            size = 0
                        rel = os.path.relpath(e.path, companion).replace(os.sep, "/")
                        files[_nfc(rel).casefold()] = (rel, size)
        except OSError:
            continue
    return files, truncated


def find_prior(conn, project_id, analysis):
    """Compare with documents already imported into this project (by HTML hash and name)."""
    same = conn.execute(
        "SELECT * FROM source_document WHERE project_id = ? AND content_hash = ? ORDER BY created_at DESC",
        (project_id, analysis.html_hash)).fetchall()
    if same:
        return "same_html", same[0]
    named = conn.execute(
        "SELECT * FROM source_document WHERE project_id = ? AND original_name = ? ORDER BY version DESC",
        (project_id, analysis.selection.name)).fetchall()
    if named:
        return "new_version", named[0]
    return "new", None


def preview(storage, conn, project_id, html_path, companion_dir, roots=()):
    sel = resolve_selection(html_path, companion_dir, roots)
    a = analyse(sel)
    doc = a.doc
    assets = list(a.assets.values())
    by_status = Counter(i.status for i in assets)
    by_type = Counter(kind_for_name(i.name) for i in assets if i.status == "available")
    total_bytes = sum(i.size or 0 for i in assets if i.status == "available")
    ref_counts = Counter(r.kind for r in doc.refs)
    hosts = Counter()
    for r in doc.refs:
        if r.external_url:
            hosts[r.external_url.split("//", 1)[-1].split("/", 1)[0]] += 1
    unref, files_total, truncated = [], 0, False
    if sel.companion:
        files, truncated = list_companion_files(sel.companion)
        files_total = len(files)
        used = set()
        for i in assets:
            if i.abs_path:
                used.add(_nfc(os.path.relpath(i.abs_path, sel.companion).replace(os.sep, "/")).casefold())
        unref = [v for k, v in files.items() if k not in used]
    prior_state, prior = find_prior(conn, project_id, a)
    prior_info = {"status": prior_state}
    if prior is not None:
        prior_info.update(document_id=prior["id"], version=prior["version"], imported_at=prior["created_at"])
        if prior_state == "same_html":
            rows = {r["source_path"]: r for r in conn.execute("SELECT * FROM asset WHERE document_id = ?", (prior["id"],))}
            same = all(
                (p in rows and rows[p]["availability"] == i.status and (i.status != "available" or rows[p]["size_bytes"] == i.size))
                for p, i in a.assets.items()) and len(rows) == len(a.assets)
            prior_info["assets_match"] = same
    try:
        free = shutil.disk_usage(storage.data_dir).free
    except OSError:
        free = None
    needed = total_bytes + SAFETY_MARGIN
    historical = [s.title for s in doc.sections if s.role == "historical" and s.level and (s.parent_index is None or doc.sections[s.parent_index].role != "historical")]
    scen_ids = [d["identifier"] for _, res in a.scenarios for d in res["drafts"]]
    warnings = []
    if a.html_note:
        warnings.append(a.html_note)
    if sel.companion is None:
        warnings.append("No companion folder was found next to the HTML file. Local images and videos will be reported as missing. Choose the folder to include them.")
    for sev, code, msg, _ in doc.diagnostics:
        if sev in ("warning", "error"):
            warnings.append(msg)
    return {
        "html": {"path": sel.html_path, "name": sel.name, "size": len(a.raw), "sha256": a.html_hash, "title": doc.title},
        "companion": {"path": sel.companion, "auto_detected": sel.auto_detected, "file_count": files_total, "truncated": truncated},
        "structure": {
            "blocks": len(doc.blocks), "sections": len(doc.sections), "tables": len(doc.tables()),
            "headings": sum(1 for b in doc.blocks if b.block_type == "heading"),
            "historical_sections": historical,
            "scenario_tables": len(a.scenarios), "scenarios": len(scen_ids), "scenario_ids": scen_ids,
            "skipped_scenario_tables": len(a.skipped_scenario_tables),
        },
        "assets": {
            "referenced": len(assets), "available": by_status.get("available", 0), "missing": by_status.get("missing", 0),
            "blocked": by_status.get("blocked", 0), "unreadable": by_status.get("unreadable", 0),
            "total_bytes": total_bytes, "by_type": dict(by_type),
            "problems": [{"path": i.source_path, "status": i.status, "problem": i.problem} for i in assets if i.status != "available"][:200],
            "unreferenced_count": len(unref), "unreferenced_sample": [u[0] for u in unref[:10]],
        },
        "external": {"links": ref_counts.get("external_link", 0), "images": ref_counts.get("external_image", 0), "hosts": dict(hosts.most_common(10))},
        "prior": prior_info,
        "disk": {"free_bytes": free, "needed_bytes": needed, "enough": free is None or free >= needed},
        "warnings": warnings,
        "can_import": free is None or free >= needed,
    }


# ---- the import itself -------------------------------------------------------------

def safe_filename(name):
    cleaned = "".join(ch for ch in name if ch.isprintable() and ch not in '/\\:*?"<>|').strip().strip(".")
    return (cleaned[:120] or "source.html")


def create_run(conn, project_id, source_name):
    run_id = uuid.uuid4().hex
    with tx(conn):
        conn.execute(
            "INSERT INTO import_run(id, project_id, kind, parser_version, state, stage, source_name, created_at) "
            "VALUES (?, ?, 'import', ?, 'running', 'starting', ?, ?)",
            (run_id, project_id, PARSER_VERSION, source_name, utc_now()))
    return run_id


def _finish_run(conn, run_id, state, *, error="", note="", document_id=None, kind=None):
    with tx(conn):
        conn.execute(
            "UPDATE import_run SET state = ?, error = ?, note = ?, finished_at = ?, "
            "document_id = COALESCE(?, document_id), kind = COALESCE(?, kind), stage = ? WHERE id = ?",
            (state, error, note, utc_now(), document_id, kind, state, run_id))


def execute_import(storage, conn, project_id, run_id, html_path, companion_dir, roots, progress):
    """Run the whole pipeline. Always leaves the run row in a final state. Returns the result dict."""
    created_files = []
    source_dir = None
    try:
        progress.stage = "reading"
        progress.check()
        sel = resolve_selection(html_path, companion_dir, roots)
        progress.stage = "analysing"
        a = analyse(sel)
        progress.check()
        storage.ensure_project_dirs(project_id)
        available = [i for i in a.assets.values() if i.status == "available"]
        progress.files_total = len(available)
        progress.bytes_total = sum(i.size for i in available)
        free = shutil.disk_usage(storage.data_dir).free
        if free < progress.bytes_total + SAFETY_MARGIN:
            raise ImportProblem(
                f"Not enough free disk space: {progress.bytes_total // (1024 * 1024)} MB of media plus a safety margin are needed, "
                f"{free // (1024 * 1024)} MB is free.", code="no_space")
        progress.stage = "copying"
        for info in available:
            progress.current = info.source_path
            store_file(storage, project_id, info, progress)
            if info.created_file:
                created_files.append(info.created_file)
            progress.files_done += 1
        progress.current = ""
        manifest = manifest_hash(asset_manifest_entry(i) for i in a.assets.values())
        progress.stage = "comparing"
        state, prior = find_prior(conn, project_id, a)
        if state == "same_html":
            return _reuse_existing(conn, storage, project_id, run_id, a, prior, manifest, created_files)
        progress.stage = "saving"
        progress.check()
        doc_id = uuid.uuid4().hex
        fname = safe_filename(sel.name)
        source_dir = storage.sources_dir(project_id, doc_id)
        os.makedirs(source_dir, exist_ok=False)
        with open(os.path.join(source_dir, fname), "wb") as fh:
            fh.write(a.raw)
            fh.flush()
            os.fsync(fh.fileno())
        version = (prior["version"] + 1) if prior is not None else 1
        summary = commit_document(conn, project_id, run_id, doc_id, a, manifest, version, f"sources/{doc_id}/{fname}")
        _finish_run(conn, run_id, "succeeded", document_id=doc_id, note=summary["note"])
        progress.stage = "done"
        return dict(summary, state="succeeded", document_id=doc_id)
    except ImportCancelled:
        _cleanup(storage, created_files, source_dir)
        _finish_run(conn, run_id, "cancelled", error="Import cancelled. Nothing was added to the project.")
        progress.stage = "cancelled"
        return {"state": "cancelled"}
    except AppError as exc:
        _cleanup(storage, created_files, source_dir)
        _finish_run(conn, run_id, "failed", error=exc.message)
        progress.stage = "failed"
        raise
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised for the caller
        _cleanup(storage, created_files, source_dir)
        _finish_run(conn, run_id, "failed", error=f"Unexpected error: {exc}")
        progress.stage = "failed"
        raise


def _cleanup(storage, created_files, source_dir):
    for path in created_files:
        _silent_remove(path)
    if source_dir:
        shutil.rmtree(source_dir, ignore_errors=True)


def _reuse_existing(conn, storage, project_id, run_id, a, prior, manifest, created_files):
    """Same HTML as an earlier import: never create a second copy of its content."""
    doc_id = prior["id"]
    if manifest == prior["manifest_hash"]:
        _finish_run(conn, run_id, "identical", document_id=doc_id,
                    note="Identical to an earlier import (same HTML and same files). Nothing was changed.")
        return {"state": "identical", "document_id": doc_id, "note": "Identical to an earlier import. Nothing was changed."}
    rows = {r["source_path"]: r for r in conn.execute("SELECT * FROM asset WHERE document_id = ?", (doc_id,))}
    recovered, ignored = 0, []
    now = utc_now()
    with tx(conn):
        for path, info in a.assets.items():
            row = rows.get(path)
            if row is None:
                continue
            if row["availability"] != "available" and info.status == "available":
                conn.execute(
                    "UPDATE asset SET relative_path = ?, media_type = ?, size_bytes = ?, content_hash = ?, "
                    "availability = 'available', problem = '', updated_at = ? WHERE id = ?",
                    (info.relative_path, info.media_type, info.size, info.content_hash, now, row["id"]))
                new_kind = kind_from_media_type(info.media_type, kind_for_name(info.name))
                conn.execute("UPDATE reference SET kind = ? WHERE asset_id = ? AND kind IN ('image','video','audio','attachment')",
                             (new_kind, row["id"]))
                recovered += 1
            elif row["availability"] == "available" and (info.status != "available" or info.content_hash != row["content_hash"]):
                ignored.append(path)
        final_rows = conn.execute("SELECT source_path, content_hash, availability FROM asset WHERE document_id = ?", (doc_id,)).fetchall()
        new_manifest = manifest_hash((r["source_path"], r["content_hash"] if r["availability"] == "available" else r["availability"].upper())
                                     for r in final_rows)
        conn.execute("UPDATE source_document SET manifest_hash = ? WHERE id = ?", (new_manifest, doc_id))
    # Files that were copied only to be compared, but are not used by any asset row, are garbage.
    used = {r["relative_path"] for r in conn.execute("SELECT relative_path FROM asset WHERE project_id = ?", (project_id,)) if r["relative_path"]}
    for path in created_files:
        rel = os.path.relpath(path, storage.project_dir(project_id)).replace(os.sep, "/")
        if rel not in used:
            _silent_remove(path)
    if recovered:
        note = f"Same HTML as an earlier import; {recovered} previously unavailable file(s) were added. The document content was not changed."
        state, kind = "succeeded", "asset_refresh"
    else:
        note = "Same HTML as an earlier import; no previously unavailable file could be added. Nothing was changed."
        state, kind = "identical", None
    if ignored:
        note += f" {len(ignored)} file(s) that differ from the earlier import were left as they were."
    _finish_run(conn, run_id, state, document_id=doc_id, note=note, kind=kind)
    return {"state": state, "document_id": doc_id, "note": note, "assets_recovered": recovered, "assets_ignored": ignored}


def commit_document(conn, project_id, run_id, doc_id, a, manifest, version, rel_source_path):
    """Write the document, blocks, assets, references, scenarios and diagnostics in one transaction."""
    doc = a.doc
    now = utc_now()
    asset_ids = {p: uuid.uuid4().hex for p in a.assets}
    block_by_seq = {b.sequence: b for b in doc.blocks}

    # reference kind follows what the file really is, when we could look
    for ref in doc.refs:
        info = a.assets.get(ref.source_path) if ref.source_path else None
        if info is not None and info.status == "available":
            ref.kind = kind_from_media_type(info.media_type, ref.kind)

    diagnostics = [(sev, code, msg, seq) for sev, code, msg, seq in doc.diagnostics]
    if a.html_note:
        diagnostics.append(("warning", "html_encoding", a.html_note, None))
    for info in a.assets.values():
        if info.warning:
            diagnostics.append(("warning", "media_type", f"{info.source_path}: {info.warning}", None))
    for block in a.skipped_scenario_tables:
        diagnostics.append(("info", "scenario_table_historical",
                            "A scenario table inside a historical section was not extracted. Mark its section current and choose “Re-extract scenarios” to use it.",
                            block.sequence))

    scenario_rows = []
    for block, res in a.scenarios:
        for sev, code, msg, row in res["notes"]:
            diagnostics.append((sev, code, msg, block.sequence))
        for d in res["drafts"]:
            scenario_rows.append((block, d))

    with tx(conn):
        conn.execute(
            "INSERT INTO source_document(id, project_id, original_name, title, local_relative_path, content_hash, size_bytes, "
            "version, companion_name, manifest_hash, parser_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, project_id, a.selection.name, doc.title, rel_source_path, a.html_hash, len(a.raw), version,
             os.path.basename(a.selection.companion) if a.selection.companion else "", manifest, doc.parser_version, now))
        conn.execute("UPDATE import_run SET document_id = ? WHERE id = ?", (doc_id, run_id))
        section_ids = {}
        for s in doc.sections:
            section_ids[s.index] = s.id
            conn.execute(
                "INSERT INTO source_section(id, project_id, document_id, parent_section_id, position, level, title, heading_path, "
                "heading_block_id, first_sequence, last_sequence, role, role_origin, role_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'parser', ?)",
                (s.id, project_id, doc_id, section_ids.get(s.parent_index), s.index, s.level, s.title,
                 json.dumps(s.path, ensure_ascii=False), s.heading_block_id, s.first_sequence, s.last_sequence, s.role, s.role_reason))
        for b in doc.blocks:
            conn.execute(
                "INSERT INTO source_block(id, project_id, document_id, import_run_id, section_id, source_element_id, locator, heading_path, "
                "sequence, depth, parent_block_id, block_type, variant, heading_level, list_kind, list_index, checked, table_rows, table_cols, "
                "table_header_rows, column_group, column_index, original_text, text_hash, inline_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (b.id, project_id, doc_id, run_id, section_ids[b.section_index], b.element_id, b.locator,
                 json.dumps(b.heading_path, ensure_ascii=False), b.sequence, b.depth, b.parent_id, b.block_type, b.variant,
                 b.heading_level, b.list_kind, b.list_index, b.checked, b.table_rows, b.table_cols, b.table_header_rows,
                 b.column_group, b.column_index, b.text, hashlib.sha256(b.text.encode("utf-8")).hexdigest(),
                 json.dumps(b.runs, ensure_ascii=False)))
            for c in b.cells:
                conn.execute(
                    "INSERT INTO source_cell(id, project_id, block_id, row_index, col_index, is_header, row_element_id, source_element_id, "
                    "original_text, inline_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, project_id, b.id, c.row, c.col, c.is_header, c.row_element_id, c.element_id, c.text,
                     json.dumps(c.runs, ensure_ascii=False)))
        for path, info in a.assets.items():
            conn.execute(
                "INSERT INTO asset(id, project_id, document_id, source_path, original_name, relative_path, media_type, size_bytes, "
                "content_hash, availability, problem, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (asset_ids[path], project_id, doc_id, path, info.name,
                 info.relative_path if info.status == "available" else None, info.media_type,
                 info.size if info.status == "available" else None,
                 info.content_hash if info.status == "available" else None, info.status, info.problem, now, now))
        for ref in doc.refs:
            asset_id = asset_ids.get(ref.source_path) if ref.source_path else None
            conn.execute(
                "INSERT INTO reference(id, project_id, document_id, source_block_id, kind, placement, position, asset_id, external_url, "
                "raw_target, link_text, caption) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ref.id, project_id, doc_id, ref.block_id, ref.kind, ref.placement, ref.position, asset_id, ref.external_url,
                 ref.raw_target or "", ref.link_text or "", ref.caption or ""))
        for pos, (block, d) in enumerate(scenario_rows):
            sid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO scenario(id, project_id, document_id, import_run_id, position, source_identifier, identifier_raw, title, "
                "description, preamble, segmentation, source_block_id, row_index, row_element_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, project_id, doc_id, run_id, pos, d["identifier"], d["identifier_raw"], d["title"], d["description"],
                 d["preamble"], d["segmentation"], block.id, d["row_index"], d["row_element_id"]))
            for i, step in enumerate(d["steps"]):
                conn.execute("INSERT INTO scenario_step(id, scenario_id, sequence, number_label, text, raw_text) VALUES (?,?,?,?,?,?)",
                             (uuid.uuid4().hex, sid, i, step["label"], step["text"], step["raw"]))
        for pos, (sev, code, msg, seq) in enumerate(diagnostics):
            blk = block_by_seq.get(seq) if seq is not None else None
            conn.execute(
                "INSERT INTO import_diagnostic(id, project_id, document_id, import_run_id, severity, code, message, source_block_id, position) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, project_id, doc_id, run_id, sev, code, msg, blk.id if blk else None, pos))
    by_status = Counter(i.status for i in a.assets.values())
    missing = len(a.assets) - by_status.get("available", 0)
    note = (f"Imported {len(doc.blocks)} blocks in {len(doc.sections)} sections, {len(scenario_rows)} scenarios, "
            f"{by_status.get('available', 0)} of {len(a.assets)} files" + (f" ({missing} unavailable)" if missing else "") + ".")
    return {"note": note, "blocks": len(doc.blocks), "sections": len(doc.sections), "scenarios": len(scenario_rows),
            "assets_total": len(a.assets), "assets_available": by_status.get("available", 0), "assets_unavailable": missing}


# ---- background jobs ---------------------------------------------------------------

class ImportManager:
    """Runs at most one import at a time in a background thread and reports its progress."""

    def __init__(self, storage, roots=()):
        self.storage = storage
        self.roots = tuple(roots)
        self._lock = threading.Lock()
        self._jobs = {}
        self._active = None

    def recover(self):
        """Called once at start-up: runs left 'running' by a stopped process were interrupted."""
        self.storage.sweep_incoming()
        conn = connect(self.storage.db_path)
        try:
            with tx(conn):
                conn.execute(
                    "UPDATE import_run SET state = 'interrupted', stage = 'interrupted', finished_at = ?, "
                    "error = 'The service stopped before this import finished. Nothing was added; import again to retry.' "
                    "WHERE state = 'running'", (utc_now(),))
        finally:
            conn.close()

    def start(self, project_id, html_path, companion_dir):
        with self._lock:
            if self._active is not None and self._jobs[self._active]["thread"].is_alive():
                raise Conflict("Another import is still running. Wait for it to finish or cancel it.", code="import_running")
            conn = connect(self.storage.db_path)
            try:
                if conn.execute("SELECT 1 FROM project WHERE id = ?", (project_id,)).fetchone() is None:
                    raise NotFound("Project not found")
                name = os.path.basename(os.path.expanduser((html_path or "").strip())) or "import"
                # validate the selection up front so obvious mistakes fail immediately and visibly
                resolve_selection(html_path, companion_dir, self.roots)
                run_id = create_run(conn, project_id, name)
            finally:
                conn.close()
            progress = Progress()
            job = {"progress": progress, "project_id": project_id, "result": None, "error": None, "error_code": None, "thread": None}
            t = threading.Thread(target=self._work, args=(run_id, job, html_path, companion_dir), daemon=True)
            job["thread"] = t
            self._jobs[run_id] = job
            self._active = run_id
            t.start()
            return run_id

    def _work(self, run_id, job, html_path, companion_dir):
        conn = connect(self.storage.db_path)
        try:
            job["result"] = execute_import(self.storage, conn, job["project_id"], run_id, html_path, companion_dir, self.roots, job["progress"])
        except AppError as exc:
            job["error"], job["error_code"] = exc.message, exc.code
        except Exception as exc:  # noqa: BLE001
            job["error"], job["error_code"] = f"Unexpected error: {exc}", "internal_error"
        finally:
            conn.close()

    def cancel(self, run_id):
        job = self._jobs.get(run_id)
        if job is None or not job["thread"].is_alive():
            raise NotFound("No running import with that id")
        job["progress"].cancelled = True
        return {"cancelling": True}

    def status(self, conn, run_id):
        row = conn.execute("SELECT * FROM import_run WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound("Import not found")
        job = self._jobs.get(run_id)
        out = {
            "id": row["id"], "project_id": row["project_id"], "document_id": row["document_id"], "kind": row["kind"],
            "state": row["state"], "stage": row["stage"], "source_name": row["source_name"], "error": row["error"],
            "note": row["note"], "created_at": row["created_at"], "finished_at": row["finished_at"],
        }
        if job is not None:
            out["progress"] = job["progress"].snapshot()
            alive = job["thread"].is_alive()
            if alive:
                out["state"], out["stage"] = "running", job["progress"].stage
            if job["result"] is not None:
                out["result"] = job["result"]
        return out
