"""Classification rules: phrase matching, evidence, and conflict resolution.

Deliberately small. A rule says "when this scope contains one of these phrases, suggest this value
for this field". A match explains a suggestion; it never confirms anything. There is no scripting and
no confidence number: a suggestion is Suggested, Conflicting, Superseded, Accepted, or Rejected.
"""

import re

from .errors import ValidationError

FIELDS = ("discipline", "category", "impact", "urgency")
SCOPES = ("title", "text", "heading_path", "any")
OPERATORS = ("contains", "equals")
VALUES = {
    "discipline": ("art", "dev", "both"),
    "category": ("core_gameplay", "content", "ui_ux", "polish", "bug_fix", "technical", "production"),
    "impact": ("critical", "high", "medium", "low"),
    "urgency": ("high", "medium", "low"),
}
FIELD_LABELS = {"discipline": "Discipline", "category": "Category", "impact": "Impact", "urgency": "Urgency"}
MAX_PHRASES = 40


def phrases_of(pattern):
    return [p.strip() for p in (pattern or "").splitlines() if p.strip()]


def _phrase_regex(phrase, case_sensitive):
    wildcard = phrase.endswith("*")
    core = phrase[:-1] if wildcard else phrase
    if not core:
        return None
    body = re.escape(core) + (r"\w*" if wildcard else r"(?!\w)")
    return re.compile(r"(?<!\w)" + body, 0 if case_sensitive else re.IGNORECASE)


def validate_rule(data, *, partial_from=None):
    """Return a cleaned rule dict or raise ValidationError. `partial_from` supplies defaults for edits."""
    base = dict(partial_from or {})
    merged = {**base, **{k: v for k, v in data.items() if k in (
        "name", "enabled", "match_scope", "operator", "pattern", "case_sensitive", "output_field", "proposed_value", "precedence", "note")}}
    name = " ".join(str(merged.get("name") or "").split())
    if not name:
        raise ValidationError("A rule needs a name", field="name")
    if len(name) > 120:
        raise ValidationError("The rule name is too long (max 120 characters)", field="name")
    scope = merged.get("match_scope", "text")
    if scope not in SCOPES:
        raise ValidationError(f"match_scope must be one of: {', '.join(SCOPES)}", field="match_scope")
    op = merged.get("operator", "contains")
    if op not in OPERATORS:
        raise ValidationError(f"operator must be one of: {', '.join(OPERATORS)}", field="operator")
    pattern = "\n".join(phrases_of(merged.get("pattern")))
    ph = phrases_of(pattern)
    if not ph:
        raise ValidationError("Enter at least one phrase", field="pattern")
    if len(ph) > MAX_PHRASES or any(len(p) > 200 for p in ph):
        raise ValidationError(f"Use at most {MAX_PHRASES} phrases of up to 200 characters", field="pattern")
    if any(p.strip("*") == "" for p in ph):
        raise ValidationError("A phrase cannot be only a wildcard", field="pattern")
    field = merged.get("output_field")
    if field not in FIELDS:
        raise ValidationError(f"output_field must be one of: {', '.join(FIELDS)}", field="output_field")
    value = merged.get("proposed_value")
    if value not in VALUES[field]:
        raise ValidationError(f"For {FIELD_LABELS[field]}, the value must be one of: {', '.join(VALUES[field])}", field="proposed_value")
    prec = merged.get("precedence", 0)
    if isinstance(prec, bool) or not isinstance(prec, int) or not -1000 <= prec <= 1000:
        raise ValidationError("precedence must be a whole number between -1000 and 1000", field="precedence")
    for flag in ("enabled", "case_sensitive"):
        if flag in merged and not isinstance(merged[flag], (bool, int)):
            raise ValidationError(f"{flag} must be true or false", field=flag)
    note = str(merged.get("note") or "")
    if len(note) > 1000:
        raise ValidationError("The note is too long", field="note")
    return {"name": name, "enabled": 1 if merged.get("enabled", True) else 0, "match_scope": scope, "operator": op,
            "pattern": pattern, "case_sensitive": 1 if merged.get("case_sensitive") else 0, "output_field": field,
            "proposed_value": value, "precedence": prec, "note": note}


