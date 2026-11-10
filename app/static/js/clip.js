// The one main reference clip of a task: a friendly caption, an optional playback range, and a player that only loads
// when it is asked to play. Used by the import review and by the board's task panel.
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { assetUrl, AUTO_LOAD_LIMIT, fmtBytes, typeLabel } from "./media.js";
import { fieldLabel, formDialog } from "./ui.js";

export function fmtClock(ms) {
  if (ms === null || ms === undefined) return "";
  const total = ms / 1000;
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  const sec = Number.isInteger(s) ? String(s).padStart(2, "0") : s.toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

/** "1:05", "1:05.5", "65" → milliseconds; "" → null; anything else → NaN. */
export function parseClock(text) {
  const t = String(text ?? "").trim();
  if (!t) return null;
  const m = /^(?:(\d+):)?(\d+(?:\.\d+)?)$/.exec(t);
  if (!m) return NaN;
  return Math.round(((m[1] ? Number(m[1]) * 60 : 0) + Number(m[2])) * 1000);
}

export function rangeLabel(start, end) {
  if (start === null || start === undefined) return "";
  return end === null || end === undefined ? `from ${fmtClock(start)}` : `${fmtClock(start)} – ${fmtClock(end)}`;
}

export const isVideo = (a) => a && a.kind === "video";
export const isGifAsset = (a) => a && a.media_type === "image/gif";

export function thumbFor(ref) {
  const a = ref.asset;
  if (a.availability !== "available") return h("div", { class: "thumb ph problem" }, icon("alert"), h("span", null, a.availability));
  if (a.kind === "image" && a.inline_ok && a.size <= AUTO_LOAD_LIMIT) return h("img", { class: "thumb", loading: "lazy", decoding: "async", alt: "", src: assetUrl(a.id) });
  return h("div", { class: "thumb ph" }, icon(a.kind === "video" ? "play" : "image"), h("span", null, typeLabel(a)));
}

/** A player that shows a play button until asked. Honours start/end: it seeks to the start and pauses at the end. */
export function clipPlayer(ref, { large = false } = {}) {
  const a = ref.asset;
  const box = h("div", { class: `clip ${large ? "large" : ""}` });
  const range = rangeLabel(ref.start_ms, ref.end_ms);
  const kindWord = isGifAsset(a) ? "animation" : a.kind === "video" ? "video" : "picture";
  function load() {
    let el;
    if (a.kind === "video") {
      el = h("video", { controls: true, autoplay: true, playsinline: true, preload: "auto", class: "clip-video", "aria-label": ref.label });
      const start = ref.start_ms ? ref.start_ms / 1000 : 0;
      el.addEventListener("loadedmetadata", () => { if (start) el.currentTime = start; });
      if (ref.end_ms) {
        el.addEventListener("timeupdate", () => {
          if (el.currentTime >= ref.end_ms / 1000) { el.pause(); el.currentTime = start; }
        });
      }
      el.addEventListener("error", () => replace(box, h("p", { class: "muted pad" }, "This video could not be played in this browser.")));
      el.src = assetUrl(a.id);
    } else {
      el = h("img", { class: "clip-img", alt: ref.label, src: assetUrl(a.id), decoding: "async" });
    }
    replace(box, el);
  }
  if (a.availability !== "available") {
    box.append(h("div", { class: "clip-missing", role: "status" }, icon("alert"), h("span", null, `The file for “${ref.label}” is not available (${a.availability}). Import the brief again with the file in place.`)));
  } else {
    box.append(h("button", { type: "button", class: "clip-start", "aria-label": `Play ${kindWord}: ${ref.label}`, onclick: load },
      h("span", { class: "clip-play" }, icon("play")),
      h("span", { class: "clip-label" }, ref.label),
      h("span", { class: "clip-sub muted" }, [typeLabel(a), range].filter(Boolean).join(" · "))));
  }
  return box;
}

/** Set or clear the part of a video that demonstrates the task. Nothing is guessed: an empty range plays the whole clip. */
export async function openRangeDialog(ref, save) {
  const a = ref.asset;
  await formDialog({
    title: `Playback range: ${ref.label}`, submitLabel: "Save range", wide: true,
    body: () => {
      const start = h("input", { type: "text", name: "start", value: fmtClock(ref.start_ms), placeholder: "0:00", size: "8", inputmode: "decimal", "aria-label": "Start time" });
      const end = h("input", { type: "text", name: "end", value: fmtClock(ref.end_ms), placeholder: "end of clip", size: "8", inputmode: "decimal", "aria-label": "End time" });
      const out = [h("p", { class: "muted" }, "Type times as minutes:seconds, for example 0:12 or 1:05.5. Leave both empty to play the whole clip. The video file itself is never changed or copied.")];
      if (a.availability === "available" && a.kind === "video") {
        const v = h("video", { controls: true, preload: "metadata", class: "clip-video", "aria-label": "Preview to pick times" });
        v.src = assetUrl(a.id);
        const grab = (target) => () => { target.value = fmtClock(Math.round(v.currentTime * 1000)); };
        out.push(v, h("div", { class: "actions-row" },
          h("button", { class: "btn small", type: "button", onclick: grab(start) }, "Use current time as start"),
          h("button", { class: "btn small", type: "button", onclick: grab(end) }, "Use current time as end")));
      }
      out.push(h("div", { class: "props" }, fieldLabel("Start", start), fieldLabel("End", end)));
      return out;
    },
    onSubmit: async (dlg) => {
      const s = parseClock(dlg.dialog.querySelector('[name="start"]').value);
      const e = parseClock(dlg.dialog.querySelector('[name="end"]').value);
      if (Number.isNaN(s) || Number.isNaN(e)) throw new Error("Enter times like 0:12 or 1:05.5.");
      if (e !== null && s === null) throw new Error("Set a start time before an end time.");
      if (s !== null && e !== null && e <= s) throw new Error("The end must be after the start.");
      await save({ start_ms: s, end_ms: e });
    },
  });
}

export function fileDetails(ref) {
  const a = ref.asset;
  return h("dl", { class: "kv small-kv" },
    h("dt", null, "File"), h("dd", null, a.name),
    h("dt", null, "Type"), h("dd", null, [typeLabel(a), a.size !== null ? fmtBytes(a.size) : ""].filter(Boolean).join(" · ")),
    ref.section ? h("dt", null, "Section") : null, ref.section ? h("dd", null, ref.section) : null,
    h("dt", null, "Source block"), h("dd", null, String((ref.sequence ?? 0) + 1)));
}
