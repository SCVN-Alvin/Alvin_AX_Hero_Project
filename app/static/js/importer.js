// Import brief: choose files, preview the inventory, run the import with visible progress.
import { api } from "./api.js";
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { pickPath } from "./filepicker.js";
import { fmtBytes } from "./media.js";
import { go, state } from "./store.js";
import { toast } from "./ui.js";

// Survives re-renders and navigation inside the app, so a running import is never lost from view.
const wiz = { step: "select", html: "", companion: "", preview: null, runId: null, status: null, error: "", busy: false, projectId: null };
let root = null;
let timer = null;

export function resetImport(clearPaths = false) {
  if (wiz.step === "running") return; // keep following a running import
  stopPolling();
  Object.assign(wiz, { step: "select", preview: null, runId: null, status: null, error: "", busy: false });
  if (clearPaths) Object.assign(wiz, { html: "", companion: "" });
}

function stopPolling() {
  if (timer) { clearTimeout(timer); timer = null; }
}

const STAGES = {
  starting: "Starting", reading: "Reading the file", analysing: "Reading the structure and checking the media files",
  copying: "Copying media into the project", comparing: "Comparing with earlier imports", saving: "Saving the document",
  done: "Finished", queued: "Starting",
};

export function renderImportBrief() {
  if (wiz.projectId !== state.projectId) {
    Object.assign(wiz, { step: "select", html: "", companion: "", preview: null, runId: null, status: null, error: "", projectId: state.projectId });
  }
  root = h("div", { class: "import-wiz" });
  paint();
  if (wiz.step === "running") poll();
  return root;
}

function paint() {
  if (!root) return;
  if (wiz.step === "running") return replace(root, runningView());
  if (wiz.step === "done") return replace(root, doneView());
  if (wiz.step === "preview" && wiz.preview) return replace(root, previewView());
  replace(root, selectView());
}

// ---- step 1: choose -------------------------------------------------------------

function selectView() {
  const htmlIn = h("input", { type: "text", name: "html", value: wiz.html, "aria-label": "HTML file path", placeholder: "/path/to/Brief export.html", autocomplete: "off", spellcheck: "false" });
  const compIn = h("input", { type: "text", name: "companion", value: wiz.companion, "aria-label": "Media folder path", placeholder: "Leave empty to use the folder next to the HTML file", autocomplete: "off", spellcheck: "false" });
  const err = h("div", { class: "form-error", role: "alert", hidden: !wiz.error }, wiz.error);
  const go1 = async () => {
    wiz.html = htmlIn.value.trim();
    wiz.companion = compIn.value.trim();
    if (!wiz.html) { wiz.error = "Choose the HTML file to import."; return paint(); }
    wiz.error = "";
    wiz.busy = true;
    replace(root, h("div", { class: "panel", role: "status" }, h("h2", null, "Checking the selection…"), h("p", { class: "muted" }, "Reading the file and looking up its media. Nothing is copied yet.")));
    try {
      wiz.preview = await api.post(`/api/projects/${state.projectId}/imports/preview`, { html_path: wiz.html, companion_path: wiz.companion }, { quiet: true });
      wiz.step = "preview";
    } catch (e) {
      wiz.error = e.message;
      wiz.step = "select";
    }
    wiz.busy = false;
    paint();
    const f = root.querySelector("input, .btn.primary");
    if (f) f.focus();
  };
  htmlIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go1(); } });
  compIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go1(); } });
  const browseHtml = async () => {
    const p = await pickPath({ mode: "file", start: htmlIn.value || "" });
    if (p) { htmlIn.value = p; wiz.html = p; }
  };
  const browseDir = async () => {
    const start = compIn.value || (htmlIn.value ? htmlIn.value.replace(/\/[^/]*$/, "") : "");
    const p = await pickPath({ mode: "dir", start });
    if (p) { compIn.value = p; wiz.companion = p; }
  };
  return h("div", { class: "import-card" },
    h("h2", null, "Import a brief"),
    h("p", { class: "muted" }, "Choose the exported HTML file and the folder that holds its images and videos. Files are copied into this project; the originals are never changed. Nothing is sent over the network."),
    err,
    h("div", { class: "field" }, h("label", { for: (htmlIn.id = "imp-html") }, "HTML file"), h("div", { class: "row" }, htmlIn, h("button", { class: "btn", type: "button", onclick: browseHtml }, icon("folder"), "Browse…"))),
    h("div", { class: "field" }, h("label", { for: (compIn.id = "imp-comp") }, "Media folder ", h("span", { class: "origin" }, "optional")),
      h("div", { class: "row" }, compIn, h("button", { class: "btn", type: "button", onclick: browseDir }, icon("folder"), "Browse…")),
      h("div", { class: "hint" }, "A folder with the same name as the HTML file (without the trailing code) next to it is used automatically.")),
    h("div", { class: "actions-row" }, h("button", { class: "btn primary", type: "button", onclick: go1 }, "Preview selection")),
    h("p", { class: "muted small-note" }, "You can also run imports from a terminal: ", h("code", null, "python3 -m organizer import --project PREFIX --html FILE")));
}

