"""Tolerant HTML tree builder.

Untrusted HTML is parsed as *data*: nothing here executes script, fetches a resource, or
renders anything. The standard library tokenizer is wrapped in a small tree builder that
applies the handful of implied-end-tag rules real exports rely on and caps nesting depth
and node count so hostile input cannot exhaust the stack or memory.
"""

from html.parser import HTMLParser

VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
RAW_TEXT = frozenset(("script", "style"))
MAX_DEPTH = 120
MAX_NODES = 2_000_000

_BLOCK_STARTS = frozenset(
    "p div ul ol table h1 h2 h3 h4 h5 h6 pre figure blockquote hr details aside section article "
    "header footer nav main dl form fieldset address".split()
)


class TooLarge(ValueError):
    pass


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs=None, parent=None, text=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.text = text

    @property
    def is_text(self):
        return self.tag == "#text"

    def get(self, name, default=""):
        return self.attrs.get(name, default)

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def elements(self):
        return [c for c in self.children if not c.is_text]


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]
        self.count = 0
        self.depth_capped = False

    # -- helpers
    def _top(self):
        return self.stack[-1]

    def _pop_to(self, index):
        del self.stack[index:]

    def _close_for(self, tag):
        stack = self.stack
        if tag == "li":
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag == "li":
                    self._pop_to(i)
                    break
                if stack[i].tag in ("ul", "ol"):
                    break
        elif tag in ("td", "th"):
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag in ("td", "th"):
                    self._pop_to(i)
                    break
                if stack[i].tag in ("tr", "table"):
                    break
        elif tag == "tr":
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag == "tr":
                    self._pop_to(i)
                    break
                if stack[i].tag in ("table", "thead", "tbody", "tfoot"):
                    break
        elif tag in ("thead", "tbody", "tfoot"):
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag in ("thead", "tbody", "tfoot"):
                    self._pop_to(i)
                    break
                if stack[i].tag == "table":
                    break
        if tag in _BLOCK_STARTS and self._top().tag == "p":
            self.stack.pop()

    def _bump(self):
        self.count += 1
        if self.count > MAX_NODES:
            raise TooLarge("The document is too large to analyse")

    # -- HTMLParser callbacks
    def handle_starttag(self, tag, attrs):
        self._bump()
        self._close_for(tag)
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, parent=self._top())
        self._top().children.append(node)
        if tag in VOID:
            return
        if len(self.stack) >= MAX_DEPTH:
            self.depth_capped = True
            return  # keep the element but do not nest any deeper
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        before = len(self.stack)
        self.handle_starttag(tag, attrs)
        if len(self.stack) > before:  # <div/> is an empty element, not an open one
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self._pop_to(i)
                return
        # unmatched end tag: ignored

    def handle_data(self, data):
        if not data:
            return
        top = self._top()
        if top.children and top.children[-1].is_text:
            top.children[-1].text += data
            return
        self._bump()
        top.children.append(Node("#text", parent=top, text=data))


def parse_html(source):
    """Return (root, depth_capped). Raises TooLarge for pathological documents."""
    builder = TreeBuilder()
    builder.feed(source)
    builder.close()
    return builder.root, builder.depth_capped


# ---- tree helpers -----------------------------------------------------------------

def iter_elements(node):
    """Depth-first, document order, iterative (no recursion limit issues)."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n is not node and not n.is_text:
            yield n
        stack.extend(reversed(n.children))


def find_first(node, tag):
    for el in iter_elements(node):
        if el.tag == tag:
            return el
    return None


def raw_text(node):
    """All text under a node, tags removed (script/style content excluded)."""
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.is_text:
            out.append(n.text)
        elif n.tag not in RAW_TEXT:
            stack.extend(reversed(n.children))
    return "".join(out)
