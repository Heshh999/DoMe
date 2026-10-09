#!/usr/bin/env node
// brand/scripts/export.mjs — renders the editable SVG brand sources (../*.svg) into the PNG and ICO
// files under ../exports/. Zero dependencies: Node 22 built-ins only.
//
// Why a rasterizer lives here: this repository's toolchain has no SVG renderer (no sharp/resvg in
// mobile-app/node_modules, ImageMagick's SVG delegate `rsvg-convert` is absent, no cairo). Instead of
// shipping hand-drawn pixel art that could drift from the SVG sources, this script parses a small,
// strictly enforced SVG subset (see parseSvg) and renders it with signed-distance anti-aliasing. Any
// SVG feature outside the subset makes the script fail loudly instead of silently rendering wrong.
//
// Run:            node brand/scripts/export.mjs          (writes brand/exports/**)
// Check drift:    node brand/scripts/export.mjs --check  (exit 1 if any export differs from a fresh render)
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";

export const BRAND_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const EXPORTS_DIR = resolve(BRAND_DIR, "exports");

/** Brand colour tokens used by the export plan (kept in sync with BRAND.md by tests/brand.test.mjs). */
export const TOKENS = Object.freeze({
  night: "#0b0f17",
  paper: "#f6f7fb",
  slate: "#182033",
  borderStrong: "#37445f",
  signal: "#5ee1c0",
  signalDeep: "#0c8f70",
  ink: "#101522",
  cloud: "#eef2f8",
  white: "#ffffff",
});

// ---------------------------------------------------------------------------------------------
// Colours
// ---------------------------------------------------------------------------------------------

/** @typedef {[number, number, number]} RGB */

/**
 * `#rrggbb` → [r, g, b]. Throws on anything else (no names, no rgba(), no short form).
 * @param {string} text
 * @returns {RGB}
 */
