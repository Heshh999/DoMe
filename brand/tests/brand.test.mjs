// Tests for brand/scripts/export.mjs, the SVG sources and the copies other components ship.
// Runs under Node's built-in test runner:   cd brand && pnpm test
import assert from "node:assert/strict";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { deflateSync } from "node:zlib";

import {
  BRAND_DIR,
  EXPORT_PLAN,
  FLATTEN_TOLERANCE,
  MASKABLE_GLYPH_SCALE,
  PNG_SIGNATURE,
  TOKENS,
  arcToPoints,
  decodeDib,
  decodeIcoImages,
  decodePng,
  encodeIco,
  encodePng,
  exportsEquivalent,
  flattenPathData,
  isGlyphGroup,
  isTileId,
  orphanedExports,
  parseHexColor,
  parseSvg,
  readIcoDirectory,
  render,
  renderEntry,
  runExport,
  tileIds,
  verifyExportBytes,
} from "../scripts/export.mjs";
import { CONSUMER_COPIES, syncConsumers } from "../scripts/sync-consumers.mjs";

const SOURCES = ["icon.svg", "icon-mono.svg", "mark.svg", "wordmark.svg", "wordmark-dark.svg", "wordmark-compact.svg", "wordmark-compact-dark.svg"];
const REPO = resolve(BRAND_DIR, "..");
/** @param {string} name */
const read = (name) => readFileSync(resolve(BRAND_DIR, name), "utf8");
/** @param {{width: number, data: Uint8Array}} img @param {number} x @param {number} y */
const px = (img, x, y) => Array.from(img.data.subarray((y * img.width + x) * 4, (y * img.width + x) * 4 + 4));
/** @param {number[]} a @param {number[]} b @param {number} [tol] */
const near = (a, b, tol = 2) => a.every((v, i) => Math.abs(v - b[i]) <= tol);
/** @param {string} hex */
const rgba = (hex) => [...parseHexColor(hex), 255];

/** Every outline point of the shapes selected by `keep` (fill subpaths, polylines, circles, rects). */
/** @param {ReturnType<typeof parseSvg>} doc @param {(s: ReturnType<typeof parseSvg>["shapes"][number]) => boolean} keep */
function outlinePoints(doc, keep) {
  /** @type {number[][]} */
  const pts = [];
  for (const s of doc.shapes) {
    if (!keep(s)) continue;
    if (s.kind === "fill") for (const sp of s.subpaths ?? []) pts.push(...sp);
    else if (s.kind === "polyline") pts.push(...(s.points ?? []));
    else if (s.kind === "circle") pts.push([(s.cx ?? 0) - (s.r ?? 0), (s.cy ?? 0) - (s.r ?? 0)], [(s.cx ?? 0) + (s.r ?? 0), (s.cy ?? 0) + (s.r ?? 0)]);
    else pts.push([s.x ?? 0, s.y ?? 0], [(s.x ?? 0) + (s.w ?? 0), (s.y ?? 0) + (s.h ?? 0)]);
  }
  return pts;
}
/** @param {number[][]} pts */
function boundsOf(pts) {
  return {
    minX: Math.min(...pts.map((p) => p[0])),
    minY: Math.min(...pts.map((p) => p[1])),
    maxX: Math.max(...pts.map((p) => p[0])),
    maxY: Math.max(...pts.map((p) => p[1])),
  };
}

// ------------------------------------------------------------------ sources

test("every SVG source parses within the brand subset, declares a 0 0 W H viewBox and has a glyph group", () => {
  for (const name of SOURCES) {
    const doc = parseSvg(read(name), { currentColor: "#ffffff" });
    assert.ok(doc.width > 0 && doc.height > 0, name);
    assert.ok(doc.shapes.length >= 1, `${name} has shapes`);
    assert.ok(doc.shapes.some((s) => isGlyphGroup(s.group)), `${name} has a glyph group`);
    assert.ok(doc.shapes.every((s) => s.kind === "fill" || isTileId(s.id)), `${name}: drawn as filled paths (plus the tile)`);
  }
  assert.ok(!existsSync(resolve(BRAND_DIR, "icon-dark.svg")), "icon-dark.svg was retired with the dome-arc mark (DECISIONS D18)");
});

/**
 * The path commands of one element and its coordinates split into x and y lists (H carries an x, V a y).
 * @param {string} svg @param {string} id
 */
function pathCoords(svg, id) {
  const m = new RegExp(`id="${id}"[^>]* d="([^"]*)"`).exec(svg);
  assert.ok(m, `path ${id} exists`);
  const tokens = m[1].match(/[MLHVCZ]|-?\d+(?:\.\d+)?/g) ?? [];
  /** @type {string[]} */
  const cmds = [];
  /** @type {number[][]} */
  const pairs = []; // [value, axis] axis 0 = x, 1 = y
  let cmd = "";
  let axis = 0;
  for (const t of tokens) {
    if (/[A-Z]/.test(t)) {
      cmd = t;
      cmds.push(t);
      axis = cmd === "V" ? 1 : 0;
      continue;
    }
    pairs.push([Number(t), axis]);
    if (cmd !== "H" && cmd !== "V") axis = 1 - axis;
  }
  return { cmds: cmds.join(""), xs: pairs.filter((p) => p[1] === 0).map((p) => p[0]), ys: pairs.filter((p) => p[1] === 1).map((p) => p[0]) };
}

