// Import review: generate candidates from a source, classify with rule evidence, split/merge/reparent,
// and approve onto the board. Source text sits beside the candidate so every proposal can be checked.
import { openAiPreview, openResults, openRunList } from "./ai.js";
import { api } from "./api.js";
import { flash, renderBlocks } from "./blocks.js";
import { openRangeDialog, rangeLabel, thumbFor } from "./clip.js";
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { assetUrl, AUTO_LOAD_LIMIT, fmtBytes, openMediaViewer, typeLabel } from "./media.js";
import { go, labelOf, state } from "./store.js";
import { clarificationPanel, primaryCard, ROLE_OPTIONS, roleLabel, suggestionCard, tierText } from "./taskview.js";
import { announce, confirmDialog, fieldLabel, formDialog, openMenu, toast } from "./ui.js";

let cache = null;
export function resetReview() { cache = null; }

export function renderReview() {
  const key = `${state.projectId}:${state.docId || ""}`;
  if (cache && cache.key === key) {
    const run = state.params && state.params.get("run");
    if (run && cache.el.__openRun) cache.el.__openRun(run);   // the same view asked to show a finished AI run
    return cache.el;
  }
  const el = reviewView();
  cache = { key, el };
  return el;
}

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const rangeText = (r) => rangeLabel(r.start_ms, r.end_ms);
const DISC = { art: "Art", dev: "Dev" };
const FIELD_NAME = { discipline: "Discipline", category: "Category", impact: "Impact", urgency: "Urgency" };
const SUG_STATE = { suggested: "Suggested", conflicting: "Conflicting", superseded: "Not used", accepted: "Accepted", rejected: "Rejected" };

function valueLabel(field, value) {
  if (field === "discipline") return DISC[value] || value;
  const list = { category: "categories", impact: "impacts", urgency: "urgencies" }[field];
  return labelOf(list, value);
}
const errBanner = (msg, retry) => h("div", { class: "banner", role: "alert" }, msg, retry ? h("button", { class: "btn small", type: "button", onclick: retry }, "Retry") : null);