export function parseHexColor(text) {
  const m = /^#([0-9a-fA-F]{6})$/.exec(text.trim());
  if (!m) throw new Error(`unsupported colour "${text}" (only #rrggbb is allowed in brand SVGs)`);
  const n = parseInt(m[1], 16);
  return /** @type {RGB} */ ([(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff]);
}

/**
 * @param {string|undefined} text
 * @param {string} currentColor
 * @returns {RGB|null|undefined} null = "none", undefined = not specified
 */
function parsePaint(text, currentColor) {
  if (text === undefined || text === null) return undefined;
  const t = text.trim();
  if (t === "none") return null;
  if (t === "currentColor") return parseHexColor(currentColor);
  return parseHexColor(t);
}

// ---------------------------------------------------------------------------------------------
// SVG subset parser
// ---------------------------------------------------------------------------------------------

const ALLOWED_ELEMENTS = new Set(["svg", "title", "desc", "g", "rect", "circle", "path", "line"]);
const COMMON_ATTRS = new Set(["id", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"]);
/** @type {Record<string, Set<string>>} */
const ELEMENT_ATTRS = {
  svg: new Set(["xmlns", "viewBox", "width", "height", "role", "aria-labelledby"]),
  title: new Set(["id"]),
  desc: new Set(["id"]),
  g: COMMON_ATTRS,
  rect: new Set([...COMMON_ATTRS, "x", "y", "width", "height", "rx", "ry"]),
  circle: new Set([...COMMON_ATTRS, "cx", "cy", "r"]),
  path: new Set([...COMMON_ATTRS, "d"]),
  line: new Set([...COMMON_ATTRS, "x1", "y1", "x2", "y2"]),
};

/**
 * @typedef {object} Shape
 * @property {"rrect"|"circle"|"polyline"} kind
 * @property {string|undefined} id
 * @property {string|undefined} group   id of the innermost enclosing <g>
 * @property {RGB|null} fill
 * @property {RGB|null} stroke
 * @property {number} strokeWidth
 * @property {number} [x] @property {number} [y] @property {number} [w] @property {number} [h] @property {number} [rx]
 * @property {number} [cx] @property {number} [cy] @property {number} [r]
 * @property {number[][]} [points]
 * @property {boolean} [closed]
 */

/**
 * @typedef {object} SvgDoc
 * @property {number} width   viewBox width (user units)
 * @property {number} height  viewBox height (user units)
 * @property {Shape[]} shapes in document (paint) order
 */

/**
 * @param {string|undefined} value
 * @param {string} what
 * @returns {number}
 */
function num(value, what) {
  if (value === undefined) throw new Error(`missing attribute ${what}`);
  const n = Number(value);
  if (!Number.isFinite(n)) throw new Error(`attribute ${what} is not a finite number: "${value}"`);
  return n;
}

/**
 * @param {string} text
 * @returns {{closing: boolean, name: string, attrs: Record<string, string>, selfClosing: boolean}[]}
 */
function tokenizeTags(text) {
  // Strip the XML declaration and comments; text nodes (title/desc content) are ignored on purpose.
  const stripped = text.replace(/<\?xml[\s\S]*?\?>/g, "").replace(/<!--[\s\S]*?-->/g, "");
  if (/<!\[CDATA\[|<!DOCTYPE|<!ENTITY/i.test(stripped)) throw new Error("CDATA/DOCTYPE/ENTITY are not allowed in brand SVGs");
  const tagRe = /<(\/?)([A-Za-z][\w:-]*)((?:\s+[\w:-]+\s*=\s*"[^"]*")*)\s*(\/?)>/g;
  const tags = [];
  let m;
  while ((m = tagRe.exec(stripped)) !== null) {
    /** @type {Record<string, string>} */
    const attrs = {};
    const attrRe = /([\w:-]+)\s*=\s*"([^"]*)"/g;
    let a;
    while ((a = attrRe.exec(m[3])) !== null) attrs[a[1]] = a[2];
    tags.push({ closing: m[1] === "/", name: m[2], attrs, selfClosing: m[4] === "/" });
  }
  // Anything that looks like a tag but did not match the strict pattern (single quotes, style attributes
  // with odd quoting, …) is a subset violation.
  const strictCount = (stripped.match(/<[A-Za-z/]/g) || []).length;
  if (strictCount !== tags.length) throw new Error("SVG contains a tag the brand subset parser cannot read (use double-quoted attributes only)");
  return tags;
}

/**
 * Parses one brand SVG into flat shapes. Supported: <svg viewBox>, <title>, <desc>, <g> (inherits
 * fill/stroke/stroke-width, no transform), <rect> (optional rx), <circle>, <line>, <path d> with
 * M/m L/l H/h V/v A/a Z/z only and fill="none". Colours: #rrggbb, none, currentColor. Caps/joins: round.
 * @param {string} text
 * @param {{currentColor?: string}} [options]
 * @returns {SvgDoc}
 */
export function parseSvg(text, options = {}) {
  const currentColor = options.currentColor ?? "#000000";
  const tags = tokenizeTags(text);
  /** @type {Shape[]} */
  const shapes = [];
  let width = 0;
  let height = 0;
  /** @type {{name: string, fill: RGB|null|undefined, stroke: RGB|null|undefined, strokeWidth: number|undefined, group: string|undefined}[]} */
  const stack = []; // inherited paint state per open element
  let sawSvg = false;

  for (const tag of tags) {
    if (tag.closing) {
      if (stack.length === 0) throw new Error(`unbalanced closing tag </${tag.name}>`);
      const open = stack.pop();
      if (open === undefined || open.name !== tag.name) throw new Error(`mismatched closing tag </${tag.name}> for <${open?.name ?? "?"}>`);
      continue;
    }
    if (!ALLOWED_ELEMENTS.has(tag.name)) throw new Error(`element <${tag.name}> is outside the brand SVG subset`);
    for (const key of Object.keys(tag.attrs)) {
      if (!ELEMENT_ATTRS[tag.name].has(key)) throw new Error(`attribute ${key} on <${tag.name}> is outside the brand SVG subset`);
    }
    const parent = stack.length ? stack[stack.length - 1] : { name: "", fill: undefined, stroke: undefined, strokeWidth: undefined, group: undefined };
    const cap = tag.attrs["stroke-linecap"];
    const join = tag.attrs["stroke-linejoin"];
    if (cap !== undefined && cap !== "round") throw new Error(`stroke-linecap="${cap}" is not supported (round only)`);
    if (join !== undefined && join !== "round") throw new Error(`stroke-linejoin="${join}" is not supported (round only)`);

    const inherited = {
      name: tag.name,
      fill: tag.attrs.fill !== undefined ? parsePaint(tag.attrs.fill, currentColor) : parent.fill,
      stroke: tag.attrs.stroke !== undefined ? parsePaint(tag.attrs.stroke, currentColor) : parent.stroke,
      strokeWidth: tag.attrs["stroke-width"] !== undefined ? num(tag.attrs["stroke-width"], "stroke-width") : parent.strokeWidth,
      group: tag.name === "g" ? tag.attrs.id ?? parent.group : parent.group,
    };

    if (tag.name === "svg") {
      if (sawSvg) throw new Error("nested <svg> is not supported");
      sawSvg = true;
      const vb = (tag.attrs.viewBox ?? "").trim().split(/[\s,]+/).map(Number);
      if (vb.length !== 4 || vb.some((v) => !Number.isFinite(v)) || vb[0] !== 0 || vb[1] !== 0 || vb[2] <= 0 || vb[3] <= 0) {
        throw new Error('viewBox must be "0 0 W H" with positive W and H');
      }
      width = vb[2];
      height = vb[3];
    } else if (!sawSvg) {
      throw new Error(`<${tag.name}> before <svg>`);
    }

    const base = {
      id: tag.attrs.id,
      group: inherited.group,
      fill: inherited.fill === undefined ? /** @type {RGB} */ ([0, 0, 0]) : inherited.fill, // SVG default fill is black
      stroke: inherited.stroke === undefined ? null : inherited.stroke,
      strokeWidth: inherited.strokeWidth === undefined ? 1 : inherited.strokeWidth,
    };
    if (base.stroke !== null && !(base.strokeWidth > 0)) throw new Error("stroke-width must be > 0 when stroke is set");

    switch (tag.name) {
      case "rect": {
        const rx = tag.attrs.rx !== undefined ? num(tag.attrs.rx, "rx") : tag.attrs.ry !== undefined ? num(tag.attrs.ry, "ry") : 0;
        if (tag.attrs.ry !== undefined && tag.attrs.rx !== undefined && num(tag.attrs.ry, "ry") !== rx) throw new Error("rect rx and ry must be equal");
        const w = num(tag.attrs.width, "rect width");
        const h = num(tag.attrs.height, "rect height");
        if (w <= 0 || h <= 0 || rx < 0 || rx > Math.min(w, h) / 2) throw new Error("rect dimensions out of range");
        shapes.push({ kind: "rrect", ...base, x: tag.attrs.x !== undefined ? num(tag.attrs.x, "x") : 0, y: tag.attrs.y !== undefined ? num(tag.attrs.y, "y") : 0, w, h, rx });
        break;
      }
      case "circle": {
        const r = num(tag.attrs.r, "circle r");
        if (r <= 0) throw new Error("circle r must be > 0");
        shapes.push({ kind: "circle", ...base, cx: num(tag.attrs.cx, "cx"), cy: num(tag.attrs.cy, "cy"), r });
        break;
      }
      case "line": {
        if (base.stroke === null) throw new Error("<line> needs a stroke");
        shapes.push({
          kind: "polyline",
          ...base,
          fill: null,
          points: [
            [num(tag.attrs.x1, "x1"), num(tag.attrs.y1, "y1")],
            [num(tag.attrs.x2, "x2"), num(tag.attrs.y2, "y2")],
          ],
          closed: false,
        });
        break;
      }
      case "path": {
        if (base.fill !== null) throw new Error('<path> must have fill="none" in the brand subset (filled shapes use rect/circle)');
        if (base.stroke === null) throw new Error("<path> needs a stroke");
        for (const sub of flattenPathData(tag.attrs.d ?? "")) {
          shapes.push({ kind: "polyline", ...base, fill: null, points: sub.points, closed: sub.closed });
        }
        break;
      }
      default:
        break; // svg, title, desc, g: structural only
    }
    if (!tag.selfClosing) stack.push(inherited);
  }
  if (stack.length !== 0) throw new Error(`unclosed element <${stack[stack.length - 1].name}>`);
  if (!sawSvg) throw new Error("no <svg> root element");
  return { width, height, shapes };
}

/**
 * Flattens path data (M/m L/l H/h V/v A/a Z/z) into polylines. Arcs are approximated by segments every
 * ARC_STEP_RAD (0.0175 rad = 1°); at 512 px that is well under 0.05 px of chord error for every arc here.
 * @param {string} d
 * @returns {{points: number[][], closed: boolean}[]}
 */
export function flattenPathData(d) {
  const tokens = d.match(/[MmLlHhVvAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) ?? [];
  const unexpected = d.replace(/[MmLlHhVvAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[\s,]/g, "");
  if (unexpected.length) throw new Error(`path data contains unsupported commands or characters: "${unexpected}" (subset: M L H V A Z)`);
  /** @type {{points: number[][], closed: boolean}[]} */
  const subpaths = [];
  /** @type {{points: number[][], closed: boolean}|null} */
  let current = null;
  let x = 0;
  let y = 0;
  let startX = 0;
  let startY = 0;
  let i = 0;
  /** @type {string|null} */
  let cmd = null;
  const next = () => {
    if (i >= tokens.length || /[A-Za-z]/.test(tokens[i])) throw new Error(`path data: missing number after ${cmd}`);
    return Number(tokens[i++]);
  };
  while (i < tokens.length) {
    const t = tokens[i];
    if (/[A-Za-z]/.test(t)) {
      cmd = t;
      i++;
    } else if (cmd === null) {
      throw new Error("path data must start with M");
    } else if (cmd === "M") cmd = "L";
    else if (cmd === "m") cmd = "l";
    else if (cmd === "Z" || cmd === "z") throw new Error("path data: numbers after Z");

    switch (cmd) {
      case "M":
      case "m": {
        const nx = next();
        const ny = next();
        x = cmd === "m" && current ? x + nx : nx;
        y = cmd === "m" && current ? y + ny : ny;
        current = { points: [[x, y]], closed: false };
        subpaths.push(current);
        startX = x;
        startY = y;
        break;
      }
      case "L":
      case "l": {
        if (!current) throw new Error("path data: L before M");
        const nx = next();
        const ny = next();
        x = cmd === "l" ? x + nx : nx;
        y = cmd === "l" ? y + ny : ny;
        current.points.push([x, y]);
        break;
      }
      case "H":
      case "h": {
        if (!current) throw new Error("path data: H before M");
        const nx = next();
        x = cmd === "h" ? x + nx : nx;
        current.points.push([x, y]);
        break;
      }
      case "V":
      case "v": {
        if (!current) throw new Error("path data: V before M");
        const ny = next();
        y = cmd === "v" ? y + ny : ny;
        current.points.push([x, y]);
        break;
      }
      case "A":
      case "a": {
        if (!current) throw new Error("path data: A before M");
        const rx = next();
        const ry = next();
        const rot = next();
        const large = next();
        const sweep = next();
        const ex0 = next();
        const ey0 = next();
        const ex = cmd === "a" ? x + ex0 : ex0;
        const ey = cmd === "a" ? y + ey0 : ey0;
        if (rot !== 0) throw new Error("path data: rotated arcs are not supported (x-axis-rotation must be 0)");
        if ((large !== 0 && large !== 1) || (sweep !== 0 && sweep !== 1)) throw new Error("path data: arc flags must be 0 or 1");
        for (const p of arcToPoints(x, y, rx, ry, large === 1, sweep === 1, ex, ey)) current.points.push(p);
        x = ex;
        y = ey;
        break;
      }
      case "Z":
      case "z": {
        if (!current) throw new Error("path data: Z before M");
        current.closed = true;
        x = startX;
        y = startY;
        current = null;
        break;
      }
      default:
        throw new Error(`path data: unsupported command ${cmd}`);
    }
  }
  for (const sp of subpaths) if (sp.points.length < 2 && !sp.closed) throw new Error("path data: a subpath with a single point has no geometry");
  return subpaths;
}

const ARC_STEP_RAD = Math.PI / 180;

/**
 * SVG arc (F.6.5 endpoint → centre parameterisation), returns the points after the start point.
 * @param {number} x1 @param {number} y1 @param {number} rxIn @param {number} ryIn
 * @param {boolean} largeArc @param {boolean} sweep @param {number} x2 @param {number} y2
 * @returns {number[][]}
 */
export function arcToPoints(x1, y1, rxIn, ryIn, largeArc, sweep, x2, y2) {
  let rx = Math.abs(rxIn);
  let ry = Math.abs(ryIn);
  if (rx === 0 || ry === 0) return [[x2, y2]];
  if (x1 === x2 && y1 === y2) return [];
  const dx2 = (x1 - x2) / 2;
  const dy2 = (y1 - y2) / 2;
  // scale radii up if the endpoints are too far apart
  const lambda = (dx2 * dx2) / (rx * rx) + (dy2 * dy2) / (ry * ry);
  if (lambda > 1) {
    const s = Math.sqrt(lambda);
    rx *= s;
    ry *= s;
  }
  const sign = largeArc === sweep ? -1 : 1;
  const sq = Math.max(0, (rx * rx * ry * ry - rx * rx * dy2 * dy2 - ry * ry * dx2 * dx2) / (rx * rx * dy2 * dy2 + ry * ry * dx2 * dx2));
  const coef = sign * Math.sqrt(sq);
  const cxp = coef * ((rx * dy2) / ry);
  const cyp = coef * (-(ry * dx2) / rx);
  const cx = cxp + (x1 + x2) / 2;
  const cy = cyp + (y1 + y2) / 2;
  const ux = (dx2 - cxp) / rx;
  const uy = (dy2 - cyp) / ry;
  const vx = (-dx2 - cxp) / rx;
  const vy = (-dy2 - cyp) / ry;
  const theta1 = Math.atan2(uy, ux);
  let dtheta = Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
  if (!sweep && dtheta > 0) dtheta -= 2 * Math.PI;
  else if (sweep && dtheta < 0) dtheta += 2 * Math.PI;
  const steps = Math.max(1, Math.ceil(Math.abs(dtheta) / ARC_STEP_RAD));
  /** @type {number[][]} */
  const points = [];
  for (let k = 1; k <= steps; k++) {
    const t = theta1 + (dtheta * k) / steps;
    points.push([cx + rx * Math.cos(t), cy + ry * Math.sin(t)]);
  }
  points[points.length - 1] = [x2, y2]; // land exactly on the endpoint
  return points;
}

// ---------------------------------------------------------------------------------------------
// Rasterizer
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} RenderOptions
 * @property {number} width   output width in px
 * @property {number} [height] output height in px (default: width × viewBox aspect)
 * @property {RGB|null} [background] fills the whole canvas first (null = transparent)
 * @property {string[]} [dropIds] shapes (by id) to leave out, e.g. ["tile"] for full-bleed exports
 * @property {number} [glyphScale] uniform scale about the canvas centre applied to shapes in group "glyph"
 * @property {Record<string, RGB>} [recolor] replace every paint equal to key colour (as #rrggbb) with value
 */

/**
 * Renders a parsed SVG into straight-alpha RGBA bytes (row-major, 4 bytes per pixel).
 * Anti-aliasing: per shape, a signed distance in pixels is evaluated at every pixel centre inside the
 * shape's bounding box and converted to coverage with clamp(0.5 − d, 0, 1); shapes are composited
 * source-over in document order. This is exact for straight edges and very close for the curves here.
 * @param {SvgDoc} doc
 * @param {RenderOptions} options
 * @returns {{width: number, height: number, data: Uint8Array}}
 */
export function render(doc, options) {
  const W = options.width;
  const H = options.height ?? Math.round((W * doc.height) / doc.width);
  if (!(W > 0 && H > 0 && Number.isInteger(W) && Number.isInteger(H))) throw new Error("render: width/height must be positive integers");
  const sx = W / doc.width;
  const sy = H / doc.height;
  const scale = Math.min(sx, sy); // uniform; the viewBox is centred (preserveAspectRatio xMidYMid meet)
  const ox = (W - doc.width * scale) / 2;
  const oy = (H - doc.height * scale) / 2;
  const canvas = new Float32Array(W * H * 4); // straight alpha, 0..1
  if (options.background) {
    const [r, g, b] = options.background;
    for (let i = 0; i < W * H; i++) {
      canvas[i * 4] = r / 255;
      canvas[i * 4 + 1] = g / 255;
      canvas[i * 4 + 2] = b / 255;
      canvas[i * 4 + 3] = 1;
    }
  }
  const drop = new Set(options.dropIds ?? []);
  const glyphScale = options.glyphScale ?? 1;
  /** @type {Map<string, RGB>} */
  const recolor = new Map(Object.entries(options.recolor ?? {}).map(([k, v]) => [parseHexColor(k).join(","), v]));

  for (const shape of doc.shapes) {
    if (shape.id && drop.has(shape.id)) continue;
    const gs = shape.group === "glyph" ? glyphScale : 1;
    // user units → pixels, scaling glyph shapes about the canvas centre
    /** @param {number} ux @param {number} uy @returns {[number, number]} */
    const toPx = (ux, uy) => {
      const px = ox + ux * scale;
      const py = oy + uy * scale;
      return [W / 2 + (px - W / 2) * gs, H / 2 + (py - H / 2) * gs];
    };
    const k = scale * gs; // length factor
    /** @param {RGB|null} c */
    const paintColor = (c) => (c === null ? null : recolor.get(c.join(",")) ?? c);
    const fill = paintColor(shape.fill);
    const stroke = paintColor(shape.stroke);
    const half = (shape.strokeWidth * k) / 2;

    if (shape.kind === "rrect") {
      const [x0, y0] = toPx(shape.x ?? 0, shape.y ?? 0);
      const w = (shape.w ?? 0) * k;
      const h = (shape.h ?? 0) * k;
      const rx = (shape.rx ?? 0) * k;
      /** @param {number} px @param {number} py */
      const sdf = (px, py) => roundedRectSdf(px - (x0 + w / 2), py - (y0 + h / 2), w / 2, h / 2, rx);
      if (fill) paintSdf(canvas, W, H, fill, sdf, [x0, y0, x0 + w, y0 + h]);
      if (stroke) paintSdf(canvas, W, H, stroke, (/** @type {number} */ px, /** @type {number} */ py) => Math.abs(sdf(px, py)) - half, [x0 - half, y0 - half, x0 + w + half, y0 + h + half]);
    } else if (shape.kind === "circle") {
      const [cx, cy] = toPx(shape.cx ?? 0, shape.cy ?? 0);
      const r = (shape.r ?? 0) * k;
      /** @param {number} px @param {number} py */
      const sdf = (px, py) => Math.hypot(px - cx, py - cy) - r;
      if (fill) paintSdf(canvas, W, H, fill, sdf, [cx - r, cy - r, cx + r, cy + r]);
      if (stroke) paintSdf(canvas, W, H, stroke, (/** @type {number} */ px, /** @type {number} */ py) => Math.abs(sdf(px, py)) - half, [cx - r - half, cy - r - half, cx + r + half, cy + r + half]);
    } else if (shape.kind === "polyline") {
      if (!stroke) continue;
      const pts = (shape.points ?? []).map(([ux, uy]) => toPx(ux, uy));
      if (shape.closed && pts.length > 1) pts.push(pts[0]);
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const [px, py] of pts) {
        minX = Math.min(minX, px);
        minY = Math.min(minY, py);
        maxX = Math.max(maxX, px);
        maxY = Math.max(maxY, py);
      }
      /** @param {number} px @param {number} py */
      const sdf = (px, py) => {
        let best = Infinity;
        if (pts.length === 1) return Math.hypot(px - pts[0][0], py - pts[0][1]) - half;
        for (let i = 0; i < pts.length - 1; i++) {
          const d = segmentDistance(px, py, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]);
          if (d < best) best = d;
        }
        return best - half;
      };
      paintSdf(canvas, W, H, stroke, sdf, [minX - half, minY - half, maxX + half, maxY + half]);
    }
  }

  const data = new Uint8Array(W * H * 4);
  for (let i = 0; i < W * H * 4; i++) data[i] = Math.round(Math.min(1, Math.max(0, canvas[i])) * 255);
  return { width: W, height: H, data };
}

/** @param {number} px @param {number} py @param {number} hw @param {number} hh @param {number} r */
function roundedRectSdf(px, py, hw, hh, r) {
  const qx = Math.abs(px) - hw + r;
  const qy = Math.abs(py) - hh + r;
  return Math.min(Math.max(qx, qy), 0) + Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) - r;
}

/** @param {number} px @param {number} py @param {number} ax @param {number} ay @param {number} bx @param {number} by */
function segmentDistance(px, py, ax, ay, bx, by) {
  const abx = bx - ax;
  const aby = by - ay;
  const apx = px - ax;
  const apy = py - ay;
  const len2 = abx * abx + aby * aby;
  const t = len2 === 0 ? 0 : Math.min(1, Math.max(0, (apx * abx + apy * aby) / len2));
  return Math.hypot(apx - abx * t, apy - aby * t);
}

/**
 * Source-over paint of `color` with coverage from `sdf` (pixels) over the bbox, clamped to the canvas.
 * @param {Float32Array} canvas @param {number} W @param {number} H @param {RGB} color
 * @param {(px: number, py: number) => number} sdf @param {number[]} bbox [minX, minY, maxX, maxY] in px
 */
function paintSdf(canvas, W, H, color, sdf, bbox) {
  const x0 = Math.max(0, Math.floor(bbox[0] - 1));
  const y0 = Math.max(0, Math.floor(bbox[1] - 1));
  const x1 = Math.min(W - 1, Math.ceil(bbox[2] + 1));
  const y1 = Math.min(H - 1, Math.ceil(bbox[3] + 1));
  const r = color[0] / 255;
  const g = color[1] / 255;
  const b = color[2] / 255;
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const d = sdf(x + 0.5, y + 0.5);
      const a = Math.min(1, Math.max(0, 0.5 - d));
      if (a <= 0) continue;
      const o = (y * W + x) * 4;
      const da = canvas[o + 3];
      const outA = a + da * (1 - a);
      if (outA <= 0) continue;
      // straight-alpha source-over
      canvas[o] = (r * a + canvas[o] * da * (1 - a)) / outA;
      canvas[o + 1] = (g * a + canvas[o + 1] * da * (1 - a)) / outA;
      canvas[o + 2] = (b * a + canvas[o + 2] * da * (1 - a)) / outA;
      canvas[o + 3] = outA;
    }
  }
}

// ---------------------------------------------------------------------------------------------
// PNG (encode + a decoder for the files this script writes) and ICO
// ---------------------------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

/** @param {Uint8Array} buf */
function crc32(buf) {
  let crc = 0xffffffff;
  for (const b of buf) crc = CRC_TABLE[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** @param {string} type @param {Buffer} data */
function pngChunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

export const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** @typedef {{width: number, height: number, data: Uint8Array}} Image straight-alpha RGBA, row-major */

/**
 * Encodes straight-alpha RGBA bytes as an 8-bit RGBA PNG (filter 0, zlib level 9).
 * @param {Image} image
 * @returns {Buffer}
 */
export function encodePng({ width, height, data }) {
  if (data.length !== width * height * 4) throw new Error("encodePng: data length mismatch");
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0;
    raw.set(data.subarray(y * stride, (y + 1) * stride), y * (stride + 1) + 1);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type RGBA
  return Buffer.concat([PNG_SIGNATURE, pngChunk("IHDR", ihdr), pngChunk("IDAT", deflateSync(raw, { level: 9 })), pngChunk("IEND", Buffer.alloc(0))]);
}

/**
 * Decodes the PNGs produced by encodePng (8-bit RGBA, filter 0 only); used by the self-check and tests.
 * @param {Buffer} buf
 * @returns {Image}
 */
export function decodePng(buf) {
  if (!buf.subarray(0, 8).equals(PNG_SIGNATURE)) throw new Error("not a PNG (bad signature)");
  let pos = 8;
  let width = 0;
  let height = 0;
  /** @type {Buffer[]} */
  const idat = [];
  let sawEnd = false;
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString("ascii", pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    const crc = buf.readUInt32BE(pos + 8 + len);
    if (crc !== crc32(buf.subarray(pos + 4, pos + 8 + len))) throw new Error(`PNG chunk ${type} has a bad CRC`);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      if (data[8] !== 8 || data[9] !== 6 || data[12] !== 0) throw new Error("decodePng supports only 8-bit RGBA non-interlaced PNGs");
    } else if (type === "IDAT") idat.push(data);
    else if (type === "IEND") sawEnd = true;
    pos += 12 + len;
  }
  if (!sawEnd || !width) throw new Error("truncated PNG");
  const raw = inflateSync(Buffer.concat(idat));
  const stride = width * 4;
  const out = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    if (raw[y * (stride + 1)] !== 0) throw new Error("decodePng supports only filter type 0");
    out.set(raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)), y * stride);
  }
  return { width, height, data: out };
}

