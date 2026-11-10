// Application shell: routing, sidebar, header, and workspace composition.
import { api, clearFailure, onSaveState, saveState } from "./api.js";
import { boardIntro, emptyProjectState, noMatchState, renderBoard } from "./board.js";
import { closeDetails, hasUnsaved, showDetails } from "./details.js";
import { h, replace } from "./dom.js";
import { renderFilters, renderSearch } from "./filters.js";
import { icon } from "./icons.js";
import { renderImportBrief, resetImport } from "./importer.js";
import { renderList } from "./list.js";
import { renderReview, resetReview } from "./review.js";
import { renderRules, resetRules } from "./rules.js";
import { noProjectState, openNewProject, projectSwitcher } from "./projects.js";
import { renderSettings } from "./settings.js";
import { isDocumentRoute, renderSources, resetSources } from "./sources.js";
import {
  activeFilterCount, clearFilters, go, lastProjectId, loadMeta, loadProject, loadProjects, loadView, parseHash, state,
} from "./store.js";
import { openCreateTask } from "./taskform.js";
import { closeLayer, confirmDialog } from "./ui.js";

const $ = (id) => document.getElementById(id);
let dataDir = "";
let prev = { projectId: null, view: null };
let currentHash = location.hash;

// ---- boot / routing ------------------------------------------------------------------

async function boot() {
  try {
    const health = await api.get("/api/health");
    dataDir = health.data_dir;
    await Promise.all([loadMeta(), loadProjects()]);
  } catch (err) {
    replace($("workspace"), h("div", { class: "banner", role: "alert" }, `Could not start: ${err.message}`));
    return;
  }
  window.addEventListener("hashchange", onRoute);
  window.addEventListener("beforeunload", (e) => {
    if (hasUnsaved() || saveState.failure || saveState.pending > 0) { e.preventDefault(); e.returnValue = ""; }
  });
  document.addEventListener("organizer:rerender", renderWorkspace);
  document.addEventListener("organizer:data", async () => { await loadView(); renderWorkspace(); renderSidebar(); });
  document.addEventListener("organizer:details", () => { if (state.taskId) renderDetails(); });
  document.addEventListener("organizer:filters", async () => { await loadView(); renderHeader(); renderWorkspace(); });
  document.addEventListener("keydown", onGlobalKey);
  // Pick up changes made elsewhere (another tab or window) when this one is focused again.
  window.addEventListener("focus", async () => {
    if (!state.projectId || document.querySelector("dialog[open]") || saveState.pending > 0) return;
    if (["sources", "importbrief", "import", "rules"].includes(state.view)) return; // these views keep their own state and scroll
    await loadView();
    renderWorkspace();
    renderSidebar();
  });
  $("sidebar-scrim").addEventListener("click", () => setSidebar(false));
  onSaveState(paintSaveState);
  await onRoute();
}

async function onRoute() {
  closeLayer();
  const r = parseHash();
  // A failed, unretried edit is never dropped silently by navigating away.
  if (hasUnsaved() && (r.taskId !== state.taskId || r.projectId !== state.projectId)) {
    const target = location.hash;
    history.replaceState(null, "", currentHash);
    const discard = await confirmDialog({
      title: "Discard unsaved changes?",
      message: "The last edit to this task could not be saved. Leaving now will discard it.",
      confirmLabel: "Discard and continue", danger: true,
    });
    if (!discard) return;
    history.replaceState(null, "", target);
    closeDetails();
  }
  currentHash = location.hash;
  if (!r.projectId) {
    if (state.projects.length) {
      const last = lastProjectId();
      const target = state.projects.find((p) => p.id === last) || state.projects[0];
      return go({ projectId: target.id, view: "board" }, { replace: true });
    }
    state.projectId = null;
    state.project = null;
    state.taskId = null;
    closeDetails();
    return renderAll();
  }
  if (state.projectId !== r.projectId || !state.project) {
    try {
      await loadProject(r.projectId);
    } catch {
      await loadProjects();
      return go("#/", { replace: true });
    }
    clearFilters();
    state.board = null;
    state.list = null;
    state.expanded = new Set();
  }
  const changedView = prev.projectId !== r.projectId || prev.view !== r.view;
  if (changedView || state.docId !== r.docId) { resetSources(); resetReview(); }
  if (changedView) resetRules();
  if (changedView && prev.view === "importbrief") resetImport();
  state.view = r.view;
  state.taskId = r.taskId;
  state.docId = r.docId;
  state.params = r.params;
  setSidebar(false);
  if (changedView) await loadView();
  prev = { projectId: r.projectId, view: r.view };
  renderAll();
}

function onGlobalKey(e) {
  if (e.key === "Escape" && state.taskId && !document.querySelector("dialog[open]")) {
    const a = document.activeElement;
    if (a && $("details").contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) {
      a.blur(); // commit the edit first; a second Escape closes the panel
      return;
    }
    const id = state.taskId;
    go({});
    setTimeout(() => {
      const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
      if (card) card.focus();
    }, 50);
  }
}

function setSidebar(open) {
  $("app").dataset.sidebar = open ? "open" : "closed";
  $("sidebar-scrim").hidden = !open;
}

// ---- rendering -----------------------------------------------------------------------

function renderAll() {
  renderSidebar();
  renderHeader();
  renderWorkspace();
  renderDetails();
  const p = state.project;
  document.title = p ? `${p.name} · ${viewTitle()} · Offline Task Organizer` : "Offline Task Organizer";
}

