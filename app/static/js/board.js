// Kanban board: columns, cards, drag and drop, and keyboard/menu alternatives.
import { moveTask, reportFailure } from "./actions.js";
import { h, initials } from "./dom.js";
import { icon } from "./icons.js";
import { clearFilters, go, labelOf, loadView, state, statusLabel } from "./store.js";
import { announce, openMenu } from "./ui.js";
import { openCreateTask } from "./taskform.js";

let draggingId = null;

export function renderBoard(rerender) {
  const board = state.board;
  const wrap = h("div", { class: "board", role: "list", "aria-label": "Board columns" });
  if (!board) return wrap;

  const cardsByCol = {};
  const refresh = async (focusId) => {
    await loadView();
    rerender();
    document.dispatchEvent(new CustomEvent("organizer:details"));
    if (focusId) {
      const el = document.querySelector(`.card[data-id="${CSS.escape(focusId)}"]`);
      if (el) el.focus();
    }
  };

  async function doMove(card, status, beforeId, { keyboard = false } = {}) {
    try {
      const out = await moveTask(card, status, beforeId);
      if (!out) return refresh(keyboard ? card.id : null); // cancelled: restore truth
      await refresh(keyboard ? card.id : null);
      const col = state.board.columns[status] || [];
      const pos = col.findIndex((c) => c.id === card.id) + 1;
      announce(`${card.key} moved to ${statusLabel(status)}${pos ? `, position ${pos} of ${col.length}` : ""}`);
    } catch (err) {
      reportFailure(err, `Could not move ${card.key}`);
      await refresh(card.id); // show what is really saved
    }
  }

  for (const s of state.meta.statuses) {
    const cards = board.columns[s.value] || [];
    const list = h("div", { class: "cards", role: "list", "aria-label": `${s.label} cards`, dataset: { status: s.value } });
    cardsByCol[s.value] = list;
    for (const c of cards) list.append(renderCard(c, cards, doMove));
    if (!cards.length) {
      list.append(h("div", { class: "col-empty" }, board.filters_active ? "No matching cards" : "No cards. Drop one here."));
    }
    const col = h("section", { class: "column", role: "listitem", "aria-labelledby": `col-${s.value}` },
      h("div", { class: "column-head" },
        h("span", { id: `col-${s.value}` }, s.label),
        h("span", { class: "count", "aria-label": `${cards.length} visible` }, String(cards.length))),
      list);
    wireDropTarget(col, list, s.value, doMove);
    wrap.append(col);
  }
  return wrap;
}