/**
 * Builds a Windows .ico: every image ≤ 48 px as a 32-bit BGRA DIB with an AND mask (what Explorer,
 * the taskbar and installers read everywhere), larger images as embedded PNG (Vista+).
 * @param {Image[]} images square, distinct sizes, ascending
 * @returns {Buffer}
 */
export function encodeIco(images) {
  if (!images.length || images.length > 255) throw new Error("encodeIco: 1..255 images");
  const entries = images.map((img) => {
    if (img.width !== img.height || img.width > 256) throw new Error("encodeIco: images must be square and ≤ 256 px");
    return img.width > 48 ? encodePng(img) : encodeDib(img);
  });
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(images.length, 4);
  const dir = Buffer.alloc(16 * images.length);
  let offset = 6 + dir.length;
  images.forEach((img, i) => {
    const o = i * 16;
    dir[o] = img.width === 256 ? 0 : img.width;
    dir[o + 1] = img.height === 256 ? 0 : img.height;
    dir[o + 2] = 0; // colour count (n/a for 32 bpp)
    dir[o + 3] = 0; // reserved
    dir.writeUInt16LE(1, o + 4); // planes
    dir.writeUInt16LE(32, o + 6); // bits per pixel
    dir.writeUInt32LE(entries[i].length, o + 8);
    dir.writeUInt32LE(offset, o + 12);
    offset += entries[i].length;
  });
  return Buffer.concat([header, dir, ...entries]);
}

