"""Turn an HTML export into ordered source blocks, sections, tables, and references.

The result is pure data (no database, no filesystem, no network). Original wording is kept
as authored: only markup is removed and source-code line breaks inside a text node are
folded into a space. Heading paths come from a heading stack, so a block always knows which
section it belongs to. Nothing in here is specific to one document: Notion-style attributes
(element ids, image figures, attachment links, column lists) are used when present and
ordinary HTML is handled the same way without them.
"""

import hashlib
import re
import uuid
from collections import Counter, OrderedDict
from urllib.parse import unquote

from .htmlparse import RAW_TEXT, TooLarge, iter_elements, parse_html, raw_text

PARSER_VERSION = "html-blocks/1"
MAX_BLOCKS = 200_000

HEADINGS = {f"h{i}": i for i in range(1, 7)}
# Never rendered, executed, or followed; counted and reported instead.
DROPPED = frozenset(
    "script style noscript template iframe frame frameset object embed applet svg canvas math form input "
    "button select textarea option link meta base title head audio-unsupported".split()
)
BLOCK_TAGS = frozenset(
    "p div ul ol li table h1 h2 h3 h4 h5 h6 figure figcaption pre blockquote hr details summary aside section "
    "article header footer nav main dl dt dd fieldset address thead tbody tfoot tr td th".split()
)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".svg", ".ico", ".tif", ".tiff", ".heic"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".ogv", ".mkv", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus"}
PAGE_EXT = {".html", ".htm", ".xhtml"}
SAFE_SCHEMES = {"http", "https", "mailto", "tel"}

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_WIN_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_NEWLINE_WS = re.compile(r"[ \t\r\f\v]*\n[ \t\r\f\v\n]*")
_ATTACH_RE = re.compile(r"^attachment:[0-9a-fA-F-]{8,}:(.+)$", re.S)


def new_id():
    return uuid.uuid4().hex


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---- results ----------------------------------------------------------------------

class ParsedRef:
    __slots__ = ("id", "kind", "placement", "position", "raw_target", "source_path", "external_url",
                 "link_text", "caption", "problem", "block_id")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, "" if k not in ("source_path", "external_url", "problem", "block_id") else None))
        self.id = new_id()


class ParsedCell:
    __slots__ = ("row", "col", "is_header", "row_element_id", "element_id", "text", "runs")


class ParsedBlock:
    def __init__(self):
        self.id = new_id()
        self.sequence = 0
        self.block_type = "paragraph"
        self.variant = ""
        self.depth = 0
        self.parent_id = None
        self.section_index = None
        self.element_id = None
        self.locator = ""
        self.heading_path = []
        self.heading_level = None
        self.list_kind = None
        self.list_index = None
        self.checked = None
        self.table_rows = self.table_cols = self.table_header_rows = None
        self.column_group = None
        self.column_index = None
        self.text = ""
        self.runs = []
        self.refs = []
        self.cells = []


class ParsedSection:
    def __init__(self, index, level, title, path, parent_index):
        self.id = new_id()
        self.index = index
        self.level = level
        self.title = title
        self.path = path
        self.parent_index = parent_index
        self.heading_block_id = None
        self.first_sequence = 0
        self.last_sequence = 0
        self.role = "current"
        self.role_reason = ""


class ParsedDocument:
    def __init__(self):
        self.title = ""
        self.blocks = []
        self.sections = []
        self.refs = []
        self.diagnostics = []  # (severity, code, message, block_sequence or None)
        self.parser_version = PARSER_VERSION

    def tables(self):
        return [b for b in self.blocks if b.block_type == "table"]


# ---- reference targets ------------------------------------------------------------

def kind_for_name(name):
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "attachment"


