// Optional AI drafting: connection settings, a payload preview, background runs that belong to their own project,
// and a draft preview where a person chooses what to import. Everything here is optional; the review and approval
// workflow never depends on it.
import { api } from "./api.js";
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { go, state } from "./store.js";
import { announce, fieldLabel, formDialog, toast } from "./ui.js";

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

function dialog(title, { wide = false, onClose } = {}) {
  const dlg = h("dialog", { class: "ai-dialog", "aria-label": title });
  dlg.style.width = wide ? "min(860px, calc(100vw - 24px))" : "min(560px, calc(100vw - 24px))";
  const body = h("div", { class: "dlg-body" });
  const foot = h("div", { class: "dlg-foot" });
  const err = h("div", { class: "form-error", role: "alert", hidden: true });
  dlg.append(h("div", { class: "ai-dlg-wrap" }, h("div", { class: "dlg-head" }, title), body, err, foot));
  dlg.addEventListener("close", () => { dlg.remove(); if (onClose) onClose(); });
  document.body.append(dlg);
  dlg.showModal();
  return {
    dlg, body, foot,
    set(...kids) { replace(body, ...kids); },
    buttons(...kids) { replace(foot, ...kids); },
    error(msg) { replace(err, msg); err.hidden = !msg; },
    close() { if (dlg.open) dlg.close(); },
  };
}

// ---- settings -------------------------------------------------------------------

let settingsCache = null;
export async function aiSettings(force = false) {
  if (!settingsCache || force || Date.now() - settingsCache.at > 4000) settingsCache = { at: Date.now(), value: await api.get("/api/ai/settings") };
  return settingsCache.value;
}

export function renderAiSettings() {
  const root = h("section", { class: "panel", "data-ai-settings": "" });
  let s = null;
  let check = null;

  async function load() {
    try { s = await aiSettings(true); paint(); } catch (e) { replace(root, h("h2", null, "AI drafting"), h("p", { class: "err" }, e.message)); }
  }

  async function save(patch) {
    try { s = await api.patch("/api/ai/settings", patch); settingsCache = { at: Date.now(), value: s }; check = null; paint(); toast("AI settings saved"); }
    catch (e) { toast(e.message, { kind: "error" }); await load(); }
  }

  async function runCheck(btn) {
    btn.disabled = true;
    replace(btn, "Checking…");
    try { check = await api.post("/api/ai/check"); } catch (e) { check = { message: e.message, available: false }; }
    paint();
  }

  function paint() {
    const enabled = h("input", { type: "checkbox", name: "ai-enabled", checked: s.enabled, onchange: (e) => save({ enabled: e.target.checked }) });
    const bin = h("input", { type: "text", name: "ai-binary", value: s.binary, maxlength: "500" });
    const model = h("input", { type: "text", name: "ai-model", value: s.model, placeholder: "default of your Codex account", maxlength: "100" });
    const timeout = h("input", { type: "text", name: "ai-timeout", value: String(s.timeout_seconds), size: "6", inputmode: "numeric" });
    const maxChars = h("input", { type: "text", name: "ai-max-chars", value: String(s.max_chars), size: "8", inputmode: "numeric" });
    const maxBlocks = h("input", { type: "text", name: "ai-max-blocks", value: String(s.max_blocks), size: "6", inputmode: "numeric" });
    const batch = h("input", { type: "text", name: "ai-batch", value: String(s.batch_size), size: "4", inputmode: "numeric" });
    const provider = s.scripted_provider_available
      ? h("select", { name: "ai-provider", "aria-label": "Provider" }, [["codex_cli", "Codex CLI"], ["fake", "Scripted test answers"]].map(([v, l]) => h("option", { value: v, selected: s.provider === v }, l))) : null;
    const statusEl = check && h("div", { class: `banner ${check.signed_in && check.flags_ok ? "ok" : "info"}`, role: "status" },
      h("b", null, check.version || (check.available ? "Installed" : "Not found")), " — ", check.message,
      check.restrictions && check.restrictions.length ? h("details", null, h("summary", null, "How requests are restricted"), h("ul", { class: "list-plain" }, check.restrictions.map((r) => h("li", null, r)))) : null,
      check.limitations ? h("ul", { class: "ai-limits" }, check.limitations.map((r) => h("li", null, r))) : null,
      check.note ? h("p", { class: "muted small-note" }, check.note) : null);
    const checkBtn = h("button", { class: "btn", type: "button", onclick: (e) => runCheck(e.currentTarget) }, "Check connection");
    replace(root,
      h("h2", null, "AI drafting (optional)"),
      h("p", { class: "muted" }, "Drafts short descriptions and subtasks from source text you select. Everything else in the organizer works without it, offline. "
        + "Only the text and captions you preview are sent, using the Codex command on this computer and your own sign-in. Nothing is ever approved automatically."),
      h("label", { class: "checks" }, enabled, " Turn on AI drafting"),
      provider ? fieldLabel("Provider", provider) : null,
      h("div", { class: "props" }, fieldLabel("Codex command", bin), fieldLabel("Model (optional)", model)),
      h("div", { class: "props" }, fieldLabel("Time limit per request (seconds)", timeout), fieldLabel("Tasks per request", batch)),
      h("div", { class: "props" }, fieldLabel("Most source blocks per request", maxBlocks), fieldLabel("Most characters per request", maxChars)),
      h("div", { class: "actions-row" },
        h("button", { class: "btn", type: "button", onclick: () => {
          const num = (el) => Number.parseInt(el.value, 10);
          save({ binary: bin.value, model: model.value, timeout_seconds: num(timeout), batch_size: num(batch), max_blocks: num(maxBlocks), max_chars: num(maxChars), ...(provider ? { provider: provider.value } : {}) });
        } }, "Save AI settings"), checkBtn),
      statusEl,
      h("p", { class: "muted small-note" }, "Codex keeps its own sign-in. The organizer never reads it, stores it, or asks for a password. To sign in, run “codex login” in a terminal."));
  }
  load();
  return root;
}

