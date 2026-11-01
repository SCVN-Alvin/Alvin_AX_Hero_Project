// Shared task mutations. Each returns the updated task or null if the user cancelled.
import { api, clearFailure } from "./api.js";
import { h } from "./dom.js";
import { state, statusLabel } from "./store.js";
import { confirmDialog, toast } from "./ui.js";

async function confirmUnfinished(err, task) {
  const items = (err.details && err.details.unfinished) || [];
  return confirmDialog({
    title: `Mark ${task.key} Done with unfinished work?`,
    message: err.message,
    detail: h("ul", { class: "list-plain" }, items.map((i) =>
      h("li", null, h("b", null, i.key), " ", i.title, " ", h("span", { class: "muted" }, `(${statusLabel(i.status)})`)))),
    confirmLabel: "Mark Done anyway",
    danger: true,
  });
}

/** POST /move with the unfinished-children decision flow. */
export async function moveTask(task, status, beforeId = null) {
  try {
    const out = await api.post(`/api/tasks/${task.id}/move`, { status, before_id: beforeId });
    return out;
  } catch (err) {
    if (err.code === "unfinished_children") {
      if (!(await confirmUnfinished(err, task))) return null;
      return api.post(`/api/tasks/${task.id}/move`, { status, before_id: beforeId, confirm_unfinished: true });
    }
    throw err;
  }
}

/** PATCH with the same decision flow (used by the details panel status control). */
export async function patchTask(task, patch) {
  try {
    return await api.patch(`/api/tasks/${task.id}`, patch);
  } catch (err) {
    if (err.code === "unfinished_children") {
      if (!(await confirmUnfinished(err, task))) return null;
      return api.patch(`/api/tasks/${task.id}`, { ...patch, confirm_unfinished: true });
    }
    throw err;
  }
}

export function reportFailure(err, what) {
  toast(`${what}: ${err.message}`, { kind: "error" });
}

export { clearFailure };
export const peopleOptions = () => state.people.map((p) => ({ value: p.id, label: p.name }));
