// Task details panel: editable properties, criteria, subtasks, dependencies, history.
//
// Persistence model: every control saves as soon as it changes (selects, checkboxes)
// or when it loses focus (text). Unsaved or failed edits live in `drafts`, so a
// re-render never discards what the user typed, and a failed save stays visible
// with Retry / Discard until resolved.
import { api, clearFailure } from "./api.js";
import { patchTask, reportFailure } from "./actions.js";
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { go, labelOf, state, statusLabel } from "./store.js";
import { disciplineChecks, eligibleParents, parentOptionLabel, parseTags, readDisciplines, selectEl } from "./taskform.js";
import { confirmDialog, fieldLabel, openMenu, toast } from "./ui.js";
import { AUTO_LOAD_LIMIT, assetUrl, openMediaViewer, typeLabel } from "./media.js";
import { openRangeDialog, thumbFor } from "./clip.js";
import { clarificationPanel, primaryCard, ROLE_OPTIONS, roleLabel } from "./taskview.js";
import { fieldLabel as _fl, formDialog } from "./ui.js";
import { openCreateTask } from "./taskform.js";

let root = null;
let task = null; // last saved server copy
let all = []; // every approved task in the project (parent / dependency pickers)
let drafts = {}; // field -> value not yet confirmed saved
let failure = null; // {patch, message}
let loadToken = 0;

const emitData = () => document.dispatchEvent(new CustomEvent("organizer:data"));

export function closeDetails() {
  task = null;
  drafts = {};
  failure = null;
}

/** True while an edit failed to save and has not been retried or discarded. */
export function hasUnsaved() {
  return !!(task && failure);
}

export async function showDetails(el, taskId) {
  root = el;
  const token = ++loadToken;
  if (!task || task.id !== taskId) {
    drafts = {};
    failure = null;
    replace(root, h("div", { class: "details-body" }, h("p", { class: "muted" }, "Loading…")));
  }
  try {
    const [t, list] = await Promise.all([api.get(`/api/tasks/${taskId}`), api.get(`/api/projects/${state.projectId}/tasks`)]);
    if (token !== loadToken) return;
    task = t;
    all = list.tasks;
    render();
  } catch (err) {
    if (token !== loadToken) return;
    task = null;
    replace(root, h("div", { class: "details-head" }, h("span", { class: "grow" }), h("button", { class: "icon-btn", type: "button", "aria-label": "Close details", onclick: () => go({}) }, icon("close"))),
      h("div", { class: "details-body" }, h("div", { class: "form-error", role: "alert" }, err.status === 404 ? "This task no longer exists." : err.message)));
  }
}

// ---- saving ----------------------------------------------------------------------

const val = (field) => (field in drafts ? drafts[field] : task[field]);

async function save(patch) {
  const mine = task;
  Object.assign(drafts, patch);
  failure = null;
  try {
    const out = await patchTask(mine, patch);
    if (task !== mine) {
      // The user opened another task while this save was in flight; do not touch its panel.
      emitData();
      return;
    }
    if (out) {
      task = out;
      for (const k of Object.keys(patch)) delete drafts[k];
      clearFailure();
      emitData();
    } else {
      for (const k of Object.keys(patch)) delete drafts[k]; // user cancelled the decision
    }
  } catch (err) {
    if (task !== mine) {
      reportFailure(err, `Could not save ${mine.key}`);
      return;
    }
    failure = { patch, message: err.message };
  }
  render();
}

function onText(field, el, { multiline = false } = {}) {
  el.addEventListener("input", () => { drafts[field] = el.value; });
  const commit = () => {
    if (!(field in drafts)) return;
    const v = drafts[field];
    if (v === (task[field] ?? "")) { delete drafts[field]; return; }
    save({ [field]: v });
  };
  el.addEventListener("blur", commit);
  if (!multiline) el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.blur(); } });
  return el;
}

