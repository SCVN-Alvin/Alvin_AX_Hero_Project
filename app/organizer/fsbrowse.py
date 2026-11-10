"""Directory listing for the import file picker.

A browser cannot hand a web page the absolute path of a chosen file, so the interface asks
this local service to list folders. Only folder names and .html/.htm file names are returned
(never file contents), the service listens on this computer only, and `--import-root` can
confine browsing and importing to chosen folders.
"""

import os

from .errors import NotFound, ValidationError

MAX_ENTRIES = 3000
HTML_EXT = (".html", ".htm", ".xhtml")


def _within(path, roots):
    if not roots:
        return True
    real = os.path.realpath(path)
    for r in roots:
        rr = os.path.realpath(r)
        try:
            if os.path.commonpath([real, rr]) == rr:
                return True
        except ValueError:
            continue
    return False


def shortcuts(roots):
    out = []
    if roots:
        for r in roots:
            out.append({"label": os.path.basename(os.path.normpath(r)) or r, "path": os.path.realpath(r)})
        return out
    home = os.path.expanduser("~")
    out.append({"label": "Home", "path": home})
    for name in ("Desktop", "Documents", "Downloads"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            out.append({"label": name, "path": p})
    if os.path.isdir("/Volumes"):
        try:
            for v in sorted(os.listdir("/Volumes")):
                p = os.path.join("/Volumes", v)
                if os.path.isdir(p) and not v.startswith("."):
                    out.append({"label": f"/Volumes/{v}", "path": p})
        except OSError:
            pass
    out.append({"label": "Computer (/)", "path": os.path.abspath(os.sep)})
    return out


def list_dir(path, roots=(), show_hidden=False):
    roots = tuple(roots)
    if not path or not path.strip():
        path = roots[0] if roots else os.path.expanduser("~")
    p = os.path.expanduser(path.strip())
    if "\x00" in p or not os.path.isabs(p):
        raise ValidationError("Enter a full folder path starting with / or ~", field="path")
    p = os.path.normpath(p)
    if not os.path.exists(p):
        raise NotFound(f"No folder exists at {p}")
    if os.path.isfile(p):
        p = os.path.dirname(p)
    p = os.path.realpath(p)
    if not _within(p, roots):
        raise ValidationError("That folder is outside the locations this service was started with (--import-root).", field="path")
    entries, truncated, hidden_other = [], False, 0
    try:
        with os.scandir(p) as it:
            for e in it:
                if not show_hidden and e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        entries.append({"name": e.name, "kind": "dir"})
                    elif e.is_file():
                        if e.name.lower().endswith(HTML_EXT):
                            size = e.stat().st_size
                            entries.append({"name": e.name, "kind": "html", "size": size})
                        else:
                            hidden_other += 1
                except OSError:
                    continue
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
    except PermissionError:
        raise ValidationError("This computer did not allow listing that folder.", field="path")
    except OSError as exc:
        raise ValidationError(f"The folder cannot be listed: {exc.strerror or exc}", field="path")
    entries.sort(key=lambda x: (x["kind"] != "dir", x["name"].casefold()))
    parent = os.path.dirname(p)
    if parent == p or not _within(parent, roots):
        parent = None
    return {"path": p, "parent": parent, "entries": entries, "truncated": truncated,
            "hidden_other_files": hidden_other, "shortcuts": shortcuts(roots)}
