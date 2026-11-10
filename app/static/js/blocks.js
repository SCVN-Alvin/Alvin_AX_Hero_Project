// Renders imported source blocks. Everything is built from text nodes and a fixed set of
// elements; imported HTML is never inserted as markup, so nothing in a brief can run.
import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { assetUrl, AUTO_LOAD_LIMIT, fmtBytes, typeLabel } from "./media.js";

const SAFE_HREF = /^(https?:|mailto:|tel:)/i;

function textRun(r) {
  if (!(r.b || r.i || r.c || r.x || r.u || r.m)) return document.createTextNode(r.s);
  const cls = ["r"];
  if (r.b) cls.push("b");
  if (r.i) cls.push("i");
  if (r.x) cls.push("x");
  if (r.u) cls.push("u");
  if (r.m) cls.push("mark", `mark-${String(r.m).replace(/[^a-z]/gi, "")}`);
  return h(r.c ? "code" : "span", { class: cls.join(" ") }, r.s);
}

export function renderRuns(runs, block, ctx) {
  const out = [];
  for (const r of runs || []) {
    if (r.t === "text") out.push(textRun(r));
    else if (r.t === "br") out.push(h("br"));
    else if (r.t === "a") out.push(...linkNodes(r, block, ctx));
    else if (r.t === "img") {
      const n = inlineImage(r, block);
      if (n) out.push(n);
    }
  }
  return out;
}

function refOf(block, id) {
  return (block.references || []).find((x) => x.id === id);
}

function linkNodes(r, block, ctx) {
  const inner = renderRuns(r.r, block, ctx);
  if (r.k === "ext") {
    if (!SAFE_HREF.test(r.href || "")) return inner;
    return [h("a", {
      class: "ext", href: r.href, target: "_blank", rel: "noopener noreferrer",
      title: "Opens a web page in your browser. Needs an internet connection; nothing is fetched until you click.",
    }, ...inner, icon("external"), h("span", { class: "sr-only" }, " (external link, needs internet)"))];
  }
  if (r.k === "anchor") {
    const id = (r.href || "").slice(1);
    return [h("button", { type: "button", class: "link", onclick: () => ctx.jumpToElement && ctx.jumpToElement(id) }, ...inner)];
  }
  const ref = refOf(block, r.ref);
  if (ref && ref.asset) {
    return [h("button", { type: "button", class: "link asset-link", title: `Preview ${ref.asset.name}`, onclick: () => ctx.openAsset(ref.asset.id) },
      ...inner, h("span", { class: "muted small-note" }, ` (${typeLabel(ref.asset)})`))];
  }
  if (ref && ref.kind === "page_link") {
    return [h("span", { class: "muted", title: "A link to another page of the original workspace. It was not part of this export." }, ...inner)];
  }
  return inner;
}

function inlineImage(r, block) {
  const ref = refOf(block, r.ref);
  const a = ref && ref.asset;
  if (!a || a.availability !== "available" || !a.inline_ok || a.kind !== "image" || a.size > AUTO_LOAD_LIMIT) return null;
  return h("img", { class: "inline-img", loading: "lazy", decoding: "async", alt: r.alt || a.name, src: assetUrl(a.id) });
}

function variantClasses(b) {
  const tokens = (b.variant || "").split(/\s+/).filter(Boolean);
  const cls = [];
  for (const t of tokens) {
    if (t.startsWith("color:")) cls.push("bg-" + t.slice(6).replace(/_background$/, "").replace(/[^a-z]/gi, ""));
  }
  return cls;
}

function mediaCard(kindIcon, name, detail, ...actions) {
  return h("div", { class: "media-card" }, icon(kindIcon),
    h("div", { class: "mc-text" }, h("div", { class: "mc-name" }, name), detail ? h("div", { class: "muted mc-detail" }, detail) : null),
    h("div", { class: "mc-actions" }, ...actions));
}

function mediaBlock(b, ctx) {
  const ref = (b.references || [])[0];
  const fig = h("figure", { class: "media-fig" });
  if (!ref) return fig;
  if (ref.kind === "external_image") {
    fig.append(h("div", { class: "media-card external" }, icon("external"),
      h("div", { class: "mc-text" }, h("div", { class: "mc-name" }, "External image"),
        h("div", { class: "muted mc-detail" }, "Not loaded: it lives on the web and needs an internet connection."),
        h("code", { class: "mc-url" }, ref.external_url)),
      SAFE_HREF.test(ref.external_url || "") ? h("a", { class: "btn small", href: ref.external_url, target: "_blank", rel: "noopener noreferrer" }, "Open in browser") : null));
    return fig;
  }
  const a = ref.asset;
  if (!a) return fig;
  const name = ref.caption && b.type !== "image" ? ref.caption : a.name;
  if (a.availability !== "available") {
    fig.append(h("div", { class: "media-card missing", role: "group", "aria-label": `Missing file ${a.name}` }, icon("alert"),
      h("div", { class: "mc-text" },
        h("div", { class: "mc-name" }, a.name, " ", h("span", { class: "badge blocked" }, a.availability === "missing" ? "Missing" : a.availability)),
        h("div", { class: "muted mc-detail" }, a.problem || "This file is not available."),
        h("div", { class: "muted mc-detail" }, "Original reference: ", h("code", null, a.source_path))),
      h("button", { class: "btn small", type: "button", onclick: () => ctx.openAsset(a.id) }, "Details")));
    return fig;
  }
  if (a.kind === "image" && a.inline_ok) {
    if (a.size > AUTO_LOAD_LIMIT) {
      const holder = h("div");
      const load = () => {
        const img = h("img", { class: "media-img", alt: a.name, decoding: "async" });
        const btn = h("button", { type: "button", class: "img-btn", "aria-label": `Open preview of ${a.name}`, onclick: () => ctx.openAsset(a.id) }, img);
        replace(holder, btn);
        img.src = assetUrl(a.id);
      };
      replace(holder, mediaCard("image", a.name, `${typeLabel(a)} · ${fmtBytes(a.size)} · loads only when you ask`,
        h("button", { class: "btn small", type: "button", onclick: load }, icon("eye"), "Show here"),
        h("button", { class: "btn small", type: "button", onclick: () => ctx.openAsset(a.id) }, "Open viewer")));
      fig.append(holder);
    } else {
      fig.append(h("button", { type: "button", class: "img-btn", "aria-label": `Open preview of ${a.name}`, onclick: () => ctx.openAsset(a.id) },
        h("img", { class: "media-img", loading: "lazy", decoding: "async", alt: a.name, src: assetUrl(a.id) })));
    }
  } else if (a.kind === "video" || a.kind === "audio") {
    fig.append(mediaCard(a.kind === "video" ? "play" : "play", name, `${typeLabel(a)} · ${fmtBytes(a.size)} · ${a.media_type}`,
      h("button", { class: "btn small primary", type: "button", onclick: () => ctx.openAsset(a.id) }, icon("play"), "Play")));
  } else {
    fig.append(mediaCard("file", name, `${typeLabel(a)} · ${fmtBytes(a.size)}`,
      h("button", { class: "btn small", type: "button", onclick: () => ctx.openAsset(a.id) }, "Details")));
  }
  const caption = b.type === "image" && b.text ? b.text : "";
  if (caption || (a.kind === "image")) {
    fig.append(h("figcaption", null, caption ? h("span", null, caption, " ") : null, h("span", { class: "muted" }, a.name)));
  }
  return fig;
}