function snapshotFocus() {
  const a = document.activeElement;
  if (!a || !root || !root.contains(a) || !a.name) return null;
  return { name: a.name, start: a.selectionStart, end: a.selectionEnd };
}
function restoreFocus(snap) {
  if (!snap) return;
  const el = root.querySelector(`[name="${CSS.escape(snap.name)}"]`);
  if (!el) return;
  el.focus();
  try { if (snap.start !== null && snap.start !== undefined) el.setSelectionRange(snap.start, snap.end); } catch { /* not text */ }
}

// ---- rendering -------------------------------------------------------------------

function origin(field) {
  const o = task.field_origins[field];
  const text = { manual: "Manually set", rule: "Accepted rule suggestion", source_extraction: "From the source", template: "From a template" }[o];
  return h("span", { class: `origin ${o === "manual" ? "manual" : ""}` }, text || (o ? `Set by ${o.replace("_", " ")}` : "Not set"));
}

function render() {
  if (!task || !root) return;
  const snap = snapshotFocus();
  const scroller = root.querySelector(".details-body");
  const scroll = scroller ? scroller.scrollTop : 0;

  const head = h("div", { class: "details-head" },
    h("span", { class: "key" }, task.key),
    h("span", { class: `badge level ${task.level}` }, task.level),
    h("span", { class: "badge origin", title: "How this task was created" }, task.provenance === "manual" ? "Manually added" : task.provenance === "source_extraction" ? "From a source document" : task.provenance.replace("_", " ")),
    h("span", { class: "grow" }),
    h("button", {
      class: "icon-btn", type: "button", "aria-label": "More actions", "aria-haspopup": "menu", "aria-expanded": "false",
      onclick: (e) => openMenu(e.currentTarget, [{ label: "Delete task…", danger: true, onSelect: deleteTask }], { align: "right" }),
    }, icon("more")),
    h("button", { class: "icon-btn", type: "button", "aria-label": "Close details", onclick: () => go({}) }, icon("close")));

  const body = h("div", { class: "details-body" });
  if (failure) {
    body.append(h("div", { class: "pending-bar", role: "alert" },
      icon("alert"),
      h("span", { class: "grow" }, `Not saved: ${failure.message}`),
      h("button", { class: "btn small", type: "button", onclick: () => save(failure.patch) }, "Retry"),
      h("button", { class: "btn small", type: "button", onclick: () => { for (const k of Object.keys(failure.patch)) delete drafts[k]; failure = null; render(); } }, "Discard change")));
  }
  if (task.parent) {
    body.append(h("div", { class: "crumbs" }, "In ", h("button", { type: "button", onclick: () => go({ taskId: task.parent.id }) }, `${task.parent.key} ${task.parent.title}`)));
  }

  const title = h("textarea", { class: "title-input", name: "title", rows: "1", maxlength: "300", "aria-label": "Title" });
  title.value = val("title");
  onText("title", title);
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } });
  title.addEventListener("input", autosize);
  body.append(title);
  requestAnimationFrame(() => autosize.call(title));

  const desc = h("textarea", { name: "description", rows: "5", placeholder: "Add a description…", "aria-label": "Description" });
  desc.value = val("description");
  onText("description", desc, { multiline: true });
  body.append(fieldLabel("Description", desc, h("span", { class: "origin" }, "plain text")));
  body.append(taskFirstBlock());

  body.append(propsGrid(), priorityBox());
  body.append(criteriaSection(), subtasksSection(), dependenciesSection(), referencesSection(), historySection());

  replace(root, head, body);
  body.scrollTop = scroll;
  restoreFocus(snap);
}

function autosize() {
  this.style.height = "auto";
  this.style.height = `${this.scrollHeight + 2}px`;
}

