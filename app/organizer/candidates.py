"""Turn imported source blocks into candidate task groups, requirement lines, and media suggestions.

Pure functions over plain dictionaries (no database). The aim is conservative, explainable grouping:

* A heading's own blocks are split into **groups** by short emphasised paragraphs ("labels", such as the
  highlighted Wood shave or Build fence in the sample) and by column layout, because a section like
  "Gameplay Sequences" holds many unrelated mechanics under one heading.
* A label with actionable lines becomes a **task**; a label with only pictures is an **asset** label; explanatory
  text is a **note**. Bullets are **requirement** lines under their task, not tasks of their own.
* "Scenario 001 + 002" lines open a scope: following lines apply only to those scenarios. When every scope of a
  group starts with its own sub-label ("Shave clean wood: ...", "Shave bad wood:") the scopes become subtasks.
* Lines that start with a negation stay **exclusions** and are never turned into positive work.
* Quantities are kept as written ("10 wood logs"); nothing is converted or rounded.
* Wording is never rewritten. Titles only lose numbering and a trailing colon.

All vocabulary below is deliberately small and editable here; the matching rules for Art/Dev/category/impact
live in rules.py and the database, not in this module.
"""

import hashlib
import json
import math
import re
from collections import Counter

GENERATOR_VERSION = "candidates/1"

SCEN_RE = re.compile(r"^\s*scenarios?\s+(\d+(?:\s*(?:\+|,|&|/|and)\s*\d+)*)\s*:?\s*$", re.I)
NEG_RE = re.compile(r"^\s*(no|not|never|without|don't|do not|dont|avoid|skip|exclude|remove|không|đừng|bỏ|tránh)\b", re.I)
NOTE_RE = re.compile(r"^\s*(important|notes?|others?|references?|referencing|overview|summary|goals?|objectives?|"
                     r"background|introduction|scenarios?)\b", re.I)
SUBLABEL_RE = re.compile(r"^([^:\n]{2,40}):(?:\s+(.*))?$", re.S)
VALUE_RE = re.compile(r"(?<![\w/.])(~?\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?)\s*(%|[^\W\d_]+(?:\s[^\W\d_]+)?)")
ORDINAL_UNITS = {"st", "nd", "rd", "th"}
STOPWORDS = set("""the a an of and or for to with in on at by per is are be as it its this that these those from into than then
when while after before once each every all any not no can will should must may also same new other more most only both either
của và các những một cho với trong khi thì là được có không đã sẽ này đó mỗi""".split())

TEXT_TYPES = {"paragraph", "list_item", "quote", "callout", "toggle", "code"}
MEDIA_TYPES = {"image", "video", "audio", "attachment"}
SKIP_TYPES = {"heading", "title", "table", "divider"}
CAPTION_MAX = 50
LABEL_MAX = 60


def norm(text):
    return " ".join((text or "").split())


def clean_title(text, *, heading=False):
    t = norm(text)
    if heading:
        t = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", t)
    t = t.rstrip(" :：").strip()
    return t or norm(text)


def find_values(text):
    out = []
    for m in VALUE_RE.finditer(text or ""):
        words = m.group(2).split()
        if words[0].lower() in ORDINAL_UNITS:
            continue
        if len(words) == 2 and (words[1].casefold() in STOPWORDS or len(words[1]) < 3):
            words = words[:1]  # "50 degrees to the right" -> "50 degrees"
        out.append(norm(f"{m.group(1)} {' '.join(words)}") if words[0] != "%" else norm(m.group(1) + "%"))
    return out


def stems(text):
    toks = re.findall(r"[^\W\d_]+", (text or "").casefold())
    return [t[:5] for t in toks if len(t) >= 3 and t not in STOPWORDS]


def _is_bold(block):
    runs = [r for r in block.get("runs", []) if r.get("t") == "text" and r["s"].strip()]
    return bool(runs) and all(r.get("b") for r in runs)


def _emphasised(block):
    return "color:" in (block.get("variant") or "") or _is_bold(block)


def is_scenario_label(text):
    return SCEN_RE.match(text or "")