function renderCard(c, siblings, doMove) {
  const expanded = state.expanded.has(c.id);
  const selected = state.taskId === c.id;
  const statuses = state.meta.statuses;

  const menuBtn = h("button", {
    class: "icon-btn menu-trigger", type: "button", "aria-haspopup": "menu", "aria-expanded": "false",
    "aria-label": `Change status of ${c.key}`,
    onclick: (e) => {
      e.stopPropagation();
      openMenu(menuBtn, statuses.map((s) => ({
        label: s.label, checked: s.value === c.status,
        onSelect: () => (s.value === c.status ? null : doMove(c, s.value, (state.board.columns[s.value][0] || {}).id || null, { keyboard: true })),
      })), { align: "right" });
    },
  }, icon("more"));

  const badges = h("div", { class: "badges" },
    c.disciplines.includes("art") ? h("span", { class: "badge art" }, "Art") : null,
    c.disciplines.includes("dev") ? h("span", { class: "badge dev" }, "Dev") : null,
    priorityBadge(c),
    h("span", { class: `badge cat ${c.category === "unassigned" ? "unset" : ""}`, title: "Category" }, labelOf("categories", c.category)),
    c.level === "epic" ? h("span", { class: "badge level epic" }, "Epic") : null,
  );

  const foot = h("div", { class: "card-foot" });
  if (c.children_total) {
    const done = c.children_done;
    const pct = Math.round((done / c.children_total) * 100);
    const fill = h("i");
    foot.append(h("button", {
      class: "progress", type: "button", "aria-expanded": String(expanded),
      "aria-label": `${done} of ${c.children_total} children done${expanded ? ", hide list" : ", show list"}`,
      onclick: (e) => {
        e.stopPropagation();
        expanded ? state.expanded.delete(c.id) : state.expanded.add(c.id);
        document.dispatchEvent(new CustomEvent("organizer:rerender"));
      },
    }, icon(expanded ? "chevDown" : "chevRight"), `${done}/${c.children_total}`,
    h("span", { class: "bar", "aria-hidden": "true" }, fill)));
    fill.style.width = `${pct}%`;
    if (c.children_hidden_by_filter) {
      foot.append(h("span", { class: "hidden-note" }, `counts include ${c.children_hidden_by_filter} hidden by filter`));
    }
  }
  if (c.matched_via_children && !c.self_match) {
    foot.append(h("span", { class: "via", title: "This parent is shown because of its matching children" },
      `↳ matches via ${c.matched_via_children} ${c.matched_via_children === 1 ? "subtask" : "subtasks"}`));
  }
  if (c.assignee_name) foot.append(h("span", { class: "avatar", title: c.assignee_name, "aria-label": `Assignee ${c.assignee_name}` }, initials(c.assignee_name)));

  const card = h("article", {
    class: `card ${c.is_blocked ? "blocked" : ""}`, tabindex: "0", draggable: "true",
    role: "listitem", "aria-selected": String(selected), dataset: { id: c.id, status: c.status },
    "aria-label": `${c.key} ${c.title}`,
    onclick: () => go({ taskId: c.id }),
    onkeydown: (e) => cardKey(e, c, doMove),
    ondragstart: (e) => {
      draggingId = c.id;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", c.id);
      requestAnimationFrame(() => card.classList.add("dragging"));
    },
    ondragend: () => { draggingId = null; card.classList.remove("dragging"); clearDropUI(); },
  },
    h("div", { class: "card-top" },
      h("span", { class: "key" }, c.key),
      c.is_blocked ? h("span", { class: "badge blocked" }, icon("block"), "Blocked") : null,
      menuBtn),
    h("div", { class: "title", title: c.title }, c.title),
    c.is_blocked && c.blocked_reason ? h("div", { class: "blocked-reason", title: c.blocked_reason }, c.blocked_reason) : null,
    badges, foot);

  if (expanded && c.children.length) {
    card.append(h("ul", { class: "kids" }, c.children.map((k) =>
      h("li", { class: k.matches ? "" : "dim" },
        h("span", { class: "k" }, k.key),
        h("button", { type: "button", onclick: (e) => { e.stopPropagation(); go({ taskId: k.id }); } }, k.title),
        h("span", { class: `badge status ${k.status}` }, statusLabel(k.status))))));
  }
  return card;
}

function priorityBadge(c) {
  if (!c.priority) return h("span", { class: "badge na", title: "Priority has not been assessed" }, "Not assessed");
  const over = c.priority_source === "overridden";
  return h("span", { class: `badge ${c.priority}`, title: over ? "Priority manually overridden" : "Suggested by the default policy" },
    c.priority.toUpperCase(), over ? icon("edit") : null);
}

// ---- keyboard alternative to dragging ------------------------------------------------
function cardKey(e, c, doMove) {
  if (e.target !== e.currentTarget) return;
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    go({ taskId: c.id });
    return;
  }
  if (!e.altKey) return;
  const statuses = state.meta.statuses.map((s) => s.value);
  const col = state.board.columns[c.status];
  const i = col.findIndex((x) => x.id === c.id);
  let handled = true;
  if (e.key === "ArrowUp" && i > 0) doMove(c, c.status, col[i - 1].id, { keyboard: true });
  else if (e.key === "ArrowDown" && i < col.length - 1) doMove(c, c.status, (col[i + 2] || {}).id || null, { keyboard: true });
  else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    const j = statuses.indexOf(c.status) + (e.key === "ArrowLeft" ? -1 : 1);
    if (j >= 0 && j < statuses.length) doMove(c, statuses[j], (state.board.columns[statuses[j]][0] || {}).id || null, { keyboard: true });
  } else handled = false;
  if (handled) e.preventDefault();
}

// ---- drag and drop ----------------------------------------------------------------------
let indicator = null;
function clearDropUI() {
  if (indicator) { indicator.remove(); indicator = null; }
  document.querySelectorAll(".column.drop-target").forEach((c) => c.classList.remove("drop-target"));
}

