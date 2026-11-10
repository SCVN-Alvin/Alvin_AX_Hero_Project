// Source browser: imported briefs, their outline, scenarios, media, and importer notes.
import { api } from "./api.js";
import { flash, renderBlocks } from "./blocks.js";
import { debounce, h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { AUTO_LOAD_LIMIT, assetUrl, fmtBytes, fmtDate, openMediaViewer, typeLabel } from "./media.js";
import { go, state } from "./store.js";
import { announce, confirmDialog, fieldLabel, formDialog, openMenu, toast } from "./ui.js";

let cache = null; // { key, el } so unrelated re-renders do not reload or reset scroll
export function resetSources() { cache = null; }

export function renderSources() {
  const key = `${state.projectId}:${state.docId || ""}`;
  if (cache && cache.key === key) return cache.el;
  const el = state.docId ? documentView(state.docId, state.params || new URLSearchParams()) : listView();
  cache = { key, el };
  return el;
}

export const isDocumentRoute = () => !!state.docId;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const errBanner = (msg, retry) => h("div", { class: "banner", role: "alert" }, msg, retry ? h("button", { class: "btn small", type: "button", onclick: retry }, "Retry") : null);

// ---- list of imported documents ---------------------------------------------------

function listView() {
  const pid = state.projectId;
  const root = h("div", { class: "sources" });
  async function load() {
    replace(root, h("p", { class: "muted", role: "status" }, "Loading sources…"));
    try {
      const [docs, runs] = await Promise.all([api.get(`/api/projects/${pid}/documents`), api.get(`/api/projects/${pid}/imports`)]);
      paint(docs.documents, runs.runs);
    } catch (e) {
      replace(root, errBanner(`Could not load sources: ${e.message}`, load));
    }
  }
  function paint(docs, runs) {
    const latest = runs[0];
    const problem = latest && ["failed", "interrupted", "cancelled"].includes(latest.state) ? latest : null;
    const importBtn = h("button", { class: "btn primary", type: "button", onclick: () => go(`#/p/${pid}/importbrief`) }, icon("import"), "Import brief");
    replace(root,
      h("div", { class: "page-head" }, h("h2", null, "Sources"), importBtn),
      problem ? h("div", { class: "banner", role: "alert" }, icon("alert"),
        h("span", null, h("b", null, `The last import of ${problem.source_name} ${problem.state === "failed" ? "failed" : problem.state}. `), problem.error, " Nothing was added to the project."),
        h("button", { class: "btn small", type: "button", onclick: () => go(`#/p/${pid}/importbrief`) }, "Import again")) : null,
      docs.length ? h("div", { class: "doc-list" }, docs.map((d) => docCard(d, load)))
        : h("div", { class: "empty inline" }, h("h2", null, "No briefs imported yet"),
          h("p", null, "Import an exported HTML brief and its media folder to browse its headings, tables, scenarios, images and videos here, with the original wording kept."),
          h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => go(`#/p/${pid}/importbrief`) }, icon("import"), "Import brief"))));
  }
  load();
  return root;
}

function docCard(d, reload) {
  const c = d.counts;
  const href = `#/p/${state.projectId}/sources/${d.id}`;
  return h("article", { class: "doc-card" },
    h("div", { class: "doc-main" },
      h("h3", null, h("a", { href }, d.title || d.name), d.version > 1 ? h("span", { class: "badge origin" }, `v${d.version}`) : null),
      h("div", { class: "muted" }, `${d.name} · imported ${fmtDate(d.created_at)} · ${fmtBytes(d.size)}`),
      h("div", { class: "doc-stats" },
        h("span", { class: "chip" }, plural(c.blocks, "block")), h("span", { class: "chip" }, plural(c.tables, "table")),
        h("span", { class: "chip" }, plural(c.scenarios, "scenario")),
        h("span", { class: "chip" }, `${c.assets_available} of ${c.assets} media files`),
        c.assets_unavailable ? h("span", { class: "chip warn" }, `${c.assets_unavailable} unavailable`) : null,
        c.historical_sections ? h("span", { class: "chip hist" }, `${c.historical_sections} historical section${c.historical_sections === 1 ? "" : "s"}`) : null)),
    h("div", { class: "doc-actions" },
      h("a", { class: "btn primary", href }, "Open"),
      h("button", { class: "btn", type: "button", onclick: () => removeDoc(d, reload) }, icon("trash"), "Remove")));
}