/** @param {Image} image */
function encodeDib({ width, height, data }) {
  const maskStride = Math.ceil(width / 32) * 4; // 1 bpp rows padded to 32 bits
  const xorSize = width * height * 4;
  const andSize = maskStride * height;
  const buf = Buffer.alloc(40 + xorSize + andSize);
  buf.writeUInt32LE(40, 0); // biSize
  buf.writeInt32LE(width, 4);
  buf.writeInt32LE(height * 2, 8); // XOR + AND
  buf.writeUInt16LE(1, 12); // planes
  buf.writeUInt16LE(32, 14); // bpp
  buf.writeUInt32LE(0, 16); // BI_RGB
  buf.writeUInt32LE(xorSize + andSize, 20);
  for (let y = 0; y < height; y++) {
    const srcRow = height - 1 - y; // bottom-up
    for (let x = 0; x < width; x++) {
      const s = (srcRow * width + x) * 4;
      const d = 40 + (y * width + x) * 4;
      buf[d] = data[s + 2]; // B
      buf[d + 1] = data[s + 1]; // G
      buf[d + 2] = data[s]; // R
      buf[d + 3] = data[s + 3]; // A
    }
  }
  // AND mask: 1 = transparent. Fully transparent pixels are masked so readers that ignore alpha still cut out the shape.
  for (let y = 0; y < height; y++) {
    const srcRow = height - 1 - y;
    for (let x = 0; x < width; x++) {
      if (data[(srcRow * width + x) * 4 + 3] === 0) buf[40 + xorSize + y * maskStride + (x >> 3)] |= 0x80 >> (x & 7);
    }
  }
  return buf;
}

