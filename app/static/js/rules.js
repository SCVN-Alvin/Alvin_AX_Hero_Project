// Classification rules: global and project-specific, editable, with a live preview of what a rule would do.
import { api } from "./api.js";
import { debounce, h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { state } from "./store.js";
import { fieldLabel, formDialog, toast } from "./ui.js";

let cache = null;
export function resetRules() { cache = null; }

export function renderRules() {
  const key = state.projectId;
  if (cache && cache.key === key) return cache.el;
  const el = rulesView();
  cache = { key, el };
  return el;
}

const SCOPE_LABEL = { title: "title", text: "text", heading_path: "heading", any: "title, text or heading" };
const FIELD_LABEL = { discipline: "Discipline", category: "Category", impact: "Impact", urgency: "Urgency" };
const VALUE_LABEL = {
  discipline: { art: "Art", dev: "Dev", both: "Art and Dev" },
  category: { core_gameplay: "Core gameplay", content: "Content", ui_ux: "UI/UX", polish: "Polish", bug_fix: "Bug fix", technical: "Technical", production: "Production" },
  impact: { critical: "Critical", high: "High", medium: "Medium", low: "Low" },
  urgency: { high: "High", medium: "Medium", low: "Low" },
};
const STATE_TEXT = { suggested: "would be suggested", conflicting: "would conflict", superseded: "would not be used (a stronger rule or an existing value wins)" };

function rulesView() {
  const pid = state.projectId;
  const root = h("div", { class: "rules" });
  let data = null;
  let docs = [];

  async function load() {
    replace(root, h("p", { class: "muted pad", role: "status" }, "Loading rules…"));
    try {
      [data, docs] = await Promise.all([api.get(`/api/projects/${pid}/rules`), api.get(`/api/projects/${pid}/documents`).then((r) => r.documents)]);
      paint();
    } catch (e) {
      replace(root, h("div", { class: "banner", role: "alert" }, `Could not load rules: ${e.message}`, h("button", { class: "btn small", type: "button", onclick: load }, "Retry")));
    }
  }

  function summary(r) {
    const ph = r.phrases.slice(0, 5).map((p) => `“${p}”`).join(", ") + (r.phrases.length > 5 ? ` and ${r.phrases.length - 5} more` : "");
    return `${r.operator === "equals" ? "When the " + SCOPE_LABEL[r.match_scope] + " equals" : "When the " + SCOPE_LABEL[r.match_scope] + " contains"} ${ph} → ${FIELD_LABEL[r.output_field]}: ${VALUE_LABEL[r.output_field][r.proposed_value]}`;
  }

  function ruleRow(r) {
    const toggle = h("input", { type: "checkbox", checked: r.enabled, "aria-label": `${r.name} enabled`, onchange: async (e) => {
      try { await api.patch(`/api/rules/${r.id}`, { enabled: e.target.checked }); await afterChange(`Rule ${e.target.checked ? "turned on" : "turned off"}.`); }
      catch (err) { toast(err.message, { kind: "error" }); await load(); }
    } });
    return h("li", { class: `rule-row ${r.enabled ? "" : "off"}` },
      h("label", { class: "rule-on" }, toggle),
      h("div", { class: "rule-main" },
        h("div", { class: "rule-name" }, h("b", null, r.name), " ", h("span", { class: `badge ${r.scope === "project" ? "level epic" : "origin"}` }, r.scope === "project" ? "Project rule" : r.origin === "builtin" ? "Global · built in" : "Global"),
          r.precedence ? h("span", { class: "badge cat" }, `precedence ${r.precedence}`) : null, h("span", { class: "muted" }, ` v${r.version}`)),
        h("div", { class: "muted" }, summary(r)),
        r.note ? h("div", { class: "muted small-note" }, r.note) : null),
      h("div", { class: "rule-actions" },
        h("button", { class: "btn small", type: "button", onclick: () => edit(r) }, icon("edit"), "Edit"),
        r.origin === "user" ? h("button", { class: "icon-btn", type: "button", "aria-label": `Delete ${r.name}`, onclick: () => remove(r) }, icon("trash")) : null));
  }

  function paint() {
    const proj = data.rules.filter((r) => r.scope === "project");
    const glob = data.rules.filter((r) => r.scope === "global");
    replace(root,
      h("div", { class: "page-head" }, h("h2", null, "Rules"), h("button", { class: "btn primary", type: "button", onclick: () => edit(null) }, icon("plus"), "Add project rule")),
      h("p", { class: "muted rules-intro" },
        "Rules suggest Art, Dev, category, impact, or urgency for candidates, and always show the wording that triggered them. A match explains a suggestion; it never confirms one. A project rule outranks a global rule for a single-value field; equal rules that disagree are marked Conflicting and nothing is chosen. A trailing * matches word endings (animat* finds animation and animations). Rules never run on negative instructions."),
      h("section", { class: "panel" }, h("h3", null, `This project (${proj.length})`),
        proj.length ? h("ul", { class: "list-plain rule-list" }, proj.map(ruleRow)) : h("p", { class: "muted" }, "No project rules yet. Add one for a central mechanic, for example “wood shav*” in the title suggests High impact.")),
      h("section", { class: "panel" }, h("h3", null, `Global rules (${glob.length})`),
        h("p", { class: "muted small-note" }, "Shared by every project on this computer. Built-in rules can be edited or turned off, not deleted."),
        h("ul", { class: "list-plain rule-list" }, glob.map(ruleRow))),
      h("p", { class: "muted small-note" }, `Ruleset ${data.ruleset_version}. Changing a rule never overwrites a value you set or a suggestion you accepted or rejected.`));
  }

  async function afterChange(message) {
    await load();
    let n = 0;
    for (const d of docs) {
      try { const l = await api.post(`/api/documents/${d.id}/candidates/apply-rules`); n += l.candidates.filter((c) => c.kind === "task").length; } catch { /* a document without candidates */ }
    }
    toast(`${message}${n ? ` Suggestions on ${n} task candidates were refreshed; nothing you set was changed.` : ""}`);
  }

  async function remove(r) {
    try { await api.del(`/api/rules/${r.id}`); await afterChange("Rule deleted."); } catch (e) { toast(e.message, { kind: "error" }); }
  }

  async function edit(r) {
    let saved = false;
    const base = r || { name: "", enabled: true, match_scope: "text", operator: "contains", pattern: "", case_sensitive: false, output_field: "impact", proposed_value: "high", precedence: 0, note: "" };
    await formDialog({
      title: r ? `Edit rule: ${r.name}` : "Add project rule", submitLabel: r ? "Save rule" : "Add rule", wide: true,
      body: (dlg) => {
        const name = h("input", { type: "text", name: "name", maxlength: "120", value: base.name });
        const scope = h("select", { name: "match_scope" }, Object.entries(SCOPE_LABEL).map(([v, l]) => h("option", { value: v, selected: v === base.match_scope }, `Look in the ${l}`)));
        const op = h("select", { name: "operator" }, [["contains", "contains one of"], ["equals", "equals one of"]].map(([v, l]) => h("option", { value: v, selected: v === base.operator }, l)));
        const phrases = h("textarea", { name: "pattern", rows: "4", placeholder: "one phrase per line\nanimat*\nvfx" });
        phrases.value = base.pattern || "";
        const field = h("select", { name: "output_field" }, Object.entries(FIELD_LABEL).map(([v, l]) => h("option", { value: v, selected: v === base.output_field }, l)));
        const value = h("select", { name: "proposed_value" });
        const fillValues = () => {
          replace(value, Object.entries(VALUE_LABEL[field.value]).map(([v, l]) => h("option", { value: v, selected: v === base.proposed_value && field.value === base.output_field }, l)));
        };
        fillValues();
        field.addEventListener("change", fillValues);
        const prec = h("input", { type: "text", name: "precedence", inputmode: "numeric", value: String(base.precedence || 0), size: "5" });
        const cs = h("input", { type: "checkbox", name: "case_sensitive", checked: !!base.case_sensitive });
        const on = h("input", { type: "checkbox", name: "enabled", checked: base.enabled !== false });
        const note = h("input", { type: "text", name: "note", maxlength: "1000", value: base.note || "", placeholder: "Optional: why this rule exists" });
        const docSel = h("select", { name: "pdoc", "aria-label": "Preview on document" }, docs.length ? docs.map((d) => h("option", { value: d.id }, d.title || d.name)) : h("option", { value: "" }, "(no imported documents)"));
        const out = h("div", { class: "rule-preview", role: "status", "aria-live": "polite" }, h("p", { class: "muted" }, docs.length ? "Preview shows what this rule would do to the current candidates. Nothing is saved until you press the button below." : "Import a brief and generate candidates to preview a rule."));
        const form = () => ({
          name: name.value, match_scope: scope.value, operator: op.value, pattern: phrases.value, output_field: field.value, proposed_value: value.value,
          precedence: Number.parseInt(prec.value || "0", 10) || 0, case_sensitive: cs.checked, enabled: on.checked, note: note.value,
        });
        const preview = debounce(async () => {
          if (!docSel.value || !phrases.value.trim()) return;
          try {
            const p = await api.post(`/api/projects/${pid}/rules/preview`, { document_id: docSel.value, rule: form(), rule_id: r ? r.id : null }, { quiet: true });
            if (!p.candidates_checked) return replace(out, h("p", { class: "muted" }, "No candidates to test against yet. Generate candidates for this document first."));
            replace(out, h("p", null, h("b", null, `${p.candidates_matched} of ${p.candidates_checked} task candidates match.`)),
              p.matches.length ? h("ul", { class: "list-plain" }, p.matches.slice(0, 40).map((m) => h("li", { class: `pv ${m.state}` },
                h("b", null, m.title), ` ${STATE_TEXT[m.state]}: ${VALUE_LABEL[m.field][m.value]}`, h("span", { class: "muted" }, ` · matched “${m.matched}” in ${m.match_in === "heading_path" ? "heading" : m.match_in}`),
                m.conflicts_with.length ? h("div", { class: "warn-text small-note" }, `Competes with: ${m.conflicts_with.join(", ")}`) : null))) : h("p", { class: "muted" }, "No candidate text matches these phrases."));
          } catch (e) { replace(out, h("p", { class: "err" }, e.message)); }
        }, 350);
        for (const el of [name, scope, op, phrases, field, value, prec, cs, docSel]) { el.addEventListener("input", preview); el.addEventListener("change", preview); }
        dlg.read = form;
        setTimeout(preview, 50);
        return [
          fieldLabel("Name", name),
          h("div", { class: "props" }, fieldLabel("Where to look", scope), fieldLabel("Match", op)),
          fieldLabel("Phrases (one per line, * at the end matches endings)", phrases),
          h("div", { class: "props" }, fieldLabel("Then suggest", field), fieldLabel("Value", value)),
          h("div", { class: "props" }, fieldLabel("Precedence (higher wins among the same kind of rule)", prec),
            h("div", { class: "field" }, h("span", { class: "label" }, "Options"), h("div", { class: "checks" }, h("label", null, cs, " case sensitive"), h("label", null, on, " enabled")))),
          fieldLabel("Note", note),
          h("hr"),
          h("div", { class: "field" }, h("label", { for: (docSel.id = "rule-pdoc") }, "Preview on"), docSel), out,
        ];
      },
      onSubmit: async (dlg) => {
        const body = dlg.read();
        if (r) await api.patch(`/api/rules/${r.id}`, body); else await api.post(`/api/projects/${pid}/rules`, body);
        saved = true;
      },
    });
    if (saved) await afterChange(r ? "Rule saved." : "Rule added.");
  }

  load();
  return root;
}