function reviewView() {
  const pid = state.projectId;
  const root = h("div", { class: "review" });
  const R = {
    docs: [], docId: null, listing: null, sel: null, detail: null, checked: new Set(), tab: "needs_review", expanded: new Set(), compare: false, openSecs: new Set(), pendingJump: null,
    assets: null, srcKey: null, list: h("aside", { class: "rv-list", "aria-label": "Candidates" }), source: h("section", { class: "rv-source", "aria-label": "Source" }),
    detailEl: h("section", { class: "rv-detail", "aria-label": "Candidate details" }),
  };

  // ---- loading ---------------------------------------------------------------
  async function load() {
    replace(root, h("p", { class: "muted pad", role: "status" }, "Loading…"));
    try {
      R.docs = (await api.get(`/api/projects/${pid}/documents`)).documents;
      if (!R.docs.length) return replace(root, emptyNoDocs());
      let docId = state.docId && R.docs.some((d) => d.id === state.docId) ? state.docId : R.docs[0].id;
      R.docId = docId;
      R.listing = await api.get(`/api/documents/${docId}/candidates`);
      const want = (state.params && state.params.get("cand")) || null;
      R.sel = want && R.listing.candidates.some((c) => c.id === want) ? want : null;
      paint();
      if (R.sel) await select(R.sel, { scroll: true });
      const runId = state.params && state.params.get("run");
      if (runId) root.__openRun(runId);
    } catch (e) {
      replace(root, errBanner(`Could not load the review workspace: ${e.message}`, load));
    }
  }

  async function refresh({ keepSelection = true } = {}) {
    R.listing = await api.get(`/api/documents/${R.docId}/candidates`);
    R.checked = new Set([...R.checked].filter((id) => R.listing.candidates.some((c) => c.id === id && c.state === "needs_review")));
    if (R.sel && !R.listing.candidates.some((c) => c.id === R.sel)) R.sel = null;
    paintList();
    if (R.sel && keepSelection) await loadDetail(R.sel);
    else if (!R.sel) paintDetail();
  }

  const emptyNoDocs = () => h("div", { class: "empty" },
    h("h2", null, "Nothing to review yet"),
    h("p", null, "Import a creative brief first. Its sections are then read into candidate tasks, requirements and exclusions that you review here before anything reaches the board."),
    h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => go(`#/p/${pid}/importbrief`) }, icon("import"), "Import brief")));

  // ---- layout ----------------------------------------------------------------
  function paint() {
    const doc = R.listing.document;
    const docSel = h("select", { "aria-label": "Source document", onchange: (e) => { go(`#/p/${pid}/import/${e.target.value}`); } },
      R.docs.map((d) => h("option", { value: d.id, selected: d.id === R.docId }, `${d.title || d.name}${d.version > 1 ? ` (v${d.version})` : ""}`)));
    const bar = h("div", { class: "rv-bar" },
      h("div", { class: "rv-bar-main" }, h("h2", null, "Import review"), docSel,
        h("a", { class: "link", href: `#/p/${pid}/sources/${R.docId}` }, "Open source")),
      h("div", { class: "rv-bar-actions" },
        h("button", { class: "btn", type: "button", "aria-pressed": String(R.compare), "data-compare-toggle": "", onclick: () => setCompare(!R.compare), title: "Show the original source beside the task" }, icon("doc"), R.compare ? "Hide source" : "Compare source"),
        h("button", { class: "btn", type: "button", "data-ai-runs": "", onclick: () => openRunList({ pid, docId: R.docId, onImported: afterImport }), title: "AI drafts for this document (optional)" }, icon("edit"), "AI drafts"),
        R.listing.generated ? h("button", { class: "btn", type: "button", onclick: rerunRules, title: "Recompute rule suggestions. Anything you set or reviewed is kept." }, icon("rules"), "Re-run rules") : null,
        h("button", { class: R.listing.generated ? "btn" : "btn primary", type: "button", onclick: openGenerate }, icon("plus"), R.listing.generated ? "Generate more…" : "Generate candidates…")));
    replace(root, bar, h("div", { class: `rv-body ${R.compare ? "compare" : ""}` }, R.list, R.detailEl, R.compare ? R.source : null));
    paintList();
    paintDetail();
    paintSource();
  }

  // ---- candidate list ----------------------------------------------------------
  function stateTabs() {
    const c = {};
    for (const x of R.listing.candidates) if (!x.parent_id) c[x.state] = (c[x.state] || 0) + 1;   // top-level items only; lines count with their task
    return h("div", { class: "pills", role: "group", "aria-label": "Show candidates" },
      [["needs_review", "Needs review"], ["approved", "Approved"], ["dismissed", "Dismissed"]].map(([k, label]) =>
        h("button", { type: "button", class: "pill", "aria-pressed": String(R.tab === k), onclick: () => { R.tab = k; paintList(); } }, `${label} ${c[k] || 0}`)));
  }

  function paintList() {
    if (!R.listing) return;
    const cands = R.listing.candidates;
    const by = new Map(cands.map((c) => [c.id, c]));
    const kids = new Map();
    for (const c of cands) if (c.parent_id && by.has(c.parent_id)) { (kids.get(c.parent_id) || kids.set(c.parent_id, []).get(c.parent_id)).push(c); }
    const inTab = (c) => c.state === R.tab;
    const top = cands.filter((c) => !c.parent_id || !by.has(c.parent_id));
    const tasks = top.filter((c) => c.kind === "task" && inTab(c));
    const loose = top.filter((c) => (c.kind === "requirement" || c.kind === "exclusion") && inTab(c));
    const notes = top.filter((c) => c.kind === "note" && inTab(c));
    const assets = top.filter((c) => c.kind === "asset" && inTab(c));
    const body = [];
    const group = (title, items, hint, open = true) => items.length
      ? h("details", { class: "rv-group", open }, h("summary", null, title, h("span", { class: "count" }, String(items.length)), hint ? h("span", { class: "muted rv-hint" }, hint) : null),
        h("ul", { class: "rv-tree" }, items.map((c) => row(c, kids, 0))))
      : null;
    if (!R.listing.generated) {
      body.push(h("div", { class: "empty inline" }, h("h3", null, "No candidates yet"),
        h("p", null, "Generate candidates to read this document's current sections into task groups, requirement lines, exclusions and media suggestions. Historical sections are left out unless you choose them."),
        h("button", { class: "btn primary", type: "button", onclick: openGenerate }, "Generate candidates…")));
    } else {
      body.push(group("Tasks", tasks, "groups with actionable lines"),
        group("Loose requirements", loose, "values and lines not under a task yet"),
        group("Notes", notes, "explanatory text, not tasks", R.tab !== "needs_review" || notes.length <= 3),
        group("Asset labels", assets, "names for sets of pictures", false));
      if (!body.some(Boolean)) {
        const total = cands.length;
        body.push(h("div", { class: "empty inline" }, h("h3", null, total ? `Nothing ${R.tab === "needs_review" ? "left to review" : R.tab === "approved" ? "approved yet" : "dismissed"}` : "No candidates were found"),
          h("p", null, total ? "Use the tabs above to see the others." : "The chosen sections had no groups of lines to propose. Choose other sections, or add tasks by hand on the board.")));
      }
    }
    const nTasks = [...R.checked].filter((id) => by.get(id) && by.get(id).kind === "task").length;
    const toolbar = R.checked.size ? h("div", { class: "rv-bulk", role: "toolbar", "aria-label": "Selected candidates" },
      h("span", { class: "grow" }, `${R.checked.size} selected`),
      h("button", { class: "btn primary small", type: "button", onclick: () => approveFlow([...R.checked]) }, icon("check"), "Approve…"),
      h("button", { class: "btn small", type: "button", onclick: bulkClassify }, "Classify…"),
      h("button", { class: "btn small", type: "button", onclick: bulkAccept }, "Accept suggestions"),
      nTasks >= 1 ? h("button", { class: "btn small", type: "button", "data-bulk-ai": "", onclick: () => startAi([...R.checked].filter((id) => (by.get(id) || {}).kind === "task"), "drafts") }, icon("edit"), "Draft with AI…") : null,
      nTasks >= 2 ? h("button", { class: "btn small", type: "button", onclick: mergeFlow }, "Merge…") : null,
      h("button", { class: "btn small", type: "button", onclick: bulkDismiss }, "Dismiss"),
      h("button", { class: "btn subtle small", type: "button", onclick: () => { R.checked.clear(); paintList(); } }, "Clear")) : null;
    const scroller = R.list.querySelector(".rv-scroll");
    const top0 = scroller ? scroller.scrollTop : 0;
    replace(R.list, stateTabs(), toolbar, h("div", { class: "rv-scroll" }, ...body));
    const sc = R.list.querySelector(".rv-scroll");
    if (sc) sc.scrollTop = top0;
  }

  function badges(c) {
    const out = [];
    if (c.level === "subtask") out.push(h("span", { class: "badge level" }, "subtask"));
    if (c.level === "epic") out.push(h("span", { class: "badge level epic" }, "epic"));
    for (const d of c.disciplines) out.push(h("span", { class: `badge ${d}` }, DISC[d]));
    if (c.kind === "exclusion") out.push(h("span", { class: "badge blocked" }, "Exclusion"));
    if (c.conflicts) out.push(h("span", { class: "badge blocked", title: "Rules disagree" }, "Conflict"));
    else if (c.pending_suggestions) out.push(h("span", { class: "badge sug", title: "Rule suggestions to review" }, `${c.pending_suggestions} suggested`));
    if (c.origin === "ai") out.push(h("span", { class: "badge sug", title: "Proposed by AI. Review it like any other candidate." }, "AI"));
    if (c.open_questions) out.push(h("span", { class: "badge blocked", title: "Needs clarification" }, `? ${c.open_questions}`));
    if (c.has_primary) out.push(h("span", { class: "badge status done", title: "A main clip is chosen" }, icon("play"), "clip"));
    else if (c.media.suggested || c.media.attached) out.push(h("span", { class: "badge cat", title: "Media" }, icon("image"), `${c.media.attached}${c.media.suggested ? `+${c.media.suggested}` : ""}`));
    if (c.state === "approved" && c.ticket_key) out.push(h("a", { class: "badge status done", href: `#/p/${pid}/board?task=${c.task_id}`, onclick: (e) => e.stopPropagation(), title: "Open on the board" }, c.ticket_key));
    if (c.state === "approved" && c.kind !== "task") out.push(h("span", { class: "badge status done" }, "Approved"));
    return out;
  }

  function row(c, kids, depth) {
    const children = (kids.get(c.id) || []).filter((k) => k.state === c.state || (R.tab === "needs_review" && k.state === "needs_review"));
    const isTask = c.kind === "task";
    const open = R.expanded.has(c.id) || (R.sel && isAncestor(c.id, R.sel));
    const check = (c.state === "needs_review" && (isTask || c.kind === "requirement" || c.kind === "exclusion") && depth === 0)
      ? h("input", { type: "checkbox", "aria-label": `Select ${c.title}`, checked: R.checked.has(c.id), onclick: (e) => e.stopPropagation(),
        onchange: (e) => { if (e.target.checked) R.checked.add(c.id); else R.checked.delete(c.id); paintList(); } }) : null;
    const li = h("li", { class: `rv-item ${c.kind} ${c.id === R.sel ? "selected" : ""}`, "data-id": c.id });
    const toggle = children.length ? h("button", { type: "button", class: "icon-btn tog", "aria-label": open ? "Collapse" : "Expand", "aria-expanded": String(!!open),
      onclick: (e) => { e.stopPropagation(); if (open) R.expanded.delete(c.id); else R.expanded.add(c.id); paintList(); } }, icon(open ? "chevDown" : "chevRight")) : h("span", { class: "tog-sp" });
    const main = h("button", { type: "button", class: "rv-main", "aria-current": c.id === R.sel ? "true" : null, onclick: () => select(c.id) },
      h("span", { class: "rv-title" }, c.title), h("span", { class: "rv-badges" }, ...badges(c)),
      !isTask && c.scenarios.length ? h("span", { class: "rv-scen muted" }, `Scenario ${c.scenarios.map((s) => s.identifier).join(" + ")}`) : null,
      children.length && !open ? h("span", { class: "muted rv-sub" }, plural(children.length, "line/subtask")) : null);
    li.append(h("div", { class: "rv-line", style: null }, toggle, check, main));
    li.firstChild.style.paddingLeft = `${depth * 16}px`;
    if (children.length && open) li.append(h("ul", { class: "rv-tree" }, children.map((k) => row(k, kids, depth + 1))));
    return li;
  }

  function isAncestor(id, of) {
    const by = new Map(R.listing.candidates.map((c) => [c.id, c]));
    let cur = by.get(of);
    while (cur && cur.parent_id) { if (cur.parent_id === id) return true; cur = by.get(cur.parent_id); }
    return false;
  }

  // ---- selection, detail, source ------------------------------------------------
  async function select(id, { scroll = false } = {}) {
    R.sel = id;
    history.replaceState(null, "", `#/p/${pid}/import/${R.docId}?cand=${id}`);
    paintList();
    await loadDetail(id);
    if (scroll) { const el = R.list.querySelector(`.rv-item[data-id="${CSS.escape(id)}"]`); if (el) el.scrollIntoView({ block: "center" }); }
  }

  async function loadDetail(id) {
    replace(R.detailEl, h("p", { class: "muted pad", role: "status" }, "Loading…"));
    try {
      const d = await api.get(`/api/candidates/${id}`);
      if (R.sel !== id) return;
      R.detail = d;
      paintDetail();
      paintSource();
    } catch (e) {
      replace(R.detailEl, errBanner(e.message, () => loadDetail(id)));
    }
  }

  async function act(fn, { message, keep = true } = {}) {
    try {
      const r = await fn();
      if (message) toast(message);
      await refresh({ keepSelection: keep });
      return r;
    } catch (e) {
      toast(e.message, { kind: "error" });
      await refresh({ keepSelection: true }).catch(() => {});
      return null;
    }
  }

  root.__openRun = (runId) => {
    history.replaceState(null, "", `#/p/${pid}/import/${R.docId}${R.sel ? `?cand=${R.sel}` : ""}`);
    openResults(runId, { pid, docId: R.docId, onImported: afterImport });
  };

  async function afterImport(out) {
    await refresh({ keepSelection: false });
    const id = (out.candidate_ids || [])[0];
    if (id) { R.tab = "needs_review"; await select(id, { scroll: true }); }
  }

  const patch = (body, message) => act(() => api.patch(`/api/candidates/${R.sel}`, body), { message });

  function setCompare(on, jump = null) {
    R.compare = on;
    R.pendingJump = jump;
    paint();
    if (on) paintSource();
  }

  function showInSource(target) {
    if (R.compare && !R.pendingJump) {
      const el = R.source.querySelector(`.blk[data-seq="${target.sequence}"]`);
      if (el) { el.scrollIntoView({ block: "center" }); flash(el); return; }
    }
    setCompare(true, target);
  }

  function paintSource() {
    if (!R.compare) return;
    const d = R.detail;
    if (!d) return replace(R.source, h("div", { class: "rv-empty muted" }, h("h3", null, "Source"), h("p", null, "Select a candidate to see the original text it came from, with its exact source blocks highlighted.")));
    const all = d.family_sources && d.family_sources.length ? d.family_sources : d.sources;
    const seqs = all.map((s) => s.sequence);
    if (R.pendingJump) seqs.push(R.pendingJump.sequence);
    if (!seqs.length) return replace(R.source, h("div", { class: "rv-empty muted" }, "This candidate has no source blocks."));
    const lo = Math.max(0, Math.min(...seqs) - 6);
    const span = Math.min(260, Math.max(...seqs) - lo + 12);
    const key = `${d.id}:${lo}:${span}`;
    replace(R.source, h("div", { class: "rv-src-head" }, h("h3", null, "Source"),
      h("span", { class: "muted" }, d.heading_path.join(" › ")),
      h("a", { class: "link", href: `#/p/${pid}/sources/${R.docId}?tab=content&seq=${Math.min(...seqs)}` }, "Open in Sources")),
      h("div", { class: "rv-src-legend muted" }, h("span", { class: "key label" }, "label"), h("span", { class: "key evidence" }, "text it came from"), h("span", { class: "key scope" }, "scenario scope"), h("span", { class: "key media" }, "media")),
      h("div", { class: "rv-src-body src-blocks", role: "list" }, h("p", { class: "muted pad", role: "status" }, "Loading source…")));
    R.srcKey = key;
    api.get(`/api/documents/${R.docId}/blocks?start=${lo}&limit=${span}`).then((r) => {
      if (R.srcKey !== key) return;
      const body = R.source.querySelector(".rv-src-body");
      replace(body, renderBlocks(r.blocks, { openAsset, jumpToElement: () => {} }));
      const roleOf = new Map(all.map((s) => [s.sequence, s.role]));
      let first = null;
      for (const el of body.querySelectorAll(".blk")) {
        const role = roleOf.get(Number(el.dataset.seq));
        if (role) { el.classList.add("src-hit", `role-${role}`); first = first || el; }
      }
      if (first) body.parentElement.scrollTop = Math.max(0, first.offsetTop - 90);
      if (R.pendingJump) {
        const el = body.querySelector(`.blk[data-seq="${R.pendingJump.sequence}"]`);
        R.pendingJump = null;
        if (el) { el.scrollIntoView({ block: "center" }); flash(el); }
      }
    }).catch((e) => { const b = R.source.querySelector(".rv-src-body"); if (b) replace(b, errBanner(e.message)); });
  }

  async function ensureAssets() {
    if (!R.assets) R.assets = (await api.get(`/api/documents/${R.docId}/assets`)).assets;
    return R.assets;
  }
  async function openAsset(assetId) {
    try {
      const list = await ensureAssets();
      openMediaViewer({ items: list.map((a) => ({ asset: a, used_in: a.used_in })), index: Math.max(0, list.findIndex((a) => a.id === assetId)),
        onJump: (seq) => go(`#/p/${pid}/sources/${R.docId}?tab=content&seq=${seq}`) });
    } catch (e) { toast(e.message, { kind: "error" }); }
  }

  // ---- candidate detail ----------------------------------------------------------
  const RANK = { epic: 0, task: 1, subtask: 2 };
  const kindWord = (d) => ({ task: d.level === "subtask" ? "Subtask" : d.level === "epic" ? "Epic" : "Task", requirement: "Requirement", exclusion: "Exclusion", note: "Note", asset: "Asset label" }[d.kind]);

  function paintDetail() {
    const d = R.detail && R.sel === R.detail.id ? R.detail : null;
    if (!d) {
      return replace(R.detailEl, h("div", { class: "rv-empty muted" }, h("h3", null, "Candidate"),
        h("p", null, "Select a candidate on the left. Nothing becomes a ticket until you approve it, and approval keeps a link to the exact source text.")));
    }
    const locked = d.state !== "needs_review";
    const parts = [];
    parts.push(headRow(d, locked));
    parts.push(...titleBlock(d, locked));
    for (const w of d.warnings) parts.push(h("div", { class: "banner info small", role: "note" }, icon("alert"), w));
    if (d.kind === "task") parts.push(...taskSections(d, locked));
    else if (d.kind === "requirement" || d.kind === "exclusion") parts.push(...lineSections(d, locked));
    else parts.push(...otherSections(d, locked));
    parts.push(actionsBar(d));
    const sc = R.detailEl.querySelector(".rv-d-scroll");
    const top0 = sc ? sc.scrollTop : 0;
    replace(R.detailEl, h("div", { class: "rv-d-scroll" }, ...parts));
    const sc2 = R.detailEl.querySelector(".rv-d-scroll");
    if (sc2) sc2.scrollTop = top0;
  }

  function headRow(d, locked) {
    const more = !locked && ["task", "note"].includes(d.kind)
      ? h("button", { class: "icon-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": "More actions for this candidate", onclick: (e) => openMenu(e.currentTarget, headMenu(d), { align: "right" }) }, icon("more")) : null;
    return h("div", { class: "rv-d-head" },
      h("span", { class: `badge ${d.kind === "exclusion" ? "blocked" : "level"}` }, kindWord(d)),
      h("span", { class: `badge status ${d.state === "approved" ? "done" : ""}` }, { needs_review: "Needs import review", approved: "Approved", dismissed: "Dismissed", merged: "Merged" }[d.state]),
      d.ai ? h("span", { class: "badge sug", title: `Proposed by AI (${d.ai.provider === "fake" ? "scripted test provider" : "Codex CLI"}${d.ai.model ? `, ${d.ai.model}` : ""}). Review it like any other candidate.` }, "AI draft") : null,
      d.ai && d.ai.basis === "inferred" ? h("span", { class: "badge cat", title: "An implementation suggestion; the source does not state it" }, "inferred") : null,
      d.ticket_key ? h("a", { class: "link", href: `#/p/${pid}/board?task=${d.task_id}` }, `Open ${d.ticket_key} on the board`) : null,
      h("span", { class: "grow" }), more);
  }

  function headMenu(d) {
    const items = [];
    if (d.kind === "task") items.push({ label: "Move or change level…", onSelect: () => moveDialog(d) });
    items.push({ label: d.kind === "task" ? "Treat as a note (not a task)" : "Treat as a task", onSelect: () => patch({ kind: d.kind === "task" ? "note" : "task" }) });
    items.push({ separator: true }, { label: "Dismiss", danger: true, onSelect: () => act(() => api.post("/api/candidates/dismiss", { ids: [d.id] }), { message: "Dismissed. It will not come back when you generate again." }) });
    return items;
  }

  function titleBlock(d, locked) {
    const out = [];
    if (d.kind === "task" || d.kind === "note" || d.kind === "asset") {
      const title = h("textarea", { class: "title-input", rows: "1", maxlength: "300", "aria-label": "Title", disabled: locked });
      title.value = d.title;
      const fit = () => { title.style.height = "auto"; title.style.height = `${title.scrollHeight}px`; };
      title.addEventListener("input", fit);
      title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } });
      title.addEventListener("blur", () => { if (title.value.trim() && title.value.trim() !== d.title) patch({ title: title.value }); });
      requestAnimationFrame(fit);
      out.push(title);
      if (d.title_origin === "user" && d.origin !== "ai") {
        const label = d.sources.find((s) => s.role === "label");
        if (label) out.push(h("div", { class: "muted small-note" }, "Source wording: ", h("q", null, label.text)));
      }
    } else {
      out.push(h("blockquote", { class: "rv-quote" }, d.original_text || d.title));
    }
    return out;
  }

  const section = (title, open, ...kids) => h("details", { class: "sec", open }, h("summary", null, title), ...kids);
  // an expander remembers whether the person opened it, per candidate, across repaints
  const expander = (d, name, title, ...kids) => {
    const key = `${d.id}:${name}`;
    const el = h("details", { class: "sec", open: R.openSecs.has(key), "data-sec": name }, h("summary", null, title), ...kids);
    el.addEventListener("toggle", () => { if (el.open) R.openSecs.add(key); else R.openSecs.delete(key); });
    return el;
  };
  const openSection = (d, name) => { R.openSecs.add(`${d.id}:${name}`); paintDetail(); };

  function taskSections(d, locked) {
    const out = [];
    out.push(badgeRow(d));
    const cp = clarifications(d, locked);
    if (cp) out.push(cp);
    out.push(descriptionBlock(d, locked));
    out.push(primaryBlock(d, locked));
    out.push(subtasksExpander(d));
    out.push(detailsExpander(d, locked));
    out.push(referencesExpander(d, locked));
    out.push(sourceExpander(d));
    return out;
  }

  // ---- the compact first view: classification as badges only
  function badgeRow(d) {
    const p = d.priority;
    const out = [];
    for (const x of d.disciplines) out.push(h("span", { class: `badge ${x}` }, DISC[x]));
    if (d.category !== "unassigned") out.push(h("span", { class: "badge cat" }, labelOf("categories", d.category)));
    if (d.impact !== "unassessed") out.push(h("span", { class: "badge cat" }, `Impact: ${labelOf("impacts", d.impact)}`));
    if (p.effective) out.push(h("span", { class: `badge ${p.effective}`, title: p.reason }, p.effective.toUpperCase()));
    if (!out.length) out.push(h("span", { class: "muted small-note" }, "Not classified yet"));
    const pending = d.suggestions.filter((s) => s.state === "suggested" || s.state === "conflicting").length + d.ai_proposals.filter((x) => x.state === "suggested" && x.field !== "primary_reference").length;
    return h("div", { class: "tf-badges", "data-badges": "" }, ...out,
      pending ? h("button", { class: "link", type: "button", onclick: () => openSection(d, "details") }, `${plural(pending, "suggestion")} to review`) : null);
  }

  // ---- questions that must stay visible
  function clarifications(d, locked) {
    return clarificationPanel(d.clarifications, {
      ownTitle: d.title, canAdd: d.state !== "merged",
      onAdd: () => addQuestion(d),
      onResolve: (c) => resolveQuestion(c),
      onReopen: (c) => act(() => api.patch(`/api/clarifications/${c.id}`, { state: "open" })),
      onDelete: (c) => act(() => api.del(`/api/clarifications/${c.id}`)),
      onShow: (b) => showInSource({ sequence: b.sequence }),
    });
  }

  async function addQuestion(d) {
    const lines = [...d.sources.filter((s) => s.role === "label" || s.role === "evidence"), ...d.children.flatMap(() => [])];
    const kids = await Promise.all(d.children.filter((c) => c.kind !== "task").slice(0, 12).map((c) => api.get(`/api/candidates/${c.id}`).catch(() => null)));
    const candidates = [...lines.map((s) => ({ id: s.block_id, text: s.text })), ...kids.filter(Boolean).flatMap((k) => k.sources.filter((s) => s.role === "evidence").map((s) => ({ id: s.block_id, text: s.text })))];
    const uniq = [...new Map(candidates.map((c) => [c.id, c])).values()];
    let done = false;
    await formDialog({
      title: "Flag a question", submitLabel: "Add question", wide: true,
      body: () => [
        h("p", { class: "muted" }, "Use this when the source contradicts itself or leaves something open. The question stays on the task, in view, until someone resolves it. It does not change what the source says."),
        fieldLabel("Question", h("textarea", { name: "text", rows: "3", maxlength: "1000", placeholder: "For example: which log is selected when these two lines disagree?" })),
        uniq.length ? h("div", { class: "field" }, h("span", { class: "label" }, "Lines it is about (optional)"), h("ul", { class: "list-plain" }, uniq.map((c) => h("li", null,
          h("label", { class: "checks" }, h("input", { type: "checkbox", name: "blk", value: c.id }), " ", c.text.length > 200 ? `${c.text.slice(0, 200)}…` : c.text))))) : null,
      ],
      onSubmit: async (dlg) => {
        const text = dlg.dialog.querySelector('[name="text"]').value.trim();
        if (!text) throw new Error("Write the question first.");
        await api.post(`/api/candidates/${d.id}/clarifications`, { text, source_block_ids: [...dlg.dialog.querySelectorAll('input[name="blk"]:checked')].map((i) => i.value) });
        done = true;
      },
    });
    if (done) { toast("Question added. It stays on the task until it is resolved."); await refresh(); }
  }

  async function resolveQuestion(c) {
    let done = false;
    await formDialog({
      title: "Mark resolved", submitLabel: "Mark resolved",
      body: () => [h("p", null, c.text), fieldLabel("What was decided? (optional)", h("textarea", { name: "resolution", rows: "3", maxlength: "1000" }))],
      onSubmit: async (dlg) => { await api.patch(`/api/clarifications/${c.id}`, { state: "resolved", resolution: dlg.dialog.querySelector('[name="resolution"]').value }); done = true; },
    });
    if (done) await refresh();
  }

  // ---- the short description
  function descriptionBlock(d, locked) {
    const desc = h("textarea", { rows: "3", name: "description", placeholder: "A sentence or two on what to build. The original wording stays linked below.", "aria-label": "Description", disabled: locked });
    desc.value = d.description;
    desc.addEventListener("blur", () => { if (desc.value !== d.description) patch({ description: desc.value }); });
    const lines = d.children.filter((c) => c.kind === "requirement" || c.kind === "exclusion");
    const aiOn = true;
    const versions = d.description_versions;
    return h("div", { class: "tf-desc" },
      fieldLabel("Description", desc, d.description_origin === "ai" && d.description ? h("span", { class: "origin ai", "data-ai-desc": "" }, "AI draft: check it against the source, then edit freely") : h("span", { class: "origin" }, "yours; source text is kept separately")),
      locked ? null : h("div", { class: "actions-row" },
        !d.description && lines.length ? h("button", { class: "btn small", type: "button", onclick: () => startFromLines(d, lines, desc) }, "Start from source lines…") : null,
        aiOn ? h("button", { class: "btn small", type: "button", "data-ai-draft": "", onclick: () => startAi([d.id], "description") }, icon("edit"), "Draft with AI…") : null),
      versions.length ? h("details", { class: "tf-versions" }, h("summary", { class: "muted" }, `Earlier versions (${versions.length})`),
        h("ul", { class: "list-plain" }, versions.map((v) => h("li", null, h("div", { class: "ev-text" }, v.description.length > 220 ? `${v.description.slice(0, 220)}…` : v.description),
          h("div", { class: "muted small-note" }, `${v.note || (v.origin === "ai" ? "AI draft" : "Yours")} · ${new Date(v.created_at).toLocaleString()}`),
          locked ? null : h("button", { class: "btn small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/descriptions/${v.id}/restore`), { message: "Earlier version restored" }) }, "Restore")))))
        : null);
  }

  async function startFromLines(d, lines, desc) {
    let text = null;
    await formDialog({
      title: "Start from the source lines", submitLabel: "Put in description", wide: true,
      body: () => [
        h("p", { class: "muted" }, "These are the source's own words. Pick the ones that say what to build, then edit them into a short description. Nothing is rewritten for you."),
        h("ul", { class: "list-plain" }, lines.map((l) => h("li", null, h("label", { class: "checks" }, h("input", { type: "checkbox", name: "line", value: l.original_text || l.title }), " ", l.original_text || l.title)))),
      ],
      onSubmit: async (dlg) => {
        const picked = [...dlg.dialog.querySelectorAll('input[name="line"]:checked')].map((i) => i.value);
        if (!picked.length) throw new Error("Tick at least one line.");
        text = picked.join("\n");
      },
    });
    if (text) { desc.value = text; await patch({ description: text }); }
  }

  async function startAi(ids, purpose) {
    openAiPreview({ pid, docId: R.docId, candidateIds: ids, purpose, onStarted: () => paint() });
  }

  // ---- one main reference clip, chosen by a person
  function primaryBlock(d, locked) {
    const base = `/api/candidates/${d.id}`;
    const wrap = h("div", { class: "tf-ref", "data-ref-block": "" });
    if (d.primary) {
      const ref = d.primary;
      wrap.append(primaryCard(ref, {
        locked,
        onRename: locked ? null : () => renameRef(ref, (caption) => api.patch(`${base}/references/${ref.reference_id}`, { caption })),
        onRange: locked ? null : () => openRangeDialog(ref, async (body) => { await api.patch(`${base}/references/${ref.reference_id}`, body); await refresh(); }),
        onChange: locked ? null : () => openPicker(d),
        onClear: locked ? null : () => act(() => api.del(`${base}/primary-reference`), { message: "Main clip cleared. The file stays attached as supporting media." }),
        onPreview: () => openAsset(ref.asset.id),
      }));
      return wrap;
    }
    if (d.reference_decision === "none") {
      wrap.append(h("div", { class: "tf-noref" }, h("h4", null, "Main reference"), h("p", { class: "muted" }, "No reference needed. You decided this task does not need a demonstration clip."),
        locked ? null : h("button", { class: "btn small", type: "button", onclick: () => act(() => api.post(`${base}/reference-decision`, { decision: "undecided" })) }, "Choose a reference instead")));
      return wrap;
    }
    const proposals = d.ai_proposals.filter((p) => p.field === "primary_reference" && p.state === "suggested");
    const byId = new Map(d.references.map((r) => [r.reference_id, r]));
    const aiCards = proposals.map((p) => ({ p, ref: byId.get(p.value) })).filter((x) => x.ref);
    const tops = d.references.filter((r) => r.top && !aiCards.some((a) => a.ref.reference_id === r.reference_id));
    const total = d.references.filter((r) => r.state !== "removed").length;
    wrap.append(h("h4", null, "Main reference"),
      h("p", { class: "muted small-note" }, "Choose the one clip that best shows what to build. Suggestions are guesses; nothing is chosen until you pick it."));
    if (!aiCards.length && !tops.length) wrap.append(h("p", { class: "muted" }, total ? "No strong suggestion. See more references." : "No picture or video was found near this task."));
    for (const { p, ref } of aiCards) {
      wrap.append(suggestionCard(ref, { aiSuggested: true, onPreview: () => openAsset(ref.asset.id),
        proposalActions: locked ? null : [
          h("button", { class: "btn small primary", type: "button", onclick: () => act(() => api.post(`${base}/ai-proposals/${p.id}`, { action: "accept" }), { message: "Main clip chosen" }) }, "Use as main clip"),
          h("button", { class: "btn subtle small", type: "button", onclick: () => act(() => api.post(`${base}/ai-proposals/${p.id}`, { action: "reject" })) }, "Not this")] }));
    }
    for (const ref of tops.slice(0, 3)) {
      wrap.append(suggestionCard(ref, { onPreview: () => openAsset(ref.asset.id), onUse: locked ? null : () => act(() => api.post(`${base}/primary-reference`, { reference_id: ref.reference_id }), { message: "Main clip chosen" }) }));
    }
    if (!locked) {
      wrap.append(h("div", { class: "actions-row" },
        h("button", { class: "btn small", type: "button", "data-more-refs": "", onclick: () => openPicker(d) }, total > tops.length + aiCards.length ? `More references (${total - tops.length - aiCards.length})…` : "Choose reference…"),
        h("button", { class: "btn subtle small", type: "button", onclick: () => act(() => api.post(`${base}/reference-decision`, { decision: "none" }), { message: "Recorded: no reference needed" }) }, "No reference needed")));
    }
    return wrap;
  }

  async function renameRef(ref, save) {
    let done = false;
    await formDialog({
      title: "Rename this clip", submitLabel: "Save",
      body: () => [h("p", { class: "muted" }, "A friendly name for the task view. The original file name and file are not changed."),
        fieldLabel("Name", h("input", { type: "text", name: "caption", maxlength: "200", value: ref.caption || ref.label }))],
      onSubmit: async (dlg) => { await save(dlg.dialog.querySelector('[name="caption"]').value); done = true; },
    });
    if (done) await refresh();
  }

  async function openPicker(d) {
    const base = `/api/candidates/${d.id}`;
    let cur = d;
    let tab = "task";
    let filter = "";
    let all = null;
    const list = h("div", { class: "tf-picker-list" });
    const tabs = h("div", { class: "pills", role: "group", "aria-label": "Which media" });
    const reload = async () => { cur = await api.get(base); R.detail = cur; paintDetail(); paintList(); await show(); };
    const row = (ref, inTask) => {
      const st = ref.state || ref.link_state;
      return h("div", { class: "tf-suggest", "data-ref": ref.reference_id },
        h("button", { type: "button", class: "rv-thumb", "aria-label": `Preview ${ref.label}`, onclick: () => openAsset(ref.asset.id) }, thumbFor(ref)),
        h("div", { class: "tf-suggest-main" },
          h("div", { class: "tf-suggest-title" }, h("b", null, ref.label), ref.role === "primary_demo" ? h("span", { class: "badge status done" }, "Main clip") : st === "attached" ? h("span", { class: "badge cat" }, "Attached") : st === "removed" ? h("span", { class: "badge cat" }, "Set aside") : null),
          h("div", { class: "muted small-note" }, [ref.section, ref.asset.name].filter(Boolean).join(" · ")),
          inTask ? h("div", { class: "muted small-note" }, tierText(ref)) : null,
          inTask && ref.reason ? h("div", { class: "muted small-note tf-reason" }, ref.reason) : null),
        h("div", { class: "actions-row" },
          ref.role === "primary_demo" ? null : h("button", { class: "btn small primary", type: "button", onclick: async () => { try { await api.post(`${base}/primary-reference`, { reference_id: ref.reference_id }); toast("Main clip chosen"); await reload(); } catch (e) { toast(e.message, { kind: "error" }); } } }, "Use as main clip"),
          st !== "attached" ? h("button", { class: "btn small", type: "button", onclick: async () => { try { await api.post(`${base}/references`, { reference_id: ref.reference_id, state: "attached" }); await reload(); } catch (e) { toast(e.message, { kind: "error" }); } } }, "Attach as supporting") : null,
          inTask && st === "suggested" ? h("button", { class: "btn subtle small", type: "button", onclick: async () => { await api.post(`${base}/references`, { reference_id: ref.reference_id, state: "removed" }); await reload(); } }, "Not this") : null));
    };
    async function show() {
      replace(tabs, [["task", `Suggested for this task (${cur.references.filter((r) => r.state !== "removed").length})`], ["all", "All media in the document"]].map(([k, l]) =>
        h("button", { type: "button", class: "pill", "aria-pressed": String(tab === k), onclick: () => { tab = k; show(); } }, l)));
      if (tab === "task") {
        const refs = cur.references.filter((r) => r.state !== "removed");
        replace(list, refs.length ? refs.map((r) => row(r, true)) : h("p", { class: "muted" }, "Nothing was suggested for this task. Use “All media in the document”."));
      } else {
        replace(list, h("p", { class: "muted", role: "status" }, "Loading…"));
        try { all = await api.get(`${base}/reference-options${filter ? `?q=${encodeURIComponent(filter)}` : ""}`); } catch (e) { return replace(list, errBanner(e.message)); }
        replace(list, h("input", { type: "search", name: "filter", placeholder: "Filter by name or section", value: filter, "aria-label": "Filter media", onchange: (e) => { filter = e.target.value; show(); } }),
          all.references.length ? all.references.map((r) => row(r, false)) : h("p", { class: "muted" }, "No matching pictures or videos."),
          all.total > all.references.length ? h("p", { class: "muted small-note" }, `Showing ${all.references.length} of ${all.total}. Filter to narrow it down.`) : null);
      }
    }
    await formDialog({
      title: "Choose the main reference", submitLabel: "Done", cancelLabel: "Close", wide: true,
      body: () => { setTimeout(show, 0); return [h("p", { class: "muted" }, "Pick one clip as the main demonstration. Supporting media stays available under References. Choosing never deletes a file."), tabs, list]; },
      onSubmit: async () => true,
    });
    await refresh();
  }

  // ---- expanders: only when asked for
  function subtasksExpander(d) {
    const subs = d.children.filter((c) => c.kind === "task");
    if (!subs.length) return h("span");
    return expander(d, "subtasks", `Subtasks (${subs.length})`, h("ul", { class: "list-plain" }, subs.map((s) => h("li", null,
      h("button", { class: "link", type: "button", onclick: () => select(s.id) }, s.title), " ", ...badges(s)))));
  }

  function detailsExpander(d, locked) {
    const out = [classification(d, locked), priorityBox(d, locked), linesSection(d, locked), attachmentOffers(d, locked)];
    const scen = d.scenarios_all || [];
    if (scen.length) {
      out.push(section(`Scenarios named by its lines (${scen.length})`, false, h("div", { class: "chips-left" }, scen.map((s) => scenChip(s.identifier, s.resolved))),
        h("p", { class: "muted small-note" }, "Taken from “Scenario …” lines in the source. They become scenario links when the lines are approved.")));
    }
    const pending = d.suggestions.filter((s) => s.state === "suggested" || s.state === "conflicting").length;
    return expander(d, "details", `Details: classification, requirements, scenarios${pending ? ` · ${plural(pending, "suggestion")}` : ""}`, ...out);
  }

  function referencesExpander(d, locked) {
    const base = `/api/candidates/${d.id}`;
    const att = d.references.filter((r) => r.state === "attached" && r.role !== "primary_demo");
    const sug = d.references.filter((r) => r.state === "suggested").length;
    const rows = att.map((r) => h("li", { class: "tf-ref-row", "data-ref": r.reference_id },
      h("button", { type: "button", class: "rv-thumb", "aria-label": `Preview ${r.label}`, onclick: () => openAsset(r.asset.id) }, thumbFor(r)),
      h("div", { class: "tf-suggest-main" }, h("b", null, r.label), h("div", { class: "muted small-note" }, [r.section, r.asset.name].filter(Boolean).join(" · "))),
      locked ? h("span", { class: "badge cat" }, roleLabel(r.role)) : h("div", { class: "actions-row" },
        h("select", { "aria-label": `Role of ${r.label}`, onchange: (e) => act(() => api.patch(`${base}/references/${r.reference_id}`, { role: e.target.value })) },
          ROLE_OPTIONS.map(([v, l]) => h("option", { value: v, selected: r.role === v }, l))),
        h("button", { class: "btn small", type: "button", onclick: () => act(() => api.post(`${base}/primary-reference`, { reference_id: r.reference_id }), { message: "Main clip chosen" }) }, "Make main clip"),
        h("button", { class: "btn small", type: "button", onclick: () => renameRef(r, (caption) => api.patch(`${base}/references/${r.reference_id}`, { caption })) }, "Rename"),
        h("button", { class: "btn subtle small", type: "button", onclick: () => act(() => api.post(`${base}/references`, { reference_id: r.reference_id, state: "removed" })) }, "Remove"))));
    return expander(d, "references", `Supporting references (${att.length})`,
      h("p", { class: "muted small-note" }, "Only attached media goes onto the ticket. A picture of a model supports art work without being the demonstration of the behaviour."),
      att.length ? h("ul", { class: "list-plain" }, rows) : h("p", { class: "muted" }, "None attached."),
      !locked ? h("div", { class: "actions-row" },
        h("button", { class: "btn small", type: "button", onclick: () => openPicker(d) }, `Browse suggestions${sug ? ` (${sug})` : ""}…`)) : null);
  }

  function sourceExpander(d) {
    const own = d.sources.filter((s) => s.role === "label" || s.role === "evidence" || s.role === "context").slice(0, 8);
    const lineCount = d.children.filter((c) => c.kind !== "task").length;
    return expander(d, "source", "Original source",
      h("p", { class: "muted small-note" }, d.heading_path.join(" › ")),
      own.length ? h("ul", { class: "list-plain" }, own.map((s) => h("li", null, h("blockquote", { class: "rv-quote" }, s.text || "(empty)")))) : null,
      lineCount ? h("p", { class: "muted small-note" }, `${plural(lineCount, "requirement line")} are listed under Details.`) : null,
      h("div", { class: "actions-row" }, h("button", { class: "btn small", type: "button", "data-compare": "", onclick: () => setCompare(true) }, "Compare source beside this task")));
  }

  // ---- move or change level: parent and level always travel together (M3 REV 002)
  async function moveDialog(d) {
    const all = R.listing.candidates;
    const kids = all.filter((c) => c.parent_id === d.id && c.kind === "task" && c.state !== "merged");
    const minChild = kids.length ? Math.min(...kids.map((k) => RANK[k.level || "task"])) : 99;
    const parents = all.filter((c) => c.kind === "task" && c.state === "needs_review" && c.id !== d.id && !descends(c.id, d.id));
    const options = [{ v: "task|", label: "A top-level task", level: "task", parent: null }, { v: "epic|", label: "An epic (top level)", level: "epic", parent: null }];
    for (const p of parents) {
      for (const level of ["task", "subtask"]) {
        if (RANK[p.level || "task"] < RANK[level] && RANK[level] < minChild) {
          options.push({ v: `${level}|${p.id}`, label: level === "subtask" ? `A subtask of “${p.title}”` : `A task under the epic “${p.title}”`, level, parent: p.id });
        }
      }
    }
    const here = `${d.level || "task"}|${d.parent_id || ""}`;
    let done = false;
    await formDialog({
      title: `Move “${d.title.length > 60 ? `${d.title.slice(0, 60)}…` : d.title}”`, submitLabel: "Move", wide: true,
      body: () => [
        h("p", { class: "muted" }, "Level and parent change together, so there is never an invalid in-between state. If the move is refused, nothing changes."),
        kids.length ? h("p", { class: "banner info small", role: "note" }, icon("alert"), `It has ${plural(kids.length, "subtask")}, so it can only be a task or an epic. Move the subtasks first to make it a subtask.`) : null,
        fieldLabel("Make it", h("select", { name: "place" }, options.map((o) => h("option", { value: o.v, selected: o.v === here }, o.label)))),
      ],
      onSubmit: async (dlg) => {
        const o = options.find((x) => x.v === dlg.dialog.querySelector('[name="place"]').value);
        await api.patch(`/api/candidates/${d.id}`, { level: o.level, parent_id: o.parent });
        done = true;
      },
    });
    if (done) { toast("Moved"); await refresh(); }
  }

  function descends(id, ancestor) {
    const by = new Map(R.listing.candidates.map((c) => [c.id, c]));
    let cur = by.get(id);
    while (cur) { if (cur.id === ancestor) return true; cur = by.get(cur.parent_id); }
    return false;
  }

  function scenChip(identifier, resolved, onRemove) {
    return h("span", { class: `chip ${resolved ? "" : "warn"}`, title: resolved ? `Scenario ${identifier}` : `Scenario ${identifier} is not defined in the document's scenario table` },
      resolved ? "" : "⚠ ", identifier, onRemove ? h("button", { type: "button", class: "chip-x", "aria-label": `Remove scenario ${identifier}`, onclick: onRemove }, "×") : null);
  }

  // classification with evidence
  function sugList(d, field, locked) {
    const items = d.suggestions.filter((s) => s.field === field);
    if (!items.length) return null;
    const active = items.filter((s) => ["suggested", "conflicting", "accepted", "rejected"].includes(s.state));
    const dim = items.filter((s) => s.state === "superseded");
    const line = (s) => h("li", { class: `sug ${s.state}` },
      h("span", { class: `sug-state ${s.state}` }, SUG_STATE[s.state]),
      h("span", { class: "sug-body" }, h("b", null, valueLabel(field, s.value)), " from ",
        h("span", { class: "sug-rule", title: `Rule version ${s.rule_version}` }, `${s.rule_name} (${s.rule_scope === "project" ? "project rule" : "global rule"})`),
        h("span", { class: "muted" }, ` · matched “${s.matched_text.length > 90 ? s.matched_text.slice(0, 90) + "…" : s.matched_text}” in ${s.match_in === "heading_path" ? "heading" : s.match_in}`),
        s.source_block_id ? h("button", { class: "link", type: "button", onclick: () => jumpTo(s.source_block_id) }, " show") : null),
      !locked && s.state !== "superseded" && s.state !== "accepted" ? h("span", { class: "sug-actions" },
        s.state !== "rejected" ? h("button", { class: "btn small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/suggestions/${s.id}`, { action: "accept" })) }, "Accept") : null,
        s.state !== "rejected" ? h("button", { class: "btn subtle small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/suggestions/${s.id}`, { action: "reject" })) }, "Reject")
          : h("button", { class: "btn subtle small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/suggestions/${s.id}`, { action: "restore" })) }, "Restore")) : null);
    return h("div", { class: "sug-box" }, h("ul", { class: "list-plain" }, active.map(line)),
      dim.length ? h("details", { class: "sug-dim" }, h("summary", null, `${dim.length} not used (a stronger rule or an existing value wins)`), h("ul", { class: "list-plain" }, dim.map(line))) : null);
  }

  function jumpTo(blockId) {
    const sq = (R.detail.sources.find((x) => x.block_id === blockId) || {}).sequence;
    if (sq !== undefined) showInSource({ sequence: sq });
  }

  function aiList(d, field, locked) {
    const items = d.ai_proposals.filter((p) => p.field === field);
    if (!items.length) return null;
    return h("div", { class: "sug-box ai-box", "data-ai-proposals": field }, h("ul", { class: "list-plain" }, items.map((p) => h("li", { class: `sug ${p.state === "suggested" ? "suggested" : p.state}` },
      h("span", { class: `sug-state ${p.state === "suggested" ? "suggested" : p.state}` }, p.state === "suggested" ? "AI proposal" : p.state === "accepted" ? "Accepted" : "Rejected"),
      h("span", { class: "sug-body" }, h("b", null, valueLabel(field, p.value)), " proposed by AI", p.reason ? h("span", { class: "muted" }, ` · ${p.reason}`) : null),
      !locked && p.state === "suggested" ? h("span", { class: "sug-actions" },
        h("button", { class: "btn small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/ai-proposals/${p.id}`, { action: "accept" })) }, "Accept"),
        h("button", { class: "btn subtle small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/ai-proposals/${p.id}`, { action: "reject" })) }, "Reject")) : null,
      !locked && p.state === "rejected" ? h("button", { class: "btn subtle small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/ai-proposals/${p.id}`, { action: "restore" })) }, "Restore") : null))));
  }

  function classification(d, locked) {
    const pending = d.suggestions.filter((s) => s.state === "suggested").length;
    const discChecks = ["art", "dev"].map((v) => h("label", null, h("input", { type: "checkbox", name: v, checked: d.disciplines.includes(v), disabled: locked,
      onchange: () => {
        const cur = new Set(d.disciplines);
        if (cur.has(v)) cur.delete(v); else cur.add(v);
        patch({ disciplines: [...cur] });
      } }), " ", DISC[v]));
    const selectFor = (field, list, current, unsetValue) => h("select", { name: field, "aria-label": FIELD_NAME[field], disabled: locked, onchange: (e) => patch({ [field]: e.target.value }) },
      state.meta[list].map((o) => h("option", { value: o.value, selected: o.value === current }, o.label)));
    const origin = (set) => h("span", { class: `origin ${set ? "manual" : ""}` }, set ? "reviewed" : "not set");
    return section("Classification", true,
      h("p", { class: "muted small-note" }, "Values you set are reviewed values. Rule suggestions are only proposals until you accept them."),
      !locked && pending ? h("div", null, h("button", { class: "btn small", type: "button", onclick: () => act(() => api.post(`/api/candidates/${d.id}/accept-suggestions`)) }, `Accept all ${pending} suggestion${pending === 1 ? "" : "s"}`)) : null,
      h("div", { class: "field" }, h("span", { class: "label" }, "Disciplines ", origin(d.disciplines.length), h("span", { class: "origin" }, " none = unassigned")), h("div", { class: "checks" }, ...discChecks), sugList(d, "discipline", locked), aiList(d, "discipline", locked)),
      h("div", { class: "field" }, h("span", { class: "label" }, "Category ", origin(d.category !== "unassigned")), selectFor("category", "categories", d.category), sugList(d, "category", locked), aiList(d, "category", locked)),
      h("div", { class: "props" },
        h("div", { class: "field" }, h("span", { class: "label" }, "Impact ", origin(d.impact !== "unassessed")), selectFor("impact", "impacts", d.impact), sugList(d, "impact", locked), aiList(d, "impact", locked)),
        h("div", { class: "field" }, h("span", { class: "label" }, "Urgency ", origin(d.urgency !== "unspecified")), selectFor("urgency", "urgencies", d.urgency), sugList(d, "urgency", locked))));
  }

  function priorityBox(d, locked) {
    const p = d.priority;
    const label = p.effective ? p.effective.toUpperCase() : "Not assessed";
    const ovr = h("select", { name: "priority_override", "aria-label": "Override priority", disabled: locked, onchange: (e) => patch({ priority_override: e.target.value || null }) },
      h("option", { value: "" }, "No override"), state.meta.priorities.map((o) => h("option", { value: o.value, selected: o.value === p.override }, o.value === "p0" ? "P0 (explicit only)" : o.label)));
    const reason = h("input", { type: "text", name: "priority_override_reason", maxlength: "1000", placeholder: "Why override?", "aria-label": "Override reason", disabled: locked || !p.override });
    reason.value = d.priority.override_reason || "";
    reason.addEventListener("blur", () => { if (reason.value !== (p.override_reason || "")) patch({ priority_override_reason: reason.value }); });
    return section("Priority", true, h("div", { class: "prio-box" },
      h("div", { class: "now" }, h("span", { class: `badge ${p.effective || "na"}` }, label), h("span", { class: "muted" }, p.source === "overridden" ? "overridden by you" : p.source === "suggested" ? "suggested by the default policy" : "no impact set")),
      h("div", { class: "why" }, p.reason, p.source === "overridden" ? ` Your override replaces the suggestion (${p.suggested ? p.suggested.toUpperCase() : "none"}).` : "", " P0 is never suggested."),
      h("div", { class: "props" }, fieldLabel("Override", ovr), fieldLabel("Reason", reason))));
  }

  // requirement lines under a task
  function linesSection(d, locked) {
    const lines = d.children.filter((c) => c.kind === "requirement" || c.kind === "exclusion");
    const moveable = d.children.filter((c) => c.kind === "task").length;
    if (!lines.length) return section("Requirement lines (0)", false, h("p", { class: "muted" }, "No lines were found under this task."));
    return section(`Requirement lines (${lines.length})`, true,
      h("p", { class: "muted small-note" }, "Each line keeps its original wording. Lines are not tasks; promote one to a subtask if it needs its own ticket."),
      h("ul", { class: "list-plain rv-lines" }, lines.map((c) => {
        const menu = locked ? null : h("button", { class: "icon-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": `Actions for line: ${c.title.slice(0, 40)}`,
          onclick: (e) => openMenu(e.currentTarget, lineMenu(d, c), { align: "right" }) }, icon("more"));
        return h("li", { class: `rv-line-item ${c.kind} ${c.state}` },
          h("div", { class: "rv-line-text" }, c.kind === "exclusion" ? h("span", { class: "badge blocked" }, "Exclusion") : null, " ", c.original_text || c.title),
          h("div", { class: "rv-line-meta" },
            ...c.values.map((v) => h("span", { class: "chip val", title: "Quantity as written" }, v)),
            ...c.scenarios.map((s) => scenChip(s.identifier, s.resolved, locked ? null : () => act(() => api.del(`/api/candidates/${c.id}/scenarios/${encodeURIComponent(s.identifier)}`)))),
            c.state === "dismissed" ? h("span", { class: "muted" }, "dismissed") : null),
          menu);
      })),
      !locked && lines.length > 1 ? h("div", { class: "actions-row" }, h("button", { class: "btn small", type: "button", onclick: () => splitFlow(d) }, "Split this task…")) : null);
  }

  function lineMenu(d, c) {
    return [
      { label: "Show in source", onSelect: () => { const s = (R.detail.children.find((x) => x.id === c.id) || {}); api.get(`/api/candidates/${c.id}`).then((cd) => { const e = cd.sources.find((x) => x.role === "evidence"); if (e) showInSource({ sequence: e.sequence }); }); } },
      { separator: true },
      { label: "Make it a subtask…", onSelect: () => splitFlow(d, [c.id], "subtask") },
      { label: "Move to another task…", onSelect: () => moveLine(c) },
      { label: c.kind === "exclusion" ? "Treat as a requirement" : "Treat as an exclusion (negative instruction)", onSelect: () => act(() => api.patch(`/api/candidates/${c.id}`, { kind: c.kind === "exclusion" ? "requirement" : "exclusion" })) },
      { separator: true },
      { label: "Dismiss this line", danger: true, onSelect: () => act(() => api.post("/api/candidates/dismiss", { ids: [c.id] }), { message: "Line dismissed" }) },
    ];
  }

  async function moveLine(c) {
    const tasks = R.listing.candidates.filter((x) => x.kind === "task" && x.state === "needs_review");
    await formDialog({
      title: "Move this line", submitLabel: "Move",
      body: () => [h("p", { class: "muted" }, c.original_text || c.title), fieldLabel("Move to", h("select", { name: "to" }, h("option", { value: "" }, "(no task: keep it loose)"), tasks.map((t) => h("option", { value: t.id, selected: t.id === c.parent_id }, t.title))))],
      onSubmit: async (dlg) => { await api.patch(`/api/candidates/${c.id}`, { parent_id: dlg.dialog.querySelector('[name="to"]').value || null }); await refresh(); },
    });
  }

  // loose requirements the rules/words suggest belong here
  function attachmentOffers(d, locked) {
    const offers = R.listing.candidates.filter((c) => c.suggested_parent_id === d.id && !c.parent_id && c.state === "needs_review");
    const attached = d.children.filter((c) => c.origin === "generated" && c.kind !== "task" && false);
    if (!offers.length) return h("span");
    return section(`Possible values and requirements from elsewhere (${offers.length})`, true,
      h("p", { class: "muted small-note" }, "These lines come from other sections. They are offered because they share wording with this task. Attach only the ones that really apply."),
      h("ul", { class: "list-plain" }, offers.map((c) => h("li", { class: "rv-offer" },
        h("div", null, h("b", null, c.title), c.values.length ? h("span", null, " ", ...c.values.map((v) => h("span", { class: "chip val" }, v))) : null,
          h("div", { class: "muted small-note" }, c.suggestion_reason, " · ", c.heading_path[c.heading_path.length - 1] || ""),
          c.original_text && c.original_text !== c.title ? h("pre", { class: "rv-pre" }, c.original_text) : null),
        locked ? null : h("div", { class: "actions-row" },
          h("button", { class: "btn small", type: "button", onclick: () => act(() => api.patch(`/api/candidates/${c.id}`, { parent_id: d.id }), { message: `Attached “${c.title}”` }) }, "Attach here"),
          h("button", { class: "btn subtle small", type: "button", onclick: () => select(c.id) }, "Open"))))));
  }

  function mediaSection(d) {
    const refs = d.references.filter((r) => r.state !== "removed");
    if (!refs.length) return section("Media (0)", false, h("p", { class: "muted" }, "No pictures or videos are near this candidate."));
    return section(`Media (${refs.length})`, false, h("div", { class: "rv-media-grid" }, refs.map((r) => h("div", { class: "rv-media" },
      h("button", { type: "button", class: "rv-thumb", "aria-label": `Preview ${r.label}`, onclick: () => openAsset(r.asset.id) }, thumbFor(r)), h("span", { class: "muted" }, r.label)))));
  }

  function lineSections(d, locked) {
    const out = [];
    if (d.values.length) out.push(h("div", { class: "field" }, h("span", { class: "label" }, "Values, exactly as written"), h("div", { class: "chips-left" }, d.values.map((v) => h("span", { class: "chip val" }, v)))));
    if (d.scenario_links.length) out.push(h("div", { class: "field" }, h("span", { class: "label" }, "Applies to scenarios"), h("div", { class: "chips-left" }, d.scenario_links.map((s) => scenChip(s.identifier, s.resolved, locked ? null : () => act(() => api.del(`/api/candidates/${d.id}/scenarios/${encodeURIComponent(s.identifier)}`)))))));
    if (!locked) {
      const tasks = R.listing.candidates.filter((c) => c.kind === "task" && c.state === "needs_review");
      const sel = h("select", { name: "parent", "aria-label": "Belongs to task", onchange: (e) => patch({ parent_id: e.target.value || null }) },
        h("option", { value: "" }, "(none: keep as a shared requirement)"), tasks.map((t) => h("option", { value: t.id, selected: t.id === d.parent_id }, t.title)));
      out.push(h("div", { class: "field" }, h("label", { for: (sel.id = "rv-owner") }, "Belongs to task"), sel,
        d.suggested_parent_id && !d.parent_id ? h("div", { class: "hint" }, `Suggested: “${d.suggested_parent_title}”. ${d.suggestion_reason}. `,
          h("button", { class: "link", type: "button", onclick: () => patch({ parent_id: d.suggested_parent_id }, "Attached") }, "Attach it")) : null));
      out.push(h("div", { class: "field" }, h("span", { class: "label" }, "Type"), h("div", { class: "actions-row" },
        h("button", { class: "btn small", type: "button", onclick: () => patch({ kind: d.kind === "exclusion" ? "requirement" : "exclusion" }) }, d.kind === "exclusion" ? "Treat as a requirement" : "Treat as an exclusion"))));
    }
    return out;
  }

  function otherSections(d, locked) {
    const out = [];
    if (d.kind === "note") {
      out.push(h("p", { class: "muted" }, "Explanatory text. It is kept for reference and is not turned into a ticket."));
      out.push(h("blockquote", { class: "rv-quote" }, d.original_text || "(no text)"));
      if (!locked) out.push(h("div", { class: "actions-row" }, h("button", { class: "btn small", type: "button", onclick: () => patch({ kind: "task" }, "Now a task candidate") }, "Treat as a task")));
    } else {
      out.push(h("p", { class: "muted" }, "A name for a set of pictures. Its media is offered to tasks that talk about the same thing; it is not a task itself."));
    }
    if (d.references.length) out.push(mediaSection(d));
    return out;
  }

  function actionsBar(d) {
    const approvable = d.state === "needs_review" && d.kind !== "note" && d.kind !== "asset";
    return h("div", { class: "rv-actions" },
      approvable ? h("button", { class: "btn primary", type: "button", onclick: () => approveFlow([d.id]) }, icon("check"), d.kind === "task" ? "Approve onto the board…" : d.parent_id ? "Approve this line now…" : "Approve as shared requirement…") : null,
      d.state === "needs_review" ? h("button", { class: "btn", type: "button", onclick: () => act(() => api.post("/api/candidates/dismiss", { ids: [d.id] }), { message: "Dismissed. It will not come back when you generate again." }) }, "Dismiss") : null,
      d.state === "dismissed" ? h("button", { class: "btn", type: "button", onclick: () => act(() => api.post("/api/candidates/restore", { ids: [d.id] }), { message: "Restored" }) }, "Restore") : null,
      d.state === "approved" && d.ticket_key ? h("a", { class: "btn", href: `#/p/${pid}/board?task=${d.task_id}` }, `Open ${d.ticket_key}`) : null);
  }

  // ---- flows --------------------------------------------------------------------
  async function openGenerate() {
    const d = await api.get(`/api/documents/${R.docId}`);
    const secs = d.sections.filter((s) => s.role !== "metadata");
    let result = null;
    await formDialog({
      title: "Generate candidates", submitLabel: "Generate", wide: true,
      body: () => [
        h("p", null, "Choose the sections to read. Current sections are selected; historical ones are left out unless you tick them. Generating again only adds what is new: your edits, approvals and dismissals are kept."),
        h("div", { class: "gen-sections" }, secs.map((s) => h("label", { class: "gen-sec" }, h("input", { type: "checkbox", name: "sec", value: s.id, checked: s.role === "current" }),
          h("span", { class: "gen-title" }, s.title || "Before the first heading"),
          s.role === "historical" ? h("span", { class: "badge hist-badge" }, icon("history"), "Historical") : null))),
        h("p", { class: "muted small-note" }, "Candidates are proposals. Nothing reaches the board until you approve it."),
      ],
      onSubmit: async (dlg) => {
        const ids = [...dlg.dialog.querySelectorAll('input[name="sec"]:checked')].map((i) => i.value);
        if (!ids.length) throw new Error("Choose at least one section.");
        result = await api.post(`/api/documents/${R.docId}/candidates/generate`, { section_ids: ids });
      },
    });
    if (result) {
      R.listing = result;
      R.tab = "needs_review";
      toast(result.new ? `Added ${plural(result.new, "candidate")}${result.existing ? `; ${result.existing} already existed and were left alone` : ""}.` : "Nothing new to add; existing candidates were left alone.");
      announce(`${result.new} candidates added`);
      paintList();
      paint();
    }
  }

  async function rerunRules() {
    try {
      R.listing = await api.post(`/api/documents/${R.docId}/candidates/apply-rules`);
      paintList();
      if (R.sel) await loadDetail(R.sel);
      toast("Rules re-run. Values you set or reviewed were not changed.");
    } catch (e) { toast(e.message, { kind: "error" }); }
  }

  async function approveFlow(ids) {
    const by = new Map(R.listing.candidates.map((c) => [c.id, c]));
    const picked = ids.map((i) => by.get(i)).filter(Boolean);
    const taskPicks = picked.filter((c) => c.kind === "task");
    const kidsOf = (id) => R.listing.candidates.filter((c) => c.parent_id === id && c.state === "needs_review");
    let subs = 0, lines = 0;
    const walk = (id) => { for (const k of kidsOf(id)) { if (k.kind === "task") subs += 1; else lines += 1; walk(k.id); } };
    taskPicks.forEach((t) => walk(t.id));
    const loose = picked.filter((c) => c.kind !== "task");
    const unclassified = taskPicks.filter((c) => !c.disciplines.length && c.category === "unassigned" && c.impact === "unassessed");
    const suggested = taskPicks.reduce((n, c) => n + c.pending_suggestions, 0);
    let details = [];
    try { details = await Promise.all(taskPicks.map((t) => api.get(`/api/candidates/${t.id}`))); } catch { /* the dialog still works without the extra facts */ }
    const detailOf = new Map(details.map((x) => [x.id, x]));
    const clipLine = (t) => {
      const x = detailOf.get(t.id);
      if (!x) return null;
      if (x.primary) return h("span", { class: "ok-text", "data-clip": "primary" }, `Main clip: ${x.primary.label}${x.primary.start_ms !== null ? ` (${rangeText(x.primary)})` : ""}`);
      if (x.reference_decision === "none") return h("span", { class: "muted", "data-clip": "none" }, "No reference needed");
      return h("span", { class: "muted", "data-clip": "undecided" }, "No main clip chosen yet (you can still approve and choose it on the ticket)");
    };
    const openQ = details.reduce((n, x) => n + x.clarifications.filter((c) => c.state === "open").length, 0);
    const suggestedMedia = taskPicks.reduce((n, c) => n + c.media.suggested, 0);
    const loneLines = loose.filter((c) => c.parent_id);
    const shared = loose.filter((c) => !c.parent_id);
    let withKids = true, out = null;
    await formDialog({
      title: taskPicks.length ? `Approve ${plural(taskPicks.length, "task")} onto the board` : loose.length === 1 && loose[0].parent_id ? "Approve this line now" : "Approve requirement", submitLabel: "Approve", wide: true,
      body: () => [
        h("p", null, "Approving creates board tickets in Backlog with a link back to the exact source text. This is a deliberate step; rule suggestions you did not accept are not applied."),
        taskPicks.length ? h("ul", { class: "list-plain tf-approve-list" }, taskPicks.map((t) => h("li", null, h("b", null, t.title), " ", ...badges(t).filter((b) => b.tagName !== "A"), h("div", null, clipLine(t))))) : null,
        subs ? h("label", { class: "checks" }, h("input", { type: "checkbox", name: "kids", checked: true, onchange: (e) => { withKids = e.target.checked; } }), ` Include ${plural(subs, "subtask")} under the selection`) : null,
        taskPicks.length ? h("p", { class: "muted" }, `${plural(lines, "requirement line")} will be kept as requirements with their scenarios.`) : null,
        loneLines.length ? h("div", { class: "banner info small", role: "note", "data-line-note": "" }, icon("alert"),
          h("span", null, loneLines.map((c) => `“${c.title.length > 60 ? `${c.title.slice(0, 60)}…` : c.title}” belongs to “${(by.get(c.parent_id) || {}).title || "its task"}”. `).join(""),
            "It is approved now and attached to that task's ticket as soon as the task is approved. It is not copied.")) : null,
        shared.length ? h("p", { class: "muted", "data-shared-note": "" }, `${plural(shared.length, "requirement")} will be kept as shared, belonging to no task: ${shared.map((c) => `“${c.title.length > 40 ? `${c.title.slice(0, 40)}…` : c.title}”`).join(", ")}.`) : null,
        openQ ? h("p", { class: "warn-text" }, `${plural(openQ, "open question")} stay on the ticket until someone resolves them.`) : null,
        suggested ? h("p", { class: "warn-text" }, `${plural(suggested, "rule suggestion")} on the selected tasks have not been accepted, so those fields stay unassigned.`) : null,
        unclassified.length ? h("p", { class: "muted" }, `${plural(unclassified.length, "task")} will have no discipline, category, or impact. That is allowed; leave unclear fields unassigned.`) : null,
        suggestedMedia ? h("p", { class: "muted" }, `${plural(suggestedMedia, "suggested picture")} will not be attached. Choose or attach the ones that belong first.`) : null,
      ],
      onSubmit: async () => { out = await api.post(`/api/projects/${pid}/candidates/approve`, { ids, with_children: withKids }); },
    });
    if (out) {
      R.checked.clear();
      const extra = out.awaiting_parent && out.awaiting_parent.length ? ` ${out.awaiting_parent.map((w) => `“${w.title}” will attach to “${w.parent_title}” when that task is approved.`).join(" ")}` : "";
      const added = out.attached_to_parent ? ` ${plural(out.attached_to_parent, "earlier-approved line")} attached.` : "";
      toast(`Approved ${out.tasks.map((t) => t.key).join(", ") || plural(out.requirements, "requirement")}.${added}${extra}`, { action: out.tasks.length ? { label: "Open board", run: () => go(`#/p/${pid}/board`) } : null });
      announce(`Approved ${plural(out.tasks.length, "task")}`);
      document.dispatchEvent(new CustomEvent("organizer:data"));
      await refresh();
    }
  }

  async function bulkDismiss() {
    if (!(await confirmDialog({ title: `Dismiss ${plural(R.checked.size, "candidate")}?`, message: "They move to Dismissed and are not recreated when you generate again. You can restore them.", confirmLabel: "Dismiss" }))) return;
    await act(() => api.post("/api/candidates/dismiss", { ids: [...R.checked] }), { message: "Dismissed" });
    R.checked.clear();
    paintList();
  }

  async function bulkAccept() {
    const r = await act(() => api.post("/api/candidates/accept-suggestions", { ids: [...R.checked] }));
    if (r) toast(r.accepted ? `Accepted ${plural(r.accepted, "suggestion")}.` : "No pending suggestions on the selection.");
  }

  async function bulkClassify() {
    const ids = [...R.checked];
    let done = false;
    const sel = (name, list, unset) => h("select", { name }, h("option", { value: "" }, "(no change)"), state.meta[list].filter((o) => o.value !== unset).map((o) => h("option", { value: o.value }, o.label)));
    await formDialog({
      title: `Classify ${plural(ids.length, "candidate")}`, submitLabel: "Apply",
      body: () => [
        h("p", { class: "muted" }, "Sets reviewed values on the selected tasks. Leave a field on “no change” to keep what each task has."),
        h("div", { class: "field" }, h("span", { class: "label" }, "Disciplines"), h("div", { class: "checks" },
          h("label", null, h("input", { type: "checkbox", name: "art" }), " Art"), h("label", null, h("input", { type: "checkbox", name: "dev" }), " Dev"),
          h("select", { name: "mode" }, h("option", { value: "add" }, "add to existing"), h("option", { value: "set" }, "replace existing")))),
        fieldLabel("Category", sel("category", "categories", "unassigned")), fieldLabel("Impact", sel("impact", "impacts", "unassessed")), fieldLabel("Urgency", sel("urgency", "urgencies", "unspecified")),
      ],
      onSubmit: async (dlg) => {
        const f = dlg.dialog;
        const body = { ids, disciplines_mode: f.querySelector('[name="mode"]').value };
        const disc = ["art", "dev"].filter((v) => f.querySelector(`[name="${v}"]`).checked);
        if (disc.length) body.disciplines = disc;
        for (const k of ["category", "impact", "urgency"]) { const v = f.querySelector(`[name="${k}"]`).value; if (v) body[k] = v; }
        await api.post("/api/candidates/classify", body);
        done = true;
      },
    });
    if (done) { toast("Classification applied"); await refresh(); }
  }

  async function splitFlow(d, preset = [], mode = "sibling") {
    const lines = d.children.filter((c) => c.kind === "requirement" || c.kind === "exclusion");
    let done = null;
    await formDialog({
      title: "Split into a new candidate", submitLabel: "Split", wide: true,
      body: () => [
        fieldLabel("Title of the new candidate", h("input", { type: "text", name: "title", maxlength: "300", placeholder: preset.length === 1 ? (lines.find((l) => l.id === preset[0]) || {}).title : "e.g. Configurable output values" })),
        h("div", { class: "field" }, h("span", { class: "label" }, "Move these lines into it"), h("ul", { class: "list-plain" }, lines.map((l) => h("li", null,
          h("label", { class: "checks" }, h("input", { type: "checkbox", name: "line", value: l.id, checked: preset.includes(l.id) }), " ", l.original_text || l.title))))),
        h("div", { class: "field" }, h("span", { class: "label" }, "The new candidate is"), h("div", { class: "checks" },
          h("label", null, h("input", { type: "radio", name: "mode", value: "sibling", checked: mode === "sibling", disabled: d.level === "task" && false }), " beside this one"),
          d.level === "task" ? h("label", null, h("input", { type: "radio", name: "mode", value: "subtask", checked: mode === "subtask" }), " a subtask of this one") : null)),
      ],
      onSubmit: async (dlg) => {
        const f = dlg.dialog;
        const child_ids = [...f.querySelectorAll('input[name="line"]:checked')].map((i) => i.value);
        const m = f.querySelector('input[name="mode"]:checked');
        let title = f.querySelector('[name="title"]').value.trim();
        if (!title && child_ids.length === 1) title = (lines.find((l) => l.id === child_ids[0]).title || "").slice(0, 120);
        done = await api.post(`/api/candidates/${d.id}/split`, { title, child_ids, mode: m ? m.value : "sibling" });
      },
    });
    if (done) { toast("Split. Review the new candidate."); R.expanded.add(d.id); await refresh(); await select(done.new_id, { scroll: true }); }
  }

  async function mergeFlow() {
    const ids = [...R.checked].filter((id) => (R.listing.candidates.find((c) => c.id === id) || {}).kind === "task");
    let preview;
    try { preview = (await api.post("/api/candidates/merge", { ids })).preview; } catch (e) { return toast(e.message, { kind: "error" }); }
    const by = new Map(R.listing.candidates.map((c) => [c.id, c]));
    let merged = null;
    await formDialog({
      title: "Merge candidates", submitLabel: "Merge", wide: true,
      body: () => [
        h("p", null, "Merging keeps one candidate and moves everything from the others into it. Nothing is lost: source blocks, lines, scenarios and media are combined, and the others stay visible as merged."),
        fieldLabel("Keep this one", h("select", { name: "into" }, ids.map((id) => h("option", { value: id, selected: id === preview.into_id }, by.get(id).title)))),
        fieldLabel("Title", h("input", { type: "text", name: "title", maxlength: "300", value: preview.title })),
        h("dl", { class: "kv" },
          h("dt", null, "Merged in"), h("dd", null, preview.merged.join(", ")),
          h("dt", null, "Lines kept"), h("dd", null, String(preview.lines)),
          h("dt", null, "Source blocks"), h("dd", null, String(preview.sources)),
          h("dt", null, "Media"), h("dd", null, String(preview.references)),
          h("dt", null, "Scenarios"), h("dd", null, preview.scenarios.join(", ") || "none")),
        ...preview.notes.map((n) => h("p", { class: "warn-text" }, n)),
      ],
      onSubmit: async (dlg) => {
        merged = await api.post("/api/candidates/merge", { ids, into_id: dlg.dialog.querySelector('[name="into"]').value, title: dlg.dialog.querySelector('[name="title"]').value, confirm: true });
      },
    });
    if (merged) { R.checked.clear(); toast("Merged"); R.sel = merged.merged_into; await refresh(); await select(merged.merged_into, { scroll: true }); }
  }

  load();
  return root;
}
