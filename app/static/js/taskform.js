// Create-task dialog and shared form controls.
import { api } from "./api.js";
import { h, replace } from "./dom.js";
import { go, loadView, state } from "./store.js";
import { fieldLabel, formDialog, toast } from "./ui.js";

export const LEVEL_RANK = { epic: 0, task: 1, subtask: 2 };

export function selectEl(name, options, value, { blank = null, onchange = null } = {}) {
  const sel = h("select", { name, onchange },
    blank !== null ? h("option", { value: "" }, blank) : null,
    options.map((o) => h("option", { value: o.value, disabled: !!o.disabled }, o.label)));
  sel.value = value === null || value === undefined ? "" : value;
  return sel;
}

export function disciplineChecks(selected, onchange) {
  return h("div", { class: "checks", role: "group", "aria-label": "Disciplines" },
    state.meta.disciplines.map((d) =>
      h("label", null, h("input", { type: "checkbox", name: `disc-${d.value}`, value: d.value, checked: selected.includes(d.value), onchange }), d.label)));
}
export function readDisciplines(root) {
  return [...root.querySelectorAll('input[name^="disc-"]:checked')].map((i) => i.value);
}

export function parseTags(text) {
  return text.split(",").map((t) => t.trim()).filter(Boolean);
}

/** Tasks that may legally parent a task of `level` (child level must rank below parent's). */
export function eligibleParents(tasks, level, selfId) {
  return tasks.filter((t) => t.id !== selfId && LEVEL_RANK[t.level] < LEVEL_RANK[level]);
}
export function parentOptionLabel(t) {
  const title = t.title.length > 60 ? `${t.title.slice(0, 57)}…` : t.title;
  return `${t.key} ${title} (${t.level})`;
}

export async function openCreateTask({ parentId = null, level = null, status = "backlog" } = {}) {
  if (!state.projectId) return;
  const all = (await api.get(`/api/projects/${state.projectId}/tasks`)).tasks;
  const initialLevel = level || (parentId ? "subtask" : "task");
  let created = null;

  await formDialog({
    title: "Create task",
    submitLabel: "Create task",
    wide: true,
    body: (dlg) => {
      const title = h("input", { type: "text", name: "title", required: true, maxlength: "300", placeholder: "What needs to be done?" });
      const levelSel = selectEl("level", state.meta.levels, initialLevel);
      const parentSel = selectEl("parent_id", [], parentId, { blank: "No parent (top level)" });
      parentSel.id = "create-parent";
      const parentHint = h("div", { class: "hint" });
      const fillParents = () => {
        const lv = levelSel.value;
        const opts = eligibleParents(all, lv, null);
        const keep = parentSel.value;
        replace(parentSel, h("option", { value: "" }, lv === "subtask" ? "Choose a parent…" : "No parent (top level)"),
          opts.map((t) => h("option", { value: t.id }, parentOptionLabel(t))));
        parentSel.value = opts.some((t) => t.id === keep) ? keep : "";
        parentSel.disabled = lv === "epic";
        parentHint.textContent = lv === "epic" ? "Epics are always top level." : lv === "subtask" ? "A subtask must sit under a task or epic." : "A task may sit under an epic.";
      };
      levelSel.addEventListener("change", fillParents);
      fillParents();
      if (parentId) parentSel.value = parentId;

      const desc = h("textarea", { name: "description", rows: "4", maxlength: "20000", placeholder: "Optional. Plain text." });
      const disc = disciplineChecks([]);
      const cat = selectEl("category", state.meta.categories, "unassigned");
      const impact = selectEl("impact", state.meta.impacts, "unassessed");
      const urgency = selectEl("urgency", state.meta.urgencies, "unspecified");
      const stat = selectEl("status", state.meta.statuses, status);
      const assignee = selectEl("assignee_id", state.people.map((p) => ({ value: p.id, label: p.name })), "", { blank: "Unassigned" });
      const due = h("input", { type: "date", name: "due_at" });
      const tags = h("input", { type: "text", name: "tags", placeholder: "comma, separated" });

      dlg.collect = () => ({
        title: title.value,
        level: levelSel.value,
        parent_id: parentSel.value || null,
        description: desc.value,
        disciplines: readDisciplines(disc),
        category: cat.value,
        impact: impact.value,
        urgency: urgency.value,
        status: stat.value,
        assignee_id: assignee.value || null,
        due_at: due.value || null,
        tags: parseTags(tags.value),
      });

      return [
        fieldLabel("Title", title),
        h("div", { class: "grid-2" }, fieldLabel("Type", levelSel), h("div", { class: "field" }, h("label", { for: "create-parent" }, "Parent"), parentSel, parentHint)),
        fieldLabel("Description", desc),
        h("div", { class: "field" }, h("span", { class: "label" }, "Disciplines ", h("span", { class: "origin" }, "none checked = unassigned")), disc),
        h("div", { class: "grid-2" }, fieldLabel("Category", cat), fieldLabel("Status", stat)),
        h("div", { class: "grid-2" }, fieldLabel("Impact", impact), fieldLabel("Urgency", urgency)),
        h("div", { class: "hint" }, "Priority is suggested from impact and urgency. Leave them unassessed if you are unsure."),
        h("div", { class: "grid-2" }, fieldLabel("Assignee", assignee), fieldLabel("Due date", due)),
        fieldLabel("Tags", tags),
      ];
    },
    onSubmit: async (dlg) => {
      created = await api.post(`/api/projects/${state.projectId}/tasks`, dlg.collect());
    },
  });

  if (created) {
    await loadView();
    toast(`Created ${created.key}`);
    document.dispatchEvent(new CustomEvent("organizer:rerender"));
    go({ taskId: created.id });
  }
  return created;
}