def is_label(block):
    if block["type"] != "paragraph" or block["depth"] != 0:
        return False
    t = norm(block["text"])
    if not t or len(t) > LABEL_MAX or is_scenario_label(t) or t.endswith("."):
        return False
    if re.search(r":\s+\S", t):  # "Label: more text" is a line, not a label
        return False
    return _emphasised(block)


def is_context_line(block):
    t = norm(block["text"])
    return block["type"] == "paragraph" and t.endswith(":") and len(t) <= LABEL_MAX and not is_scenario_label(t)


def sublabel(text):
    m = SUBLABEL_RE.match(norm(text))
    if not m:
        return None
    label = m.group(1).strip()
    if not re.search(r"[^\W\d_]", label) or label.endswith("."):
        return None
    return label, (m.group(2) or "").strip()


class Group:
    def __init__(self, label=None):
        self.label = label
        self.members = []


def split_groups(blocks):
    """Group a section's own blocks by labels and column layout. Returns (implicit_group, labeled_groups)."""
    implicit, top, cols, groups = Group(None), None, {}, []
    for b in blocks:
        if b["type"] in SKIP_TYPES:
            continue
        key = (b["column_group"], b["column_index"]) if b.get("column_group") else None
        if is_label(b):
            g = Group(b)
            groups.append(g)
            if key is None:
                top = g
                cols.clear()
            else:
                cols[key] = g
            continue
        target = cols.get(key) if key else None
        if target is None:
            target = top if top is not None else implicit
        target.members.append(b)
    return implicit, groups


def _scoped(members, scen_numbers):
    """Walk a group's members, tracking 'Scenario ...' scopes. Yields dicts with block, role, scope, scope_block."""
    scope, scope_block, out, context = None, None, [], None
    for b in members:
        t = norm(b["text"])
        if b["type"] in MEDIA_TYPES:
            out.append({"block": b, "role": "media", "scope": scope, "scope_block": scope_block, "context": None})
            continue
        m = is_scenario_label(t)
        if m:
            ids = tuple(re.findall(r"\d+", m.group(1)))
            scope, scope_block = ids, b
            continue
        if b["type"] not in TEXT_TYPES or not t:
            continue
        if is_context_line(b):
            context = b
            out.append({"block": b, "role": "context", "scope": scope, "scope_block": scope_block, "context": None})
            continue
        out.append({"block": b, "role": "line", "scope": scope, "scope_block": scope_block, "context": context})
    return out


def _identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


class Draft(dict):
    """A candidate before it is saved. Keys are plain data so the service layer can store them."""


def _req_draft(item, parent_key, path, ordinal_counter):
    b = item["block"]
    text = b["text"].strip()
    kind = "exclusion" if NEG_RE.match(text) else "requirement"
    norm_text = norm(text)
    ordn = ordinal_counter[(parent_key, norm_text)]
    ordinal_counter[(parent_key, norm_text)] += 1
    sources = [(b["id"], "evidence")]
    if item["scope_block"] is not None:
        sources.append((item["scope_block"]["id"], "scope"))
    if item["context"] is not None:
        sources.append((item["context"]["id"], "context"))
    return Draft(
        key=f"r:{b['id']}", kind=kind, level=None, parent_key=parent_key, title=norm_text[:300], original_text=text,
        values=find_values(text), sources=sources, scenarios=[(i, item["scope_block"]["id"]) for i in (item["scope"] or ())] if item["scope_block"] is not None else [],
        refs=[], heading_path=path, identity=_identity("req", path, parent_key, norm_text, ordn), section_id=b["section_id"], first_sequence=b["sequence"])


def _media_refs(items, reason):
    out, seen = [], set()
    for it in items:
        for r in it["block"].get("refs", []):
            if r["kind"] in ("image", "video", "audio", "attachment") and r["id"] not in seen:
                seen.add(r["id"])
                out.append((r["id"], reason))
    return out