async function removeDoc(d, after) {
  const ok = await confirmDialog({
    title: `Remove “${d.title || d.name}” from this project?`,
    message: "The imported copy of the document and its media is removed from the project (files are moved to the trash folder inside the data folder). Your original files are not touched. Candidates that are not approved yet are discarded. A document that approved tasks cite as their source cannot be removed.",
    confirmLabel: "Remove import", danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/api/documents/${d.id}`);
    toast("Import removed");
    if (state.docId) { cache = null; go(`#/p/${state.projectId}/sources`); } else after();
  } catch (e) { toast(e.message, { kind: "error" }); }
}

// ---- one document -----------------------------------------------------------------

function documentView(docId, params) {
  const pid = state.projectId;
  const root = h("div", { class: "src-doc" });
  const S = {
    detail: null, assets: null, scenarios: null, tab: ["content", "scenarios", "media", "notes"].includes(params.get("tab")) ? params.get("tab") : "content",
    first: 0, next: null, total: 0, query: "", hits: null, mediaFilter: "all", panel: null, blocksEl: null, reader: null, outlineEl: null, secOf: new Map(),
    wantSeq: params.get("seq") !== null && params.get("seq") !== "" ? Number(params.get("seq")) : null,
  };

  const hashFor = () => `#/p/${pid}/sources/${docId}?tab=${S.tab}`;
  function syncUrl() { history.replaceState(null, "", hashFor()); }

  async function load() {
    replace(root, h("p", { class: "muted", role: "status" }, "Loading document…"));
    try {
      S.detail = await api.get(`/api/documents/${docId}`);
    } catch (e) {
      replace(root, errBanner(e.status === 404 ? "This source document no longer exists." : `Could not load the document: ${e.message}`,
        e.status === 404 ? () => go(`#/p/${pid}/sources`) : load));
      return;
    }
    paint();
  }

  function paint() {
    const d = S.detail;
    const c = d.counts;
    const tabs = [
      ["content", "Content", null], ["scenarios", "Scenarios", c.scenarios], ["media", "Media", c.assets],
      ["notes", "Notes", d.diagnostics.length + c.assets_unavailable],
    ];
    const tablist = h("div", { class: "tabs", role: "tablist", "aria-label": "Document views" },
      tabs.map(([key, label, n]) => h("button", {
        type: "button", role: "tab", id: `tab-${key}`, "aria-selected": String(S.tab === key), "aria-controls": "src-panel", tabindex: S.tab === key ? "0" : "-1",
        class: key === "notes" && c.assets_unavailable ? "has-warn" : "",
        onclick: () => setTab(key),
      }, label, n !== null ? h("span", { class: "count" }, String(n)) : null)));
    tablist.addEventListener("keydown", (e) => {
      const keys = tabs.map((t) => t[0]);
      const i = keys.indexOf(S.tab);
      let n = null;
      if (e.key === "ArrowRight") n = (i + 1) % keys.length;
      else if (e.key === "ArrowLeft") n = (i - 1 + keys.length) % keys.length;
      else if (e.key === "Home") n = 0;
      else if (e.key === "End") n = keys.length - 1;
      if (n !== null) { e.preventDefault(); setTab(keys[n]); document.getElementById(`tab-${keys[n]}`).focus(); }
    });
    S.panel = h("div", { class: "src-panel", id: "src-panel", role: "tabpanel", "aria-labelledby": `tab-${S.tab}` });
    replace(root,
      h("div", { class: "src-head" },
        h("a", { class: "back-link", href: `#/p/${pid}/sources` }, icon("arrowLeft"), "Sources"),
        h("div", { class: "src-titles" },
          h("h2", null, d.title || d.name, d.version > 1 ? h("span", { class: "badge origin" }, `v${d.version}`) : null),
          h("div", { class: "muted" }, `${d.name} · imported ${fmtDate(d.created_at)} · ${fmtBytes(d.size)} · sha256 ${d.sha256.slice(0, 12)}…`)),
        h("div", { class: "src-stats" },
          h("span", { class: "chip" }, plural(c.blocks, "block")), h("span", { class: "chip" }, plural(c.sections, "section")),
          h("span", { class: "chip" }, plural(c.scenarios, "scenario")),
          h("span", { class: c.assets_unavailable ? "chip warn" : "chip" }, `${c.assets_available} of ${c.assets} media`)),
        h("a", { class: "btn small primary", href: `#/p/${pid}/import/${docId}` }, icon("import"), "Review candidates"),
        h("button", { class: "btn small", type: "button", onclick: () => removeDoc({ id: docId, title: d.title, name: d.name }) }, icon("trash"), "Remove")),
      tablist, S.panel);
    renderTab();
  }

  function setTab(key, seq = null) {
    S.tab = key;
    if (seq !== null) S.wantSeq = seq;
    syncUrl();
    paint();
  }

  function renderTab() {
    if (S.tab === "content") return contentTab();
    if (S.tab === "scenarios") return scenariosTab();
    if (S.tab === "media") return mediaTab();
    return notesTab();
  }

  // ---- assets (shared by the content previews and the media tab) ----
  async function ensureAssets() {
    if (!S.assets) S.assets = (await api.get(`/api/documents/${docId}/assets`)).assets;
    return S.assets;
  }
  async function openAsset(assetId) {
    try {
      const list = await ensureAssets();
      const index = Math.max(0, list.findIndex((a) => a.id === assetId));
      openMediaViewer({ items: list.map((a) => ({ asset: a, used_in: a.used_in })), index, onJump: (seq) => setTab("content", seq) });
    } catch (e) { toast(e.message, { kind: "error" }); }
  }
  const ctx = {
    openAsset,
    jumpToElement(eid) {
      const el = document.querySelector(`.blk[data-eid="${CSS.escape(eid)}"]`);
      if (el) { el.scrollIntoView({ block: "center" }); flash(el); } else toast("That part of the page is not in this view.");
    },
  };

  // ---- content tab ----
  function contentTab() {
    const d = S.detail;
    const search = h("input", { type: "search", name: "find", class: "src-search", "aria-label": "Search this document", placeholder: "Search the document, including historical sections", value: S.query });
    const status = h("span", { class: "muted", role: "status", "aria-live": "polite" });
    const hitsEl = h("div", { class: "hits", hidden: true });
    S.blocksEl = h("div", { class: "src-blocks" });
    S.reader = h("div", { class: "reader", tabindex: "-1" },
      h("div", { class: "reader-bar" }, h("div", { class: "search" }, icon("search"), search), status), hitsEl, S.blocksEl);
    S.outlineEl = h("nav", { class: "outline", "aria-label": "Document outline" });
    replace(S.panel, h("div", { class: "src-split" }, S.outlineEl, S.reader));
    paintOutline();
    const runSearch = debounce(async () => {
      S.query = search.value.trim();
      if (!S.query) { hitsEl.hidden = true; S.blocksEl.hidden = false; replace(status); return; }
      try {
        const r = await api.get(`/api/documents/${docId}/search?q=${encodeURIComponent(S.query)}`);
        hitsEl.hidden = false;
        S.blocksEl.hidden = true;
        replace(status, `${r.hits.length}${r.truncated ? "+" : ""} match${r.hits.length === 1 ? "" : "es"}`);
        replace(hitsEl, r.hits.length ? r.hits.map((hit) => hitRow(hit, S.query)) : h("div", { class: "muted pad" }, `Nothing in this document matches “${S.query}”.`));
      } catch (e) { replace(status, e.message); }
    }, 220);
    search.addEventListener("input", runSearch);
    search.addEventListener("keydown", (e) => { if (e.key === "Escape" && search.value) { search.value = ""; runSearch(); e.stopPropagation(); } });
    const start = S.wantSeq !== null ? Math.max(0, S.wantSeq - 30) : 0;
    showWindow(start).then(() => {
      if (S.wantSeq !== null) { const t = S.wantSeq; S.wantSeq = null; scrollToSeq(t); }
    });
    if (S.query) runSearch();
  }

  function hitRow(hit, q) {
    const i = hit.snippet.toLowerCase().indexOf(q.toLowerCase());
    const text = i >= 0
      ? [hit.snippet.slice(0, i), h("mark", null, hit.snippet.slice(i, i + q.length)), hit.snippet.slice(i + q.length)]
      : [hit.snippet];
    return h("button", { type: "button", class: "hit", onclick: () => {
      const input = S.reader.querySelector(".src-search");
      input.value = ""; S.query = "";
      S.reader.querySelector(".hits").hidden = true; S.blocksEl.hidden = false;
      replace(S.reader.querySelector(".reader-bar .muted"));
      scrollToSeq(hit.sequence);
    } },
    h("span", { class: "hit-path muted" }, hit.heading_path.join(" › ") || "Document start", hit.section_role === "historical" ? h("span", { class: "badge hist-badge" }, icon("history"), "Historical") : null),
    h("span", { class: "hit-text" }, ...text));
  }

  async function fetchBlocks(start, limit = 400) {
    return api.get(`/api/documents/${docId}/blocks?start=${start}&limit=${limit}`);
  }
  function noteSections(blocks) { for (const b of blocks) S.secOf.set(b.sequence, b.section_id); }

  async function showWindow(start) {
    try {
      const r = await fetchBlocks(start);
      noteSections(r.blocks);
      S.first = r.blocks.length ? r.blocks[0].sequence : start;
      S.next = r.next_start;
      S.total = r.total;
      paintWindow(r.blocks);
    } catch (e) {
      replace(S.blocksEl, errBanner(`Could not load the content: ${e.message}`, () => showWindow(start)));
    }
  }
  function paintWindow(blocks) {
    const parts = [];
    if (S.first > 0) parts.push(h("button", { class: "btn more more-before", type: "button", onclick: loadEarlier }, icon("arrowLeft"), `Show earlier blocks (${S.first} before this)`));
    parts.push(h("div", { class: "blk-run", role: "list" }, renderBlocks(blocks, ctx)));
    parts.push(moreButton());
    replace(S.blocksEl, ...parts);
  }
  function moreButton() {
    if (S.next === null) return h("div", { class: "muted end-note" }, `End of document · ${S.total} blocks`);
    return h("button", { class: "btn more more-after", type: "button", onclick: loadMore }, `Show more (${S.total - S.next} blocks remaining)`);
  }
  async function loadMore() {
    const btn = S.blocksEl.querySelector(".more-after");
    if (btn) btn.disabled = true;
    try {
      const r = await fetchBlocks(S.next);
      noteSections(r.blocks);
      S.next = r.next_start;
      S.blocksEl.querySelector(".blk-run").append(renderBlocks(r.blocks, ctx));
      const m = S.blocksEl.querySelector(".more-after, .end-note");
      if (m) m.replaceWith(moreButton());
    } catch (e) { toast(e.message, { kind: "error" }); if (btn) btn.disabled = false; }
  }
  async function loadEarlier() {
    const start = Math.max(0, S.first - 400);
    try {
      const r = await fetchBlocks(start, S.first - start);
      noteSections(r.blocks);
      const reader = S.reader;
      const before = reader.scrollHeight;
      S.blocksEl.querySelector(".blk-run").prepend(renderBlocks(r.blocks, ctx));
      S.first = start;
      const btn = S.blocksEl.querySelector(".more-before");
      if (start === 0) btn.remove(); else replace(btn, icon("arrowLeft"), `Show earlier blocks (${start} before this)`);
      reader.scrollTop += reader.scrollHeight - before;
    } catch (e) { toast(e.message, { kind: "error" }); }
  }
  async function refreshWindow() {
    const top = S.reader ? S.reader.scrollTop : 0;
    try {
      const r = await fetchBlocks(S.first, 1000);
      noteSections(r.blocks);
      S.next = r.next_start;
      paintWindow(r.blocks);
      if (S.reader) S.reader.scrollTop = top;
    } catch (e) { toast(e.message, { kind: "error" }); }
  }

  async function scrollToSeq(seq) {
    if (S.tab !== "content") return setTab("content", seq);
    let el = S.blocksEl && S.blocksEl.querySelector(`#b-${seq}`);
    if (!el) {
      await showWindow(Math.max(0, seq - 30));
      el = S.blocksEl.querySelector(`#b-${seq}`);
    }
    if (el) { el.scrollIntoView({ block: "start" }); flash(el); el.setAttribute("tabindex", "-1"); el.focus({ preventScroll: true }); }
  }

  // ---- outline ----
  function paintOutline() {
    const secs = S.detail.sections;
    const depthOf = new Map();
    const byId = new Map(secs.map((s) => [s.id, s]));
    for (const s of secs) {
      let dep = 0, p = s.parent_id;
      while (p) { dep += 1; p = byId.get(p) && byId.get(p).parent_id; }
      depthOf.set(s.id, dep);
    }
    const hist = secs.filter((s) => s.role === "historical").length;
    replace(S.outlineEl,
      h("div", { class: "outline-head" }, "Outline",
        h("span", { class: "muted" }, plural(secs.length, "section"))),
      hist ? h("p", { class: "muted outline-note" }, `${hist} historical section${hist === 1 ? " is" : "s are"} kept and searchable, and left out of candidate generation by default.`) : null,
      h("ul", { class: "outline-list" }, secs.map((s) => {
        const menuBtn = s.role === "metadata" ? null : h("button", {
          class: "icon-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": `Options for section ${s.title}`,
          onclick: (e) => { e.stopPropagation(); sectionMenu(menuBtn, s); },
        }, icon("more"));
        const row = h("div", { class: `outline-row role-${s.role}` },
          h("button", { type: "button", class: "outline-link", title: s.role_reason || s.title, onclick: () => scrollToSeq(s.first_sequence) },
            h("span", { class: "ol-title" }, s.title || "(untitled)"),
            s.role === "historical" ? h("span", { class: "badge hist-badge" }, icon("history"), "Historical") : s.role === "metadata" ? h("span", { class: "badge origin" }, "Properties") : null,
            s.role_origin === "user" ? h("span", { class: "sr-only" }, " (set by you)") : null),
          menuBtn);
        const li = h("li", { class: "outline-item" }, row);
        li.style.paddingLeft = `${depthOf.get(s.id) * 14}px`;
        return li;
      })));
  }

  function sectionMenu(anchor, s) {
    const toRole = s.role === "historical" ? "current" : "historical";
    openMenu(anchor, [
      { label: s.role === "historical" ? "Treat as current requirements" : "Treat as historical (exclude from candidates)", onSelect: () => setRole(s, toRole) },
      { label: "Show in document", onSelect: () => scrollToSeq(s.first_sequence) },
    ], { align: "right" });
  }
  async function setRole(s, role) {
    try {
      const r = await api.patch(`/api/sections/${s.id}`, { role });
      const map = new Map(r.sections.map((x) => [x.id, x]));
      for (const sec of S.detail.sections) Object.assign(sec, map.get(sec.id) || {});
      S.detail.counts.historical_sections = S.detail.sections.filter((x) => x.role === "historical").length;
      paintOutline();
      await refreshWindow();
      const msg = `“${s.title}”${r.changed > 1 ? ` and ${r.changed - 1} subsection(s)` : ""} marked ${role}. Nothing was deleted; this only affects which sections candidate generation uses.`;
      toast(msg);
      announce(msg);
    } catch (e) { toast(e.message, { kind: "error" }); }
  }

  // ---- scenarios tab ----
  async function scenariosTab() {
    replace(S.panel, h("p", { class: "muted pad", role: "status" }, "Loading scenarios…"));
    try {
      if (!S.scenarios) S.scenarios = (await api.get(`/api/documents/${docId}/scenarios`)).scenarios;
    } catch (e) { return replace(S.panel, errBanner(e.message, scenariosTab)); }
    const list = S.scenarios;
    const tables = S.detail.tables;
    const chooser = h("button", { class: "btn", type: "button", onclick: chooseTable }, icon("list"), "Choose table and columns…");
    const intro = h("div", { class: "scen-intro" },
      h("p", { class: "muted" }, list.length
        ? `${list.length} scenario${list.length === 1 ? "" : "s"} read from the document's table. Identifiers and numbered steps are shown as written; step numbers are the document's, not database ids. “Original wording” always shows the untouched cell text.`
        : tables.length ? "No scenario table was recognised automatically. Choose the table and its columns to extract scenarios." : "This document has no tables."),
      tables.length ? chooser : null);
    replace(S.panel, h("div", { class: "scroll-panel" }, intro, list.length ? h("div", { class: "scen-list" }, list.map(scenarioCard)) : null));
  }

  function scenarioCard(s) {
    const numbers = s.steps.map((x) => x.label);
    return h("article", { class: "scen", id: `scen-${s.identifier}` },
      h("header", null,
        h("span", { class: "badge scen-id" }, s.identifier),
        h("h3", null, s.title || h("span", { class: "muted" }, "(no concept text)")),
        h("button", { class: "link", type: "button", onclick: () => setTab("content", s.source_sequence) }, "Show in document")),
      s.preamble ? h("p", { class: "scen-pre" }, s.preamble) : null,
      s.segmentation === "steps"
        ? h("ol", { class: "steps" }, s.steps.map((st, i) => h("li", { class: numbers.indexOf(st.label) !== i ? "dup" : "" },
          h("span", { class: "n", title: numbers.indexOf(st.label) !== i ? "This number appears more than once in the original" : null }, `${st.label}.`), h("span", null, st.text))))
        : s.description ? h("p", { class: "scen-plain" }, s.description, s.segmentation === "ambiguous" ? h("span", { class: "muted" }, " (not split into steps: the numbering is unclear)") : null) : null,
      h("details", { class: "orig" }, h("summary", null, "Original wording"), h("pre", null, s.description || "(empty)")));
  }

  async function chooseTable() {
    const tables = S.detail.tables;
    let done = false;
    await formDialog({
      title: "Choose the scenario table", submitLabel: "Extract scenarios", wide: true,
      body: (dlg) => {
        const sel = h("select", { name: "table" }, tables.map((t, i) => h("option", { value: t.block_id }, `Table ${i + 1}: ${(t.heading_path[t.heading_path.length - 1] || "document start")} (${t.rows} rows × ${t.cols} columns)`)));
        const colSel = (name, optional) => h("select", { name }, optional ? h("option", { value: "" }, "(none)") : null);
        const id = colSel("identifier"), title = colSel("title", true), desc = colSel("description", true);
        const first = h("input", { type: "text", name: "first_data_row", inputmode: "numeric", value: "1", size: "3" });
        const fill = () => {
          const t = tables.find((x) => x.block_id === sel.value);
          for (const [el, optional, guess] of [[id, false, 0], [title, true, 1], [desc, true, 2]]) {
            replace(el, optional ? h("option", { value: "" }, "(none)") : null, t.header.map((x, i) => h("option", { value: String(i) }, `Column ${i + 1}: ${x || "(blank)"}`)));
            if (guess < t.cols) el.value = String(guess);
          }
          first.value = String(t.header_rows || 1);
        };
        sel.addEventListener("change", fill);
        fill();
        return [
          fieldLabel("Table", sel), fieldLabel("Identifier column (for example Video No.)", id), fieldLabel("Title or concept column", title), fieldLabel("Description or scenario column", desc),
          fieldLabel("First data row (rows before it are headings; the first row is 0)", first),
          h("p", { class: "muted" }, "This replaces the scenarios taken from that table. The table itself, and its original text, are not changed."),
        ];
      },
      onSubmit: async (dlg) => {
        const f = dlg.dialog;
        const num = (n) => { const v = f.querySelector(`[name="${n}"]`).value; return v === "" ? null : Number(v); };
        const r = await api.post(`/api/documents/${docId}/scenarios/extract`, {
          table_block_id: f.querySelector('[name="table"]').value,
          columns: { identifier: num("identifier"), title: num("title"), description: num("description") },
          first_data_row: Number(f.querySelector('[name="first_data_row"]').value || 0),
        });
        S.scenarios = r.scenarios;
        S.detail = await api.get(`/api/documents/${docId}`);
        done = true;
      },
    });
    if (done) { toast("Scenarios updated"); paint(); }
  }

  // ---- media tab ----
  async function mediaTab() {
    replace(S.panel, h("p", { class: "muted pad", role: "status" }, "Loading media…"));
    let list;
    try { list = await ensureAssets(); } catch (e) { return replace(S.panel, errBanner(e.message, mediaTab)); }
    const filters = [
      ["all", "All", () => true], ["image", "Images", (a) => a.kind === "image" && a.media_type !== "image/gif"], ["gif", "GIFs", (a) => a.media_type === "image/gif"],
      ["video", "Video", (a) => a.kind === "video"], ["problem", "Unavailable", (a) => a.availability !== "available"],
    ];
    const grid = h("div", { class: "media-grid" });
    const bar = h("div", { class: "pills", role: "group", "aria-label": "Filter media" });
    function draw() {
      const fn = filters.find((f) => f[0] === S.mediaFilter)[2];
      const shown = list.filter(fn);
      replace(bar, filters.map(([key, label, f]) => h("button", { type: "button", class: "pill", "aria-pressed": String(S.mediaFilter === key), onclick: () => { S.mediaFilter = key; draw(); } }, `${label} ${list.filter(f).length}`)));
      replace(grid, shown.length ? shown.map((a) => tile(a, shown)) : h("div", { class: "muted pad" }, "No files in this group."));
    }
    function tile(a, shown) {
      const open = () => openMediaViewer({ items: shown.map((x) => ({ asset: x, used_in: x.used_in })), index: shown.indexOf(a), onJump: (seq) => setTab("content", seq) });
      let thumb;
      if (a.availability !== "available") thumb = h("div", { class: "thumb ph problem" }, icon("alert"), h("span", null, a.availability));
      else if (a.kind === "image" && a.inline_ok && a.size <= AUTO_LOAD_LIMIT) thumb = h("img", { class: "thumb", loading: "lazy", decoding: "async", alt: "", src: assetUrl(a.id) });
      else thumb = h("div", { class: "thumb ph" }, icon(a.kind === "video" || a.kind === "audio" ? "play" : "image"), h("span", null, typeLabel(a)));
      return h("button", { type: "button", class: "tile", onclick: open, "aria-label": `${a.name}, ${typeLabel(a)}, ${a.size ? fmtBytes(a.size) : a.availability}` },
        thumb, h("span", { class: "tile-name" }, a.name), h("span", { class: "muted tile-meta" }, a.availability === "available" ? `${typeLabel(a)} · ${fmtBytes(a.size)}` : a.availability));
    }
    draw();
    const missing = list.filter((a) => a.availability !== "available").length;
    replace(S.panel, h("div", { class: "scroll-panel" },
      h("p", { class: "muted" }, `${list.length} files referenced by this document; ${list.length - missing} imported${missing ? `, ${missing} unavailable` : ""}. Large GIFs and videos load only when opened.`),
      bar, grid));
  }

  // ---- notes tab ----
  async function notesTab() {
    replace(S.panel, h("p", { class: "muted pad", role: "status" }, "Loading notes…"));
    let list;
    try { list = await ensureAssets(); } catch (e) { return replace(S.panel, errBanner(e.message, notesTab)); }
    const problems = list.filter((a) => a.availability !== "available");
    const diags = S.detail.diagnostics.slice().sort((a, b) => ({ error: 0, warning: 1, info: 2 }[a.severity] - { error: 0, warning: 1, info: 2 }[b.severity]));
    replace(S.panel, h("div", { class: "scroll-panel" },
      h("h3", null, "Files that could not be imported"),
      problems.length ? h("ul", { class: "list-plain notes" }, problems.map((a) => h("li", { class: "note warning" }, icon("alert"),
        h("div", null, h("b", null, a.name), " ", h("span", { class: "badge blocked" }, a.availability), h("div", { class: "muted" }, a.problem),
          h("div", { class: "muted" }, "Referenced as ", h("code", null, a.source_path)),
          a.used_in.length ? h("button", { class: "link", type: "button", onclick: () => setTab("content", a.used_in[0].sequence) }, "Show where it is used") : null))))
        : h("p", { class: "muted" }, "Every referenced file was found and imported."),
      h("h3", null, "What the importer noticed"),
      diags.length ? h("ul", { class: "list-plain notes" }, diags.map((n) => h("li", { class: `note ${n.severity}` }, icon(n.severity === "info" ? "check" : "alert"),
        h("div", null, n.message, n.sequence !== null ? h("div", null, h("button", { class: "link", type: "button", onclick: () => setTab("content", n.sequence) }, "Show in document")) : null))))
        : h("p", { class: "muted" }, "Nothing to report."),
      h("p", { class: "muted small-note" }, `Parser ${S.detail.parser_version}. Notes are written once when the document is imported; file problems above reflect the current state.`)));
  }

  load();
  return root;
}