/**
 * Minimal structural read of an .ico (directory + entry kinds); used by the self-check and tests.
 * @param {Buffer} buf
 * @returns {{width: number, height: number, bpp: number, size: number, offset: number, kind: "png"|"dib"}[]}
 */
export function readIcoDirectory(buf) {
  if (buf.readUInt16LE(0) !== 0 || buf.readUInt16LE(2) !== 1) throw new Error("not an ICO file");
  const count = buf.readUInt16LE(4);
  /** @type {{width: number, height: number, bpp: number, size: number, offset: number, kind: "png"|"dib"}[]} */
  const entries = [];
  for (let i = 0; i < count; i++) {
    const o = 6 + i * 16;
    const size = buf.readUInt32LE(o + 8);
    const offset = buf.readUInt32LE(o + 12);
    if (offset + size > buf.length) throw new Error("ICO entry points past the end of the file");
    const body = buf.subarray(offset, offset + size);
    const isPng = body.subarray(0, 8).equals(PNG_SIGNATURE);
    const width = buf[o] === 0 ? 256 : buf[o];
    const height = buf[o + 1] === 0 ? 256 : buf[o + 1];
    const bpp = buf.readUInt16LE(o + 6);
    if (!isPng && body.readUInt32LE(0) !== 40) throw new Error("ICO DIB entry has an unexpected header size");
    if (!isPng && body.readInt32LE(8) !== height * 2) throw new Error("ICO DIB entry height does not cover XOR + AND masks");
    entries.push({ width, height, bpp, size, offset, kind: isPng ? "png" : "dib" });
  }
  return entries;
}