const NAV = [
  ["board", "Board", "board"],
  ["backlog", "Backlog", "list"],
  ["import", "Import Review", "import"],
  ["sources", "Sources", "doc"],
  ["rules", "Rules", "rules"],
  ["settings", "Settings", "settings"],
];
const viewTitle = () => (state.view === "importbrief" ? "Import brief" : (NAV.find((n) => n[0] === state.view) || NAV[0])[1]);

function renderSidebar() {
  const nav = h("ul", { class: "nav" });
  if (state.projectId) {
    NAV.forEach(([view, label, ic, soon], i) => {
      if (i === 2) nav.append(h("li", { class: "nav-sep", role: "presentation" }));
      nav.append(h("li", null, h("a", {
        href: `#/p/${state.projectId}/${view}`, "aria-current": state.view === view ? "page" : null,
      }, icon(ic), label, soon ? h("span", { class: "soon", title: `Planned for Milestone ${soon.slice(1)}` }, "Soon") : null)));
    });
  }
  replace($("sidebar"),
    h("div", { class: "brand" }, h("span", { class: "brand-mark", "aria-hidden": "true" }, icon("board")), "Task Organizer"),
    projectSwitcher(), nav,
    h("div", { class: "sidebar-foot" }, "Offline · stored on this computer"));
}

let saveEl = null;
function paintSaveState(s) {
  if (!saveEl) return;
  saveEl.className = `save-state ${s.status === "failed" ? "failed" : ""}`;
  saveEl.setAttribute("role", s.status === "failed" ? "alert" : "status");
  replace(saveEl);
  if (s.status === "saving") saveEl.append("Saving…");
  else if (s.status === "saved") saveEl.append(icon("check"), "All changes saved");
  else if (s.status === "failed") {
    saveEl.append(icon("alert"), h("span", { class: "msg", title: s.failure }, `Not saved: ${s.failure}`),
      h("button", { class: "btn subtle small", type: "button", onclick: () => clearFailure() }, "Dismiss"));
  }
}

function renderHeader() {
  saveEl = h("div", { class: "save-state" });
  const p = state.project;
  const header = $("header");
  const items = [
    h("button", { class: "icon-btn menu-btn", type: "button", "aria-label": "Open navigation", onclick: () => setSidebar(true) }, icon("menu")),
    h("h1", { class: "title" }, p ? p.name : "Offline Task Organizer", p ? h("span", { class: "sub" }, `${p.ticket_prefix} · ${viewTitle()}`) : null),
  ];
  let tools = null;
  if (p && (state.view === "board" || state.view === "backlog")) {
    const onChange = async (opts = {}) => {
      await loadView();
      if (opts.rebuild) renderHeader();
      renderWorkspace();
      if (opts.keepFocus) { const q = document.querySelector('.search input'); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }
    };
    tools = h("div", { class: "header-tools" }, renderSearch(onChange), renderFilters(onChange));
  }
  items.push(h("span", { class: "spacer" }), saveEl);
  if (p) {
    items.push(
      h("button", { class: "btn", type: "button", onclick: () => {
        resetImport(true); // the header button always starts a fresh selection (a running import is kept in view)
        if (state.view === "importbrief") renderWorkspace(); else go(`#/p/${p.id}/importbrief`);
      } }, icon("import"), "Import"),
      h("button", { class: "btn primary", type: "button", onclick: () => openCreateTask() }, icon("plus"), "Create task"));
  } else {
    items.push(h("button", { class: "btn primary", type: "button", onclick: openNewProject }, icon("plus"), "Create project"));
  }
  replace(header, h("div", { class: "header-main" }, ...items), tools);
  paintSaveState(saveState);
}

function renderWorkspace() {
  const ws = $("workspace");
  ws.classList.toggle("board-ws", state.view === "board" && !!state.project);
  ws.classList.toggle("fill-ws", (state.view === "sources" && isDocumentRoute() || state.view === "import") && !!state.project);
  if (!state.project) return replace(ws, noProjectState());
  const kids = [];
  if (state.loadError) {
    kids.push(h("div", { class: "banner", role: "alert" }, `Could not load data: ${state.loadError}`,
      h("button", { class: "btn small", type: "button", onclick: async () => { await loadView(); renderWorkspace(); } }, "Retry")));
  }
  const empty = state.project.task_count === 0;
  switch (state.view) {
    case "board":
      if (empty) kids.push(emptyProjectState());
      else if (state.board && state.board.total_visible === 0 && state.board.filters_active) kids.push(boardIntro(), noMatchState());
      else kids.push(boardIntro(), renderBoard(renderWorkspace));
      break;
    case "backlog":
      if (empty) kids.push(emptyProjectState());
      else if (state.list && state.list.length === 0 && (activeFilterCount() > 0 || state.listStatus.length)) {
        kids.push(renderList(renderWorkspace), noMatchState());
      } else if (state.list) kids.push(renderList(renderWorkspace));
      break;
    case "import":
      kids.push(renderReview());
      break;
    case "importbrief":
      kids.push(renderImportBrief());
      break;
    case "sources":
      kids.push(renderSources());
      break;
    case "rules":
      kids.push(renderRules());
      break;
    case "settings":
      kids.push(renderSettings(async (opts = {}) => { if (opts.header) renderHeader(); renderSidebar(); renderWorkspace(); }, dataDir));
      break;
  }
  replace(ws, ...kids);
}

function renderDetails() {
  const el = $("details");
  if (!state.project || !state.taskId || (state.view !== "board" && state.view !== "backlog")) {
    el.hidden = true;
    closeDetails();
    return;
  }
  el.hidden = false;
  showDetails(el, state.taskId);
}

boot();
