// Backlog / list view: every approved task (all levels) in a sortable-by-status table.
import { moveTask, reportFailure } from "./actions.js";
import { h, initials } from "./dom.js";
import { go, labelOf, loadView, state, statusLabel } from "./store.js";
import { selectEl } from "./taskform.js";

export function renderList(rerender) {
  const tasks = state.list || [];
  const pills = h("div", { class: "pills", role: "group", "aria-label": "Status filter" });
  const all = [{ value: "", label: "All" }, ...state.meta.statuses];
  for (const s of all) {
    const pressed = s.value === "" ? state.listStatus.length === 0 : state.listStatus.length === 1 && state.listStatus[0] === s.value;
    pills.append(h("button", {
      class: "pill", type: "button", "aria-pressed": String(pressed),
      onclick: async () => { state.listStatus = s.value ? [s.value] : []; await loadView(); rerender(); },
    }, s.label));
  }

  const rows = tasks.map((t) => {
    const status = selectEl(`status-${t.id}`, state.meta.statuses, t.status, {
      onchange: async (e) => {
        try {
          await moveTask(t, e.target.value, null);
          await loadView();
          rerender();
          document.dispatchEvent(new CustomEvent("organizer:details"));
        } catch (err) { reportFailure(err, `Could not change ${t.key}`); await loadView(); rerender(); }
      },
    });
    status.setAttribute("aria-label", `Status of ${t.key}`);
    return h("tr", { "aria-selected": String(state.taskId === t.id) },
      h("td", null, h("b", null, t.key)),
      h("td", { class: "t" }, t.parent_key ? h("span", { class: "indent", title: `Under ${t.parent_key}` }, "↳") : null,
        h("button", { class: "rowbtn", type: "button", onclick: () => go({ taskId: t.id }) }, t.title)),
      h("td", null, h("span", { class: `badge level ${t.level}` }, t.level)),
      h("td", null, status, t.is_blocked ? h("span", { class: "badge blocked", title: t.blocked_reason }, "Blocked") : null),
      h("td", null, h("div", { class: "badges" },
        t.disciplines.includes("art") ? h("span", { class: "badge art" }, "Art") : null,
        t.disciplines.includes("dev") ? h("span", { class: "badge dev" }, "Dev") : null,
        !t.disciplines.length ? h("span", { class: "badge na" }, "None") : null)),
      h("td", null, t.priority ? h("span", { class: `badge ${t.priority}` }, t.priority.toUpperCase()) : h("span", { class: "badge na" }, "Not assessed")),
      h("td", null, labelOf("categories", t.category)),
      h("td", null, t.assignee_name ? h("span", null, h("span", { class: "avatar" }, initials(t.assignee_name)), " ", t.assignee_name) : h("span", { class: "muted" }, "Unassigned")));
  });

  const table = h("table", { class: "tasks" },
    h("thead", null, h("tr", null, ["Key", "Title", "Type", "Status", "Disciplines", "Priority", "Category", "Assignee"].map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", null, rows));
  return h("div", null, pills, h("div", { class: "list-wrap" }, table));
}

export { statusLabel };