// ---------------------------------------------------------------------------------------------
// Export plan
// ---------------------------------------------------------------------------------------------

/**
 * Every file under exports/. `mode`:
 *  - "tile":  the SVG as drawn (transparent outside the rounded tile)
 *  - "bleed": the tile is dropped and the canvas is filled with `background`; the glyph is scaled by
 *             `glyphScale` about the centre (maskable icons keep the glyph inside the 80 % safe zone)
 *  - "mono":  icon-mono.svg with currentColor = `color`, transparent background
 *  - "wordmark": the lockup at `width` px, transparent background
 * @typedef {object} ExportEntry
 * @property {string} out @property {string} source @property {"tile"|"bleed"|"mono"|"wordmark"} mode
 * @property {number} [size] @property {number} [width] @property {number[]} [ico]
 * @property {string} [background] @property {number} [glyphScale] @property {string} [color]
 */

/** @type {readonly ExportEntry[]} */
export const EXPORT_PLAN = Object.freeze([
  // PWA (mobile-app/public/icons)
  { out: "pwa/icon-192.png", source: "icon.svg", mode: "tile", size: 192 },
  { out: "pwa/icon-512.png", source: "icon.svg", mode: "tile", size: 512 },
  { out: "pwa/icon-192-maskable.png", source: "icon.svg", mode: "bleed", size: 192, background: TOKENS.night, glyphScale: 0.85 },
  { out: "pwa/icon-512-maskable.png", source: "icon.svg", mode: "bleed", size: 512, background: TOKENS.night, glyphScale: 0.85 },
  { out: "pwa/apple-touch-icon-180.png", source: "icon.svg", mode: "bleed", size: 180, background: TOKENS.night, glyphScale: 1 },
  // Favicons (website + PWA)
  { out: "favicon/favicon-16.png", source: "icon.svg", mode: "tile", size: 16 },
  { out: "favicon/favicon-32.png", source: "icon.svg", mode: "tile", size: 32 },
  { out: "favicon/favicon-48.png", source: "icon.svg", mode: "tile", size: 48 },
  { out: "favicon/favicon.ico", source: "icon.svg", mode: "tile", ico: [16, 32, 48] },
  // Windows (pc-agent tray, PyInstaller executable icon, installer)
  { out: "windows/tray-16.png", source: "icon.svg", mode: "tile", size: 16 },
  { out: "windows/tray-32.png", source: "icon.svg", mode: "tile", size: 32 },
  { out: "windows/tray-48.png", source: "icon.svg", mode: "tile", size: 48 },
  { out: "windows/tray-256.png", source: "icon.svg", mode: "tile", size: 256 },
  { out: "windows/tray-mono-light-16.png", source: "icon-mono.svg", mode: "mono", size: 16, color: TOKENS.white },
  { out: "windows/tray-mono-light-32.png", source: "icon-mono.svg", mode: "mono", size: 32, color: TOKENS.white },
  { out: "windows/tray-mono-light-48.png", source: "icon-mono.svg", mode: "mono", size: 48, color: TOKENS.white },
  { out: "windows/tray-mono-light-256.png", source: "icon-mono.svg", mode: "mono", size: 256, color: TOKENS.white },
  { out: "windows/tray-mono-dark-16.png", source: "icon-mono.svg", mode: "mono", size: 16, color: TOKENS.ink },
  { out: "windows/tray-mono-dark-32.png", source: "icon-mono.svg", mode: "mono", size: 32, color: TOKENS.ink },
  { out: "windows/tray-mono-dark-48.png", source: "icon-mono.svg", mode: "mono", size: 48, color: TOKENS.ink },
  { out: "windows/tray-mono-dark-256.png", source: "icon-mono.svg", mode: "mono", size: 256, color: TOKENS.ink },
  { out: "windows/dome.ico", source: "icon.svg", mode: "tile", ico: [16, 32, 48, 256] },
  // Wordmark lockups (website header, installer pages, documents)
  { out: "wordmark/wordmark-504.png", source: "wordmark.svg", mode: "wordmark", width: 504 },
  { out: "wordmark/wordmark-1008.png", source: "wordmark.svg", mode: "wordmark", width: 1008 },
  { out: "wordmark/wordmark-dark-504.png", source: "wordmark-dark.svg", mode: "wordmark", width: 504 },
  { out: "wordmark/wordmark-dark-1008.png", source: "wordmark-dark.svg", mode: "wordmark", width: 1008 },
]);

