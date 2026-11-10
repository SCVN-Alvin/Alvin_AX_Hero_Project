"""Scenario tables: recognise the columns, then segment inline numbered steps.

Both steps are conservative. A table is used only when its header names a scenario
identifier and at least one more known column, and a cell is split into steps only when
the numbered-line pattern is unambiguous. Everything else keeps the cell's original text
and an unsplit fallback, and the person can pick the table and columns by hand. Document
numbering is evidence (shown as written), never a database identity.
"""

import re

# Header vocabulary, matched after lower-casing and dropping punctuation and spaces.
# Data, not code paths: extending a list adds a recognised layout.
COLUMN_VOCABULARY = {
    "identifier": ["video no", "video number", "video id", "scenario no", "scenario number", "scenario id",
                   "no", "number", "id", "stt", "so", "ma", "#"],
    "title": ["concept", "title", "name", "summary", "idea", "ten", "y tuong"],
    "description": ["scenario", "description", "flow", "steps", "details", "detail", "mo ta", "kich ban"],
}

_STEP_LINE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+(\S.*)$")
_INLINE_MARK = re.compile(r"(?:(?<=\s)|^)(\d{1,3})[.)]\s+(?=\S)")


def _key(text):
    import unicodedata
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^a-z0-9#]+", " ", t.casefold()).strip()


def detect_columns(header_texts):
    """Map roles to column indexes, or None when the header is not recognisable."""
    found = {}
    for idx, text in enumerate(header_texts):
        k = _key(text)
        for role, words in COLUMN_VOCABULARY.items():
            if role not in found and k in words:
                found[role] = idx
                break
    if "identifier" in found and ("title" in found or "description" in found):
        return found
    return None


def segment(text):
    """Split a scenario cell into (preamble, steps, mode, notes).

    steps: list of dicts {label, text, raw}
    mode:  'steps' (clear numbered pattern), 'plain' (no numbered steps), or
           'ambiguous' (looked numbered but unclear; the cell is left unsplit)
    """
    lines = text.split("\n")
    marks = [(i, _STEP_LINE.match(line)) for i, line in enumerate(lines)]
    marks = [(i, m) for i, m in marks if m]
    if len(marks) >= 2:
        first = marks[0][0]
        after = [i for i, line in enumerate(lines) if i > first and line.strip()]
        matched = {i for i, _ in marks}
        if all(i in matched for i in after):
            steps = [{"label": m.group(1), "text": m.group(2).strip(), "raw": lines[i].strip()} for i, m in marks]
            preamble = "\n".join(lines[:first]).strip()
            return preamble, steps, "steps", _numbering_notes([s["label"] for s in steps])
        return "", [], "ambiguous", ["Lines that are not numbered steps sit between numbered lines, so the cell was left unsplit."]
    # Several steps written on one line: accept only a clean 1, 2, 3, ... sequence.
    flat = text
    marks = list(_INLINE_MARK.finditer(flat))
    if len(marks) >= 3 and [int(m.group(1)) for m in marks] == list(range(1, len(marks) + 1)):
        steps = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(flat)
            raw = flat[m.start():end].strip()
            steps.append({"label": m.group(1), "text": flat[m.end():end].strip(), "raw": raw})
        return flat[:marks[0].start()].strip(), steps, "steps", []
    if len(marks) >= 2:
        return "", [], "ambiguous", ["Numbers that look like steps were found but do not form a clear sequence, so the cell was left unsplit."]
    return "", [], "plain", []


def _numbering_notes(labels):
    notes, prev = [], None
    nums = [int(x) for x in labels]
    if nums and nums[0] != 1:
        notes.append(f"numbering starts at {nums[0]}")
    for n in nums:
        if prev is not None:
            if n == prev:
                notes.append(f"step number {n} repeats")
            elif n != prev + 1:
                notes.append(f"numbering jumps from {prev} to {n}")
        prev = n
    return ["Step numbering as written: " + "; ".join(dict.fromkeys(notes)) + ". Order was kept as in the source."] if notes else []


def extract_rows(rows, mapping, *, first_data_row):
    """rows: list of rows, each a list of {text, row_element_id}. Returns (drafts, notes).

    A draft carries identifier/title/description exactly as written plus the segmentation.
    Rows without an identifier are reported, not invented.
    """
    drafts, notes = [], []
    seen = {}
    ident_col, title_col, desc_col = mapping.get("identifier"), mapping.get("title"), mapping.get("description")

    def cell(row, col):
        return row[col]["text"] if col is not None and col < len(row) else ""

    for r in range(first_data_row, len(rows)):
        row = rows[r]
        raw_ident = cell(row, ident_col)
        ident = " ".join(raw_ident.split())
        title = " ".join(cell(row, title_col).split()) if title_col is not None else ""
        desc = cell(row, desc_col).strip() if desc_col is not None else ""
        if not ident and not title and not desc:
            continue
        if not ident:
            notes.append(("warning", "scenario_missing_identifier",
                          f"Row {r + 1} has a title or description but no identifier, so it was not turned into a scenario.", r))
            continue
        pre, steps, mode, seg_notes = segment(desc) if desc else ("", [], "plain", [])
        for n in seg_notes:
            notes.append(("info" if mode == "steps" else "warning", "scenario_steps", f"Scenario {ident}: {n}", r))
        if ident in seen:
            notes.append(("warning", "scenario_duplicate_identifier",
                          f"Identifier {ident} appears in rows {seen[ident] + 1} and {r + 1}; both were kept.", r))
        seen.setdefault(ident, r)
        drafts.append({
            "row_index": r,
            "row_element_id": row[0].get("row_element_id") if row else None,
            "identifier": ident,
            "identifier_raw": raw_ident,
            "title": title,
            "description": desc,
            "preamble": pre,
            "steps": steps,
            "segmentation": mode,
        })
    return drafts, notes


def extract_from_table(block):
    """Recognise and extract scenarios from a parsed table block, or return None."""
    rows = _rows_of(block)
    if not rows or not block.table_header_rows:
        return None
    mapping = detect_columns([c["text"] for c in rows[block.table_header_rows - 1]])
    if mapping is None:
        return None
    drafts, notes = extract_rows(rows, mapping, first_data_row=block.table_header_rows)
    return {"mapping": mapping, "drafts": drafts, "notes": notes}


def _rows_of(block):
    rows = {}
    for c in block.cells:
        rows.setdefault(c.row, []).append(c)
    out = []
    for r in sorted(rows):
        cells = sorted(rows[r], key=lambda c: c.col)
        out.append([{"text": c.text, "row_element_id": c.row_element_id} for c in cells])
    return out