test("one drawing everywhere: the mark in the wordmarks, mark.svg and icon.svg differs only by a uniform scale and a shift", () => {
  const parts = ["ring", "tip", "cursor"];
  const ref = read("wordmark.svg");
  for (const [file, prefix] of [["wordmark-dark.svg", "dome-wordmark-dark"], ["wordmark-compact.svg", "dome-wordmark-compact"], ["mark.svg", "dome-mark"], ["icon.svg", "dome-icon"]]) {
    /** @type {number[]} */
    const xs = [];
    /** @type {number[]} */
    const ys = [];
    /** @type {number[]} */
    const rx = [];
    /** @type {number[]} */
    const ry = [];
    for (const part of parts) {
      const a = pathCoords(ref, `dome-wordmark-${part}`);
      const b = pathCoords(read(file), `${prefix}-${part}`);
      assert.equal(b.cmds, a.cmds, `${file} ${part}: same path commands as the wordmark`);
      rx.push(...a.xs);
      ry.push(...a.ys);
      xs.push(...b.xs);
      ys.push(...b.ys);
    }
    // least-squares fit of x' = k x + bx and y' = k y + by with one k
    const n = rx.length + ry.length;
    const mx = rx.reduce((s, v) => s + v, 0) / rx.length;
    const my = ry.reduce((s, v) => s + v, 0) / ry.length;
    const mxp = xs.reduce((s, v) => s + v, 0) / xs.length;
    const myp = ys.reduce((s, v) => s + v, 0) / ys.length;
    let num = 0;
    let den = 0;
    rx.forEach((v, i) => {
      num += (v - mx) * (xs[i] - mxp);
      den += (v - mx) ** 2;
    });
    ry.forEach((v, i) => {
      num += (v - my) * (ys[i] - myp);
      den += (v - my) ** 2;
    });
    const k = num / den;
    let worst = 0;
    rx.forEach((v, i) => (worst = Math.max(worst, Math.abs(k * (v - mx) + mxp - xs[i]))));
    ry.forEach((v, i) => (worst = Math.max(worst, Math.abs(k * (v - my) + myp - ys[i]))));
    // coordinates are rounded to 0.1 units in both files
    assert.ok(worst <= 0.05 * (1 + k) + 1e-9, `${file}: residual ${worst.toFixed(3)} after scale ${k.toFixed(4)} (${n} numbers)`);
  }
});

test("icon-mono.svg is the icon's mark as one silhouette: same footprint as the colour mark (≥ 97 % overlap)", () => {
  const icon = parseSvg(read("icon.svg"));
  const glyph = render(icon, { width: 256, height: 256, dropIds: tileIds(icon) });
  const mono = render(parseSvg(read("icon-mono.svg"), { currentColor: "#ffffff" }), { width: 256, height: 256 });
  let both = 0;
  let either = 0;
  for (let i = 3; i < glyph.data.length; i += 4) {
    const a = glyph.data[i] >= 128;
    const b = mono.data[i] >= 128;
    if (a && b) both++;
    if (a || b) either++;
  }
  assert.ok(both / either >= 0.97, `IoU ${(both / either).toFixed(4)}`);
});