def _children_by_scope(items):
    """Split scoped items into subtask scopes when every scope opens with its own 'Label:' line."""
    scopes, order = {}, []
    for it in items:
        if it["scope"] is None:
            continue
        k = it["scope"]
        if k not in scopes:
            scopes[k] = []
            order.append(k)
        scopes[k].append(it)
    if len(order) < 2:
        return None
    firsts = []
    for k in order:
        texty = [i for i in scopes[k] if i["role"] in ("line", "context")]
        if not texty or not any(i["role"] == "line" for i in scopes[k]) and len(texty) < 1:
            return None
        sl = sublabel(texty[0]["block"]["text"])
        if sl is None:
            return None
        firsts.append((sl, texty[0]))
    if len({norm(f[0][0]).casefold() for f in firsts}) != len(firsts):
        return None
    return [(k, scopes[k], firsts[i][0], firsts[i][1]) for i, k in enumerate(order)]


def build_task(group, section, path, counters, scen_numbers):
    """Drafts for one labeled (or implicit) group that is a task."""
    label_block = group.label
    title = clean_title(label_block["text"]) if label_block else clean_title(section["title"], heading=True)
    items = _scoped(group.members, scen_numbers)
    ident_path = path
    key = f"t:{label_block['id']}" if label_block else f"t:section:{section['id']}"
    ordn = counters[("task", tuple(path), title.casefold())]
    counters[("task", tuple(path), title.casefold())] += 1
    drafts = []
    task = Draft(key=key, kind="task", level="task", parent_key=None, title=title, original_text="", values=[],
                 sources=[(label_block["id"], "label")] if label_block else [], scenarios=[], refs=[],
                 heading_path=path, identity=_identity("task", path, norm(title).casefold(), ordn), section_id=section["id"],
                 first_sequence=(label_block or group.members[0])["sequence"])
    drafts.append(task)
    req_counter = Counter()
    children = _children_by_scope(items)
    consumed = set()
    if children:
        for k, scoped_items, (sub_title, remainder), first_line in children:
            ckey = f"t:{first_line['block']['id']}"
            child = Draft(key=ckey, kind="task", level="subtask", parent_key=key, title=clean_title(sub_title), original_text="", values=[],
                          sources=[(first_line["block"]["id"], "label")] + ([(first_line["scope_block"]["id"], "scope")] if first_line["scope_block"] is not None else []),
                          scenarios=[], refs=[], heading_path=path,
                          identity=_identity("task", path, ident_key(task), norm(sub_title).casefold(), 0), section_id=section["id"],
                          first_sequence=first_line["block"]["sequence"])
            drafts.append(child)
            for it in scoped_items:
                consumed.add(id(it))
                if it["role"] == "media":
                    child["sources"].append((it["block"]["id"], "media"))
                    continue
                if it is first_line and not remainder:
                    continue  # the line only carried the sub-label
                if it["role"] == "line":
                    if it["context"] is first_line["block"]:
                        it = dict(it, context=None)  # the sub-label became the title, not a context line
                    drafts.append(_req_draft(it, ckey, path, req_counter))
            child["refs"] = _media_refs([i for i in scoped_items if i["role"] == "media"], f"Shown directly under “{sub_title.rstrip(': ')}” in {path[-1] if path else 'the document'}")
    for it in items:
        if id(it) in consumed:
            continue
        if it["role"] == "media":
            task["sources"].append((it["block"]["id"], "media"))
        elif it["role"] == "line":
            drafts.append(_req_draft(it, key, path, req_counter))
    own_media = [i for i in items if i["role"] == "media" and id(i) not in consumed]
    task["refs"] = _media_refs(own_media, f"Shown directly under “{title}” in {path[-1] if path else 'the document'}")
    return drafts


def ident_key(task):
    return task["identity"]