// ---- step 2: preview ------------------------------------------------------------

function kv(label, value) {
  return [h("dt", null, label), h("dd", null, value)];
}

function previewView() {
  const p = wiz.preview;
  const a = p.assets, st = p.structure;
  const issues = a.missing + a.blocked + a.unreadable;
  const priorNote = {
    same_html: p.prior.assets_match
      ? "This exact file and media were already imported. Importing again changes nothing."
      : "This exact HTML file was already imported. Importing again will not create a second copy; it will only attach media that was previously unavailable.",
    new_version: `A file with this name was imported before (version ${p.prior.version}). The new content will be added as version ${p.prior.version + 1}; the earlier version is kept.`,
  }[p.prior.status];
  const problems = a.problems.length
    ? h("details", { class: "sec", open: a.problems.length <= 8 },
      h("summary", null, `${a.problems.length} file(s) cannot be imported`, h("span", { class: "muted" }, " — the references are kept")),
      h("ul", { class: "list-plain problems" }, a.problems.slice(0, 50).map((x) => h("li", null, h("code", null, x.path), h("div", { class: "muted" }, `${x.status}: ${x.problem}`)))))
    : null;
  return h("div", { class: "import-card wide" },
    h("h2", null, "Preview"),
    h("p", { class: "muted" }, "This is what was found. Nothing has been copied or saved yet."),
    p.disk.enough ? null : h("div", { class: "form-error", role: "alert" }, `Not enough free disk space: ${fmtBytes(p.disk.needed_bytes)} needed, ${fmtBytes(p.disk.free_bytes)} free.`),
    priorNote ? h("div", { class: "banner info" }, icon("alert"), priorNote) : null,
    h("dl", { class: "kv wide-kv" },
      ...kv("Document", h("div", null, h("b", null, p.html.title || p.html.name), h("div", { class: "muted" }, `${p.html.name} · ${fmtBytes(p.html.size)}`))),
      ...kv("Media folder", p.companion.path ? h("span", null, h("code", null, p.companion.path), p.companion.auto_detected ? h("span", { class: "muted" }, " (found next to the file)") : null) : h("span", { class: "warn-text" }, "None found. Local media will be reported as missing.")),
      ...kv("Structure", `${st.blocks} blocks in ${st.sections} sections, ${st.tables} tables`),
      ...kv("Scenarios", st.scenarios ? `${st.scenarios} found (${st.scenario_ids[0]} to ${st.scenario_ids[st.scenario_ids.length - 1]}) in ${st.scenario_tables} table${st.scenario_tables === 1 ? "" : "s"}` : "None recognised. You can choose a table and its columns after importing."),
      ...kv("Historical sections", st.historical_sections.length
        ? h("span", null, st.historical_sections.map((t) => h("span", { class: "chip", key: t }, t)), h("span", { class: "muted" }, " kept and searchable; excluded from candidate generation by default. You can change this after importing."))
        : "None detected"),
      ...kv("Media", h("span", null, `${a.referenced} files referenced: `, h("b", { class: issues ? "" : "ok-text" }, `${a.available} found`), issues ? h("span", { class: "warn-text" }, `, ${a.missing} missing${a.blocked ? `, ${a.blocked} blocked` : ""}${a.unreadable ? `, ${a.unreadable} unreadable` : ""}`) : null,
        h("span", { class: "muted" }, ` · ${fmtBytes(a.total_bytes)}`, Object.keys(a.by_type).length ? ` · ${Object.entries(a.by_type).map(([k, v]) => `${v} ${k}`).join(", ")}` : ""))),
      ...(a.unreferenced_count ? kv("Other files in the folder", h("span", { class: "muted" }, `${a.unreferenced_count} file(s) are not used by the document and will not be copied`)) : []),
      ...kv("Web links", p.external.links || p.external.images
        ? h("span", null, `${p.external.links} link(s) and ${p.external.images} image(s) point to the internet (${Object.keys(p.external.hosts).join(", ") || "none"}). `, h("span", { class: "muted" }, "They are kept as links and never fetched."))
        : "None")),
    problems,
    p.warnings.length ? h("ul", { class: "list-plain warnings" }, p.warnings.map((w) => h("li", null, icon("alert"), w))) : null,
    h("div", { class: "actions-row" },
      h("button", { class: "btn", type: "button", onclick: () => { wiz.step = "select"; wiz.error = ""; paint(); } }, "Change selection"),
      h("button", { class: "btn primary", type: "button", disabled: !p.can_import, onclick: start }, icon("import"), issues ? "Import anyway" : "Import brief")));
}