test("icon-mono.svg uses only currentColor so the tray can tint it; mark.svg's ring follows the text colour", () => {
  const text = read("icon-mono.svg");
  assert.ok(!/#[0-9a-fA-F]{6}/.test(text.replace(/<!--[\s\S]*?-->/g, "")), "no literal colours");
  const white = parseSvg(text, { currentColor: "#ffffff" });
  const ink = parseSvg(text, { currentColor: TOKENS.ink });
  assert.deepEqual(white.shapes.map((s) => s.fill), white.shapes.map(() => [255, 255, 255]));
  assert.deepEqual(ink.shapes.map((s) => s.fill), ink.shapes.map(() => parseHexColor(TOKENS.ink)));
  const mark = parseSvg(read("mark.svg"), { currentColor: TOKENS.navy });
  assert.deepEqual(mark.shapes.map((s) => s.fill), [parseHexColor(TOKENS.navy), parseHexColor(TOKENS.sky), parseHexColor(TOKENS.coral)], "ring = currentColor, tip = Sky, cursor = Coral");
});

test("the -dark lockups are the light ones with the dark-surface colours only; the compact lockups are the full ones without REMOTE", () => {
  /** @param {string} light @param {string} name */
  const toDark = (light, name) =>
    read(light).replace("Navy DoMe wordmark", "White DoMe wordmark").replace("for light backgrounds", "for dark backgrounds").replaceAll(TOKENS.navy, TOKENS.white).replaceAll(`"dome-${name}-`, `"dome-${name}-dark-`);
  assert.equal(read("wordmark-dark.svg"), toDark("wordmark.svg", "wordmark"));
  assert.equal(read("wordmark-compact-dark.svg"), toDark("wordmark-compact.svg", "wordmark-compact"));
  const full = parseSvg(read("wordmark.svg"));
  const compact = parseSvg(read("wordmark-compact.svg"));
  assert.equal(compact.width, full.width);
  assert.ok(compact.height < full.height * 0.85, "compact drops the REMOTE line");
  assert.equal(full.shapes.length, compact.shapes.length + 1, "the only extra shape is REMOTE");
  assert.ok(full.shapes.some((s) => s.id === "dome-wordmark-remote"));
  assert.deepEqual(
    compact.shapes.map((s) => s.subpaths?.map((sp) => sp.length)),
    full.shapes.filter((s) => s.id !== "dome-wordmark-remote").map((s) => s.subpaths?.map((sp) => sp.length)),
    "same letters and mark at the same coordinates",
  );
});

test("every colour used by the sources is a documented token and appears in BRAND.md", () => {
  const brandMd = read("BRAND.md").toLowerCase();
  const tokenSet = new Set(/** @type {string[]} */ (Object.values(TOKENS)));
  for (const name of SOURCES) {
    const colours = read(name).replace(/<!--[\s\S]*?-->/g, "").match(/#[0-9a-fA-F]{6}/g) ?? [];
    for (const c of colours) assert.ok(tokenSet.has(c.toLowerCase()), `${name} uses undocumented colour ${c}`);
  }
  for (const [key, hex] of Object.entries(TOKENS)) assert.ok(brandMd.includes(hex), `BRAND.md lacks token ${key} ${hex}`);
  for (const hex of Object.values(TOKENS)) assert.equal(hex, hex.toLowerCase(), "tokens are lower-case #rrggbb");
});

test("ids are unique across all sources (they may be inlined together on one page) and carry the file prefix", () => {
  /** @type {Map<string, string>} */
  const seen = new Map();
  for (const name of SOURCES) {
    const prefix = `dome-${name.replace(/\.svg$/, "")}-`;
    const text = read(name).replace(/<!--[\s\S]*?-->/g, "");
    const ids = [...text.matchAll(/\sid="([^"]*)"/g)].map((m) => m[1]);
    assert.ok(ids.length >= 2, `${name} declares ids`);
    for (const id of ids) {
      assert.ok(id.startsWith(prefix), `${name}: id ${id} must start with ${prefix}`);
      assert.ok(!seen.has(id), `${name}: id ${id} also used by ${seen.get(id)}`);
      seen.set(id, name);
    }
    const labelled = text.match(/aria-labelledby="([^"]*)"/);
    assert.ok(labelled && ids.includes(labelled[1]), `${name}: aria-labelledby points at an id in the same file`);
    assert.ok(text.includes(`<title id="${labelled?.[1]}">`), `${name}: the labelled element is the <title>`);
  }
  // roles are recognised by suffix, so prefixed ids still drive the export script
  assert.ok(isTileId("dome-icon-tile") && isTileId("tile") && !isTileId("dome-icon-title") && !isTileId(undefined));
  assert.ok(isGlyphGroup("dome-wordmark-dark-glyph") && isGlyphGroup("glyph") && !isGlyphGroup("glyphs") && !isGlyphGroup("dome-icon-letters"));
});

test("icon geometry: 512 tile with 21 % corners, the mark inside it, and inside the maskable safe circle after MASKABLE_GLYPH_SCALE", () => {
  const doc = parseSvg(read("icon.svg"));
  assert.deepEqual([doc.width, doc.height], [512, 512]);
  const tile = doc.shapes.find((s) => isTileId(s.id));
  assert.ok(tile && tile.kind === "rrect");
  assert.deepEqual([tile.x, tile.y, tile.w, tile.h, tile.rx], [0, 0, 512, 512, 108]);
  assert.deepEqual(tile.fill, parseHexColor(TOKENS.blue));
  const pts = outlinePoints(doc, (s) => isGlyphGroup(s.group));
  const b = boundsOf(pts);
  // the owner's placement: about 7 % margin left, 10 % right, 19 % top, 18 % bottom
  assert.ok(b.minX > 30 && b.minY > 90 && b.maxX < 470 && b.maxY < 430, `mark bounds ${JSON.stringify(b)}`);
  const far = Math.max(...pts.map(([x, y]) => Math.hypot(x - 256, y - 256)));
  const safe = 0.4 * 512;
  assert.ok(far > safe, `unscaled, the mark (${far.toFixed(1)}) would leave the safe circle: maskable exports must scale it`);
  const maskable = EXPORT_PLAN.filter((e) => e.out.includes("maskable"));
  assert.ok(maskable.length >= 2);
  for (const entry of maskable) {
    assert.equal(entry.glyphScale, MASKABLE_GLYPH_SCALE);
    assert.ok(far * MASKABLE_GLYPH_SCALE < safe - 8, `${entry.out}: farthest point ${(far * MASKABLE_GLYPH_SCALE).toFixed(1)} vs safe radius ${safe}`);
  }
  const brandMd = read("BRAND.md");
  assert.ok(brandMd.includes(`${far.toFixed(1)} units`), `BRAND.md states the measured farthest point (${far.toFixed(1)} units)`);
});

test("wordmark viewBoxes fit the ink tightly (≤ 1 unit of slack per side), as BRAND.md §4 states", () => {
  for (const name of ["wordmark.svg", "wordmark-compact.svg", "mark.svg"]) {
    const doc = parseSvg(read(name));
    const b = boundsOf(outlinePoints(doc, () => true));
    const slack = [b.minX, b.minY, doc.width - b.maxX, doc.height - b.maxY];
    assert.ok(slack.every((v) => v >= -0.01 && v <= 1), `${name}: slack ${slack.map((v) => v.toFixed(2))}`);
  }
  assert.match(read("BRAND.md"), /no built-in clear space/);
});

// ------------------------------------------------------------------ parser

test("parser rejects everything outside the subset", () => {
  /** @param {string} inner */
  const wrap = (inner) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">${inner}</svg>`;
  /** @type {[string, RegExp][]} */
  const bad = [
    ['<g transform="scale(2)"><circle cx="5" cy="5" r="2"/></g>', /transform/],
    ['<path d="M1 1L9 9" fill="none" stroke="none"/>', /paints nothing/],
    ['<path d="M1 1L9 9" stroke="#ffffff" stroke-linecap="butt"/>', /linecap/],
    ['<path d="M1 1L9 9Z" fill="#ffffff" fill-rule="inherit"/>', /fill-rule/],
    ['<path d="M1 1X9 9" fill="#ffffff"/>', /unsupported commands/],
    ['<path d="M1 1A2 2 30 0 1 5 5Z" fill="#ffffff"/>', /rotated/],
    ['<text x="1" y="1">DoMe</text>', /outside the brand SVG subset/],
    ['<linearGradient id="g"/>', /outside the brand SVG subset/],
    ['<circle cx="5" cy="5" r="2" fill="red"/>', /unsupported colour/],
    ['<circle cx="5" cy="5" r="2" fill="#fff"/>', /unsupported colour/],
    ['<circle cx="5" cy="5" r="2" style="fill:#000"/>', /outside the brand SVG subset/],
    ["<circle cx='5' cy='5' r='2'/>", /double-quoted/],
    ['<rect width="4" height="4" rx="3"/>', /out of range/],
    ['<circle cx="5" cy="5" r="2"', /cannot read/],
  ];
  for (const [inner, re] of bad) assert.throws(() => parseSvg(wrap(inner)), re, inner);
  assert.throws(() => parseSvg(wrap('<circle id="a" cx="5" cy="5" r="2"/><circle id="a" cx="5" cy="5" r="1"/>')), /duplicate id/);
  assert.throws(() => parseSvg(wrap('<circle id="a b" cx="5" cy="5" r="2"/>')), /simple identifier/);
  assert.throws(() => tileIds(parseSvg(wrap('<circle id="dome-x-glyph" cx="5" cy="5" r="2"/>'))), /no shape with a tile id/);
  assert.throws(() => parseSvg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="1 0 10 10"/>'), /viewBox/);
  assert.throws(() => parseSvg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><g></svg>'), /mismatched|unclosed/);
  assert.throws(() => parseSvg("<!DOCTYPE svg><svg/>"), /DOCTYPE/);
});

test("parser inherits fill, fill-rule, stroke and stroke-width from <g> and applies SVG defaults", () => {
  const doc = parseSvg(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><g id="k" fill="none" stroke="#112233" stroke-width="2"><line x1="0" y1="0" x2="1" y2="1"/><circle cx="1" cy="1" r="1" fill="#445566"/></g><rect width="2" height="2"/><g fill="#778899" fill-rule="evenodd"><path d="M0 0H4V4H0Z"/></g><path d="M0 0H1V1Z"/></svg>',
  );
  assert.equal(doc.shapes.length, 5);
  assert.deepEqual(doc.shapes[0], { kind: "polyline", id: undefined, group: "k", fill: null, stroke: [0x11, 0x22, 0x33], strokeWidth: 2, points: [[0, 0], [1, 1]], closed: false });
  assert.deepEqual(doc.shapes[1].fill, [0x44, 0x55, 0x66]);
  assert.deepEqual(doc.shapes[2].fill, [0, 0, 0], "default fill is black");
  assert.equal(doc.shapes[2].stroke, null);
  assert.equal(doc.shapes[2].group, undefined);
  assert.equal(doc.shapes[3].kind, "fill");
  assert.equal(doc.shapes[3].fillRule, "evenodd", "fill-rule inherited from <g>");
  assert.deepEqual(doc.shapes[3].fill, [0x77, 0x88, 0x99]);
  assert.equal(doc.shapes[4].fillRule, "nonzero", "default fill rule");
});

test("path data: absolute/relative M L H V Z and arcs land exactly on their endpoints", () => {
  const [sp] = flattenPathData("M1 2 l2 0 h-1 v3 L0 0 Z");
  assert.deepEqual(sp.points, [[1, 2], [3, 2], [2, 2], [2, 5], [0, 0]]);
  assert.equal(sp.closed, true);
  const [arc] = flattenPathData("M15 39A17 17 0 0 1 49 39");
  assert.deepEqual(arc.points.at(-1), [49, 39]);
  assert.ok(arc.points.length > 90, "≈1° steps over a semicircle");
  const top = arc.points.reduce((a, b) => (b[1] < a[1] ? b : a));
  assert.ok(near(top, [32, 22], 0.2), `semicircle apex ≈ (32,22), got ${top}`);
  const two = flattenPathData("M0 0L1 1M5 5L6 6");
  assert.equal(two.length, 2);
  const afterZ = flattenPathData("M2 2h2v2zm1 1h1");
  assert.deepEqual(afterZ[1].points, [[3, 3], [4, 3]], "m after z is relative to the closed subpath's start");
  assert.throws(() => flattenPathData("L1 1"), /before M/);
  assert.throws(() => flattenPathData("5 5"), /start with M/);
});

test("path data: cubic and quadratic curves (C S Q T, absolute and relative) are flattened within the tolerance", () => {
  /** @param {number[][]} c @param {number} t */
  const cubicAt = (c, t) => [0, 1].map((k) => (1 - t) ** 3 * c[0][k] + 3 * (1 - t) ** 2 * t * c[1][k] + 3 * (1 - t) * t * t * c[2][k] + t ** 3 * c[3][k]);
  /** distance from p to the polyline @param {number[]} p @param {number[][]} poly */
  const distTo = (p, poly) => {
    let best = Infinity;
    for (let i = 0; i < poly.length - 1; i++) {
      const [ax, ay] = poly[i];
      const [bx, by] = poly[i + 1];
      const l2 = (bx - ax) ** 2 + (by - ay) ** 2;
      const t = l2 ? Math.max(0, Math.min(1, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / l2)) : 0;
      best = Math.min(best, Math.hypot(p[0] - ax - t * (bx - ax), p[1] - ay - t * (by - ay)));
    }
    return best;
  };
  const ctrl = [[0, 0], [10, 40], [60, -20], [80, 20]];
  for (const tol of [FLATTEN_TOLERANCE, 0.5]) {
    const [sp] = flattenPathData("M0 0C10 40 60 -20 80 20", { tolerance: tol });
    assert.deepEqual(sp.points.at(-1), [80, 20], "lands exactly on the end point");
    let worst = 0;
    for (let k = 0; k <= 400; k++) worst = Math.max(worst, distTo(cubicAt(ctrl, k / 400), sp.points));
    assert.ok(worst <= tol, `max deviation ${worst.toFixed(4)} ≤ ${tol}`);
    if (tol === 0.5) assert.ok(sp.points.length < 30, "a coarse tolerance uses few chords");
  }
  // relative forms and the reflected control points of S and T
  const abs = flattenPathData("M10 10C20 0 30 0 40 10C50 20 60 20 70 10")[0].points;
  assert.deepEqual(flattenPathData("M10 10c10 -10 20 -10 30 0s20 10 30 0")[0].points, abs);
  assert.deepEqual(flattenPathData("M10 10C20 0 30 0 40 10S60 20 70 10")[0].points, abs);
  const q = flattenPathData("M0 0Q10 10 20 0Q30 -10 40 0")[0].points;
  assert.deepEqual(flattenPathData("M0 0q10 10 20 0t20 0")[0].points, q);
  assert.deepEqual(flattenPathData("M0 0Q10 10 20 0T40 0")[0].points, q);
  assert.throws(() => flattenPathData("M0 0C1 1 2 2"), /missing number/);
});

test("arcToPoints: sweep flag picks the side, large-arc flag picks the long way round", () => {
  const up = arcToPoints(0, 0, 10, 10, false, true, 20, 0); // clockwise on screen from (0,0) to (20,0): goes through y<0
  const down = arcToPoints(0, 0, 10, 10, false, false, 20, 0);
  assert.ok(up.every((p) => p[1] <= 1e-9) && up.some((p) => p[1] < -9));
  assert.ok(down.every((p) => p[1] >= -1e-9) && down.some((p) => p[1] > 9));
  const long = arcToPoints(10, 0, 10, 10, true, false, 9, 1); // nearly full circle
  assert.ok(long.length > 300, `large arc has many steps (${long.length})`);
  assert.deepEqual(arcToPoints(1, 1, 5, 5, false, true, 1, 1), [], "degenerate arc");
});

// ------------------------------------------------------------------ renderer

const BLUE = rgba(TOKENS.blue);
const WHITE = rgba(TOKENS.white);
const CORAL = rgba(TOKENS.coral);
const SKY_LIGHT = rgba(TOKENS.skyLight);
const NAVY = rgba(TOKENS.navy);
/** @param {string} inner @param {number} [w] @param {number} [h] */
const tiny = (inner, w = 4, h = 4) => parseSvg(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}">${inner}</svg>`);

test("filled paths get exact area coverage: half and quarter pixels, whole pixels, fill rules", () => {
  const img = render(tiny('<path d="M0.5 0.5H3.5V3.5H0.5Z" fill="#ffffff"/>'), { width: 4, height: 4 });
  assert.deepEqual(px(img, 1, 1), [255, 255, 255, 255], "interior");
  assert.equal(px(img, 0, 1)[3], 128, "left edge pixel half covered");
  assert.equal(px(img, 1, 0)[3], 128, "top edge pixel half covered");
  assert.equal(px(img, 0, 0)[3], 64, "corner pixel quarter covered");
  // a winding hole: same-direction inner square is filled under nonzero, empty under evenodd
  const d = "M0 0H8V8H0ZM2 2H6V6H2Z";
  const nz = render(tiny(`<path d="${d}" fill="#ffffff"/>`, 8, 8), { width: 8, height: 8 });
  const eo = render(tiny(`<path d="${d}" fill="#ffffff" fill-rule="evenodd"/>`, 8, 8), { width: 8, height: 8 });
  assert.equal(px(nz, 4, 4)[3], 255);
  assert.equal(px(eo, 4, 4)[3], 0);
  // an opposite-direction inner square is a hole under both rules
  const hole = render(tiny('<path d="M0 0H8V8H0ZM2 2V6H6V2Z" fill="#ffffff"/>', 8, 8), { width: 8, height: 8 });
  assert.equal(px(hole, 4, 4)[3], 0);
  // total coverage of a circle drawn with curves matches its area
  const r = 10;
  const k = 0.5523 * r;
  const circle = `M20 10C${20 + k} 10 30 ${20 - k} 30 20C30 ${20 + k} ${20 + k} 30 20 30C${20 - k} 30 10 ${20 + k} 10 20C10 ${20 - k} ${20 - k} 10 20 10Z`;
  const ci = render(tiny(`<path d="${circle}" fill="#ffffff"/>`, 40, 40), { width: 40, height: 40 });
  let sum = 0;
  for (let i = 3; i < ci.data.length; i += 4) sum += ci.data[i] / 255;
  assert.ok(Math.abs(sum - Math.PI * r * r) / (Math.PI * r * r) < 0.003, `area ${sum.toFixed(2)} vs ${(Math.PI * r * r).toFixed(2)}`);
});

// Interior points of each colour region of icon.svg on its 512 grid (each ≥ 16 units from any edge).
/** @type {Record<"ring"|"tip"|"cursor"|"tileTop"|"upperCounter"|"lowerCounter", [number, number]>} */
const ICON_POINTS = { ring: [376, 265], tip: [58, 314], cursor: [412, 150], tileTop: [167, 40], upperCounter: [263, 223], lowerCounter: [294, 312] };

test("icon.svg renders the tile, the white ring, the light-blue tip, the coral cursor and the counters where the geometry puts them", () => {
  const img = render(parseSvg(read("icon.svg")), { width: 512, height: 512 });
  assert.deepEqual(px(img, 0, 0), [0, 0, 0, 0], "corner outside the rounded tile is transparent");
  assert.ok(near(px(img, ...ICON_POINTS.tileTop), BLUE), "tile");
  assert.ok(near(px(img, ...ICON_POINTS.ring), WHITE), "ring");
  assert.ok(near(px(img, ...ICON_POINTS.tip), SKY_LIGHT), "tip");
  assert.ok(near(px(img, ...ICON_POINTS.cursor), CORAL), "cursor");
  assert.ok(near(px(img, ...ICON_POINTS.upperCounter), BLUE), "upper counter shows the tile");
  assert.ok(near(px(img, ...ICON_POINTS.lowerCounter), BLUE), "lower counter shows the tile");
  const small = render(parseSvg(read("icon.svg")), { width: 64, height: 64 });
  assert.ok(near(px(small, 51, 18), CORAL, 30), "cursor still coral at 64 px");
  assert.deepEqual(px(small, 0, 0), [0, 0, 0, 0]);
});

test("bleed mode drops the tile, fills the background and scales the glyph about the centre", () => {
  const doc = parseSvg(read("icon.svg"));
  assert.deepEqual(tileIds(doc), ["dome-icon-tile"]);
  const img = render(doc, { width: 512, height: 512, background: parseHexColor(TOKENS.blue), dropIds: tileIds(doc), glyphScale: 0.5 });
  assert.ok(near(px(img, 0, 0), BLUE), "corner is background, no transparency");
  const [cx, cy] = ICON_POINTS.cursor;
  assert.ok(near(px(img, Math.round(256 + (cx - 256) * 0.5), Math.round(256 + (cy - 256) * 0.5)), CORAL), "the cursor moved in by the glyph scale");
  assert.ok(near(px(img, cx, cy), BLUE), "nothing left at the unscaled cursor position");
});

test("mono mode tints the silhouette with currentColor on a transparent background", () => {
  const white = render(parseSvg(read("icon-mono.svg"), { currentColor: TOKENS.white }), { width: 512, height: 512 });
  assert.deepEqual(px(white, 0, 0), [0, 0, 0, 0]);
  assert.deepEqual(px(white, ...ICON_POINTS.ring), [255, 255, 255, 255]);
  assert.deepEqual(px(white, ...ICON_POINTS.cursor), [255, 255, 255, 255], "the cursor is part of the silhouette");
  assert.deepEqual(px(white, ...ICON_POINTS.upperCounter), [0, 0, 0, 0], "counters stay open");
  const ink = render(parseSvg(read("icon-mono.svg"), { currentColor: TOKENS.ink }), { width: 512, height: 512 });
  assert.deepEqual(px(ink, ...ICON_POINTS.tip), rgba(TOKENS.ink));
});

test("the icon stays legible at 16 px: a near-white ring pixel, a coral cursor pixel, transparent corners", () => {
  const img = render(parseSvg(read("icon.svg")), { width: 16, height: 16 });
  let whitest = 0;
  let coral = 0;
  for (let i = 0; i < img.data.length; i += 4) {
    whitest = Math.max(whitest, Math.min(img.data[i], img.data[i + 1], img.data[i + 2]));
    coral = Math.max(coral, img.data[i] - img.data[i + 2]);
  }
  assert.ok(whitest > 240, `ring reaches white (${whitest})`);
  assert.ok(coral > 120, `cursor reads as coral (red − blue = ${coral})`);
  assert.deepEqual(px(img, 0, 0), [0, 0, 0, 0], "rounded corner still transparent at 16 px");
});

test("wordmark renders navy letters, the accents and empty counters (1 px = 1 unit)", () => {
  const doc = parseSvg(read("wordmark.svg"));
  const img = render(doc, { width: doc.width });
  assert.equal(img.height, doc.height);
  assert.ok(near(px(img, 52, 218), NAVY), "D stem");
  assert.ok(near(px(img, 702, 168), NAVY), "M stem");
  assert.ok(near(px(img, 125, 385), NAVY), "REMOTE: E stem");
  assert.ok(near(px(img, 600, 105), CORAL), "cursor");
  assert.deepEqual(px(img, 172, 153), [0, 0, 0, 0], "inside the D counter is empty");
  assert.deepEqual(px(img, 1300, 380), [0, 0, 0, 0], "beside REMOTE is empty");
  const dark = render(parseSvg(read("wordmark-dark.svg")), { width: doc.width });
  assert.ok(near(px(dark, 52, 218), WHITE), "dark lockup: white D stem");
  assert.ok(near(px(dark, 600, 105), CORAL), "dark lockup: same coral cursor");
});

test("recolor replaces a paint colour exactly; inset leaves a transparent margin", () => {
  const img = render(parseSvg(read("icon.svg")), { width: 512, height: 512, recolor: { [TOKENS.coral]: parseHexColor("#ff0000") } });
  assert.deepEqual(px(img, ...ICON_POINTS.cursor), [255, 0, 0, 255]);
  const inset = render(parseSvg(read("icon.svg")), { width: 128, height: 128, inset: 16 });
  for (const [x, y] of [[8, 64], [64, 8], [119, 64], [64, 119], [15, 64]]) assert.equal(px(inset, x, y)[3], 0, `margin (${x},${y})`);
  assert.ok(near(px(inset, 20, 64), BLUE), "tile starts at the inset");
  assert.throws(() => render(parseSvg(read("icon.svg")), { width: 16, height: 16, inset: 8 }), /inset/);
});

// ------------------------------------------------------------------ containers

test("PNG encoder output starts with the PNG signature and round-trips through the decoder", () => {
  const img = render(parseSvg(read("icon.svg")), { width: 24, height: 24 });
  const bytes = encodePng(img);
  assert.ok(bytes.subarray(0, 8).equals(PNG_SIGNATURE));
  const back = decodePng(bytes);
  assert.equal(back.width, 24);
  assert.equal(back.height, 24);
  assert.deepEqual(Array.from(back.data), Array.from(img.data));
  const corrupt = Buffer.from(bytes);
  corrupt[40] ^= 0xff;
  assert.throws(() => decodePng(corrupt), /CRC|PNG/);
});

test("ICO encoder writes DIB entries up to 48 px and a PNG entry for 256 px, with contiguous offsets", () => {
  const doc = parseSvg(read("icon.svg"));
  const ico = encodeIco([16, 32, 48, 256].map((s) => render(doc, { width: s, height: s })));
  const dir = readIcoDirectory(ico);
  assert.deepEqual(dir.map((e) => [e.width, e.kind, e.bpp]), [[16, "dib", 32], [32, "dib", 32], [48, "dib", 32], [256, "png", 32]]);
  let expected = 6 + 16 * 4;
  for (const e of dir) {
    assert.equal(e.offset, expected);
    expected += e.size;
  }
  assert.equal(expected, ico.length, "no trailing or missing bytes");
  // 16 px DIB: 40-byte header + 16*16*4 XOR + 16 rows × 4-byte AND mask
  assert.equal(dir[0].size, 40 + 1024 + 64);
  assert.throws(() => encodeIco([render(doc, { width: 10, height: 12 })]), /square/);
});

test("ICO entries decode back to the exact rendered pixels (DIB via decodeDib, 256 px via PNG)", () => {
  const doc = parseSvg(read("icon.svg"));
  const images = [16, 32, 48, 256].map((s) => render(doc, { width: s, height: s }));
  const ico = encodeIco(images);
  const back = decodeIcoImages(ico);
  assert.equal(back.length, 4);
  images.forEach((img, i) => {
    assert.equal(back[i].width, img.width);
    assert.deepEqual(Array.from(back[i].data), Array.from(img.data), `entry ${img.width}`);
  });
  const dir = readIcoDirectory(ico);
  const dib = Buffer.from(ico.subarray(dir[0].offset, dir[0].offset + dir[0].size));
  dib[40 + 1024] ^= 0x80; // flip one AND-mask bit so it disagrees with alpha
  assert.throws(() => decodeDib(dib), /AND mask/);
});

// ------------------------------------------------------------------ export plan

test("export plan: unique outputs, existing sources, and every rendered file verifies", () => {
  const outs = new Set();
  for (const entry of EXPORT_PLAN) {
    assert.ok(!outs.has(entry.out), `duplicate ${entry.out}`);
    outs.add(entry.out);
    assert.ok(existsSync(resolve(BRAND_DIR, entry.source)), `${entry.source} exists`);
    const info = verifyExportBytes(entry, renderEntry(entry));
    if (info.kind === "ico") assert.deepEqual(info.sizes, entry.ico);
    else assert.equal(info.width, entry.size ?? entry.width);
  }
  const wanted = [
    "pwa/icon-192.png", "pwa/icon-512.png", "pwa/icon-192-maskable.png", "pwa/icon-512-maskable.png", "pwa/apple-touch-icon-180.png",
    "favicon/favicon-16.png", "favicon/favicon-32.png", "favicon/favicon-48.png", "favicon/favicon.ico",
    "windows/tray-16.png", "windows/tray-32.png", "windows/tray-48.png", "windows/tray-256.png", "windows/dome.ico",
    "extension/icon-16.png", "extension/icon-32.png", "extension/icon-48.png", "extension/icon-128.png",
    "wordmark/wordmark-512.png", "wordmark/wordmark-1024.png", "wordmark/wordmark-dark-512.png", "wordmark/wordmark-dark-1024.png",
  ];
  for (const w of wanted) assert.ok(outs.has(w), `plan includes ${w}`);
});

test("maskable and apple-touch exports are fully opaque DoMe Blue; standard icons have transparent corners", () => {
  const byOut = Object.fromEntries(EXPORT_PLAN.map((e) => [e.out, e]));
  for (const out of ["pwa/icon-192-maskable.png", "pwa/icon-512-maskable.png", "pwa/apple-touch-icon-180.png"]) {
    const img = decodePng(renderEntry(byOut[out]));
    assert.deepEqual(px(img, 0, 0).slice(0, 3), BLUE.slice(0, 3), out);
    for (let i = 3; i < img.data.length; i += 4) if (img.data[i] !== 255) assert.fail(`${out} has a non-opaque pixel`);
  }
  for (const out of ["pwa/icon-192.png", "pwa/icon-512.png", "windows/tray-256.png", "extension/icon-16.png"]) assert.equal(px(decodePng(renderEntry(byOut[out])), 0, 0)[3], 0, out);
  const ext = decodePng(renderEntry(byOut["extension/icon-128.png"]));
  assert.equal(px(ext, 64, 10)[3], 0, "extension 128 keeps the store's 16 px transparent margin");
  assert.ok(near(px(ext, 64, 20), BLUE), "extension 128 tile inside the margin");
});

/** WCAG relative luminance of an RGB triple (0..1). @param {number[]} rgb */
function luminance([r, g, b]) {
  const lin = (/** @type {number} */ c) => (c / 255 <= 0.03928 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

test("file-name suffix convention (DECISIONS D4): every export named *-dark-* paints light, *-light-* paints dark", () => {
  const dark = EXPORT_PLAN.filter((e) => /-dark[-.]/.test(e.out));
  const light = EXPORT_PLAN.filter((e) => /-light[-.]/.test(e.out));
  assert.ok(dark.length >= 5 && light.length >= 4, `plan has suffixed entries (${dark.length} dark, ${light.length} light)`);
  assert.ok(EXPORT_PLAN.filter((e) => e.mode === "mono").every((e) => /tray-mono-on-(dark|light)-\d+\.png$/.test(e.out)), "mono tray files say which taskbar they are for");
  /** @param {import("../scripts/export.mjs").ExportEntry} entry */
  const meanOpaqueLuminance = (entry) => {
    const img = decodePng(renderEntry(entry));
    let sum = 0;
    let n = 0;
    for (let i = 0; i < img.data.length; i += 4) {
      if (img.data[i + 3] !== 255) continue;
      sum += luminance([img.data[i], img.data[i + 1], img.data[i + 2]]);
      n += 1;
    }
    assert.ok(n > 0, `${entry.out} has opaque pixels`);
    return sum / n;
  };
  for (const e of dark) {
    assert.ok(!e.ico, `${e.out}: suffix test covers PNG exports`);
    const l = meanOpaqueLuminance(e);
    assert.ok(l > 0.5, `${e.out} is "for dark backgrounds" so its paint must be light; mean opaque luminance ${l.toFixed(3)}`);
    if (e.color) assert.ok(luminance(parseHexColor(e.color)) > 0.5, `${e.out}: manifest colour ${e.color} is light`);
  }
  for (const e of light) {
    const l = meanOpaqueLuminance(e);
    assert.ok(l < 0.2, `${e.out} is "for light backgrounds" so its paint must be dark; mean opaque luminance ${l.toFixed(3)}`);
    if (e.color) assert.ok(luminance(parseHexColor(e.color)) < 0.2, `${e.out}: manifest colour ${e.color} is dark`);
  }
});

test("mobile-app/scripts/make-icons.mjs only asks for exports the plan produces from icon.svg, and public/icons holds the current renders", (t) => {
  const consumer = resolve(REPO, "mobile-app", "scripts", "make-icons.mjs");
  if (!existsSync(consumer)) return t.skip("mobile-app consumer not present in this checkout");
  const text = readFileSync(consumer, "utf8");
  const copies = [...text.matchAll(/\["((?:pwa|favicon|windows|wordmark|extension)\/[^"]+)",\s*"([^"]+)"\]/g)].map((m) => [m[1], m[2]]);
  assert.ok(copies.length >= 8, `consumer lists export paths (${copies.length})`);
  const byOut = new Map(EXPORT_PLAN.map((e) => [e.out, e]));
  for (const [path, dest] of copies) {
    const entry = byOut.get(path);
    assert.ok(entry, `mobile-app copies ${path}, which the export plan no longer produces (rename both together; BRAND.md §7)`);
    assert.equal(entry.source, "icon.svg", `${path}: the consumer requires icon.svg as the source`);
    assert.ok(existsSync(resolve(BRAND_DIR, "exports", path)), `${path} is committed`);
    const installed = resolve(REPO, "mobile-app", "public", "icons", dest);
    assert.ok(existsSync(installed) && exportsEquivalent(entry, readFileSync(installed), readFileSync(resolve(BRAND_DIR, "exports", path))), `mobile-app/public/icons/${dest} matches exports/${path} (run pnpm gen:icons in mobile-app)`);
  }
  assert.equal(readFileSync(resolve(REPO, "mobile-app", "public", "icons", "favicon.svg"), "utf8"), read("icon.svg"), "favicon.svg is icon.svg");
  const manifest = JSON.parse(readFileSync(resolve(BRAND_DIR, "exports", "manifest.json"), "utf8"));
  for (const f of manifest.files) assert.ok(typeof f.file === "string" && typeof f.source === "string", "manifest entries carry file + source");
});

test("browser-extension and pc-agent copies (scripts/sync-consumers.mjs) are current, and the extension manifest points at them", () => {
  assert.deepEqual(syncConsumers({ check: true }).stale, [], "run `pnpm sync` in brand/");
  const byOut = new Map(EXPORT_PLAN.map((e) => [e.out, e]));
  for (const { from } of CONSUMER_COPIES) assert.ok(byOut.has(from), `${from} is in the export plan`);
  const manifestPath = resolve(REPO, "browser-extension", "public", "manifest.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const icons = { 16: "icons/icon-16.png", 32: "icons/icon-32.png", 48: "icons/icon-48.png", 128: "icons/icon-128.png" };
  assert.deepEqual(manifest.icons, icons);
  assert.deepEqual(manifest.action.default_icon, { 16: icons[16], 32: icons[32], 48: icons[48] });
  for (const rel of Object.values(icons)) assert.ok(existsSync(resolve(REPO, "browser-extension", "public", rel)), `${rel} exists`);
});

test("drift check compares decoded pixels, not compressed bytes: a re-deflated PNG/ICO is not drift, one changed pixel is", () => {
  const byOut = Object.fromEntries(EXPORT_PLAN.map((e) => [e.out, e]));
  /** Re-encode a PNG's IDAT with a different zlib level, leaving the pixels alone. @param {Buffer} png */
  const recompress = (png) => {
    const img = decodePng(png);
    const stride = img.width * 4;
    const raw = Buffer.alloc((stride + 1) * img.height);
    for (let y = 0; y < img.height; y++) raw.set(img.data.subarray(y * stride, (y + 1) * stride), y * (stride + 1) + 1);
    // splice: signature + IHDR chunk, new IDAT, IEND
    const ihdrLen = png.readUInt32BE(8);
    const head = png.subarray(0, 8 + 12 + ihdrLen);
    const idat = deflateSync(raw, { level: 1 });
    const crcTable = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
    /** @param {Buffer} buf */
    const crc = (buf) => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
    /** @param {string} type @param {Buffer} data */
    const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type, "ascii"), data]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]); };
    return Buffer.concat([head, chunk("IDAT", idat), chunk("IEND", Buffer.alloc(0))]);
  };
  const entry = byOut["favicon/favicon-32.png"];
  const fresh = renderEntry(entry);
  const other = recompress(fresh);
  assert.ok(!other.equals(fresh), "the re-encoded file has different bytes");
  assert.deepEqual(decodePng(other).data, decodePng(fresh).data, "…and identical pixels");
  assert.equal(exportsEquivalent(entry, fresh, other), true);
  const changed = decodePng(fresh);
  changed.data[(16 * 32 + 16) * 4] ^= 0x40;
  assert.equal(exportsEquivalent(entry, fresh, encodePng(changed)), false, "one pixel differs → drift");
  assert.equal(exportsEquivalent(entry, fresh, Buffer.from("not a png")), false, "unreadable file → drift");
  assert.equal(exportsEquivalent(entry, fresh, renderEntry(byOut["favicon/favicon-16.png"])), false, "different size → drift");
  // ICO: re-deflate the 256 px PNG entry inside dome.ico
  const icoEntry = byOut["windows/dome.ico"];
  const ico = renderEntry(icoEntry);
  const dir = readIcoDirectory(ico);
  const pngDir = dir[3];
  const newPng = recompress(Buffer.from(ico.subarray(pngDir.offset, pngDir.offset + pngDir.size)));
  const rebuilt = Buffer.concat([ico.subarray(0, pngDir.offset), newPng]);
  rebuilt.writeUInt32LE(newPng.length, 6 + 3 * 16 + 8);
  assert.ok(!rebuilt.equals(ico));
  assert.equal(exportsEquivalent(icoEntry, ico, rebuilt), true, "ICO with a re-deflated PNG entry is not drift");
  assert.equal(exportsEquivalent(icoEntry, ico, renderEntry(byOut["favicon/favicon.ico"])), false, "ICO with a different directory is drift");
});

test("export paths named in BRAND.md and README.md exist in the plan; retired names are gone", () => {
  const planned = new Set(EXPORT_PLAN.map((e) => e.out));
  for (const doc of ["BRAND.md", "README.md"]) {
    const text = read(doc);
    const named = [...text.matchAll(/(?:exports\/)?((?:pwa|favicon|windows|wordmark|extension)\/[\w.-]+\.(?:png|ico))/g)].map((m) => m[1]);
    assert.ok(named.length >= 5, `${doc} names export files`);
    for (const path of named) assert.ok(planned.has(path), `${doc} names ${path}, which the export plan does not produce`);
    assert.doesNotMatch(text, /tray-mono-(light|dark)-/, `${doc} still uses a pre-D12 mono tray name`);
    assert.doesNotMatch(text, /#5ee1c0|#0b0f17|Signal deep/i, `${doc} still describes the retired dome-arc palette`);
  }
  // BRAND.md §7 recommends the colour tile for the tray and keeps mono as the fallback (DECISIONS D15)
  const brandMd = read("BRAND.md");
  assert.match(brandMd, /\| Windows tray \(default\) \| `exports\/windows\/tray-16\.png`/);
  assert.match(brandMd, /\| Windows tray \(fallback: high-contrast \/ monochrome only\) \| `exports\/windows\/tray-mono-on-dark-16\.png`/);
});

test("a renamed export cannot leave a stale copy behind: orphans fail --check and are removed on write", () => {
  assert.deepEqual(orphanedExports(), [], "committed exports/ holds only files the plan produces");
  const dir = mkdtempSync(join(tmpdir(), "dome-brand-"));
  try {
    // seed a copy of the committed exports plus a file under the pre-rename name
    for (const entry of EXPORT_PLAN) {
      mkdirSync(resolve(dir, entry.out, ".."), { recursive: true });
      copyFileSync(resolve(BRAND_DIR, "exports", entry.out), resolve(dir, entry.out));
    }
    copyFileSync(resolve(BRAND_DIR, "exports", "manifest.json"), resolve(dir, "manifest.json"));
    writeFileSync(resolve(dir, "windows", "tray-mono-dark-16.png"), renderEntry(EXPORT_PLAN[0]));
    writeFileSync(resolve(dir, "windows", "notes.txt"), "not an image; left alone");
    assert.deepEqual(orphanedExports(dir), ["windows/tray-mono-dark-16.png"]);
    assert.deepEqual(runExport({ check: true, dir }).drifted, ["windows/tray-mono-dark-16.png (not in the export plan)"]);
    const report = runExport({ dir });
    assert.deepEqual(report.removed, ["windows/tray-mono-dark-16.png"]);
    assert.ok(!readdirSync(resolve(dir, "windows")).includes("tray-mono-dark-16.png"));
    assert.ok(existsSync(resolve(dir, "windows", "notes.txt")), "non-image files are not touched");
    assert.deepEqual(runExport({ check: true, dir }).drifted, []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("committed exports match a fresh render of the sources (run `pnpm export` after editing an SVG)", () => {
  const report = runExport({ check: true });
  assert.deepEqual(report.drifted, []);
  assert.equal(report.manifest.length, EXPORT_PLAN.length);
});
