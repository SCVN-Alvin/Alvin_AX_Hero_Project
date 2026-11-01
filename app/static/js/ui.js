// Shared interface pieces: menus, popovers, dialogs, toasts, live announcements.
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";

export function announce(message) {
  const live = document.getElementById("live");
  if (!live) return;
  live.textContent = "";
  setTimeout(() => (live.textContent = message), 30);
}

export function toast(message, { kind = "info", action = null, ms = 6000 } = {}) {
  const box = document.getElementById("toasts");
  const el = h("div", { class: `toast ${kind === "error" ? "error" : ""}`, role: kind === "error" ? "alert" : "status" }, h("span", null, message));
  if (action) el.append(h("button", { type: "button", onclick: () => { action.run(); el.remove(); } }, action.label));
  box.append(el);
  if (kind !== "error" || ms) setTimeout(() => el.remove(), kind === "error" ? Math.max(ms, 9000) : ms);
}

// ---- positioned floating layers -------------------------------------------

let activeLayer = null;

export function closeLayer() {
  if (activeLayer) {
    const { el, anchor, restoreFocus, onClose, cleanup } = activeLayer;
    activeLayer = null;
    cleanup();
    el.remove();
    anchor.setAttribute("aria-expanded", "false");
    if (restoreFocus && document.contains(anchor)) anchor.focus();
    if (onClose) onClose();
  }
}

function place(el, anchor, align) {
  el.style.visibility = "hidden";
  document.body.append(el);
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth;
  const hgt = el.offsetHeight;
  let left = align === "right" ? r.right - w : r.left;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  let top = r.bottom + 4;
  if (top + hgt > window.innerHeight - 8) top = Math.max(8, r.top - hgt - 4);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  el.style.visibility = "";
}

function openLayer(el, anchor, { align = "left", onClose } = {}) {
  closeLayer();
  place(el, anchor, align);
  anchor.setAttribute("aria-expanded", "true");
  const onDown = (e) => {
    if (!el.contains(e.target) && !anchor.contains(e.target)) closeLayer();
  };
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeLayer();
    }
  };
  const onScroll = (e) => {
    if (!el.contains(e.target)) closeLayer();
  };
  document.addEventListener("mousedown", onDown, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("scroll", onScroll, true);
  window.addEventListener("resize", closeLayer);
  activeLayer = {
    el, anchor, onClose, restoreFocus: true,
    cleanup() {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", closeLayer);
    },
  };
}

/** items: [{label, onSelect, checked?, disabled?, danger?}] or {separator:true} */
export function openMenu(anchor, items, opts = {}) {
  const menu = h("div", { class: "menu", role: "menu" });
  const buttons = [];
  for (const it of items) {
    if (it.separator) {
      menu.append(h("div", { class: "menu-sep", role: "separator" }));
      continue;
    }
    const b = h(
      "button",
      {
        type: "button", role: it.checked === undefined ? "menuitem" : "menuitemradio",
        "aria-checked": it.checked === undefined ? null : String(!!it.checked),
        class: it.danger ? "danger" : "", disabled: !!it.disabled,
        tabindex: "-1",
        onclick: () => {
          closeLayer();
          it.onSelect();
        },
      },
      it.checked === undefined ? null : h("span", { class: "check" }, it.checked ? icon("check") : null),
      it.label,
    );
    // role=menuitemradio needs the menuitem styling hook
    b.setAttribute("role", it.checked === undefined ? "menuitem" : "menuitemradio");
    b.dataset.item = "1";
    buttons.push(b);
    menu.append(b);
  }
  menu.addEventListener("keydown", (e) => {
    const enabled = buttons.filter((b) => !b.disabled);
    const i = enabled.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); enabled[(i + 1) % enabled.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); enabled[(i - 1 + enabled.length) % enabled.length].focus(); }
    else if (e.key === "Home") { e.preventDefault(); enabled[0].focus(); }
    else if (e.key === "End") { e.preventDefault(); enabled[enabled.length - 1].focus(); }
    else if (e.key === "Tab") { closeLayer(); }
  });
  openLayer(menu, anchor, opts);
  const start = buttons.find((b) => b.getAttribute("aria-checked") === "true") || buttons.find((b) => !b.disabled);
  if (start) start.focus();
  return menu;
}

/** Free-form popover (used for multi-select filters). `build` returns a node. */
export function openPopover(anchor, node, opts = {}) {
  const pop = h("div", { class: "popover", role: "group" }, node);
  openLayer(pop, anchor, opts);
  const first = pop.querySelector("input,button,select");
  if (first) first.focus();
  return pop;
}

// ---- dialogs ----------------------------------------------------------------

/**
 * Modal form dialog. `build(api)` returns the body node(s). On submit, `onSubmit(api)`
 * may throw (message shown inline, dialog stays open with the user's input intact).
 * Resolves with the onSubmit result, or undefined if cancelled.
 */
export function formDialog({ title, body, submitLabel = "Save", danger = false, cancelLabel = "Cancel", onSubmit, wide = false }) {
  return new Promise((resolve) => {
    const err = h("div", { class: "form-error", role: "alert", hidden: true });
    const submit = h("button", { class: `btn ${danger ? "danger solid" : "primary"}`, type: "submit" }, submitLabel);
    const dlg = h("dialog", { "aria-label": title });
    if (wide) dlg.style.width = "min(720px, calc(100vw - 24px))";
    const handle = {
      dialog: dlg,
      showError(message, field) {
        replace(err, message);
        err.hidden = false;
        dlg.querySelectorAll(".invalid").forEach((n) => n.classList.remove("invalid"));
        if (field) {
          const f = dlg.querySelector(`[name="${CSS.escape(field)}"]`);
          if (f) { f.classList.add("invalid"); f.focus(); }
        }
      },
      close(value) { dlg.close(); resolve(value); },
    };
    const form = h(
      "form", { method: "dialog", novalidate: true },
      h("div", { class: "dlg-head" }, title),
      h("div", { class: "dlg-body" }, err, body(handle)),
      h("div", { class: "dlg-foot" },
        h("button", { class: "btn", type: "button", onclick: () => handle.close(undefined) }, cancelLabel),
        submit),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.hidden = true;
      submit.disabled = true;
      try {
        const result = await onSubmit(handle);
        if (result !== false) handle.close(result === undefined ? true : result);
      } catch (ex) {
        handle.showError(ex.message || "Something went wrong", ex.details && ex.details.field);
      } finally {
        submit.disabled = false;
      }
    });
    dlg.append(form);
    dlg.addEventListener("cancel", () => resolve(undefined));
    dlg.addEventListener("close", () => { dlg.remove(); resolve(undefined); });
    document.body.append(dlg);
    dlg.showModal();
    const first = dlg.querySelector("input:not([type=hidden]),textarea,select");
    if (first) first.focus();
  });
}

/** Simple confirmation. Resolves true/false. `detail` is an optional node. */
export function confirmDialog({ title, message, detail = null, confirmLabel = "Confirm", danger = false }) {
  return formDialog({
    title,
    submitLabel: confirmLabel,
    danger,
    body: () => [h("p", { style: null }, message), detail],
    onSubmit: () => true,
  }).then((r) => r === true);
}

let uid = 0;
/** Labelled form field; the label is programmatically tied to the control. */
export function fieldLabel(text, input, extra) {
  if (!input.id) input.id = `f${++uid}`;
  return h("div", { class: "field" }, h("label", { for: input.id }, text, extra), input);
}
