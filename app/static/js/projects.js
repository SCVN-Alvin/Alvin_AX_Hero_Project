// Project creation, the sidebar switcher, and the no-project state.
import { api } from "./api.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { go, loadProjects, state } from "./store.js";
import { fieldLabel, formDialog, openMenu } from "./ui.js";

export function suggestPrefix(name) {
  const words = name.toUpperCase().replace(/[^A-Z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  let p = words.length > 1 ? words.map((w) => w[0]).join("") : (words[0] || "").slice(0, 3);
  p = p.replace(/^[0-9]+/, "");
  if (p.length < 2) p = (words.join("") || "PRJ").replace(/^[0-9]+/, "").slice(0, 3).padEnd(2, "X");
  return p.slice(0, 5);
}

export async function openNewProject() {
  let created = null;
  await formDialog({
    title: "Create project",
    submitLabel: "Create project",
    body: () => {
      const name = h("input", { type: "text", name: "name", maxlength: "120", placeholder: "e.g. Human Face Log" });
      const prefix = h("input", { type: "text", name: "ticket_prefix", maxlength: "10", placeholder: "LOG", autocomplete: "off" });
      let touched = false;
      prefix.addEventListener("input", () => { touched = true; prefix.value = prefix.value.toUpperCase().replace(/[^A-Z0-9]/g, ""); });
      name.addEventListener("input", () => { if (!touched) prefix.value = suggestPrefix(name.value); });
      const desc = h("textarea", { name: "description", rows: "3", maxlength: "5000", placeholder: "Optional" });
      return [
        fieldLabel("Project name", name),
        h("div", { class: "field" }, h("label", { for: (prefix.id = "np-prefix") }, "Ticket prefix"), prefix,
          h("div", { class: "hint" }, "Used in ticket keys such as ", h("b", null, "LOG-12"), ". It cannot be changed later and keys are never reused.")),
        fieldLabel("Description", desc),
      ];
    },
    onSubmit: async (dlg) => {
      const f = dlg.dialog;
      created = await api.post("/api/projects", {
        name: f.querySelector('[name="name"]').value,
        ticket_prefix: f.querySelector('[name="ticket_prefix"]').value,
        description: f.querySelector('[name="description"]').value,
      });
    },
  });
  if (created) {
    await loadProjects();
    go({ projectId: created.id, view: "board" });
  }
  return created;
}

export function projectSwitcher() {
  const p = state.project;
  const btn = h("button", {
    class: "project-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": `Project: ${p ? p.name : "none"}. Switch project`,
    onclick: () => openMenu(btn, [
      ...state.projects.map((x) => ({ label: `${x.name}  (${x.ticket_prefix})`, checked: x.id === state.projectId, onSelect: () => go({ projectId: x.id, view: state.view }) })),
      ...(state.projects.length ? [{ separator: true }] : []),
      { label: "New project…", onSelect: openNewProject },
    ]),
  }, h("span", { class: "pname" }, p ? p.name : "No project"), p ? h("span", { class: "pkey" }, p.ticket_prefix) : null, icon("chevDown"));
  return h("div", { class: "project-switch" }, btn);
}

export function noProjectState() {
  return h("div", { class: "empty" },
    h("h2", null, "Organize a creative brief into tickets"),
    h("p", null, "Everything stays on this computer. No account, cloud service, or AI model is needed."),
    h("ol", null,
      h("li", null, "Create a project"),
      h("li", null, "Import a brief to browse its text and media, or add tasks by hand"),
      h("li", null, "Review and approve work, then manage it on the board")),
    h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: openNewProject }, icon("plus"), "Create project")));
}
