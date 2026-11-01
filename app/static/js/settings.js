// Project settings: details, local people, storage info, deletion.
import { api } from "./api.js";
import { clearFailure } from "./actions.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { go, loadPeople, loadProject, loadProjects, state } from "./store.js";
import { confirmDialog, fieldLabel, formDialog, toast } from "./ui.js";

export function renderSettings(rerender, dataDir) {
  const p = state.project;
  const name = h("input", { type: "text", name: "name", value: p.name, maxlength: "120" });
  const desc = h("textarea", { name: "description", rows: "3", maxlength: "5000" });
  desc.value = p.description;
  const err = h("div", { class: "err", role: "alert" });
  const saveProject = async () => {
    err.textContent = "";
    try {
      await api.patch(`/api/projects/${p.id}`, { name: name.value, description: desc.value });
      clearFailure();
      await Promise.all([loadProject(p.id), loadProjects()]);
      toast("Project saved");
      rerender({ header: true });
    } catch (e) { err.textContent = e.message; }
  };

  const peopleList = h("ul", { class: "list-plain people" });
  for (const person of state.people) {
    peopleList.append(h("li", null,
      h("span", { class: "grow" }, person.name, " ", h("span", { class: "muted" }, `${person.assigned_count} assigned`)),
      h("button", { class: "btn small", type: "button", onclick: () => renamePerson(person, rerender) }, "Rename"),
      h("button", { class: "icon-btn", type: "button", "aria-label": `Remove ${person.name}`, onclick: () => removePerson(person, rerender) }, icon("trash"))));
  }
  if (!state.people.length) peopleList.append(h("li", { class: "muted" }, "No people yet. Add someone to assign tasks."));
  const newPerson = h("input", { type: "text", name: "new-person", placeholder: "Add a person", maxlength: "80", "aria-label": "New person name" });
  const perr = h("div", { class: "err", role: "alert" });
  const addPerson = async () => {
    perr.textContent = "";
    if (!newPerson.value.trim()) return;
    try {
      await api.post(`/api/projects/${p.id}/people`, { name: newPerson.value });
      clearFailure();
      await loadPeople();
      rerender({ header: true });
    } catch (e) { perr.textContent = e.message; }
  };
  newPerson.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addPerson(); } });

  return h("div", { class: "settings" },
    h("section", { class: "panel" }, h("h2", null, "Project"),
      fieldLabel("Name", name), fieldLabel("Description", desc),
      h("dl", { class: "kv" },
        h("dt", null, "Ticket prefix"), h("dd", null, h("b", null, p.ticket_prefix), " (fixed)"),
        h("dt", null, "Next ticket"), h("dd", null, `${p.ticket_prefix}-${p.next_ticket_number}`),
        h("dt", null, "Tasks"), h("dd", null, String(p.task_count))),
      err,
      h("div", null, h("button", { class: "btn primary", type: "button", onclick: saveProject }, "Save project"))),
    h("section", { class: "panel" }, h("h2", null, "People"),
      h("p", { class: "muted" }, "Local names you can assign tasks to. These are labels, not accounts."),
      peopleList, h("div", { class: "row" }, newPerson, h("button", { class: "btn", type: "button", onclick: addPerson }, "Add")), perr),
    h("section", { class: "panel" }, h("h2", null, "Storage"),
      h("dl", { class: "kv" },
        h("dt", null, "Data folder"), h("dd", null, h("code", null, dataDir || "unknown")),
        h("dt", null, "Project files"), h("dd", null, h("code", null, `projects/${p.id}/`)),
        h("dt", null, "Database"), h("dd", null, h("code", null, "organizer.db (SQLite)")))),
    h("section", { class: "panel danger" }, h("h2", null, "Delete project"),
      h("p", { class: "muted" }, "Removes this project's tasks, people, and history. Its files are moved to the trash folder inside the data folder, not erased."),
      h("div", null, h("button", { class: "btn danger", type: "button", onclick: () => deleteProject() }, "Delete project…"))));
}

async function renamePerson(person, rerender) {
  await formDialog({
    title: "Rename person", submitLabel: "Rename",
    body: () => fieldLabel("Name", h("input", { type: "text", name: "name", value: person.name, maxlength: "80" })),
    onSubmit: async (dlg) => {
      await api.patch(`/api/people/${person.id}`, { name: dlg.dialog.querySelector('[name="name"]').value });
      await loadPeople();
      rerender({ header: true });
    },
  });
}

async function removePerson(person, rerender) {
  if (!(await confirmDialog({ title: `Remove ${person.name}?`, message: "They will no longer be available to assign.", confirmLabel: "Remove", danger: true }))) return;
  try {
    await api.del(`/api/people/${person.id}`);
    await loadPeople();
    rerender({ header: true });
  } catch (e) { toast(e.message, { kind: "error" }); }
}

async function deleteProject() {
  const p = state.project;
  let done = false;
  await formDialog({
    title: `Delete ${p.name}?`, submitLabel: "Delete project", danger: true,
    body: () => [
      h("p", null, `This deletes ${p.task_count} task(s). Type the prefix `, h("b", null, p.ticket_prefix), " to confirm."),
      fieldLabel("Ticket prefix", h("input", { type: "text", name: "confirm", autocomplete: "off" })),
    ],
    onSubmit: async (dlg) => {
      await api.del(`/api/projects/${p.id}`, { confirm_prefix: dlg.dialog.querySelector('[name="confirm"]').value.trim() });
      done = true;
    },
  });
  if (done) {
    await loadProjects();
    toast("Project deleted");
    go(state.projects.length ? { projectId: state.projects[0].id, view: "board" } : "#/");
  }
}