def build_config(group, section, path, counters):
    """A list of tuning values: one standalone requirement per top-level item, with its nested lines."""
    items = [i for i in _scoped(group.members, None) if i["role"] in ("line", "context")]
    top_depth = min((i["block"]["depth"] for i in items if i["role"] == "line"), default=0)
    drafts, current, context = [], None, group.label  # a group's own label counts as context for its items
    req_counter = Counter()
    for it in items:
        b = it["block"]
        if it["role"] == "context":
            context = b
            continue
        if b["depth"] <= top_depth or current is None:
            current = {"lines": [it], "context": context}
            drafts.append(current)
        else:
            current["lines"].append(it)
    out = []
    for d in drafts:
        lines = d["lines"]
        texts = [l["block"]["text"].strip() for l in lines]
        full = "\n".join(texts)
        first = norm(texts[0])
        ordn = req_counter[first]
        req_counter[first] += 1
        kind = "exclusion" if NEG_RE.match(first) and len(lines) == 1 else "requirement"
        sources = [(l["block"]["id"], "evidence") for l in lines]
        if d["context"] is not None:
            sources.append((d["context"]["id"], "context"))
        out.append(Draft(key=f"r:{lines[0]['block']['id']}", kind=kind, level=None, parent_key=None, title=first[:300], original_text=full,
                         values=find_values(full), sources=sources, scenarios=[], refs=[], heading_path=path,
                         identity=_identity("config", path, norm(full).casefold(), ordn), section_id=section["id"],
                         first_sequence=lines[0]["block"]["sequence"]))
    return out


def build_other(group, section, path, kind, counters):
    """A note (explanatory text) or an asset label (a name for a set of pictures)."""
    label_block = group.label
    title = clean_title(label_block["text"]) if label_block else clean_title(section["title"], heading=True)
    items = _scoped(group.members, None)
    lines = [i for i in items if i["role"] in ("line", "context")]
    media = [i for i in items if i["role"] == "media"]
    ordn = counters[(kind, tuple(path), title.casefold())]
    counters[(kind, tuple(path), title.casefold())] += 1
    text = "\n".join(l["block"]["text"].strip() for l in lines)
    sources = ([(label_block["id"], "label")] if label_block else []) + [(l["block"]["id"], "evidence") for l in lines] + [(m["block"]["id"], "media") for m in media]
    if not sources:
        return None
    return Draft(key=f"{kind[0]}:{(label_block or group.members[0])['id']}", kind=kind, level=None, parent_key=None, title=title[:300], original_text=text,
                 values=find_values(text), sources=sources, scenarios=[], refs=_media_refs(media, f"Shown directly under “{title}”"),
                 heading_path=path, identity=_identity(kind, path, norm(title).casefold(), ordn), section_id=section["id"],
                 first_sequence=(label_block or group.members[0])["sequence"])


def _line_texts(group):
    return [norm(m["text"]) for m in group.members if m["type"] in TEXT_TYPES and norm(m["text"])
            and not is_scenario_label(m["text"]) and not is_context_line(m)]


def classify_group(group, section, has_labeled_groups):
    """'task', 'config', 'note', 'asset' or None (nothing to propose)."""
    texts = _line_texts(group)
    media = [m for m in group.members if m["type"] in MEDIA_TYPES]
    has_list = any(m["type"] == "list_item" for m in group.members)
    if group.label is None:
        if not texts:
            return None
        valued = sum(1 for t in texts if find_values(t))
        if valued / len(texts) >= 0.4 and len(texts) >= 4:
            return "config"
        if has_labeled_groups or NOTE_RE.match(clean_title(section["title"], heading=True)) or NOTE_RE.match(texts[0]):
            return "note"
        return "task"
    if not texts and not media:
        return None
    if texts and len(texts) >= 4 and sum(1 for t in texts if find_values(t)) / len(texts) >= 0.4 and not media:
        return "config"
    if not texts:
        return "asset"
    if media and not has_list and all(len(t) <= CAPTION_MAX for t in texts):
        return "asset"
    if NOTE_RE.match(clean_title(group.label["text"])):
        return "note"
    return "task"


