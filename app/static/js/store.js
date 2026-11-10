// Application state, routing, and data loading. The server is the source of truth.
import { api, qs } from "./api.js";
import { safeStorage } from "./dom.js";

export const FILTER_DIMS = ["discipline", "category", "impact", "urgency", "priority", "assignee"];
export const VIEWS = ["board", "backlog", "import", "importbrief", "sources", "rules", "settings"];

export const state = {
  meta: null,
  projects: [],
  projectId: null,
  project: null,
  people: [],
  view: "board",
  taskId: null,
  docId: null, // open source document (Sources view)
  params: new URLSearchParams(),
  q: "",
  filters: emptyFilters(),
  listStatus: [],
  board: null,
  list: null,
  loadError: null,
  expanded: new Set(), // card ids with children expanded
};

const subs = new Set();
export const subscribe = (fn) => (subs.add(fn), () => subs.delete(fn));
export const notify = () => subs.forEach((fn) => fn(state));

export function emptyFilters() {
  return Object.fromEntries(FILTER_DIMS.map((d) => [d, []]));
}
export function activeFilterCount() {
  return FILTER_DIMS.reduce((n, d) => n + state.filters[d].length, 0) + (state.q.trim() ? 1 : 0);
}
export function clearFilters() {
  state.filters = emptyFilters();
  state.q = "";
  state.listStatus = [];
}
function filterQuery(includeStatus) {
  const p = { q: state.q.trim(), ...state.filters };
  if (includeStatus) p.status = state.listStatus;
  return qs(p);
}

// ---- routing --------------------------------------------------------------------

const store = safeStorage();
export function lastProjectId() {
  return store ? store.getItem("lastProject") : null;
}

export function parseHash() {
  const raw = location.hash.replace(/^#/, "");
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  const params = new URLSearchParams(query);
  if (parts[0] === "p" && parts[1]) {
    const view = VIEWS.includes(parts[2]) ? parts[2] : "board";
    return { projectId: parts[1], view, taskId: params.get("task"), docId: view === "sources" || view === "import" ? parts[3] || null : null, params };
  }
  return { projectId: null, view: "board", taskId: null, docId: null, params: new URLSearchParams() };
}

export function hashFor({ projectId = state.projectId, view = state.view, taskId = null, docId = null, params = null } = {}) {
  if (!projectId) return "#/";
  const q = new URLSearchParams(params || {});
  if (taskId) q.set("task", taskId);
  const qs = q.toString();
  return `#/p/${projectId}/${view}${docId ? `/${docId}` : ""}${qs ? `?${qs}` : ""}`;
}

export function go(target, { replace = false } = {}) {
  const h = typeof target === "string" ? target : hashFor(target);
  if (replace) history.replaceState(null, "", h), window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = h;
}

// ---- loading --------------------------------------------------------------------

export async function loadMeta() {
  state.meta = await api.get("/api/meta");
}

export async function loadProjects() {
  state.projects = (await api.get("/api/projects")).projects;
}

export async function loadProject(id) {
  state.project = await api.get(`/api/projects/${id}`);
  state.projectId = id;
  state.people = (await api.get(`/api/projects/${id}/people`)).people;
  if (store) store.setItem("lastProject", id);
}

export async function loadPeople() {
  state.people = (await api.get(`/api/projects/${state.projectId}/people`)).people;
}

/** Refresh whichever data the current view displays. */
export async function loadView() {
  if (!state.projectId) return;
  state.loadError = null;
  try {
    if (state.view === "board") {
      state.board = await api.get(`/api/projects/${state.projectId}/board${filterQuery(false)}`);
    } else if (state.view === "backlog") {
      state.list = (await api.get(`/api/projects/${state.projectId}/tasks${filterQuery(true)}`)).tasks;
    }
    // Unfiltered totals tell us whether the project is empty or merely filtered.
    state.project = await api.get(`/api/projects/${state.projectId}`);
  } catch (err) {
    state.loadError = err.message;
  }
}

export const statusLabel = (v) => (state.meta.statuses.find((s) => s.value === v) || {}).label || v;
export const labelOf = (list, v) => ((state.meta[list] || []).find((x) => x.value === v) || {}).label || v;