function propsGrid() {
  const g = h("div", { class: "props" });

  // Status (guarded for unfinished children by patchTask)
  const status = selectEl("status", state.meta.statuses, val("status"), { onchange: (e) => save({ status: e.target.value }) });
  g.append(fieldLabel("Status", status, h("span", { class: "origin" }, "production status")));

  // Blocked is independent of status.
  const blocked = h("input", { type: "checkbox", name: "is_blocked", checked: !!val("is_blocked"), onchange: (e) => save(e.target.checked ? { is_blocked: true } : { is_blocked: false, blocked_reason: "" }) });
  const reason = h("input", { type: "text", name: "blocked_reason", maxlength: "1000", placeholder: "Why is it blocked?", "aria-label": "Blocked reason", disabled: !val("is_blocked") });
  reason.value = val("blocked_reason");
  onText("blocked_reason", reason);
  g.append(h("div", { class: "field" }, h("span", { class: "label" }, "Blocked ", h("span", { class: "origin" }, "independent of status")),
    h("div", { class: "checks" }, h("label", null, blocked, "Blocked")), reason));

  // Type and parent (reparent)
  const level = selectEl("level", state.meta.levels, val("level"), {
    onchange: (e) => save({ level: e.target.value, parent_id: e.target.value === "epic" ? null : val("parent_id") }),
  });
  g.append(fieldLabel("Type", level));
  const parents = eligibleParents(all, val("level"), task.id);
  const parentSel = selectEl("parent_id", parents.map((t) => ({ value: t.id, label: parentOptionLabel(t) })), val("parent_id"),
    { blank: val("level") === "subtask" ? "Choose a parent…" : "No parent (top level)", onchange: (e) => save({ parent_id: e.target.value || null, level: val("level") }) });
  parentSel.disabled = val("level") === "epic";
  if (val("parent_id") && !parents.some((t) => t.id === val("parent_id"))) {
    parentSel.append(h("option", { value: val("parent_id") }, "(current parent: no longer valid for this type)"));
    parentSel.value = val("parent_id");
  }
  g.append(fieldLabel("Parent", parentSel));

  const disc = disciplineChecks(val("disciplines"), () => save({ disciplines: readDisciplines(g) }));
  g.append(h("div", { class: "field wide" }, h("span", { class: "label" }, "Disciplines ", origin("disciplines"), h("span", { class: "origin" }, " none checked = unassigned")), disc));

  g.append(fieldLabel("Category", selectEl("category", state.meta.categories, val("category"), { onchange: (e) => save({ category: e.target.value }) }), origin("category")));
  g.append(fieldLabel("Impact", selectEl("impact", state.meta.impacts, val("impact"), { onchange: (e) => save({ impact: e.target.value }) }), origin("impact")));
  g.append(fieldLabel("Urgency", selectEl("urgency", state.meta.urgencies, val("urgency"), { onchange: (e) => save({ urgency: e.target.value }) }), origin("urgency")));

  const assignee = selectEl("assignee_id", state.people.map((p) => ({ value: p.id, label: p.name })), val("assignee_id"), { blank: "Unassigned", onchange: (e) => save({ assignee_id: e.target.value || null }) });
  g.append(fieldLabel("Assignee", assignee, origin("assignee_id")));

  const dueVal = val("due_at");
  const due = h("input", { type: "date", name: "due_at", "aria-describedby": "due-note" });
  due.value = dueVal ? String(dueVal).slice(0, 10) : "";
  due.addEventListener("change", () => save({ due_at: due.value || null }));
  g.append(h("div", { class: "field" }, h("label", { for: (due.id = "due-input") }, "Due date", origin("due_at")), due,
    dueVal && String(dueVal).length > 10 ? h("div", { class: "hint", id: "due-note" }, `Stored with a time: ${dueVal}. Changing the date makes it date-only.`) : null));

  const tags = h("input", { type: "text", name: "tags", placeholder: "comma, separated", "aria-label": "Tags" });
  tags.value = "tags" in drafts ? drafts.tags : task.tags.join(", ");
  tags.addEventListener("input", () => { drafts.tags = tags.value; });
  const commitTags = () => {
    if (!("tags" in drafts)) return;
    const list = parseTags(drafts.tags);
    if (list.join("\u0000").toLowerCase() === task.tags.join("\u0000").toLowerCase()) { delete drafts.tags; return; }
    save({ tags: list });
  };
  tags.addEventListener("blur", commitTags);
  tags.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); tags.blur(); } });
  g.append(h("div", { class: "field wide" }, h("label", { for: (tags.id = "tags-input") }, "Tags", origin("tags")), tags));
  return g;
}