// ---- starting: choose, preview, send ------------------------------------------------------

/** Show exactly what would be sent, let the person remove material, then start a run. */
export async function openAiPreview({ pid, docId, candidateIds, purpose = "drafts", retryOf = null, onStarted, exclude = {} }) {
  let settings;
  try { settings = await aiSettings(true); } catch (e) { return toast(e.message, { kind: "error" }); }
  const excludeBlocks = new Set(exclude.blocks || []);
  const excludeRefs = new Set(exclude.refs || []);
  const ui = dialog(purpose === "description" ? "Draft a description with AI" : "Generate AI drafts", { wide: true });
  let preview = null;

  async function load() {
    ui.set(h("p", { class: "muted", role: "status" }, "Preparing what would be sent…"));
    try {
      preview = await api.post(`/api/documents/${docId}/ai/preview`, { candidate_ids: candidateIds, exclude_block_ids: [...excludeBlocks], exclude_reference_ids: [...excludeRefs] });
    } catch (e) { ui.set(h("div", { class: "banner", role: "alert" }, e.message)); ui.buttons(h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Close")); return; }
    paint();
  }

  function paint() {
    const p = preview;
    const off = !settings.enabled;
    const blocksByHeading = new Map();
    for (const b of p.blocks) (blocksByHeading.get(b.heading || "") || blocksByHeading.set(b.heading || "", []).get(b.heading || "")).push(b);
    ui.set(
      off ? h("div", { class: "banner info", role: "note" }, icon("alert"), " AI drafting is turned off. You can review what would be sent, but nothing is sent until you turn it on in ",
        h("a", { class: "link", href: `#/p/${pid}/settings`, onclick: () => ui.close() }, "Settings"), ".") : null,
      h("p", null, h("b", null, "Nothing has been sent yet. "), "This is exactly what would leave this computer for ", h("b", null, p.systems.map((s) => s.title).join(", ") || "your selection"), ". ",
        "Untick anything unrelated."),
      p.notes.map((n) => h("p", { class: "muted small-note" }, n)),
      h("div", { class: "ai-limits-line muted", "data-limits": "" }, `${plural(p.limits.blocks, "source block")} (limit ${p.limits.max_blocks}) · ${p.limits.chars.toLocaleString()} characters (limit ${p.limits.max_chars.toLocaleString()}) · ${plural(p.limits.tasks, "task")} per request (limit ${p.limits.batch_size})`),
      p.problems.map((x) => h("div", { class: "banner", role: "alert" }, x)),
      h("h4", null, `Source text (${p.blocks.length})`),
      h("div", { class: "ai-blocks" }, [...blocksByHeading].map(([heading, bs]) => h("div", { class: "ai-group" }, heading ? h("div", { class: "muted small-note" }, heading) : null,
        bs.map((b) => h("label", { class: "ai-block" }, h("input", { type: "checkbox", checked: true, "data-block": b.block_id, "aria-label": `Send: ${b.text.slice(0, 60)}`, onchange: (e) => { if (e.target.checked) excludeBlocks.delete(b.block_id); else excludeBlocks.add(b.block_id); load(); } }),
          h("span", { class: "ai-text" }, b.text)))))),
      excludeBlocks.size ? h("p", { class: "muted small-note" }, `${plural(excludeBlocks.size, "block")} left out. `, h("button", { class: "link", type: "button", onclick: () => { excludeBlocks.clear(); load(); } }, "Put them back")) : null,
      p.scenarios.length ? h("div", null, h("h4", null, "Scenarios named"), h("div", { class: "chips-left" }, p.scenarios.map((s) => h("span", { class: "chip", title: s.title }, s.id)))) : null,
      h("h4", null, `Reference captions (${p.references.length})`),
      p.references.length ? h("ul", { class: "list-plain ai-refs" }, p.references.map((r) => h("li", null,
        h("label", { class: "checks" }, h("input", { type: "checkbox", checked: true, "data-ref": r.reference_id, onchange: (e) => { if (e.target.checked) excludeRefs.delete(r.reference_id); else excludeRefs.add(r.reference_id); load(); } }),
          ` ${r.caption} `, h("span", { class: "muted" }, r.media_type))))) : h("p", { class: "muted" }, "No references near this task."),
      h("p", { class: "muted small-note" }, p.what_is_not_sent),
      p.known_questions.length ? h("p", { class: "muted small-note" }, `${plural(p.known_questions.length, "open question")} will be included so it is not asked again.`) : null,
      h("details", null, h("summary", null, "Show the exact request"), h("pre", { class: "rv-pre ai-json" }, JSON.stringify(p.request, null, 2))));
    const send = h("button", { class: "btn primary", type: "button", disabled: off || p.problems.length > 0, onclick: start }, icon("check"), purpose === "description" ? "Draft description" : "Send and generate drafts");
    ui.buttons(h("span", { class: "grow muted small-note" }, `${settings.provider === "fake" ? "Scripted test provider" : "Codex CLI"}${settings.model ? ` · ${settings.model}` : ""}`),
      h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Cancel"), send);
  }

  async function start(e) {
    e.currentTarget.disabled = true;
    try {
      const run = await api.post(`/api/documents/${docId}/ai/runs`, { candidate_ids: candidateIds, exclude_block_ids: [...excludeBlocks], exclude_reference_ids: [...excludeRefs], purpose, ...(retryOf ? { retry_of: retryOf } : {}) });
      ui.close();
      watchRun(run.id, { pid, docId, title: run.titles.join(", ") });
      if (onStarted) onStarted(run);
      openProgress(run.id, { pid, docId });
    } catch (err) { ui.error(err.message); e.currentTarget.disabled = false; }
  }
  load();
}

// ---- runs belong to their own project, whatever is on screen ------------------------------

const watchers = new Map();
const listeners = new Set();
export function onAiRunChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export function watchRun(runId, { pid, docId, title }) {
  if (watchers.has(runId)) return;
  const w = { pid, docId, title, timer: null };
  watchers.set(runId, w);
  const tick = async () => {
    try {
      const run = await api.get(`/api/ai/runs/${runId}`);
      listeners.forEach((fn) => fn(run));
      if (run.state === "queued" || run.state === "running") { w.timer = setTimeout(tick, 600); return; }
      watchers.delete(runId);
      finished(run, w);
    } catch { w.timer = setTimeout(tick, 2000); }
  };
  tick();
}

function onThisDocument(w) {
  return state.projectId === w.pid && state.view === "import" && state.docId === w.docId;
}

function finished(run, w) {
  announce(`AI run ${run.state}`);
  const open = () => go(`#/p/${w.pid}/import/${w.docId}?run=${run.id}`);
  const where = onThisDocument(w) ? "" : " in the project where you started it";
  if (run.state === "succeeded") toast(`AI drafts ready${where}: ${plural(run.task_drafts, "task")}, ${plural(run.subtask_drafts, "subtask")}.`, { action: { label: "Review drafts", run: open }, ms: 20000 });
  else if (run.state === "failed") toast(`${run.error_message}${where}`, { kind: "error", action: { label: "Details", run: open } });
  else if (run.state === "cancelled") toast("AI draft cancelled. Nothing was changed.");
}

/** Waiting dialog: can be closed without stopping the run, and can cancel it. */
export function openProgress(runId, { pid, docId }) {
  const ui = dialog("Waiting for the AI");
  let done = false;
  const status = h("p", { role: "status", "aria-live": "polite" }, "Sending the selected text and waiting for a reply. This usually takes under a minute.");
  ui.set(status, h("p", { class: "muted small-note" }, "You can close this window. The run keeps going and belongs to this project; you will be told when it finishes. Nothing in your project changes until you import drafts."));
  const cancel = h("button", { class: "btn", type: "button", onclick: async () => { try { await api.post(`/api/ai/runs/${runId}/cancel`); replace(status, "Cancelling…"); } catch (e) { ui.error(e.message); } } }, "Cancel the request");
  ui.buttons(cancel, h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Close and keep waiting"));
  const off = onAiRunChange((run) => {
    if (run.id !== runId || done || run.state === "queued" || run.state === "running") return;
    done = true;
    off();
    ui.close();
    if (onThisDocument({ pid, docId })) openResults(runId, { pid, docId });
  });
  ui.dlg.addEventListener("close", off);
}

// ---- results: choose what to import ------------------------------------------------------

export async function openResults(runId, { pid, docId, onImported }) {
  const ui = dialog("AI drafts", { wide: true });
  ui.set(h("p", { class: "muted", role: "status" }, "Loading…"));
  let run;
  try { run = await api.get(`/api/ai/runs/${runId}`); } catch (e) { ui.set(h("div", { class: "banner", role: "alert" }, e.message)); ui.buttons(h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Close")); return; }
  if (run.project_id !== pid) { ui.set(h("div", { class: "banner", role: "alert" }, "This AI run belongs to a different project.")); ui.buttons(h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Close")); return; }
  const provenance = h("p", { class: "muted small-note", "data-provenance": "" },
    `${run.provider === "fake" ? "Scripted test provider" : "Codex CLI"} · ${run.reported_model || run.requested_model || "default model"} · prompt ${run.prompt_version} · schema ${run.schema_version} · request ${run.input_hash.slice(0, 10)} · ${new Date(run.created_at).toLocaleString()}`);
  const retry = () => { ui.close(); openAiPreview({ pid, docId, candidateIds: run.candidate_ids, purpose: run.purpose, retryOf: run.id }); };
  if (run.state !== "succeeded") {
    ui.set(h("div", { class: "banner", role: "alert", "data-run-error": run.error_code }, run.state === "cancelled" ? "Cancelled. Nothing was changed." : run.error_message || "This run did not finish."),
      run.issues.filter((i) => i.level === "error").length ? h("div", null, h("h4", null, "What was wrong with the answer"), h("ul", { class: "list-plain" }, run.issues.filter((i) => i.level === "error").map((i) => h("li", null, i.text)))) : null,
      h("p", { class: "muted small-note" }, "Nothing in your project was changed."), provenance);
    ui.buttons(h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Close"), run.state !== "queued" && run.state !== "running" ? h("button", { class: "btn primary", type: "button", onclick: retry }, "Try again") : null);
    return;
  }
  const listing = await api.get(`/api/documents/${docId}/candidates`);
  const titleOf = new Map(listing.candidates.map((c) => [c.id, c.title]));
  const first = await api.post(`/api/ai/runs/${runId}/import`, { project_id: pid, confirm: false });
  const choice = new Map(first.plan.map((p) => [p.draft_id, {
    on: !p.already_imported, target: p.target.candidate_id || "new", description: p.description.apply,
    subs: new Set(p.subtasks.filter((s) => s.selected && !s.already_imported).map((s) => s.draft_id)),
  }]));
  const qchoice = new Set(first.questions.filter((q) => q.selected).map((q) => q.index));
  const targets = run.candidate_ids.filter((id) => titleOf.has(id));

  function draftCard(p) {
    const c = choice.get(p.draft_id);
    const d = run.drafts.find((x) => x.draft_id === p.draft_id);
    const flagged = p.issues.filter((i) => i.level === "warning");
    const target = h("select", { "aria-label": `Where to put “${p.title}”`, onchange: (e) => { c.target = e.target.value; } },
      h("option", { value: "new", selected: c.target === "new" }, "A new candidate"),
      targets.map((id) => h("option", { value: id, selected: c.target === id }, `Apply to “${titleOf.get(id)}”`)));
    const desc = p.description;
    return h("section", { class: `ai-draft ${c.on ? "" : "off"}`, "data-draft": p.draft_id },
      h("label", { class: "ai-draft-head" }, h("input", { type: "checkbox", checked: c.on, disabled: p.already_imported, "aria-label": `Import ${p.title}`, onchange: (e) => { c.on = e.target.checked; } }),
        h("b", null, p.title), p.already_imported ? h("span", { class: "badge status done" }, "Already imported") : null),
      flagged.map((i) => h("div", { class: "banner info small", role: "note", "data-flag": i.code }, icon("alert"), i.text)),
      h("div", { class: "field" }, h("span", { class: "label" }, "Goes to"), target),
      h("div", { class: "field" }, h("span", { class: "label" }, "Proposed description"), h("blockquote", { class: "rv-quote ai-desc" }, desc.proposed || "(none)"),
        desc.proposed ? h("label", { class: "checks" }, h("input", { type: "checkbox", checked: c.description, name: `desc-${p.draft_id}`, onchange: (e) => { c.description = e.target.checked; } }),
          desc.current ? ` Use it instead of the current description (the current one stays in history)` : " Use it as the description") : null,
        desc.current ? h("div", { class: "muted small-note" }, "Current description: ", h("q", null, desc.current.length > 160 ? `${desc.current.slice(0, 160)}…` : desc.current)) : null),
      p.proposals.length ? h("div", { class: "field" }, h("span", { class: "label" }, "Proposals, not decisions"),
        h("ul", { class: "list-plain" }, p.proposals.map((x) => h("li", { class: "muted" }, x.field === "primary_reference" ? `Main clip: ${(run.references || {})[x.value] || "a suggested reference"} (from its caption only)` : `${x.field}: ${x.value}`))),
        h("p", { class: "muted small-note" }, "These appear on the task as suggestions to accept or reject. Nothing is set for you, and no clip is chosen.")) : null,
      p.subtasks.length ? h("div", { class: "field" }, h("span", { class: "label" }, `Subtasks (${p.subtasks.length})`),
        h("ul", { class: "list-plain" }, p.subtasks.map((s) => h("li", null,
          h("label", { class: "checks" }, h("input", { type: "checkbox", checked: c.subs.has(s.draft_id), disabled: s.already_imported || s.action === "skipped_nested", name: `sub-${s.draft_id}`,
            onchange: (e) => { if (e.target.checked) c.subs.add(s.draft_id); else c.subs.delete(s.draft_id); } }), " ", h("b", null, s.title)),
          " ", s.basis === "inferred" ? h("span", { class: "badge sug", title: "Not stated in the source" }, "inferred") : h("span", { class: "badge cat" }, "from the source"),
          " ", h("span", { class: "muted small-note" }, { create: "will be added", update_existing: "matches an existing subtask: only its description is used", exists: "already exists, skipped", skipped_nested: "a subtask cannot contain subtasks" }[s.action] || ""),
          s.description ? h("div", { class: "muted small-note ai-sub-desc" }, s.description) : null)))) : null,
      h("details", null, h("summary", null, `Evidence cited (${d.evidence.length})`), h("ul", { class: "list-plain" }, d.evidence.map((b) => h("li", null, h("blockquote", { class: "rv-quote" }, b.text))))),
      d.payload.classification_reason ? h("p", { class: "muted small-note" }, "Why: ", d.payload.classification_reason) : null);
  }

  ui.set(
    h("p", null, h("b", null, "Nothing has been imported. "), "Choose what to bring in as candidates for review. Nothing reaches the board, classifications stay proposals, and no clip is chosen for you."),
    first.plan.length ? first.plan.map(draftCard) : h("p", { class: "muted" }, "The AI proposed no tasks for this selection."),
    first.questions.length ? h("section", { class: "ai-draft" }, h("h4", null, "Open questions the AI found"), h("ul", { class: "list-plain" }, first.questions.map((q) => h("li", null,
      h("label", { class: "checks" }, h("input", { type: "checkbox", checked: qchoice.has(q.index), name: `q-${q.index}`, onchange: (e) => { if (e.target.checked) qchoice.add(q.index); else qchoice.delete(q.index); } }), " ", q.text))))) : null,
    run.issues.filter((i) => i.level === "info").length ? h("p", { class: "muted small-note" }, run.issues.filter((i) => i.level === "info").map((i) => i.text).join(" ")) : null,
    provenance);
  const importBtn = h("button", { class: "btn primary", type: "button", onclick: doImport }, "Import selected as candidates");
  ui.buttons(h("button", { class: "btn", type: "button", onclick: retry }, "Try again"), h("span", { class: "grow" }), h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Close"), importBtn);

  async function doImport() {
    importBtn.disabled = true;
    const body = {
      project_id: pid, confirm: true,
      drafts: first.plan.filter((p) => choice.get(p.draft_id).on).map((p) => {
        const c = choice.get(p.draft_id);
        return { draft_id: p.draft_id, target: c.target, description: c.description, subtasks: [...c.subs] };
      }),
      questions: [...qchoice],
    };
    try {
      const out = await api.post(`/api/ai/runs/${runId}/import`, body);
      ui.close();
      const bits = [];
      if (out.created.length) bits.push(`${plural(out.created.length, "candidate")} added`);
      if (out.updated.length) bits.push(`${plural(out.updated.length, "description")} applied`);
      if (out.proposals) bits.push(`${plural(out.proposals, "proposal")} to review`);
      if (out.questions) bits.push(`${plural(out.questions, "question")}`);
      toast(`${bits.join(", ") || "Nothing new to import"}. Review them before approving anything.`);
      if (onImported) onImported(out);
    } catch (e) { ui.error(e.message); importBtn.disabled = false; }
  }
}

/** The runs recorded for this document, newest first, with a way back into each one. */
export async function openRunList({ pid, docId, onImported }) {
  const ui = dialog("AI drafts for this document", { wide: true });
  ui.set(h("p", { class: "muted", role: "status" }, "Loading…"));
  try {
    const { runs } = await api.get(`/api/documents/${docId}/ai/runs`);
    ui.set(runs.length ? h("ul", { class: "list-plain ai-runs" }, runs.map((r) => h("li", { "data-run": r.id },
      h("div", null, h("b", null, r.titles.join(", ") || "Selection"), " ", h("span", { class: `badge ${r.state === "succeeded" ? "status done" : r.state === "failed" ? "blocked" : "cat"}` }, r.state_text)),
      h("div", { class: "muted small-note" }, `${new Date(r.created_at).toLocaleString()} · ${plural(r.task_drafts, "task")}, ${plural(r.subtask_drafts, "subtask")}${r.imported ? ` · ${r.imported} imported` : ""}${r.error_message && r.state !== "succeeded" ? ` · ${r.error_message}` : ""}`),
      h("div", { class: "actions-row" }, h("button", { class: "btn small", type: "button", onclick: () => { ui.close(); openResults(r.id, { pid, docId, onImported }); } }, r.state === "succeeded" ? "Review drafts" : "Details"),
        r.state === "running" || r.state === "queued" ? h("button", { class: "btn small", type: "button", onclick: () => { ui.close(); openProgress(r.id, { pid, docId }); } }, "Show progress") : null))))
      : h("p", { class: "muted" }, "No AI drafts yet. Select a task and choose “Draft with AI”."));
  } catch (e) { ui.set(h("div", { class: "banner", role: "alert" }, e.message)); }
  ui.buttons(h("button", { class: "btn", type: "button", onclick: () => ui.close() }, "Close"));
}

export { formDialog };