// ---- step 3: running ------------------------------------------------------------

async function start() {
  try {
    const run = await api.post(`/api/projects/${state.projectId}/imports`, { html_path: wiz.html, companion_path: wiz.companion }, { quiet: true });
    wiz.runId = run.id;
    wiz.status = run;
    wiz.step = "running";
    paint();
    poll();
  } catch (e) {
    wiz.error = e.message;
    wiz.step = "select";
    paint();
  }
}

function poll() {
  stopPolling();
  const tick = async () => {
    try {
      wiz.status = await api.get(`/api/imports/${wiz.runId}`);
    } catch (e) {
      wiz.status = { state: "failed", error: e.message };
    }
    if (wiz.status.state === "running") {
      if (root && document.contains(root)) replace(root, runningView());
      timer = setTimeout(tick, 400);
      return;
    }
    wiz.step = "done";
    if (root && document.contains(root)) paint();
    else if (wiz.status.state === "succeeded") toast("Import finished");
  };
  tick();
}

function runningView() {
  const s = wiz.status || {};
  const pr = s.progress || {};
  const pct = pr.bytes_total ? Math.min(100, Math.round((pr.bytes_done / pr.bytes_total) * 100)) : null;
  const fill = h("i");
  fill.style.width = `${pct || 0}%`; // CSSOM, allowed by the policy (style attributes are not)
  return h("div", { class: "import-card" },
    h("h2", null, "Importing…"),
    h("p", { role: "status", "aria-live": "polite" }, STAGES[pr.stage || s.stage] || pr.stage || "Working"),
    pr.stage === "copying" || pr.files_total
      ? h("div", null,
        h("div", { class: "progressbar", role: "progressbar", "aria-label": "Copy progress", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": pct === null ? null : String(pct) }, fill),
        h("div", { class: "muted" }, `${pr.files_done || 0} of ${pr.files_total || 0} files · ${fmtBytes(pr.bytes_done || 0)} of ${fmtBytes(pr.bytes_total || 0)}`, pr.current ? ` · ${pr.current.split("/").pop()}` : ""))
      : null,
    h("p", { class: "muted" }, "You can leave this page; the import keeps running and nothing appears in the project until it finishes."),
    h("div", { class: "actions-row" }, h("button", { class: "btn danger", type: "button", onclick: cancel }, "Cancel import")));
}

async function cancel() {
  try { await api.post(`/api/imports/${wiz.runId}/cancel`, {}, { quiet: true }); } catch (e) { toast(e.message, { kind: "error" }); }
}

// ---- step 4: result -------------------------------------------------------------

function doneView() {
  const s = wiz.status || {};
  const open = (docId) => { wiz.step = "select"; wiz.preview = null; wiz.runId = null; go(`#/p/${state.projectId}/sources/${docId}`); };
  const again = (keep) => { wiz.step = keep ? "preview" : "select"; wiz.runId = null; wiz.status = null; wiz.error = ""; paint(); };
  const r = s.result || {};
  if (s.state === "succeeded" || s.state === "identical") {
    const same = s.state === "identical";
    return h("div", { class: "import-card" },
      h("h2", null, same ? "Already imported" : "Import finished"),
      h("div", { class: same ? "banner info" : "banner success", role: "status" }, icon(same ? "alert" : "check"), s.note || r.note),
      !same && r.assets_unavailable ? h("p", { class: "warn-text" }, `${r.assets_unavailable} referenced file(s) were not available. They are listed under Media in the source browser, and the references are kept.`) : null,
      h("div", { class: "actions-row" },
        h("button", { class: "btn primary", type: "button", onclick: () => open(s.document_id) }, icon("doc"), "Open the source browser"),
        h("button", { class: "btn", type: "button", onclick: () => { wiz.html = ""; wiz.companion = ""; again(false); } }, "Import another brief")));
  }
  const title = { cancelled: "Import cancelled", interrupted: "Import interrupted" }[s.state] || "Import failed";
  return h("div", { class: "import-card" },
    h("h2", null, title),
    h("div", { class: "form-error", role: "alert" }, s.error || "The import did not finish."),
    h("p", { class: "muted" }, "Nothing was added to the project, and everything imported earlier is unchanged."),
    h("div", { class: "actions-row" },
      h("button", { class: "btn primary", type: "button", onclick: () => again(!!wiz.preview) }, "Try again"),
      h("button", { class: "btn", type: "button", onclick: () => again(false) }, "Change selection")));
}