function priorityBox() {
  const eff = task.priority;
  const label = eff ? h("span", { class: `badge ${eff}` }, eff.toUpperCase()) : h("span", { class: "badge na" }, "Not assessed");
  const sourceText = task.priority_source === "overridden" ? "Overridden by you" : task.priority_source === "suggested" ? "Suggested by the default policy" : "No suggestion: impact is unassessed";
  const ovr = selectEl("priority_override", state.meta.priorities, val("priority_override"), {
    blank: "Use suggestion", onchange: (e) => save({ priority_override: e.target.value || null }),
  });
  const reason = h("input", { type: "text", name: "priority_override_reason", maxlength: "1000", placeholder: "Why override?", "aria-label": "Override reason", disabled: !val("priority_override") });
  reason.value = val("priority_override_reason");
  onText("priority_override_reason", reason);
  return h("div", { class: "prio-box" },
    h("div", { class: "now" }, "Priority: ", label, h("span", { class: "muted" }, sourceText)),
    h("div", { class: "why" }, task.priority_reason || "No rationale recorded."),
    task.priority_source === "overridden" && task.suggested_priority ? h("div", { class: "why" }, `Policy would suggest ${task.suggested_priority.toUpperCase()}.`) : null,
    h("div", { class: "grid-2" },
      fieldLabel("Override priority", ovr, h("span", { class: "origin" }, "P0 is manual only")),
      fieldLabel("Override reason", reason)));
}

// ---- sections --------------------------------------------------------------------

function section(title, count, open, ...content) {
  return h("details", { class: "sec", open }, h("summary", null, title, count !== null ? h("span", { class: "muted" }, count) : null), ...content);
}

async function mutate(fn) {
  const mine = task;
  try {
    await fn();
    const [fresh, list] = await Promise.all([api.get(`/api/tasks/${mine.id}`), api.get(`/api/projects/${state.projectId}/tasks`)]);
    clearFailure();
    emitData();
    if (task !== mine) return;
    task = fresh;
    all = list.tasks;
  } catch (err) {
    toast(err.message, { kind: "error" });
  }
  if (task) render();
}

function criteriaSection() {
  const done = task.criteria.filter((c) => c.checked).length;
  const list = h("ul", { class: "list-plain" });
  for (const c of task.criteria) {
    const text = h("button", { class: "link ctext", type: "button", title: "Edit", onclick: () => editCriterion(c, li) }, c.text);
    const li = h("li", { class: `row crit ${c.checked ? "done" : ""}` },
      h("input", { type: "checkbox", checked: c.checked, "aria-label": `Done: ${c.text}`, onchange: (e) => mutate(() => api.patch(`/api/criteria/${c.id}`, { checked: e.target.checked })) }),
      h("span", { class: "grow" }, text),
      h("button", { class: "icon-btn", type: "button", "aria-label": `Delete criterion: ${c.text}`, onclick: () => mutate(() => api.del(`/api/criteria/${c.id}`)) }, icon("trash")));
    list.append(li);
  }
  const input = h("input", { type: "text", name: "new-criterion", placeholder: "Add acceptance criterion", maxlength: "1000", "aria-label": "New acceptance criterion" });
  const add = () => { const t = input.value.trim(); if (t) mutate(() => api.post(`/api/tasks/${task.id}/criteria`, { text: t })); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  return section("Acceptance criteria", task.criteria.length ? `${done}/${task.criteria.length}` : null, task.criteria.length > 0,
    list, h("div", { class: "row", style: null }, input, h("button", { class: "btn small", type: "button", onclick: add }, "Add")));
}

function editCriterion(c, li) {
  const input = h("input", { type: "text", name: "edit-criterion", value: c.text, maxlength: "1000", "aria-label": "Edit criterion" });
  const grow = li.querySelector(".grow");
  replace(grow, input);
  input.focus();
  const finish = (saveIt) => {
    const t = input.value.trim();
    if (saveIt && t && t !== c.text) mutate(() => api.patch(`/api/criteria/${c.id}`, { text: t }));
    else render();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); finish(true); } if (e.key === "Escape") { e.preventDefault(); finish(false); } });
  input.addEventListener("blur", () => finish(true));
}

