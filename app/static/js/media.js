// Local media: URLs, formatting helpers, and the preview window for images, GIFs, and video.
// Media is only ever requested from this application's own /api/assets route.
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";

export const assetUrl = (id) => `/api/assets/${id}/file`;
// Large files are loaded only when asked for, so opening a document never decodes hundreds of MB.
export const AUTO_LOAD_LIMIT = 3 * 1024 * 1024;

export function fmtBytes(n) {
  if (n === null || n === undefined) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = Number(n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? v : v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const isGif = (a) => a && a.media_type === "image/gif";
export function typeLabel(a) {
  if (!a) return "";
  if (isGif(a)) return "GIF";
  if (a.kind === "image") return (a.media_type.split("/")[1] || "image").toUpperCase();
  if (a.kind === "video") return "Video";
  if (a.kind === "audio") return "Audio";
  return "File";
}

const AVAIL_TEXT = {
  missing: "This file was not found when the brief was imported. The reference is kept.",
  blocked: "This file was not read because its path is unsafe.",
  unreadable: "This file exists but could not be read.",
};

/** Content for one asset in a preview: the media element, or an explanation. */
export function mediaElement(asset, { large = false } = {}) {
  if (asset.availability !== "available") {
    return h("div", { class: "lb-note", role: "status" },
      h("b", null, `Not available (${asset.availability})`),
      h("p", null, asset.problem || AVAIL_TEXT[asset.availability] || ""),
      h("p", { class: "muted" }, "Original reference: ", h("code", null, asset.source_path)),
      h("p", { class: "muted" }, "To attach the file, put it back in the media folder and import the same brief again. The existing import is kept and the file is added to it."));
  }
  if (!asset.inline_ok) {
    return h("div", { class: "lb-note" },
      h("b", null, "This file type is not previewed"),
      h("p", { class: "muted" }, "It can be opened by downloading it. Previews are limited to images, GIFs, video and audio so that imported files can never run as code."),
      h("a", { class: "btn", href: assetUrl(asset.id), download: asset.name }, icon("download"), "Download"));
  }
  const status = h("div", { class: "lb-status muted", role: "status" }, "Loading…");
  const fail = (msg) => replace(status, msg);
  if (asset.kind === "image") {
    const img = h("img", { alt: asset.name, class: large ? "lb-img" : "", decoding: "async" });
    img.addEventListener("load", () => status.remove());
    img.addEventListener("error", () => fail("This image could not be displayed. The stored copy may be damaged."));
    img.src = assetUrl(asset.id);
    return h("div", { class: "lb-wrap" }, img, status);
  }
  const tag = asset.kind === "audio" ? "audio" : "video";
  const el = h(tag, { controls: true, preload: "metadata", playsinline: true, class: "lb-video" });
  el.addEventListener("loadedmetadata", () => status.remove());
  el.addEventListener("error", () => fail("This file could not be played in this browser."));
  el.src = assetUrl(asset.id);
  return h("div", { class: "lb-wrap" }, el, status);
}

/**
 * Modal viewer over a list of assets. items: [{asset, used_in}], index: start position,
 * onJump(sequence): called when the person picks a place the file is used.
 */
export function openMediaViewer({ items, index = 0, onJump }) {
  if (!items.length) return;
  let i = Math.max(0, Math.min(index, items.length - 1));
  const dlg = h("dialog", { class: "lightbox", "aria-label": "Media preview" });
  const title = h("div", { class: "lb-title" });
  const meta = h("div", { class: "lb-meta muted" });
  const stage = h("div", { class: "lb-stage" });
  const used = h("div", { class: "lb-used" });
  const pos = h("span", { class: "muted", "aria-live": "polite" });
  const prev = h("button", { class: "btn", type: "button", onclick: () => go(-1) }, icon("arrowLeft"), "Previous");
  const next = h("button", { class: "btn", type: "button", onclick: () => go(1) }, "Next", icon("arrowRight"));
  const closeBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Close preview", onclick: () => dlg.close() }, icon("close"));

  function stopMedia() {
    stage.querySelectorAll("video,audio").forEach((m) => { m.pause(); m.removeAttribute("src"); m.load(); });
  }
  function show() {
    stopMedia();
    const { asset, used_in: where = [] } = items[i];
    replace(title, asset.name);
    replace(meta, [typeLabel(asset), asset.size !== null ? fmtBytes(asset.size) : "", asset.media_type, asset.hash ? `sha256 ${asset.hash.slice(0, 12)}…` : ""]
      .filter(Boolean).join(" · "));
    replace(stage, mediaElement(asset, { large: true }));
    replace(used);
    if (where.length) {
      used.append(h("span", { class: "muted" }, "Used in "));
      where.forEach((w, k) => {
        used.append(k ? ", " : "", h("button", { class: "link", type: "button", onclick: () => { dlg.close(); if (onJump) onJump(w.sequence); } },
          `${w.heading_path[w.heading_path.length - 1] || "Document start"} (block ${w.sequence + 1})`));
      });
    }
    replace(pos, `${i + 1} of ${items.length}`);
    prev.disabled = i === 0;
    next.disabled = i === items.length - 1;
  }
  function go(d) {
    const n = i + d;
    if (n < 0 || n >= items.length) return;
    i = n;
    show();
  }
  dlg.append(
    h("div", { class: "lb-head" }, h("div", { class: "lb-heads" }, title, meta), closeBtn),
    stage,
    h("div", { class: "lb-foot" }, h("div", { class: "lb-nav" }, prev, pos, next), used));
  dlg.addEventListener("keydown", (e) => {
    if (e.target && /^(VIDEO|AUDIO|INPUT)$/.test(e.target.tagName)) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
  });
  dlg.addEventListener("close", () => { stopMedia(); dlg.remove(); });
  document.body.append(dlg);
  show();
  dlg.showModal();
  closeBtn.focus();
  return dlg;
}
