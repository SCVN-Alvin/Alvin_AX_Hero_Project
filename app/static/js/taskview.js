// Pieces of the task-first view, shared by the import review and the board's task panel: the one main clip, the
// short list of suggested clips, and the "needs clarification" warning that is never tucked away.
import { clipPlayer, fileDetails, rangeLabel, thumbFor } from "./clip.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";

const ROLE_LABEL = { primary_demo: "Main clip", supporting_demo: "Supporting clip", asset_reference: "Asset reference", source_evidence: "Source evidence" };
export const ROLE_OPTIONS = [["supporting_demo", "Supporting clip"], ["asset_reference", "Asset reference"], ["source_evidence", "Source evidence"]];
export const roleLabel = (r) => ROLE_LABEL[r] || r;

/** The chosen main clip: a player that loads on demand, its friendly caption and range, and the way to change them. */
export function primaryCard(ref, { locked, onRename, onRange, onChange, onClear, onPreview }) {
  const range = rangeLabel(ref.start_ms, ref.end_ms);
  return h("div", { class: "tf-primary", "data-primary": ref.reference_id },
    h("div", { class: "tf-primary-head" },
      h("h4", null, "Main reference"),
      h("span", { class: "tf-primary-label" }, ref.label),
      range ? h("span", { class: "chip", title: "Playback range" }, icon("play"), range) : null),
    clipPlayer(ref, { large: true }),
    locked && !onChange ? null : h("div", { class: "actions-row" },
      onRename ? h("button", { class: "btn small", type: "button", onclick: onRename }, icon("edit"), "Rename") : null,
      onRange && ref.asset.kind === "video" ? h("button", { class: "btn small", type: "button", onclick: onRange }, range ? "Change range…" : "Set range…") : null,
      onChange ? h("button", { class: "btn small", type: "button", onclick: onChange }, "Choose another…") : null,
      onClear ? h("button", { class: "btn subtle small", type: "button", onclick: onClear }, "Clear") : null,
      onPreview ? h("button", { class: "btn subtle small", type: "button", onclick: onPreview }, "Open large") : null),
    h("details", { class: "tf-file" }, h("summary", { class: "muted" }, "File details"), fileDetails(ref)));
}

export const tierText = (ref) => (ref.tier === 0 ? "In this task's own lines" : ref.tier === 1 ? "Directly under this task" : "Shares wording with this task");

/** One suggested clip: what it is, where it came from, why it was suggested, and the one explicit way to choose it. */
export function suggestionCard(ref, { onUse, onPreview, onDismiss, aiSuggested = false, proposalActions = null }) {
  const tier = tierText(ref);
  return h("div", { class: "tf-suggest", "data-ref": ref.reference_id },
    h("button", { type: "button", class: "rv-thumb", "aria-label": `Preview ${ref.label}`, onclick: onPreview }, thumbFor(ref)),
    h("div", { class: "tf-suggest-main" },
      h("div", { class: "tf-suggest-title" }, h("b", null, ref.label), aiSuggested ? h("span", { class: "badge sug", title: "Proposed from the caption only; the AI has not seen the clip" }, "AI suggested") : null),
      h("div", { class: "muted small-note" }, [ref.section, tier].filter(Boolean).join(" · ")),
      ref.reason ? h("div", { class: "muted small-note tf-reason", title: ref.reason }, ref.reason) : null),
    h("div", { class: "actions-row" },
      proposalActions || h("button", { class: "btn small primary", type: "button", onclick: onUse }, "Use as main clip"),
      onDismiss ? h("button", { class: "btn subtle small", type: "button", onclick: onDismiss }, "Not this") : null));
}

/** Open questions stay in view above everything collapsible; resolved ones are one click away. */
export function clarificationPanel(items, { onAdd, onResolve, onReopen, onDelete, onShow, canAdd = true, ownTitle = "" }) {
  const open = items.filter((c) => c.state === "open");
  const done = items.filter((c) => c.state === "resolved");
  const line = (c) => h("li", { class: `tf-q ${c.state}`, "data-question": c.id },
    h("div", { class: "tf-q-text" }, c.state === "open" ? icon("alert") : icon("check"), h("span", null, c.text),
      c.origin === "ai" ? h("span", { class: "badge sug" }, "AI asked") : null,
      c.candidate_title && c.candidate_title !== ownTitle ? h("span", { class: "muted small-note" }, ` (${c.candidate_title})`) : null),
    c.resolution ? h("div", { class: "muted small-note" }, "Decision: ", c.resolution) : null,
    h("div", { class: "actions-row" },
      c.blocks.length && onShow ? h("button", { class: "btn subtle small", type: "button", onclick: () => onShow(c.blocks[0]) }, `Show the lines (${c.blocks.length})`) : null,
      c.state === "open" && onResolve ? h("button", { class: "btn small", type: "button", onclick: () => onResolve(c) }, "Mark resolved…") : null,
      c.state === "resolved" && onReopen ? h("button", { class: "btn subtle small", type: "button", onclick: () => onReopen(c) }, "Reopen") : null,
      onDelete ? h("button", { class: "btn subtle small", type: "button", onclick: () => onDelete(c) }, "Remove") : null));
  if (!open.length && !done.length) {
    return canAdd && onAdd ? h("div", { class: "tf-flag" }, h("button", { class: "btn subtle small", type: "button", "data-flag-question": "", onclick: onAdd }, icon("plus"), "Flag a question…")) : null;
  }
  return h("div", { class: `tf-clarify ${open.length ? "has-open" : ""}`, role: open.length ? "group" : null, "aria-label": open.length ? "Needs clarification" : null },
    open.length ? h("div", { class: "tf-clarify-head" }, icon("alert"), h("b", null, `Needs clarification (${open.length})`), h("span", { class: "muted" }, " The source leaves this open. It stays here until someone decides.")) : null,
    open.length ? h("ul", { class: "list-plain" }, open.map(line)) : null,
    done.length ? h("details", { class: "tf-resolved" }, h("summary", { class: "muted" }, `Resolved (${done.length})`), h("ul", { class: "list-plain" }, done.map(line))) : null,
    canAdd && onAdd ? h("div", { class: "actions-row" }, h("button", { class: "btn subtle small", type: "button", onclick: onAdd }, icon("plus"), "Flag a question…")) : null);
}