function dropPoint(list, y) {
  const cards = [...list.querySelectorAll(":scope > .card:not(.dragging)")];
  for (const el of cards) {
    const r = el.getBoundingClientRect();
    if (y < r.top + r.height / 2) return { before: el, beforeId: el.dataset.id };
  }
  return { before: null, beforeId: null };
}

function wireDropTarget(col, list, status, doMove) {
  col.addEventListener("dragover", (e) => {
    if (!draggingId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    col.classList.add("drop-target");
    // An empty column is highlighted only: changing the element under the pointer
    // mid-drag makes browsers drop the drop target.
    if (!list.querySelector(":scope > .card")) return;
    const { before } = dropPoint(list, e.clientY);
    if (!indicator) indicator = h("div", { class: "drop-indicator", "aria-hidden": "true" });
    if (before) list.insertBefore(indicator, before);
    else list.append(indicator);
  });
  col.addEventListener("dragleave", (e) => {
    if (!col.contains(e.relatedTarget)) { col.classList.remove("drop-target"); if (indicator && list.contains(indicator)) indicator.remove(); }
  });
  col.addEventListener("drop", (e) => {
    if (!draggingId) return;
    e.preventDefault();
    const { beforeId } = dropPoint(list, e.clientY);
    const id = draggingId;
    draggingId = null;
    clearDropUI();
    const card = Object.values(state.board.columns).flat().find((c) => c.id === id);
    if (card && beforeId !== id) doMove(card, status, beforeId);
  });
}

// ---- board header strip ------------------------------------------------------------------
export function boardIntro() {
  const b = state.board;
  const top = h("div", { class: "board-top" });
  if (b && b.filters_active) {
    top.append(
      h("span", { class: "note", role: "status" },
        `Showing ${b.total_visible} of ${b.total_top_level} top-level tasks. Parents appear when a subtask matches.`),
      h("button", { class: "btn small", type: "button", onclick: () => { clearFilters(); document.dispatchEvent(new CustomEvent("organizer:filters")); } }, "Clear filters"));
  } else {
    top.append(h("span", { class: "note" }, "Approved top-level tasks. Drag cards, or use the ⋯ menu / Alt+Arrow keys to change status and order."));
  }
  return top;
}

export function emptyProjectState() {
  return h("div", { class: "empty" },
    h("h2", null, "This project has no tasks yet"),
    h("p", null, "Import a creative brief to generate task candidates, or add tasks by hand."),
    h("div", { class: "actions" },
      h("button", { class: "btn", type: "button", disabled: true, title: "Brief import arrives in Milestone 2" }, icon("import"), "Import brief (Milestone 2)"),
      h("button", { class: "btn primary", type: "button", onclick: () => openCreateTask() }, icon("plus"), "Create task")));
}

export function noMatchState() {
  const labels = activeFilterSummary();
  return h("div", { class: "empty inline" },
    h("h2", null, "No cards match these filters"),
    h("div", { class: "chips" }, labels.map((l) => h("span", { class: "chip" }, l))),
    h("button", { class: "btn primary", type: "button", onclick: () => { clearFilters(); document.dispatchEvent(new CustomEvent("organizer:filters")); } }, "Clear filters"));
}

export function activeFilterSummary() {
  const out = [];
  if (state.q.trim()) out.push(`Search: “${state.q.trim()}”`);
  const names = { discipline: "Discipline", category: "Category", impact: "Impact", urgency: "Urgency", priority: "Priority", assignee: "Assignee" };
  for (const [dim, vals] of Object.entries(state.filters)) {
    if (!vals.length) continue;
    out.push(`${names[dim]}: ${vals.map((v) => valueLabel(dim, v)).join(" or ")}`);
  }
  return out;
}

export function valueLabel(dim, v) {
  if (dim === "discipline") return { art: "Art", dev: "Dev", both: "Both", none: "Unassigned" }[v] || v;
  if (dim === "priority") return v === "not_assessed" ? "Not assessed" : v.toUpperCase();
  if (dim === "assignee") return v === "unassigned" ? "Unassigned" : (state.people.find((p) => p.id === v) || {}).name || v;
  const list = { category: "categories", impact: "impacts", urgency: "urgencies" }[dim];
  return labelOf(list, v);
}