function briefRow(t, extra) {
  return h("li", { class: "row" },
    h("span", { class: "grow" }, h("button", { class: "link", type: "button", onclick: () => go({ taskId: t.id }) }, h("b", null, t.key), " ", t.title)),
    h("span", { class: `badge status ${t.status}` }, statusLabel(t.status)),
    t.is_blocked ? h("span", { class: "badge blocked" }, "Blocked") : null, extra);
}

function subtasksSection() {
  const done = task.children.filter((c) => c.status === "done").length;
  const canAdd = task.level !== "subtask";
  const childLevel = task.level === "epic" ? "task" : "subtask";
  const list = h("ul", { class: "list-plain" }, task.children.map((c) => briefRow(c)));
  return section(task.level === "epic" ? "Child tasks" : "Subtasks", task.children.length ? `${done}/${task.children.length} done` : null, task.children.length > 0,
    task.children.length ? list : h("p", { class: "muted" }, "None yet."),
    task.children.length && task.status === "done" && done < task.children.length
      ? h("p", { class: "form-error" }, `This task is Done but ${task.children.length - done} child task(s) are unfinished. They stay as they are until you update them.`) : null,
    canAdd ? h("div", null, h("button", { class: "btn small", type: "button", onclick: () => openCreateTask({ parentId: task.id, level: childLevel }) }, icon("plus"), `Add ${childLevel}`)) : null);
}

function dependenciesSection() {
  const have = new Set(task.depends_on.map((d) => d.id));
  const candidates = all.filter((t) => t.id !== task.id && !have.has(t.id));
  const sel = selectEl("new-dependency", candidates.map((t) => ({ value: t.id, label: `${t.key} ${t.title.slice(0, 50)}` })), "", { blank: "Add a dependency…" });
  const errBox = h("div", { class: "err", role: "alert" });
  const add = () => {
    if (!sel.value) return;
    api.post(`/api/tasks/${task.id}/dependencies`, { depends_on_id: sel.value })
      .then(async () => mutate(async () => {}))
      .catch((e) => replace(errBox, e.message));
  };
  const waiting = task.depends_on.filter((d) => d.status !== "done");
  return section("Dependencies", task.depends_on.length || task.dependents.length ? `${task.depends_on.length} on · ${task.dependents.length} blocking` : null, task.depends_on.length + task.dependents.length > 0,
    h("div", { class: "field" }, h("span", { class: "label" }, "Depends on"),
      task.depends_on.length ? h("ul", { class: "list-plain" }, task.depends_on.map((d) =>
        briefRow(d, h("button", { class: "icon-btn", type: "button", "aria-label": `Remove dependency on ${d.key}`, onclick: () => mutate(() => api.del(`/api/tasks/${task.id}/dependencies/${d.id}`)) }, icon("close")))))
        : h("span", { class: "muted" }, "None"),
      waiting.length ? h("div", { class: "hint" }, `Waiting on ${waiting.length} unfinished task(s). This does not set Blocked; you control that flag.`) : null),
    h("div", { class: "field" }, h("span", { class: "label" }, "Required by"),
      task.dependents.length ? h("ul", { class: "list-plain" }, task.dependents.map((d) => briefRow(d))) : h("span", { class: "muted" }, "None")),
    h("div", { class: "row" }, sel, h("button", { class: "btn small", type: "button", onclick: add }, "Add")), errBox);
}

let evidenceCache = { id: null, data: null };

function evidenceFor(id) {
  if (evidenceCache.id === id && evidenceCache.data) return Promise.resolve(evidenceCache.data);
  return api.get(`/api/tasks/${id}/evidence`).then((ev) => { evidenceCache = { id, data: ev }; return ev; });
}