def classify_target(raw):
    """Classify an href/src value without touching the filesystem.

    Returns one of
      ("external", url)                       web address, never fetched
      ("anchor", fragment)                    link within the page
      ("local", source_path, problem|None)    path relative to the HTML file; problem set when it
                                              can never be read (absolute, parent traversal, ...)
      ("unsafe", reason)                      javascript:, data:, ... dropped
      ("empty", None)
    """
    t = re.sub(r"[\t\r\n]", "", (raw or "").strip())
    if not t:
        return ("empty", None)
    if t.startswith("#"):
        return ("anchor", t[1:])
    if _WIN_DRIVE_RE.match(t) or t.startswith("\\\\"):
        return ("local", t, "absolute paths are never read")
    m = _SCHEME_RE.match(t)
    if m:
        scheme = m.group(0)[:-1].lower()
        if scheme in SAFE_SCHEMES:
            return ("external", t)
        if scheme == "file":
            return ("local", t, "file: addresses are never read")
        return ("unsafe", scheme)
    if t.startswith("//"):
        return ("external", "https:" + t)
    path = re.split(r"[?#]", t, maxsplit=1)[0]
    if not path:
        return ("anchor", "")
    try:
        decoded = unquote(path, errors="strict")
    except UnicodeDecodeError:
        return ("local", path, "the path is not valid percent-encoded UTF-8")
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        shown = "".join(c if ord(c) >= 32 and ord(c) != 127 else f"\\x{ord(c):02x}" for c in decoded)
        return ("local", shown, "the path contains control characters")
    if "\\" in decoded:
        return ("local", decoded, "backslashes are not allowed in paths")
    if decoded.startswith("/"):
        return ("local", decoded, "absolute paths are never read")
    segments = decoded.split("/")
    if any(s == ".." for s in segments):
        return ("local", decoded, "the path leaves the import folder")
    segments = [s for s in segments if s not in ("", ".")]
    if not segments:
        return ("local", decoded, "the path is empty")
    return ("local", "/".join(segments), None)


# ---- runs -------------------------------------------------------------------------

def norm_text(s):
    return _NEWLINE_WS.sub(" ", s.replace("\r\n", "\n").replace("\r", "\n"))


def runs_text(runs):
    out = []
    for r in runs:
        t = r["t"]
        if t == "text":
            out.append(r["s"])
        elif t == "br":
            out.append("\n")
        elif t == "a":
            out.append(runs_text(r.get("r", [])))
    return "".join(out)


def trim_runs(runs):
    """Drop leading/trailing line breaks and surrounding whitespace of a whole block."""
    runs = list(runs)
    while runs and (runs[0]["t"] == "br" or (runs[0]["t"] == "text" and not runs[0]["s"].strip())):
        runs.pop(0)
    while runs and (runs[-1]["t"] == "br" or (runs[-1]["t"] == "text" and not runs[-1]["s"].strip())):
        runs.pop()
    if runs and runs[0]["t"] == "text":
        runs[0] = dict(runs[0], s=runs[0]["s"].lstrip())
    if runs and runs[-1]["t"] == "text":
        runs[-1] = dict(runs[-1], s=runs[-1]["s"].rstrip())
    return runs


_VALUE_SPANS = {"user", "selected-value", "status-value"}


def _has_element_after(n):
    if n.parent is None:
        return False
    siblings = n.parent.children
    i = next((k for k, c in enumerate(siblings) if c is n), -1)
    return any(not c.is_text for c in siblings[i + 1:])


def _push_text(out, s, style):
    if not s:
        return
    if out and out[-1]["t"] == "text" and {k: v for k, v in out[-1].items() if k not in ("t", "s")} == style:
        out[-1]["s"] += s
        return
    run = {"t": "text", "s": s}
    run.update(style)
    out.append(run)