/** @type {Map<string, SvgDoc>} */
const docCache = new Map();
/** @param {string} source @param {string|undefined} currentColor @returns {SvgDoc} */
function loadDoc(source, currentColor) {
  const key = `${source}|${currentColor ?? ""}`;
  if (!docCache.has(key)) docCache.set(key, parseSvg(readFileSync(resolve(BRAND_DIR, source), "utf8"), { currentColor }));
  return /** @type {SvgDoc} */ (docCache.get(key));
}

/** @param {ExportEntry} entry @param {number|undefined} size @returns {Image} */
function renderEntryImage(entry, size) {
  const doc = loadDoc(entry.source, entry.mode === "mono" ? entry.color : undefined);
  switch (entry.mode) {
    case "tile":
    case "mono":
      if (size === undefined) throw new Error(`${entry.out}: size missing`);
      return render(doc, { width: size, height: size });
    case "bleed":
      if (size === undefined || entry.background === undefined) throw new Error(`${entry.out}: size/background missing`);
      return render(doc, { width: size, height: size, background: parseHexColor(entry.background), dropIds: ["tile"], glyphScale: entry.glyphScale });
    case "wordmark":
      if (entry.width === undefined) throw new Error(`${entry.out}: width missing`);
      return render(doc, { width: entry.width });
    default:
      throw new Error(`unknown export mode ${entry.mode}`);
  }
}