function setEvidence(ev) {
  evidenceCache = { id: task.id, data: ev };
  render();
}

/** What a person wants first: any open question, then the one main clip. Everything else is below, collapsed. */
function taskFirstBlock() {
  const box = h("div", { class: "tf-top", "data-task-first": "" });
  const id = task.id;
  evidenceFor(id).then((ev) => {
    if (!task || task.id !== id) return;
    fillTop(box, ev);
  }).catch(() => {});
  return box;
}

function fillTop(box, ev) {
  const out = [];
  const hasSource = ev.sources.length || ev.references.length || ev.clarifications.length;
  if (!hasSource) return replace(box);
  const docId = ev.document_id || (ev.sources[0] && ev.sources[0].document_id);
  const q = clarificationPanel(ev.clarifications, {
    canAdd: false, ownTitle: task.title,
    onResolve: async (c) => {
      let done = false;
      await formDialog({ title: "Mark resolved", submitLabel: "Mark resolved",
        body: () => [h("p", null, c.text), _fl("What was decided? (optional)", h("textarea", { name: "resolution", rows: "3", maxlength: "1000" }))],
        onSubmit: async (dlg) => { const r = await api.patch(`/api/clarifications/${c.id}`, { state: "resolved", resolution: dlg.dialog.querySelector('[name="resolution"]').value }); done = !!r; } });
      if (done) setEvidence(await api.get(`/api/tasks/${task.id}/evidence`));
    },
    onReopen: async (c) => { await api.patch(`/api/clarifications/${c.id}`, { state: "open" }); setEvidence(await api.get(`/api/tasks/${task.id}/evidence`)); },
    onShow: (b) => go(`#/p/${state.projectId}/sources/${docId}?tab=content&seq=${b.sequence}`),
  });
  if (q && ev.clarifications.length) out.push(q);
  if (ev.references.length || ev.primary || ev.reference_decision === "none") out.push(refBlock(ev));
  replace(box, ...out);
}

function refBlock(ev) {
  const base = `/api/tasks/${task.id}`;
  const wrap = h("div", { class: "tf-ref", "data-ref-block": "" });
  const doit = async (fn, message) => { try { setEvidence(await fn()); if (message) toast(message); } catch (e) { toast(e.message, { kind: "error" }); } };
  const rename = async (ref) => {
    let value = null;
    await formDialog({ title: "Rename this clip", submitLabel: "Save",
      body: () => [h("p", { class: "muted" }, "A friendly name for the task view. The original file name and file are not changed."), _fl("Name", h("input", { type: "text", name: "caption", maxlength: "200", value: ref.caption || ref.label }))],
      onSubmit: async (dlg) => { value = dlg.dialog.querySelector('[name="caption"]').value; } });
    if (value !== null) doit(() => api.patch(`${base}/references/${ref.reference_id}`, { caption: value }));
  };
  if (ev.primary) {
    const ref = ev.primary;
    wrap.append(primaryCard(ref, {
      locked: false,
      onRename: () => rename(ref),
      onRange: () => openRangeDialog(ref, async (body) => { setEvidence(await api.patch(`${base}/references/${ref.reference_id}`, body)); }),
      onChange: ev.references.length > 1 ? () => chooseMain(ev) : null,
      onClear: () => doit(() => api.del(`${base}/primary-reference`), "Main clip cleared. The file stays attached."),
      onPreview: () => openMediaViewer({ items: [{ asset: ref.asset, used_in: [] }], index: 0 }),
    }));
  } else if (ev.reference_decision === "none") {
    wrap.append(h("h4", null, "Main reference"), h("p", { class: "muted" }, "No reference needed. This was decided during review."),
      h("button", { class: "btn small", type: "button", onclick: () => doit(() => api.post(`${base}/reference-decision`, { decision: "undecided" })) }, "Choose a reference instead"));
  } else {
    wrap.append(h("h4", null, "Main reference"), h("p", { class: "muted" }, ev.references.length ? "No main clip is chosen. Pick one of the attached references, or say none is needed." : "No reference is attached."),
      ev.references.length ? h("div", { class: "actions-row" }, h("button", { class: "btn small", type: "button", onclick: () => chooseMain(ev) }, "Choose main clip…"),
        h("button", { class: "btn subtle small", type: "button", onclick: () => doit(() => api.post(`${base}/reference-decision`, { decision: "none" }), "Recorded: no reference needed") }, "No reference needed")) : null);
  }
  return wrap;
}

