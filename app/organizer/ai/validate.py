"""Validate an AI response independently of whatever the model was told to produce.

The CLI may enforce a JSON schema, but nothing here trusts that: shape, enums, every id and the draft ids are
checked against what the request actually supplied. A response that fails is rejected whole. Quantities that the
cited source does not contain are reported per draft as issues, because a person can still use the structure of a
good draft while discarding an invented number.
"""

import json
import re

CATEGORIES = ("core_gameplay", "content", "ui_ux", "polish", "bug_fix", "technical", "production", "unassigned")
IMPACTS = ("critical", "high", "medium", "low", "unassessed")
DISCIPLINES = ("art", "dev")
BASIS = ("source_supported", "inferred")

MAX_TITLE = 200
MAX_DESCRIPTION = 2000
MAX_REASON = 600
MAX_QUESTION = 1000

_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")


class InvalidOutput(Exception):
    """The response cannot be used. `errors` is a list of short, human-readable reasons."""

    def __init__(self, errors, code="invalid_output"):
        super().__init__("; ".join(errors[:3]))
        self.errors = errors
        self.code = code


class Context:
    """What the request supplied: the only ids the response may use."""

    def __init__(self, request_id, blocks, references, scenarios, task_limit):
        self.request_id = request_id
        self.blocks = blocks          # alias -> real block id
        self.block_text = {}          # alias -> text (for quantity checks)
        self.references = references  # alias -> real reference id
        self.scenarios = set(scenarios)
        self.task_limit = task_limit


def parse_json(text):
    """Accept exactly one JSON object, optionally wrapped in a Markdown code fence."""
    if text is None or not text.strip():
        raise InvalidOutput(["The response was empty."], code="empty_output")
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
    except ValueError as exc:
        raise InvalidOutput([f"The response is not valid JSON ({exc})."])
    if not isinstance(obj, dict):
        raise InvalidOutput(["The response must be one JSON object."])
    return obj


def _numbers(text):
    return {n.replace(",", ".") for n in _NUMBER.findall(text or "")}


def _str(obj, key, errors, where, *, required=True, max_len=None, allow_empty=False):
    v = obj.get(key)
    if not isinstance(v, str):
        errors.append(f"{where}: “{key}” must be text.")
        return ""
    if not allow_empty and not v.strip():
        errors.append(f"{where}: “{key}” is empty.")
    if max_len and len(v) > max_len:
        errors.append(f"{where}: “{key}” is longer than {max_len} characters.")
    return v


def _keys(obj, required, errors, where):
    if not isinstance(obj, dict):
        errors.append(f"{where} must be an object.")
        return False
    missing = [k for k in required if k not in obj]
    extra = [k for k in obj if k not in required]
    if missing:
        errors.append(f"{where} is missing {', '.join(missing)}.")
    if extra:
        errors.append(f"{where} has unexpected field {', '.join(extra)}.")
    return not missing


def _ids(obj, key, allowed, errors, where, kind):
    v = obj.get(key)
    if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
        errors.append(f"{where}: “{key}” must be a list of ids.")
        return []
    unknown = [x for x in v if x not in allowed]
    if unknown:
        errors.append(f"{where}: unknown {kind} id {', '.join(repr(u) for u in unknown[:3])}.")
    seen = []
    for x in v:
        if x in allowed and x not in seen:
            seen.append(x)
    return seen