class Extractor:
    def __init__(self, root, filename=""):
        self.root = root
        self.filename = filename
        self.doc = ParsedDocument()
        self.parents = []
        self.column = None
        self.in_header = False
        self.section_stack = []
        self.meta_section = None
        self.intro_section = None
        self.removed = Counter()
        self.removed_notes = []
        self.external_inline_images = 0
        self.unsafe_links = Counter()
        self._loc_idx = {}
        self._position = 0

    # ---- bookkeeping --------------------------------------------------------------
    def diag(self, severity, code, message, sequence=None):
        self.doc.diagnostics.append((severity, code, message, sequence))

    def _locator(self, node):
        parts = []
        n = node
        while n is not None and n.parent is not None:
            idx = self._index_of(n)
            parts.append(f"{n.tag}[{idx}]")
            n = n.parent
        return "/".join(reversed(parts))

    def _index_of(self, node):
        cached = self._loc_idx.get(id(node))
        if cached:
            return cached
        counts = Counter()
        for child in node.parent.children:
            if child.is_text:
                continue
            counts[child.tag] += 1
            self._loc_idx[id(child)] = counts[child.tag]
        return self._loc_idx[id(node)]

    def _section(self, level, title, path, parent_index):
        s = ParsedSection(len(self.doc.sections), level, title, path, parent_index)
        self.doc.sections.append(s)
        return s

    def _current_section(self):
        if self.in_header:
            if self.meta_section is None:
                self.meta_section = self._section(0, "Page properties", ["Page properties"], None)
                self.meta_section.role = "metadata"
                self.meta_section.role_reason = "Properties exported with the page; a snapshot, not live project status."
            return self.meta_section
        if self.section_stack:
            return self.section_stack[-1]
        if self.intro_section is None:
            self.intro_section = self._section(0, "Before the first heading", [], None)
        return self.intro_section

    def add_block(self, node, block_type, text, runs, *, refs=(), anon=False, **kw):
        if len(self.doc.blocks) >= MAX_BLOCKS:
            raise TooLarge("The document has too many blocks to analyse")
        b = ParsedBlock()
        b.sequence = len(self.doc.blocks)
        b.block_type = block_type
        b.depth = len(self.parents)
        b.parent_id = self.parents[-1].id if self.parents else None
        for k, v in kw.items():
            setattr(b, k, v)
        b.text = text
        b.runs = runs
        b.element_id = (node.attrs.get("id") or None) if node is not None and not anon else None
        b.locator = (self._locator(node) + ("#inline" if anon else "")) if node is not None else ""
        if self.column:
            b.column_group, b.column_index = self.column
        sec = self._current_section() if block_type != "heading" and block_type != "title" else None
        if block_type == "heading":
            sec = self._open_section(kw.get("heading_level") or 2, text, b)
        elif block_type == "title":
            sec = self._current_section()
        b.section_index = sec.index
        b.heading_path = list(sec.path)
        for i, ref in enumerate(refs):
            ref.block_id = b.id
            ref.position = i
            self.doc.refs.append(ref)
        b.refs = list(refs)
        self.doc.blocks.append(b)
        return b

    def _open_section(self, level, title, block):
        if self.in_header:
            return self._current_section()
        while self.section_stack and self.section_stack[-1].level >= level:
            self.section_stack.pop()
        parent = self.section_stack[-1] if self.section_stack else None
        path = (list(parent.path) if parent else []) + [title]
        sec = self._section(level, title, path, parent.index if parent else None)
        sec.heading_block_id = block.id
        self.section_stack.append(sec)
        return sec

    # ---- inline content -----------------------------------------------------------
    def _make_ref(self, target, *, placement, link_text="", caption="", image=False):
        """Reference for a classified target, or None when nothing should be kept."""
        kind = target[0]
        if kind == "external":
            return ParsedRef(kind="external_image" if image else "external_link", placement=placement,
                             raw_target=target[1], external_url=target[1], link_text=link_text, caption=caption)
        if kind == "local":
            _, path, problem = target
            name = path.rsplit("/", 1)[-1] if path else ""
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext in PAGE_EXT and not problem and not image:
                return ParsedRef(kind="page_link", placement=placement, raw_target=path, link_text=link_text, caption=caption)
            return ParsedRef(kind=kind_for_name(name), placement=placement, raw_target=path, source_path=path,
                             link_text=link_text, caption=caption, problem=problem)
        return None

    def _style_for(self, el, style):
        tag = el.tag
        if tag in ("strong", "b"):
            return dict(style, b=1)
        if tag in ("em", "i", "cite", "dfn"):
            return dict(style, i=1)
        if tag in ("code", "kbd", "samp", "tt"):
            return dict(style, c=1)
        if tag in ("s", "del", "strike"):
            return dict(style, x=1)
        if tag in ("u", "ins"):
            return dict(style, u=1)
        if tag == "mark":
            color = next((c.split("-", 1)[1] for c in el.classes if c.startswith("highlight-")), "yellow")
            return dict(style, m=color)
        return style

    def inline(self, nodes, *, placement="inline", flat=False, style=None):
        """Return (runs, refs) for inline nodes. `flat` folds block children into line breaks."""
        out, refs = [], []
        self._inline_into(out, refs, nodes, style or {}, placement, flat)
        return out, refs

    def _br(self, out):
        if out and out[-1]["t"] != "br":
            out.append({"t": "br"})

    def _inline_into(self, out, refs, nodes, style, placement, flat):
        for n in nodes:
            if n.is_text:
                _push_text(out, norm_text(n.text), style)
                continue
            tag = n.tag
            if tag in DROPPED:
                continue
            if tag == "br":
                out.append({"t": "br"})
            elif tag == "img":
                run = self._inline_image(n, refs, placement)
                if run:
                    out.append(run)
            elif tag == "a":
                self._inline_link(out, refs, n, style, placement, flat)
            elif tag in ("video", "audio"):
                self._inline_media(out, refs, n, placement)
            elif flat and (tag in BLOCK_TAGS or tag in ("figure",)):
                self._br(out)
                self._inline_into(out, refs, n.children, style, placement, flat)
                self._br(out)
            elif tag in BLOCK_TAGS:
                # A block element in an inline-only context: keep its text, separated by a break.
                self._br(out)
                self._inline_into(out, refs, n.children, style, placement, flat)
                self._br(out)
            elif "checkbox" in n.classes:
                continue
            else:
                self._inline_into(out, refs, n.children, self._style_for(n, style), placement, flat)
                if tag == "span" and (n.classes & _VALUE_SPANS) and _has_element_after(n):
                    _push_text(out, " ", style)  # adjacent person/option chips read as separate values

    def _inline_image(self, img, refs, placement):
        raw = img.attrs.get("src", "")
        target = classify_target(raw)
        alt = " ".join(img.attrs.get("alt", "").split())
        if target[0] == "external":
            self.external_inline_images += 1
            return {"t": "img", "k": "ext", "alt": alt} if alt else None
        if target[0] in ("unsafe", "empty", "anchor"):
            if target[0] == "unsafe":
                self.unsafe_links[target[1]] += 1
            return None
        ref = self._make_ref(target, placement=placement, image=True)
        refs.append(ref)
        return {"t": "img", "ref": ref.id, "alt": alt}

    def _inline_media(self, out, refs, el, placement):
        sources = [el.attrs.get("src", "")] + [c.attrs.get("src", "") for c in el.children if not c.is_text and c.tag == "source"]
        for raw in sources:
            target = classify_target(raw)
            ref = self._make_ref(target, placement="link", link_text=raw)
            if ref is not None:
                refs.append(ref)
                out.append({"t": "a", "k": "ref", "ref": ref.id, "r": [{"t": "text", "s": raw.rsplit("/", 1)[-1] or raw}]})
                break

    def _inline_link(self, out, refs, a, style, placement, flat):
        raw = a.attrs.get("href", "")
        target = classify_target(raw)
        inner_nodes = a.children
        # <a href=X><img src=X></a>: one reference, the image.
        imgs = [c for c in iter_elements(a) if c.tag == "img"]
        if len(imgs) == 1 and not raw_text(a).strip():
            it = classify_target(imgs[0].attrs.get("src", ""))
            if it == target:
                self._inline_into(out, refs, inner_nodes, style, placement, flat)
                return
        if target[0] in ("empty",):
            self._inline_into(out, refs, inner_nodes, style, placement, flat)
            return
        if target[0] == "unsafe":
            self.unsafe_links[target[1]] += 1
            self._inline_into(out, refs, inner_nodes, style, placement, flat)
            return
        if target[0] == "anchor":
            inner, inner_refs = self.inline(inner_nodes, placement=placement, flat=flat, style=style)
            refs.extend(inner_refs)
            out.append({"t": "a", "k": "anchor", "href": "#" + target[1], "r": inner})
            return
        inner, inner_refs = self.inline(inner_nodes, placement=placement, flat=flat, style=style)
        link_text = " ".join(runs_text(inner).split())
        ref = self._make_ref(target, placement="link", link_text=link_text)
        if ref is None:
            out.extend(inner)
            refs.extend(inner_refs)
            return
        refs.append(ref)
        refs.extend(inner_refs)
        if target[0] == "external":
            out.append({"t": "a", "k": "ext", "href": target[1], "r": inner})
        else:
            out.append({"t": "a", "k": "ref", "ref": ref.id, "r": inner})

    def _note_dropped(self, el):
        if el.tag not in ("meta", "link", "base"):
            self.removed[el.tag] += 1
        if el.tag == "style":
            css = "".join(c.text for c in el.children if c.is_text)
            for m in re.finditer(r"@import\s+(?:url\()?['\"]?(https?://[^'\")\s]+)", css):
                self.removed_notes.append(f"A style sheet asked to load {m.group(1)}; it was not fetched.")
        elif el.tag == "link" and (el.attrs.get("href") or "").startswith(("http:", "https:", "//")):
            self.removed_notes.append(f"A <link> element pointed at {el.attrs.get('href')}; it was not fetched.")
        elif el.tag == "meta" and (el.attrs.get("http-equiv") or "").lower() == "refresh":
            self.removed_notes.append("A meta refresh redirect was ignored.")
        elif el.tag == "base" and el.attrs.get("href"):
            self.removed_notes.append("A <base> element was ignored; paths are resolved against the HTML file.")

    # ---- blocks -------------------------------------------------------------------
    def run(self):
        root = self.root
        title_el = None
        for el in iter_elements(root):
            if el.tag == "title":
                title_el = el
                break
        self.doc.title = " ".join(raw_text(title_el).split()) if title_el else ""
        for el in iter_elements(root):
            if el.tag in DROPPED and el.tag not in ("title", "head"):
                self._note_dropped(el)
        body = next((e for e in iter_elements(root) if e.tag == "body"), root)
        self.visit_children(body.children)
        # a heading-less, body-less document
        self._finish()
        return self.doc

    def visit_children(self, children):
        buf = []

        def flush():
            if buf:
                self._flush_inline(list(buf), children)
                buf.clear()

        for c in children:
            if c.is_text:
                buf.append(c)
                continue
            if c.tag in DROPPED:
                continue
            if c.tag in BLOCK_TAGS or "column-list" in c.classes:
                flush()
                self.visit_element(c)
            else:
                buf.append(c)
        flush()

    def _flush_inline(self, nodes, siblings):
        runs, refs = self.inline(nodes)
        runs = trim_runs(runs)
        text = runs_text(runs)
        if not text.strip() and not refs:
            return
        anchor = next((n for n in nodes if not n.is_text), nodes[0])
        node = anchor.parent if anchor.parent is not None else anchor
        only_image = (not text.strip() and len(refs) == 1 and refs[0].kind in ("image", "external_image"))
        if only_image:
            refs[0].placement = "figure"
            self.add_block(node, "image", "", [], refs=refs, anon=True)
        else:
            self.add_block(node, "paragraph", text, runs, refs=refs, anon=True)

    def visit_element(self, el):
        tag = el.tag
        if tag in DROPPED:
            return
        if tag in HEADINGS:
            self.visit_heading(el)
        elif tag == "p" or tag in ("dt", "dd", "figcaption"):
            self.visit_paragraph(el)
        elif tag in ("ul", "ol"):
            self.visit_list(el)
        elif tag == "li":
            self.visit_item(el, "list_item", list_kind="bulleted", list_index=None)
        elif tag == "table":
            self.visit_table(el)
        elif tag == "figure":
            self.visit_figure(el)
        elif tag == "hr":
            self.add_block(el, "divider", "", [])
        elif tag == "pre":
            text = raw_text(el).replace("\r\n", "\n").strip("\n")
            runs = [{"t": "text", "s": text, "c": 1}] if text else []
            if text:
                self.add_block(el, "code", text, runs)
        elif tag == "details":
            self.visit_details(el)
        elif tag == "blockquote":
            self.visit_item(el, "quote")
        elif tag == "aside" or "callout" in el.classes:
            self.visit_item(el, "callout")
        elif tag == "header":
            was = self.in_header
            self.in_header = True
            try:
                self.visit_children(el.children)
            finally:
                self.in_header = was
        elif "column-list" in el.classes:
            self.visit_columns(el)
        elif "column" in el.classes and self.column is not None:
            self.visit_children(el.children)
        else:
            self.visit_children(el.children)

    def visit_columns(self, el):
        group = el.attrs.get("id") or f"columns-{self._locator(el)}"
        saved = self.column
        try:
            index = 0
            for child in el.children:
                if child.is_text or child.tag in DROPPED:
                    continue
                if "column" in child.classes:
                    self.column = (group, index)
                    index += 1
                    self.visit_children(child.children)
                else:
                    self.column = (group, index)
                    self.visit_element(child)
        finally:
            self.column = saved

    def _variant(self, el):
        tokens = []
        for c in sorted(el.classes):
            if c.startswith("block-color-"):
                tokens.append("color:" + c[len("block-color-"):])
        return " ".join(tokens)

    def visit_heading(self, el, toggle=False):
        runs, refs = self.inline(el.children, placement="inline")
        runs = trim_runs(runs)
        text = " ".join(runs_text(runs).split())
        if not text and not refs:
            return
        level = HEADINGS[el.tag]
        variant = self._variant(el)
        if "page-title" in el.classes:
            self.add_block(el, "title", text, runs, refs=refs, heading_level=level, variant=(variant + " page_title").strip())
            return
        if toggle:
            variant = (variant + " toggle").strip()
        self.add_block(el, "heading", text, runs, refs=refs, heading_level=level, variant=variant)

    def visit_paragraph(self, el):
        runs, refs = self.inline(el.children, placement="inline")
        runs = trim_runs(runs)
        text = runs_text(runs)
        if not text.strip() and not refs:
            return
        variant = self._variant(el)
        if "page-description" in el.classes and not text.strip():
            return
        if not text.strip() and len(refs) == 1 and refs[0].kind in ("image", "external_image"):
            refs[0].placement = "figure"
            self.add_block(el, "image", "", [], refs=refs, variant=variant)
            return
        self.add_block(el, "paragraph", text, runs, refs=refs, variant=variant)

    def visit_list(self, lst):
        if "to-do-list" in lst.classes:
            kind = "todo"
        elif "toggle" in lst.classes:
            kind = "toggle"
        elif lst.tag == "ol":
            kind = "numbered"
        else:
            kind = "bulleted"
        try:
            index = int(lst.attrs.get("start") or 1)
        except ValueError:
            index = 1
        for child in lst.children:
            if child.is_text:
                if child.text.strip():
                    self.visit_children([child])
                continue
            if child.tag == "li":
                self.visit_item(child, "list_item", list_kind=kind, list_index=index)
                index += 1
            else:
                self.visit_element(child)

    def visit_item(self, el, block_type, **kw):
        """A block with its own inline text followed by nested blocks (li, quote, callout)."""
        buf, block = [], None
        checked = None
        if kw.get("list_kind") == "todo":
            for c in iter_elements(el):
                if "checkbox" in c.classes:
                    checked = 1 if "checkbox-on" in c.classes else 0
                    break
        variant = self._variant(el)

        def make(nodes):
            runs, refs = self.inline(nodes)
            runs = trim_runs(runs)
            text = runs_text(runs)
            return text, runs, refs

        def ensure_block():
            nonlocal block, buf
            text, runs, refs = make(buf)
            buf = []
            extra = dict(kw)
            if checked is not None:
                extra["checked"] = checked
            block = self.add_block(el, block_type, text, runs, refs=refs, variant=variant, **extra)
            self.parents.append(block)

        pushed = False
        try:
            for c in el.children:
                if c.is_text or (c.tag not in BLOCK_TAGS and c.tag not in DROPPED and "column-list" not in c.classes):
                    buf.append(c)
                    continue
                if c.tag in DROPPED:
                    continue
                if block is None:
                    ensure_block()
                    pushed = True
                elif buf:
                    self._flush_inline(buf, el.children)
                    buf = []
                self.visit_element(c)
            if block is None:
                text, runs, refs = make(buf)
                if text.strip() or refs:
                    extra = dict(kw)
                    if checked is not None:
                        extra["checked"] = checked
                    self.add_block(el, block_type, text, runs, refs=refs, variant=variant, **extra)
            elif buf:
                self._flush_inline(buf, el.children)
        finally:
            if pushed:
                self.parents.pop()

    def visit_details(self, el):
        summary = next((c for c in el.children if not c.is_text and c.tag == "summary"), None)
        rest = [c for c in el.children if c is not summary]
        pushed = False
        try:
            if summary is not None:
                heading = next((e for e in iter_elements(summary) if e.tag in HEADINGS), None)
                if heading is not None:
                    self.visit_heading(heading, toggle=True)
                else:
                    runs, refs = self.inline(summary.children)
                    runs = trim_runs(runs)
                    text = runs_text(runs)
                    if text.strip():
                        block = self.add_block(summary, "toggle", text, runs, refs=refs)
                        self.parents.append(block)
                        pushed = True
            self.visit_children(rest)
        finally:
            if pushed:
                self.parents.pop()

    # ---- tables -------------------------------------------------------------------
    def visit_table(self, tbl):
        rows = []

        def collect(node, in_head):
            for ch in node.children:
                if ch.is_text:
                    continue
                if ch.tag == "tr":
                    rows.append((ch, in_head))
                elif ch.tag in ("thead", "tbody", "tfoot"):
                    collect(ch, ch.tag == "thead")
                elif ch.tag in ("colgroup", "col", "caption"):
                    continue
                elif ch.tag not in ("td", "th"):
                    collect(ch, in_head)

        collect(tbl, False)
        if not rows:
            return
        header_rows = 0
        for tr, in_head in rows:
            cells = [c for c in tr.children if not c.is_text and c.tag in ("td", "th")]
            if in_head or (cells and all(c.tag == "th" for c in cells)):
                header_rows += 1
            else:
                break
        variant = "properties" if "properties" in tbl.classes else "data"
        parsed_rows, all_refs = [], []
        n_cols = 0
        for r, (tr, in_head) in enumerate(rows):
            row_cells = []
            tds = [c for c in tr.children if not c.is_text and c.tag in ("td", "th")]
            for c, td in enumerate(tds):
                runs, refs = self.inline(td.children, placement="cell", flat=True)
                runs = trim_runs(runs)
                cell = ParsedCell()
                cell.row, cell.col = r, c
                cell.is_header = 1 if (td.tag == "th" or in_head) else 0
                cell.row_element_id = tr.attrs.get("id") or None
                cell.element_id = td.attrs.get("id") or None
                cell.runs = runs
                cell.text = runs_text(runs)
                row_cells.append(cell)
                all_refs.extend(refs)
            n_cols = max(n_cols, len(tds))
            parsed_rows.append(row_cells)
        text = "\n".join(" | ".join(" ".join(c.text.split()) for c in row) for row in parsed_rows)
        block = self.add_block(tbl, "table", text, [], refs=all_refs, variant=variant,
                               table_rows=len(parsed_rows), table_cols=n_cols, table_header_rows=header_rows)
        for row in parsed_rows:
            block.cells.extend(row)

    # ---- figures and media --------------------------------------------------------
    def visit_figure(self, fig):
        caption_el = next((c for c in fig.children if not c.is_text and c.tag == "figcaption"), None)
        caption_runs, caption_refs = ([], [])
        if caption_el is not None:
            caption_runs, caption_refs = self.inline(caption_el.children)
            caption_runs = trim_runs(caption_runs)
        caption = runs_text(caption_runs)

        def inside_caption(n):
            while n is not None and n is not fig:
                if n is caption_el:
                    return True
                n = n.parent
            return False

        primaries, extras, seen = [], [], set()
        anchors = [e for e in iter_elements(fig) if e.tag == "a" and not inside_caption(e)]
        for el in iter_elements(fig):
            if inside_caption(el):
                continue
            if el.tag == "img":
                raw = el.attrs.get("src", "")
                target = classify_target(raw)
                key = ("img", target)
                if target[0] in ("unsafe", "empty", "anchor") or key in seen:
                    if target[0] == "unsafe":
                        self.unsafe_links[target[1]] += 1
                    continue
                seen.add(key)
                ref = self._make_ref(target, placement="figure", image=True, caption=caption)
                if ref is not None:
                    primaries.append((ref, " ".join(el.attrs.get("alt", "").split())))
            elif el.tag in ("video", "audio"):
                for raw in [el.attrs.get("src", "")] + [c.attrs.get("src", "") for c in el.children if not c.is_text and c.tag == "source"]:
                    target = classify_target(raw)
                    if target[0] in ("local", "external") and ("m", target) not in seen:
                        seen.add(("m", target))
                        ref = self._make_ref(target, placement="figure", caption=caption)
                        if ref is not None:
                            if ref.kind == "external_link":
                                extras.append(ref)
                            else:
                                primaries.append((ref, ""))
        for a in anchors:
            raw = a.attrs.get("href", "")
            target = classify_target(raw)
            if target[0] in ("unsafe", "empty", "anchor"):
                if target[0] == "unsafe":
                    self.unsafe_links[target[1]] += 1
                continue
            if ("img", target) in seen or ("m", target) in seen:
                continue  # the link only wraps an image already captured
            link_text = " ".join(raw_text(a).split())
            m = _ATTACH_RE.match(link_text)
            name = m.group(1).strip() if m else link_text
            in_source = a.parent is not None and "source" in a.parent.classes
            ref = self._make_ref(target, placement="figure" if (in_source or m) else "link", link_text=link_text, caption=name)
            if ref is None:
                continue
            if ref.kind in ("external_link", "page_link") or (not in_source and not m and ref.kind == "attachment" and primaries):
                ref.placement = "link"
                extras.append(ref)
            else:
                primaries.append((ref, ""))
        if not primaries:
            # nothing to preview: keep whatever text the figure carried
            runs, refs = self.inline(fig.children)
            runs = trim_runs(runs)
            if runs_text(runs).strip() or refs:
                self.add_block(fig, "paragraph", runs_text(runs), runs, refs=refs)
            return
        for i, (ref, alt) in enumerate(primaries):
            btype = {"image": "image", "external_image": "image", "video": "video", "audio": "audio"}.get(ref.kind, "attachment")
            text = caption if i == 0 else ""
            if btype in ("attachment", "video", "audio") and ref.link_text and not text:
                text = ref.link_text
            refs = [ref] + (extras + caption_refs if i == 0 else [])
            self.add_block(fig, btype, text, caption_runs if i == 0 and caption else [], refs=refs,
                           variant=self._variant(fig))

    # ---- sections -----------------------------------------------------------------
    def _finish(self):
        doc = self.doc
        # first/last sequence per section, rolling descendants up to ancestors
        first = {}
        last = {}
        for b in doc.blocks:
            idx = b.section_index
            while idx is not None:
                first.setdefault(idx, b.sequence)
                last[idx] = b.sequence
                idx = doc.sections[idx].parent_index
        for s in doc.sections:
            s.first_sequence = first.get(s.index, 0)
            s.last_sequence = last.get(s.index, 0)
        title_block = next((b for b in doc.blocks if b.block_type == "title"), None)
        if title_block is not None:
            doc.title = title_block.text or doc.title
        assign_roles(doc.sections)
        if self.removed:
            parts = ", ".join(f"{n} <{t}>" for t, n in sorted(self.removed.items()))
            self.diag("info", "active_content_removed", f"Removed {parts}. Scripts and styles are never executed or applied to the preview.")
        for note in OrderedDict.fromkeys(self.removed_notes):
            self.diag("warning", "remote_resource_blocked", note)
        if self.external_inline_images:
            self.diag("info", "external_images_not_loaded",
                      f"{self.external_inline_images} small web-hosted image(s) (such as property icons) were not loaded.")
        for scheme, n in self.unsafe_links.items():
            self.diag("warning", "unsafe_link_removed", f"{n} link(s) or image(s) using '{scheme}:' were removed; their text was kept.")