async function chooseMain(ev) {
  const base = `/api/tasks/${task.id}`;
  await formDialog({
    title: "Choose the main reference", submitLabel: "Done", cancelLabel: "Close", wide: true,
    body: (dlg) => [h("p", { class: "muted" }, "Pick the one clip that best shows what to build. The others stay as supporting references."),
      h("div", { class: "tf-picker-list" }, ev.references.map((r) => h("div", { class: "tf-suggest" },
        thumbFor(r), h("div", { class: "tf-suggest-main" }, h("b", null, r.label), h("div", { class: "muted small-note" }, [r.section, r.asset.name].filter(Boolean).join(" · "))),
        h("div", { class: "actions-row" }, r.role === "primary_demo" ? h("span", { class: "badge status done" }, "Main clip")
          : h("button", { class: "btn small primary", type: "button", onclick: async () => { try { setEvidence(await api.post(`${base}/primary-reference`, { reference_id: r.reference_id })); dlg.close(true); toast("Main clip chosen"); } catch (e) { toast(e.message, { kind: "error" }); } } }, "Use as main clip")))))],
    onSubmit: async () => true,
  });
}

function referencesSection() {
  const box = h("div", { class: "evidence" }, h("p", { class: "muted", role: "status" }, "Loading…"));
  const id = task.id;
  evidenceFor(id).then((ev) => { if (task && task.id === id) replace(box, renderEvidence(ev)); })
    .catch((e) => replace(box, h("p", { class: "err", role: "alert" }, `Could not load the source details: ${e.message}`)));
  return h("details", { class: "sec", "data-sec": "evidence" }, h("summary", null, "Source, requirements and supporting references"), box);
}