def validate(obj, ctx):
    """Return (clean, issues). `clean` uses real block/reference ids; `issues` are non-fatal findings."""
    errors, issues = [], []
    top = ("request_id", "tasks", "questions")
    if not _keys(obj, top, errors, "The response"):
        raise InvalidOutput(errors)
    if obj.get("request_id") != ctx.request_id:
        errors.append("The response answers a different request (request_id does not match).")
    tasks = obj.get("tasks")
    questions = obj.get("questions")
    if not isinstance(tasks, list):
        errors.append("“tasks” must be a list.")
        tasks = []
    if not isinstance(questions, list):
        errors.append("“questions” must be a list.")
        questions = []
    if len(tasks) > ctx.task_limit:
        errors.append(f"The response contains {len(tasks)} tasks but at most {ctx.task_limit} were asked for.")

    task_keys = ("draft_id", "title", "description", "disciplines", "category", "impact", "classification_reason", "source_block_ids",
                 "scenario_ids", "suggested_primary_reference_id", "subtasks")
    sub_keys = ("draft_id", "title", "description", "basis", "source_block_ids", "scenario_ids")
    seen_ids = set()
    clean_tasks = []

    def claim(draft_id, where):
        if not isinstance(draft_id, str) or not draft_id.strip():
            errors.append(f"{where}: draft_id must be text.")
        elif draft_id in seen_ids:
            errors.append(f"{where}: draft_id {draft_id!r} is used more than once.")
        else:
            seen_ids.add(draft_id)

    for i, t in enumerate(tasks):
        where = f"Task {i + 1}"
        if not _keys(t, task_keys, errors, where):
            continue
        claim(t.get("draft_id"), where)
        title = _str(t, "title", errors, where, max_len=MAX_TITLE)
        desc = _str(t, "description", errors, where, max_len=MAX_DESCRIPTION, allow_empty=True)
        reason = _str(t, "classification_reason", errors, where, max_len=MAX_REASON, allow_empty=True)
        discs = t.get("disciplines")
        if not isinstance(discs, list) or any(d not in DISCIPLINES for d in discs):
            errors.append(f"{where}: disciplines may only contain art and dev.")
            discs = []
        if t.get("category") not in CATEGORIES:
            errors.append(f"{where}: category {t.get('category')!r} is not one of the allowed values.")
        if t.get("impact") not in IMPACTS:
            errors.append(f"{where}: impact {t.get('impact')!r} is not one of the allowed values.")
        blocks = _ids(t, "source_block_ids", ctx.blocks, errors, where, "source block")
        if not blocks and isinstance(t.get("source_block_ids"), list):
            errors.append(f"{where} cites no source block. A task needs supplied evidence.")
        scen = _ids(t, "scenario_ids", ctx.scenarios, errors, where, "scenario")
        pref = t.get("suggested_primary_reference_id")
        if pref is not None and not isinstance(pref, str):
            errors.append(f"{where}: suggested_primary_reference_id must be text or null.")
            pref = None
        elif pref is not None and pref not in ctx.references:
            errors.append(f"{where}: unknown reference id {pref!r}.")
            pref = None
        subs_raw = t.get("subtasks")
        if not isinstance(subs_raw, list):
            errors.append(f"{where}: subtasks must be a list.")
            subs_raw = []
        subs = []
        for j, s in enumerate(subs_raw):
            sw = f"{where}, subtask {j + 1}"
            if not _keys(s, sub_keys, errors, sw):
                continue
            claim(s.get("draft_id"), sw)
            stitle = _str(s, "title", errors, sw, max_len=MAX_TITLE)
            sdesc = _str(s, "description", errors, sw, max_len=MAX_DESCRIPTION, allow_empty=True)
            basis = s.get("basis")
            if basis not in BASIS:
                errors.append(f"{sw}: basis must be source_supported or inferred.")
            sb = _ids(s, "source_block_ids", ctx.blocks, errors, sw, "source block")
            if basis == "source_supported" and not sb and isinstance(s.get("source_block_ids"), list):
                errors.append(f"{sw} is marked source_supported but cites no source block.")
            ss = _ids(s, "scenario_ids", ctx.scenarios, errors, sw, "scenario")
            subs.append({"draft_id": s.get("draft_id"), "title": " ".join(stitle.split()), "description": sdesc.strip(), "basis": basis,
                         "source_aliases": sb, "scenario_ids": ss})
        clean_tasks.append({"draft_id": t.get("draft_id"), "title": " ".join(title.split()), "description": desc.strip(), "disciplines": sorted(set(discs)),
                            "category": t.get("category"), "impact": t.get("impact"), "classification_reason": reason.strip(),
                            "source_aliases": blocks, "scenario_ids": scen, "suggested_primary_reference_alias": pref, "subtasks": subs})

    clean_questions = []
    for i, q in enumerate(questions):
        where = f"Question {i + 1}"
        if not _keys(q, ("text", "source_block_ids"), errors, where):
            continue
        text = _str(q, "text", errors, where, max_len=MAX_QUESTION)
        qb = _ids(q, "source_block_ids", ctx.blocks, errors, where, "source block")
        clean_questions.append({"text": " ".join(text.split()), "source_aliases": qb})

    if errors:
        raise InvalidOutput(errors)

    # Non-fatal: a number the cited source does not contain is flagged on the draft that used it.
    def check_numbers(draft, cited_aliases, label):
        have = set()
        for a in cited_aliases:
            have |= _numbers(ctx.block_text.get(a, ""))
        extra = sorted(_numbers(draft["title"] + " " + draft["description"]) - have - _numbers(label))
        if extra:
            issues.append({"level": "warning", "code": "unsupported_value", "draft_id": draft["draft_id"],
                           "text": f"“{draft['title']}” mentions {', '.join(extra)}, which does not appear in the source text it cites. Check it before using the description."})
            draft["unsupported_values"] = extra

    titles = set()
    for t in clean_tasks:
        check_numbers(t, t["source_aliases"], "")
        for s in t["subtasks"]:
            check_numbers(s, s["source_aliases"] or t["source_aliases"], "")
            if s["basis"] == "inferred":
                issues.append({"level": "info", "code": "inferred", "draft_id": s["draft_id"],
                               "text": f"“{s['title']}” is an implementation suggestion, not something the source states."})
        key = t["title"].casefold()
        if key in titles:
            issues.append({"level": "warning", "code": "duplicate_title", "draft_id": t["draft_id"], "text": f"Two tasks are both called “{t['title']}”."})
        titles.add(key)
    if not clean_tasks and not clean_questions:
        issues.append({"level": "info", "code": "nothing_proposed", "draft_id": None, "text": "The AI proposed nothing for this selection."})
    # translate aliases to real ids so nothing downstream depends on the request's temporary names
    def real(aliases):
        return [ctx.blocks[a] for a in aliases]
    for t in clean_tasks:
        t["source_block_ids"] = real(t.pop("source_aliases"))
        a = t.pop("suggested_primary_reference_alias")
        t["suggested_primary_reference_id"] = ctx.references[a] if a else None
        for s in t["subtasks"]:
            s["source_block_ids"] = real(s.pop("source_aliases"))
    for q in clean_questions:
        q["source_block_ids"] = real(q.pop("source_aliases"))
    return {"request_id": ctx.request_id, "tasks": clean_tasks, "questions": clean_questions}, issues
