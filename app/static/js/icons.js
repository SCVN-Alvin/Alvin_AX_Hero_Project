// Small inline icon set (no external assets). 16x16 stroke icons.
const NS = "http://www.w3.org/2000/svg";
const PATHS = {
  plus: "M8 3v10M3 8h10",
  search: "M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM11 11l3 3",
  close: "M4 4l8 8M12 4l-8 8",
  chevDown: "M4 6l4 4 4-4",
  chevRight: "M6 4l4 4-4 4",
  more: "M3.5 8h.01M8 8h.01M12.5 8h.01",
  check: "M3.5 8.5l3 3 6-7",
  alert: "M8 2l6.5 11.5h-13zM8 6.5v3M8 11.5h.01",
  block: "M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12zM3.8 3.8l8.4 8.4",
  board: "M2.5 3h3v10h-3zM6.5 3h3v6h-3zM10.5 3h3v8h-3z",
  list: "M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01",
  import: "M8 2v8M5 7l3 3 3-3M3 13h10",
  doc: "M4 2h5l3 3v9H4zM9 2v3h3M6 8h4M6 11h4",
  rules: "M3 4h6M11 4h2M3 8h2M7 8h6M3 12h8M13 12h0M9 2.5v3M5 6.5v3M11 10.5v3",
  settings: "M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4",
  menu: "M2.5 4h11M2.5 8h11M2.5 12h11",
  filter: "M2 3.5h12L9.5 9v4l-3-1.5V9z",
  trash: "M3 4.5h10M6 4.5V3h4v1.5M4.5 4.5l.5 8.5h6l.5-8.5",
  link: "M7 9a3 3 0 0 0 4 0l2-2a3 3 0 0 0-4-4l-1 1M9 7a3 3 0 0 0-4 0l-2 2a3 3 0 0 0 4 4l1-1",
  edit: "M3 13l1-3 7-7 2 2-7 7zM10 4l2 2",
  folder: "M2 4.5h4l1.5 1.5H14v7H2z",
  image: "M2.5 3h11v10h-11zM2.5 11l3.5-3.5 3 3 2-2 2.5 2.5M10.5 6.5h.01",
  play: "M5.5 3.5l7 4.5-7 4.5z",
  external: "M9 2.5h4.5V7M13.5 2.5L7 9M6 4H3v9h9v-3",
  history: "M2.5 8a5.5 5.5 0 1 0 1.8-4M2.5 2.5v3h3M8 5v3.2l2 1.3",
  arrowLeft: "M13 8H3M7 4L3 8l4 4",
  arrowRight: "M3 8h10M9 4l4 4-4 4",
  file: "M4 2h5l3 3v9H4zM9 2v3h3",
  download: "M8 2v8M5 7l3 3 3-3M3 13h10",
  eye: "M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
};

export function icon(name, label) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("class", "icon");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", PATHS[name] || "");
  svg.append(p);
  return svg;
}