function renderEvidence(ev) {
  if (!ev.sources.length && !ev.requirements.length && !ev.references.length) {
    return h("div", { class: "placeholder-box" }, "Added by hand: there is no source document behind this task. Tasks approved from an imported brief show their original text, requirements, scenarios, and media here.");
  }
  const out = [];
  const docId = ev.document_id || (ev.sources[0] && ev.sources[0].document_id);
  const link = (seq, label) => h("a", { class: "link", href: `#/p/${state.projectId}/sources/${docId}?tab=content&seq=${seq}` }, label);
  const own = ev.sources.filter((s) => ["label", "evidence", "scope", "context"].includes(s.role));
  if (own.length) {
    out.push(h("h4", null, "Original wording"));
    out.push(h("ul", { class: "list-plain ev-sources" }, own.slice(0, 12).map((s) => h("li", null,
      h("div", { class: "muted small-note" }, s.heading_path.join(" › "), " · ", s.role === "label" ? "label" : s.role === "scope" ? "scenario scope" : s.role === "context" ? "context" : "source text"),
      h("blockquote", { class: "rv-quote" }, s.text || "(empty)"), link(s.sequence, "Open in the source browser")))));
    if (own.length > 12) out.push(h("p", { class: "muted small-note" }, `${own.length - 12} more source blocks are linked.`));
  }
  if (ev.requirements.length) {
    out.push(h("h4", null, `Requirements (${ev.requirements.length})`));
    out.push(h("ul", { class: "list-plain ev-reqs" }, ev.requirements.map((r) => h("li", { class: r.kind },
      r.kind === "exclusion" ? h("span", { class: "badge blocked" }, "Exclusion") : null, " ", h("span", { class: "ev-text" }, r.text),
      h("div", { class: "ev-meta" }, ...r.values.map((v) => h("span", { class: "chip val" }, v)), ...r.scenarios.map((s) => h("span", { class: "chip" }, `Scenario ${s}`)),
        r.blocks[0] ? link(r.blocks[0].sequence, "source") : null)))));
  }
  if (ev.scenarios.length) {
    out.push(h("h4", null, `Scenarios (${ev.scenarios.length})`));
    out.push(h("div", { class: "chips-left" }, ev.scenarios.map((s) => h("span", { class: "chip", title: s.title || "" }, s.identifier, s.state === "proposed" ? " (proposed)" : ""))));
  }
  const supporting = ev.references.filter((r) => r.role !== "primary_demo");
  if (supporting.length) {
    const base = `/api/tasks/${task.id}`;
    out.push(h("h4", null, `Supporting references (${supporting.length})`));
    const items = supporting.map((r) => ({ asset: r.asset, used_in: [] }));
    out.push(h("ul", { class: "list-plain" }, supporting.map((r, i) => h("li", { class: "tf-ref-row", "data-ref": r.reference_id },
      h("button", { type: "button", class: "rv-thumb", "aria-label": `Preview ${r.label}`, onclick: () => openMediaViewer({ items, index: i }) }, thumbFor(r)),
      h("div", { class: "tf-suggest-main" }, h("b", null, r.label), h("div", { class: "muted small-note" }, [r.section, r.asset.name].filter(Boolean).join(" · "))),
      h("div", { class: "actions-row" },
        h("select", { "aria-label": `Role of ${r.label}`, onchange: async (e) => { try { setEvidence(await api.patch(`${base}/references/${r.reference_id}`, { role: e.target.value })); } catch (err) { toast(err.message, { kind: "error" }); } } },
          ROLE_OPTIONS.map(([v, l]) => h("option", { value: v, selected: r.role === v }, l))),
        h("button", { class: "btn small", type: "button", onclick: async () => { try { setEvidence(await api.post(`${base}/primary-reference`, { reference_id: r.reference_id })); toast("Main clip chosen"); } catch (err) { toast(err.message, { kind: "error" }); } } }, "Make main clip"))))));
  }
  return h("div", null, ...out);
}

const FIELD_NAMES = { parent_id: "parent", assignee_id: "assignee", is_blocked: "blocked", due_at: "due date", priority_override: "priority override", priority_override_reason: "override reason", dependency_added: "dependency added", dependency_removed: "dependency removed" };

function historySection() {
  const items = task.history;
  return section("History", items.length ? String(items.length) : null, false,
    h("ul", { class: "list-plain hist" }, items.map((e) => {
      const f = FIELD_NAMES[e.field] || e.field.replace("_", " ");
      let line;
      if (e.field === "created") line = `Created as ${e.new_value}`;
      else if (e.field === "description") line = "Description edited";
      else line = `${f}: ${show(e, e.old_value)} → ${show(e, e.new_value)}`;
      return h("li", null, h("span", null, h("b", null, line)), h("span", null, `${e.origin} · ${new Date(e.changed_at).toLocaleString()}`));
    })));
}

function show(e, v) {
  if (v === null || v === undefined || v === "") return "none";
  if (e.field === "status") return statusLabel(v);
  const map = { category: "categories", impact: "impacts", urgency: "urgencies", level: "levels" };
  if (map[e.field]) return labelOf(map[e.field], v);
  if (e.field === "assignee_id") return (state.people.find((p) => p.id === v) || {}).name || "someone";
  if (e.field === "parent_id" || e.field.startsWith("dependency")) { const t = all.find((x) => x.id === v); return t ? t.key : "a task"; }
  return v.length > 60 ? `${v.slice(0, 57)}…` : v;
}

async function deleteTask() {
  const ok = await confirmDialog({
    title: `Delete ${task.key}?`,
    message: `“${task.title}” will be permanently deleted with its history and criteria. The number ${task.key} will not be reused.`,
    confirmLabel: "Delete task", danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/api/tasks/${task.id}`);
    emitData();
    toast(`Deleted ${task.key}`);
    go({});
  } catch (err) {
    toast(err.message, { kind: "error" });
  }
}