/**
 * Renders one plan entry to its final bytes (PNG or ICO).
 * @param {ExportEntry} entry
 * @returns {Buffer}
 */
export function renderEntry(entry) {
  if (entry.ico) return encodeIco(entry.ico.map((s) => renderEntryImage(entry, s)));
  return encodePng(renderEntryImage(entry, entry.size));
}

/**
 * Validates the bytes of one export the way the task asks: magic bytes, dimensions, structure.
 * @param {ExportEntry} entry @param {Buffer} bytes
 * @returns {{kind: "ico", sizes: number[]} | {kind: "png", width: number, height: number}}
 */
export function verifyExportBytes(entry, bytes) {
  if (entry.ico) {
    const dir = readIcoDirectory(bytes);
    const sizes = dir.map((e) => e.width);
    if (sizes.join(",") !== entry.ico.join(",")) throw new Error(`${entry.out}: ICO sizes ${sizes} ≠ ${entry.ico}`);
    return { kind: /** @type {const} */ ("ico"), sizes };
  }
  const png = decodePng(bytes);
  const expectW = entry.size ?? entry.width;
  if (png.width !== expectW) throw new Error(`${entry.out}: width ${png.width} ≠ ${expectW}`);
  if (entry.size && png.height !== entry.size) throw new Error(`${entry.out}: height ${png.height} ≠ ${entry.size}`);
  return { kind: /** @type {const} */ ("png"), width: png.width, height: png.height };
}

/**
 * Writes every export (or, with check=true, compares against the files on disk). Returns a report.
 * @param {{check?: boolean}} [options]
 */
export function runExport({ check = false } = {}) {
  /** @type {string[]} */
  const written = [];
  /** @type {string[]} */
  const drifted = [];
  /** @type {Record<string, unknown>[]} */
  const manifest = [];
  for (const entry of EXPORT_PLAN) {
    const bytes = renderEntry(entry);
    const info = verifyExportBytes(entry, bytes);
    const target = resolve(EXPORTS_DIR, entry.out);
    manifest.push({ file: entry.out, source: entry.source, mode: entry.mode, ...(info.kind === "ico" ? { sizes: info.sizes } : { width: info.width, height: info.height }), ...(entry.background ? { background: entry.background } : {}), ...(entry.color ? { color: entry.color } : {}), ...(entry.glyphScale ? { glyph_scale: entry.glyphScale } : {}) });
    if (check) {
      if (!existsSync(target) || !readFileSync(target).equals(bytes)) drifted.push(entry.out);
    } else {
      mkdirSync(dirname(target), { recursive: true });
      writeFileSync(target, bytes);
      written.push(entry.out);
    }
  }
  const manifestText = `${JSON.stringify({ generated_by: "brand/scripts/export.mjs", note: "Regenerate with `node brand/scripts/export.mjs`; `--check` fails on drift.", files: manifest }, null, 2)}\n`;
  const manifestPath = resolve(EXPORTS_DIR, "manifest.json");
  if (check) {
    if (!existsSync(manifestPath) || readFileSync(manifestPath, "utf8") !== manifestText) drifted.push("manifest.json");
  } else {
    writeFileSync(manifestPath, manifestText);
    written.push("manifest.json");
  }
  return { written, drifted, manifest };
}

/** @param {string[]} argv */
function main(argv) {
  const check = argv.includes("--check");
  const report = runExport({ check });
  if (check) {
    if (report.drifted.length) {
      console.error(`brand exports are stale (${report.drifted.length}): ${report.drifted.join(", ")}\nrun: node brand/scripts/export.mjs`);
      process.exit(1);
    }
    console.error(`brand exports up to date (${EXPORT_PLAN.length} files + manifest.json)`);
    return;
  }
  console.error(`wrote ${report.written.length} files to ${relative(process.cwd(), EXPORTS_DIR) || "."}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main(process.argv.slice(2));