# Dated or ordinal revision labels and history keywords; a guess the person can overrule.
_HISTORICAL_PATTERNS = [
    (re.compile(r"^\s*\d+\s*(st|nd|rd|th)\b", re.I), "starts with an ordinal such as 1st or 2nd"),
    (re.compile(r"\(\s*\d{1,2}\s*[/.\-]\s*\d{1,2}(\s*[/.\-]\s*\d{2,4})?\s*\)"), "contains a date in brackets"),
    (re.compile(r"\b(revision|history|changelog|change log|previous|older|outdated|deprecated|archived?|"
                r"superseded|legacy|old version|obsolete|lịch sử|bản cũ)\b", re.I), "contains a history keyword"),
    (re.compile(r"^\s*v\d+(\.\d+)*\b", re.I), "looks like a version label"),
]


def assign_roles(sections):
    for s in sections:
        if s.role == "metadata":
            continue
        reason = ""
        for rx, why in _HISTORICAL_PATTERNS:
            if rx.search(s.title):
                reason = f"Heading “{s.title}” {why}."
                break
        if reason:
            s.role, s.role_reason = "historical", reason
            continue
        parent = sections[s.parent_index] if s.parent_index is not None else None
        if parent is not None and parent.role == "historical":
            s.role = "historical"
            s.role_reason = f"Inside the historical section “{parent.title}”."
        else:
            s.role, s.role_reason = "current", ""


def parse_document(source, filename=""):
    """Parse HTML text into a ParsedDocument. Raises htmlparse.TooLarge for pathological input."""
    try:
        root, depth_capped = parse_html(source)
        ex = Extractor(root, filename)
        doc = ex.run()
    except RecursionError:
        raise TooLarge("The document is nested too deeply to analyse")
    if depth_capped:
        doc.diagnostics.append(("warning", "nesting_capped", "The document nests elements very deeply; the deepest levels were flattened.", None))
    return doc
