// Server-side file picker. A web page cannot learn the absolute path of a file chosen in the
// browser, so the local service lists folders (names only) and the person picks from them.
import { api } from "./api.js";
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { fmtBytes } from "./media.js";
import { formDialog } from "./ui.js";

/** mode: "file" (an .html file) or "dir". Resolves with the chosen path or undefined. */
export function pickPath({ mode, start = "", title }) {
  let current = "", selected = "";
  return formDialog({
    title: title || (mode === "file" ? "Choose an HTML file" : "Choose a folder"),
    submitLabel: mode === "file" ? "Choose file" : "Choose this folder",
    wide: true,
    body: (dlg) => {
      const pathInput = h("input", { type: "text", name: "path", "aria-label": "Folder path", autocomplete: "off", spellcheck: "false", class: "fp-path" });
      const list = h("div", { class: "fp-list", role: "listbox", "aria-label": "Folders and HTML files" });
      const shortcuts = h("div", { class: "fp-shortcuts" });
      const note = h("div", { class: "muted fp-note", role: "status" });
      async function load(path) {
        try {
          const r = await api.get(`/api/fs/list?path=${encodeURIComponent(path)}`);
          current = r.path;
          selected = mode === "dir" ? r.path : "";
          pathInput.value = r.path;
          replace(shortcuts, r.shortcuts.map((s) => h("button", { class: "btn small subtle", type: "button", onclick: () => load(s.path) }, icon("folder"), s.label)));
          const rows = [];
          selBtn = null;
          if (r.parent) rows.push(row("dir", "..", "Up one folder", icon("arrowLeft"), () => load(r.parent)));
          for (const e of r.entries) {
            const full = r.path.replace(/\/$/, "") + "/" + e.name;
            if (e.kind === "dir") rows.push(row("dir", e.name, "", icon("folder"), () => load(full)));
            else if (mode === "file") {
              const btn = row("html", e.name, fmtBytes(e.size), icon("file"), () => choose(btn, full));
              btn.addEventListener("dblclick", () => { choose(btn, full); dlg.dialog.querySelector("form").requestSubmit(); });
              rows.push(btn);
            }
          }
          replace(list, rows.length ? rows : h("div", { class: "muted fp-empty" }, mode === "file" ? "No folders or HTML files here." : "No subfolders here."));
          replace(note, [r.truncated ? "Only the first entries are shown. " : "", mode === "file" && r.hidden_other_files ? `${r.hidden_other_files} other file(s) hidden; only .html files can be imported.` : ""].join(""));
        } catch (e) {
          replace(note, h("span", { class: "err" }, e.message));
        }
      }
      function row(kind, name, detail, ic, onclick) {
        return h("button", { type: "button", class: `fp-row ${kind}`, role: "option", "aria-selected": "false", onclick },
          ic, h("span", { class: "fp-name" }, name), detail ? h("span", { class: "muted" }, detail) : null);
      }
      let selBtn = null;
      function choose(btn, full) {
        if (selBtn) selBtn.setAttribute("aria-selected", "false");
        selBtn = btn;
        btn.setAttribute("aria-selected", "true");
        selected = full;
      }
      pathInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); load(pathInput.value); } });
      load(start);
      dlg.pick = () => ({ current, selected });
      return [
        h("div", { class: "field" }, h("label", { for: (pathInput.id = "fp-path") }, "Location"),
          h("div", { class: "row" }, pathInput, h("button", { class: "btn", type: "button", onclick: () => load(pathInput.value) }, "Go"))),
        shortcuts, list, note,
      ];
    },
    onSubmit: (dlg) => {
      const { current: cur, selected: sel } = dlg.pick();
      if (mode === "file") {
        if (!sel) throw new Error("Select an .html file in the list first.");
        return sel;
      }
      return sel || cur;
    },
  });
}