def generate(blocks, sections, selected_ids, scenario_numbers=()):
    """Candidate drafts for the chosen sections.

    blocks: every block of the document (dicts); sections: all sections (dicts with id, title, level, role);
    selected_ids: section ids to read. Returns drafts in document order with parent links and suggestions.
    """
    by_section = {}
    for b in blocks:
        by_section.setdefault(b["section_id"], []).append(b)
    counters = Counter()
    drafts = []
    for sec in sorted(sections, key=lambda s: s["position"]):
        if sec["id"] not in selected_ids or sec["role"] == "metadata":
            continue
        own = sorted(by_section.get(sec["id"], []), key=lambda b: b["sequence"])
        path = list(own[0]["heading_path"]) if own else [sec["title"]]
        implicit, groups = split_groups(own)
        for g in [implicit] + groups:
            kind = classify_group(g, sec, bool(groups))
            if kind == "task":
                drafts.extend(build_task(g, sec, path, counters, scenario_numbers))
            elif kind == "config":
                drafts.extend(build_config(g, sec, path, counters))
            elif kind in ("note", "asset"):
                d = build_other(g, sec, path, kind, counters)
                if d is not None:
                    drafts.append(d)
    for i, d in enumerate(sorted(drafts, key=lambda d: d["first_sequence"])):
        d["position"] = i
    suggest_links(drafts)
    return sorted(drafts, key=lambda d: d["position"])


# ---- cross-section suggestions ----------------------------------------------------

def _profile(draft, children):
    w = Counter()
    for t in stems(draft["title"]):
        w[t] += 3
    for t in stems(draft["original_text"]):
        w[t] += 1
    for c in children:
        for t in stems(c["title"]):
            w[t] += 1
        for t in stems(c["original_text"]):
            w[t] += 1
    return w


def suggest_links(drafts):
    """Offer, never apply: which task a loose requirement belongs to, and which pictures suit a task."""
    tasks = [d for d in drafts if d["kind"] == "task"]
    if not tasks:
        return
    kids = {}
    for d in drafts:
        if d["parent_key"]:
            kids.setdefault(d["parent_key"], []).append(d)
    profiles = {}
    for t in tasks:
        sub = list(kids.get(t["key"], []))
        for c in list(sub):
            sub.extend(kids.get(c["key"], []))
        profiles[t["key"]] = _profile(t, sub if t["level"] == "task" else sub)
    df = Counter()
    for p in profiles.values():
        df.update(p.keys())
    n = len(tasks)

    def score(words, prof):
        shared = [s for s in words if s in prof]
        uniq = sorted(set(shared))
        return sum(min(prof[s], 3) * math.log(1 + n / df[s]) for s in uniq), uniq

    label_of = {}
    for t in tasks:
        label_of[t["key"]] = t["title"]
    for d in drafts:
        if d["kind"] == "requirement" and d["parent_key"] is None:
            words = list(dict.fromkeys(stems(d["title"] + " " + d["original_text"])))
            best = None
            for t in tasks:
                if t["level"] != "task":
                    continue
                sc, shared = score(words, profiles[t["key"]])
                if len(shared) >= 2 and (best is None or sc > best[0]):
                    best = (sc, t, shared)
            if best:
                d["suggested_parent_key"] = best[1]["key"]
                d["suggestion_reason"] = f"Shares {', '.join('“' + w + '”' for w in best[2][:4])} with “{best[1]['title']}”"
    # pictures named by an asset label are offered to tasks that talk about the same thing
    for a in (d for d in drafts if d["kind"] == "asset" and d["refs"]):
        words = list(dict.fromkeys(stems(a["title"] + " " + a["original_text"])))
        scored = []
        for t in tasks:
            sc, shared = score(words, profiles[t["key"]])
            if len(shared) >= 2:
                scored.append((sc, t, shared))
        scored.sort(key=lambda x: -x[0])
        for sc, t, shared in scored[:2]:
            existing = {r for r, _ in t["refs"]}
            reason = f"Pictures labelled “{a['title']}” ({a['heading_path'][-1] if a['heading_path'] else 'document'}) share {', '.join('“' + w + '”' for w in shared[:3])} with this task"
            for rid, _ in a["refs"]:
                if rid not in existing and len(t["refs"]) < 40:
                    t["refs"].append((rid, reason))
                    existing.add(rid)