def _targets(scope, texts):
    """texts: {'title': (text, block_id), 'text': [(text, block_id)...], 'heading_path': (text, block_id)}"""
    out = []
    if scope in ("title", "any"):
        out.append(("title", *texts["title"]))
    if scope in ("text", "any"):
        out.extend(("text", t, b) for t, b in texts["text"])
    if scope in ("heading_path", "any"):
        out.append(("heading_path", *texts["heading_path"]))
    return out


def _snippet(text, start, end):
    a = max(0, start - 30)
    b = min(len(text), end + 40)
    return ("…" if a else "") + " ".join(text[a:b].split()) + ("…" if b < len(text) else "")


def match_rule(rule, texts):
    """First match of a rule against a candidate's texts, or None.

    Returns {matched, snippet, match_in, block_id}. `matched` is the exact text that triggered it.
    """
    cs = bool(rule["case_sensitive"])
    ph = phrases_of(rule["pattern"])
    for where, text, block_id in _targets(rule["match_scope"], texts):
        if not text:
            continue
        if rule["operator"] == "equals":
            norm = " ".join(text.split())
            for p in ph:
                q = " ".join(p.rstrip("*").split())
                if (norm == q) if cs else (norm.casefold() == q.casefold()):
                    return {"matched": norm, "snippet": norm, "match_in": where, "block_id": block_id}
            continue
        for p in ph:
            rx = _phrase_regex(p, cs)
            m = rx.search(text) if rx else None
            if m:
                return {"matched": m.group(0), "snippet": _snippet(text, m.start(), m.end()), "match_in": where, "block_id": block_id}
    return None


def evaluate(rules, texts):
    """All matches for one candidate: list of suggestion dicts (before conflict resolution)."""
    found = []
    for r in rules:
        if not r["enabled"]:
            continue
        m = match_rule(r, texts)
        if m is None:
            continue
        values = ("art", "dev") if (r["output_field"] == "discipline" and r["proposed_value"] == "both") else (r["proposed_value"],)
        for value in values:
            found.append({
                "rule_ref": r["id"], "rule_name": r["name"], "rule_version": r["version"],
                "rule_scope": "project" if r["project_id"] else "global", "field": r["output_field"],
                "proposed_value": value, "precedence": r["precedence"], "matched_text": m["snippet"],
                "matched": m["matched"], "match_in": m["match_in"], "source_block_id": m["block_id"],
            })
    return found


def resolve(found, current):
    """Assign a state to each suggestion.

    `current` holds the candidate's reviewed values: {'category': ..., 'impact': ..., 'urgency': ..., 'disciplines': set}.
    - Disciplines accumulate: Art and Dev matches can both stand. Disciplines already set are superseded.
    - For a single-value field a project rule outranks a global rule, then the higher precedence wins;
      equal-ranked rules that disagree are all Conflicting and nothing is chosen.
    - A field that already has a value is never overwritten: its suggestions are Superseded.
    """
    defaults = {"category": "unassigned", "impact": "unassessed", "urgency": "unspecified"}
    by_field = {}
    for s in found:
        by_field.setdefault(s["field"], []).append(s)
    out = []
    for field, items in by_field.items():
        if field == "discipline":
            for s in items:
                s["state"] = "superseded" if s["proposed_value"] in current["disciplines"] else "suggested"
            out.extend(items)
            continue
        if current[field] != defaults[field]:
            for s in items:
                s["state"] = "superseded"
            out.extend(items)
            continue
        rank = lambda s: (1 if s["rule_scope"] == "project" else 0, s["precedence"])  # noqa: E731
        top = max(rank(s) for s in items)
        leaders = [s for s in items if rank(s) == top]
        distinct = {s["proposed_value"] for s in leaders}
        for s in items:
            if s in leaders:
                s["state"] = "suggested" if len(distinct) == 1 else "conflicting"
            else:
                s["state"] = "superseded"
        out.extend(items)
    return out