function tableBlock(b, ctx) {
  const rows = new Map();
  for (const c of b.cells) {
    if (!rows.has(c.row)) rows.set(c.row, []);
    rows.get(c.row).push(c);
  }
  const head = h("thead");
  const body = h("tbody");
  for (const [ri, cells] of [...rows.entries()].sort((x, y) => x[0] - y[0])) {
    const tr = h("tr");
    for (const c of cells) {
      const inHead = ri < (b.header_rows || 0);
      const el = h(c.header || inHead ? "th" : "td", { scope: inHead ? "col" : c.header ? "row" : null }, ...renderRuns(c.runs, b, ctx));
      tr.append(el);
    }
    (ri < (b.header_rows || 0) ? head : body).append(tr);
  }
  const table = h("table", { class: `src-table ${b.variant.includes("properties") ? "props" : ""}` }, head.children.length ? head : null, body);
  return h("div", { class: "tbl-wrap", tabindex: "0", role: "region", "aria-label": `Table, ${b.rows} rows and ${b.cols} columns` }, table);
}

const BULLETS = ["•", "◦", "▪"];

export function renderBlock(b, ctx) {
  const common = {
    id: `b-${b.sequence}`,
    class: `blk blk-${b.type} ${variantClasses(b).join(" ")} ${b.section_role === "historical" ? "hist" : ""}`.trim(),
    dataset: { seq: String(b.sequence), eid: b.element_id || "" },
  };
  let el;
  switch (b.type) {
    case "title":
      el = h("h2", { ...common, class: common.class + " src-title" }, ...renderRuns(b.runs, b, ctx));
      break;
    case "heading": {
      const tag = `h${Math.min((b.heading_level || 2) + 1, 6)}`;
      const toggle = (b.variant || "").includes("toggle");
      el = h(tag, { ...common, class: `${common.class} src-h lvl-${b.heading_level}` }, ...renderRuns(b.runs, b, ctx),
        b.section_role === "historical" ? h("span", { class: "badge hist-badge", title: "Kept for reference. Excluded from task candidates by default." }, icon("history"), "Historical") : null,
        toggle ? null : null);
      break;
    }
    case "list_item": {
      let marker = BULLETS[Math.min(b.depth, 2)];
      if (b.list_kind === "numbered") marker = `${b.list_index ?? ""}.`;
      else if (b.list_kind === "todo") marker = b.checked ? "☑" : "☐";
      else if (b.list_kind === "toggle") marker = "▸";
      el = h("div", { ...common, role: "listitem" },
        h("span", { class: "li-mark", "aria-hidden": "true" }, marker),
        h("div", { class: "li-body" }, ...renderRuns(b.runs, b, ctx)));
      break;
    }
    case "table":
      el = h("div", common, tableBlock(b, ctx));
      break;
    case "image": case "video": case "audio": case "attachment":
      el = h("div", common, mediaBlock(b, ctx));
      break;
    case "divider":
      el = h("div", common, h("hr"));
      break;
    case "code":
      el = h("div", common, h("pre", null, h("code", null, b.text)));
      break;
    case "quote":
      el = h("blockquote", common, ...renderRuns(b.runs, b, ctx));
      break;
    case "callout":
      el = h("div", { ...common, class: common.class + " callout" }, ...renderRuns(b.runs, b, ctx));
      break;
    case "toggle":
      el = h("div", { ...common, class: common.class + " toggle" }, h("span", { class: "li-mark", "aria-hidden": "true" }, "▸"), h("b", null, ...renderRuns(b.runs, b, ctx)));
      break;
    default:
      el = h("p", common, ...renderRuns(b.runs, b, ctx));
  }
  el.style.setProperty("--depth", String(b.depth || 0));
  return el;
}

export function renderBlocks(blocks, ctx) {
  const frag = document.createDocumentFragment();
  for (const b of blocks) frag.append(renderBlock(b, ctx));
  return frag;
}

export function flash(el) {
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1800);
}
