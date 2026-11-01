// Header search and multi-select filters. Dimensions combine with AND; values within one dimension use OR.
import { h, debounce } from "./dom.js";
import { icon } from "./icons.js";
import { FILTER_DIMS, activeFilterCount, clearFilters, state } from "./store.js";
import { openPopover } from "./ui.js";

function options(dim) {
  const m = state.meta;
  const opts = {
    discipline: [
      { value: "dev", label: "Dev (includes Art + Dev)" },
      { value: "art", label: "Art (includes Art + Dev)" },
      { value: "both", label: "Both (requires Art and Dev)" },
      { value: "none", label: "Unassigned" },
    ],
    category: m.categories,
    impact: m.impacts,
    urgency: m.urgencies,
    priority: [...m.priorities, { value: "not_assessed", label: "Not assessed" }],
    assignee: [{ value: "unassigned", label: "Unassigned" }, ...state.people.map((p) => ({ value: p.id, label: p.name }))],
  };
  return opts[dim];
}
const TITLES = { discipline: "Discipline", category: "Category", impact: "Impact", urgency: "Urgency", priority: "Priority", assignee: "Assignee" };

export function renderFilters(onChange) {
  const bar = h("div", { class: "filters", role: "group", "aria-label": "Filters" });
  for (const dim of FILTER_DIMS) {
    const n = state.filters[dim].length;
    const btn = h("button", {
      class: "filter-btn", type: "button", "aria-haspopup": "true", "aria-expanded": "false",
      dataset: { active: String(n > 0) }, "aria-label": `${TITLES[dim]} filter${n ? `, ${n} selected` : ""}`,
      onclick: () => {
        const box = h("div", null, h("div", { class: "pop-title" }, TITLES[dim]));
        for (const o of options(dim)) {
          box.append(h("label", null,
            h("input", {
              type: "checkbox", checked: state.filters[dim].includes(o.value),
              onchange: (e) => {
                const set = new Set(state.filters[dim]);
                e.target.checked ? set.add(o.value) : set.delete(o.value);
                state.filters[dim] = [...set];
                onChange();
                // keep the trigger's count in sync without closing the popover
                btn.dataset.active = String(set.size > 0);
              },
            }), o.label));
        }
        openPopover(btn, box, { onClose: () => onChange({ rebuild: true }) });
      },
    }, TITLES[dim], n ? h("span", { class: "count" }, String(n)) : icon("chevDown"));
    bar.append(btn);
  }
  if (activeFilterCount() > 0) {
    bar.append(h("button", { class: "btn subtle small", type: "button", onclick: () => { clearFilters(); onChange({ rebuild: true }); } }, "Clear filters"));
  }
  return bar;
}

export function renderSearch(onChange) {
  const input = h("input", {
    type: "search", name: "q", placeholder: "Search title, description, key", "aria-label": "Search tasks",
    value: state.q, autocomplete: "off",
  });
  input.addEventListener("input", debounce(() => { state.q = input.value; onChange({ keepFocus: true }); }, 200));
  return h("div", { class: "search" }, icon("search"), input);
}

