// Tests for brand/scripts/export.mjs and the SVG sources. Runs under Node's built-in test runner:
//   cd brand && pnpm test
import assert from "node:assert/strict";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { deflateSync } from "node:zlib";

import {
  BRAND_DIR,
  EXPORT_PLAN,
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

const SOURCES = ["icon.svg", "icon-dark.svg", "icon-mono.svg", "wordmark.svg", "wordmark-dark.svg"];
/** @param {string} name */
const read = (name) => readFileSync(resolve(BRAND_DIR, name), "utf8");
/** @param {{width: number, data: Uint8Array}} img @param {number} x @param {number} y */
const px = (img, x, y) => Array.from(img.data.subarray((y * img.width + x) * 4, (y * img.width + x) * 4 + 4));
/** @param {number[]} a @param {number[]} b @param {number} [tol] */
const near = (a, b, tol = 2) => a.every((v, i) => Math.abs(v - b[i]) <= tol);

// ------------------------------------------------------------------ sources

test("every SVG source parses within the brand subset and declares a 0 0 W H viewBox", () => {
  for (const name of SOURCES) {
    const doc = parseSvg(read(name), { currentColor: "#ffffff" });
    assert.ok(doc.width > 0 && doc.height > 0, name);
    assert.ok(doc.shapes.length >= 2, `${name} has shapes`);
    assert.ok(doc.shapes.some((s) => isGlyphGroup(s.group)), `${name} has a glyph group`);
  }
});

test("icon.svg, icon-dark.svg and icon-mono.svg share the same glyph geometry", () => {
  /** @param {string} name */
  const glyph = (name) =>
    parseSvg(read(name), { currentColor: "#ffffff" })
      .shapes.filter((s) => isGlyphGroup(s.group))
      .map((s) => (s.kind === "circle" ? ["circle", s.cx, s.cy, s.r] : ["polyline", s.strokeWidth, s.points?.length, s.points?.[0], s.points?.at(-1)]));
  assert.deepEqual(glyph("icon.svg"), glyph("icon-dark.svg"));
  assert.deepEqual(glyph("icon.svg"), glyph("icon-mono.svg"));
});

test("icon-mono.svg uses only currentColor so the tray can tint it", () => {
  const text = read("icon-mono.svg");
  assert.ok(!/#[0-9a-fA-F]{6}/.test(text.replace(/<!--[\s\S]*?-->/g, "")), "no literal colours");
  const white = parseSvg(text, { currentColor: "#ffffff" });
  const ink = parseSvg(text, { currentColor: TOKENS.ink });
  assert.deepEqual(white.shapes.map((s) => s.fill ?? s.stroke), white.shapes.map(() => [255, 255, 255]));
  assert.deepEqual(ink.shapes.map((s) => s.fill ?? s.stroke), ink.shapes.map(() => parseHexColor(TOKENS.ink)));
});

test("wordmark-dark.svg is wordmark.svg with the dark-surface colours only", () => {
  const light = read("wordmark.svg").replace("for light backgrounds", "for dark backgrounds").replaceAll(TOKENS.signalDeep, TOKENS.signal).replaceAll(TOKENS.ink, TOKENS.cloud).replaceAll('"dome-wordmark-', '"dome-wordmark-dark-');
  assert.equal(read("wordmark-dark.svg"), light);
});

test("every colour used by the sources is a documented token and appears in BRAND.md", () => {
  const brandMd = read("BRAND.md").toLowerCase();
  const tokenSet = new Set(/** @type {string[]} */ (Object.values(TOKENS)));
  for (const name of SOURCES) {
    const colours = read(name).replace(/<!--[\s\S]*?-->/g, "").match(/#[0-9a-fA-F]{6}/g) ?? [];
    for (const c of colours) assert.ok(tokenSet.has(c.toLowerCase()), `${name} uses undocumented colour ${c}`);
  }
  for (const [key, hex] of Object.entries(TOKENS)) assert.ok(brandMd.includes(hex), `BRAND.md lacks token ${key} ${hex}`);
});

test("ids are unique across all five sources (they may be inlined together on one page) and carry the file prefix", () => {
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

/**
 * Visual bounding box of the shapes in the glyph group, in user units (shape bounds + half the stroke).
 * @param {ReturnType<typeof parseSvg>} doc
 */
function glyphBounds(doc) {
  const b = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const s of doc.shapes) {
    if (!isGlyphGroup(s.group)) continue;
    const half = s.stroke ? s.strokeWidth / 2 : 0;
    /** @type {number[][]} */
    const pts = s.kind === "circle" ? [[(s.cx ?? 0) - (s.r ?? 0), (s.cy ?? 0) - (s.r ?? 0)], [(s.cx ?? 0) + (s.r ?? 0), (s.cy ?? 0) + (s.r ?? 0)]] : s.points ?? [];
    for (const [x, y] of pts) {
      b.minX = Math.min(b.minX, x - half);
      b.minY = Math.min(b.minY, y - half);
      b.maxX = Math.max(b.maxX, x + half);
      b.maxY = Math.max(b.maxY, y + half);
    }
  }
  return b;
}

test("icon glyph matches the geometry BRAND.md §1 states and stays inside the maskable safe zone after the 0.85 scale", () => {
  const doc = parseSvg(read("icon.svg"));
  const b = glyphBounds(doc);
  assert.ok(near([b.minX, b.minY, b.maxX, b.maxY], [12, 19, 52, 44.5], 0.05), `visual bounds 12–52 × 19–44.5, got ${JSON.stringify(b)}`);
  // maskable: platform masks must keep an 80 % centred circle (radius 0.4 × side); every bbox corner of
  // the glyph scaled by the plan's glyphScale about the tile centre must lie inside it
  const maskable = EXPORT_PLAN.filter((e) => e.out.includes("maskable"));
  assert.ok(maskable.length >= 2);
  for (const entry of maskable) {
    const k = entry.glyphScale ?? 1;
    const safe = 0.4 * doc.width;
    for (const [x, y] of [[b.minX, b.minY], [b.maxX, b.minY], [b.minX, b.maxY], [b.maxX, b.maxY]]) {
      const d = Math.hypot((x - doc.width / 2) * k, (y - doc.height / 2) * k);
      assert.ok(d < safe, `${entry.out}: glyph corner (${x},${y}) scaled ${k} is ${d.toFixed(2)} from centre, safe radius ${safe}`);
    }
  }
  // the unscaled glyph would NOT fit: that is why maskable exports scale it (guards against dropping glyphScale)
  const corner = Math.hypot(b.maxX - doc.width / 2, b.maxY - doc.height / 2);
  assert.ok(corner < 0.4 * doc.width || maskable.every((e) => (e.glyphScale ?? 1) < 1), "maskable entries scale the glyph");
});

test("wordmark lockup: wordmark-weight glyph (r 20, dot 6.5, stroke 6) and the built-in padding BRAND.md §4 states (8 left, 9 right, 9 top, 9 bottom)", () => {
  const doc = parseSvg(read("wordmark.svg"));
  const glyph = doc.shapes.filter((s) => isGlyphGroup(s.group));
  const dot = glyph.find((s) => s.kind === "circle");
  const arc = glyph.find((s) => s.kind === "polyline");
  assert.ok(dot && arc);
  assert.deepEqual([dot.cx, dot.cy, dot.r], [31, 40, 6.5]);
  assert.equal(arc.strokeWidth, 6);
  const apex = (arc.points ?? []).reduce((a, b) => (b[1] < a[1] ? b : a));
  assert.ok(near(apex, [31, 20], 0.2), `arc radius 20 → apex (31, 20), got ${apex}`);
  const all = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const s of doc.shapes) {
    const half = s.stroke ? s.strokeWidth / 2 : 0;
    const pts = s.kind === "circle" ? [[(s.cx ?? 0) - (s.r ?? 0), (s.cy ?? 0) - (s.r ?? 0)], [(s.cx ?? 0) + (s.r ?? 0), (s.cy ?? 0) + (s.r ?? 0)]] : s.points ?? [];
    for (const [x, y] of pts) {
      all.minX = Math.min(all.minX, x - half);
      all.minY = Math.min(all.minY, y - half);
      all.maxX = Math.max(all.maxX, x + half);
      all.maxY = Math.max(all.maxY, y + half);
    }
  }
  const padding = [all.minX, doc.width - all.maxX, all.minY, doc.height - all.maxY];
  assert.ok(near(padding, [8, 9, 9, 9], 0.05), `padding left/right/top/bottom = ${padding.map((v) => v.toFixed(2))}`);
  const brandMd = read("BRAND.md");
  assert.ok(brandMd.includes("8 units of built-in padding on the left, 9 on the right and 9 on top and bottom"), "BRAND.md §4 states the measured padding");
});

// ------------------------------------------------------------------ parser

test("parser rejects everything outside the subset", () => {
  /** @param {string} inner */
  const wrap = (inner) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">${inner}</svg>`;
  /** @type {[string, RegExp][]} */
  const bad = [
    ['<g transform="scale(2)"><circle cx="5" cy="5" r="2"/></g>', /transform/],
    ['<path d="M1 1L9 9" fill="#000000" stroke="#ffffff"/>', /fill="none"/],
    ['<path d="M1 1L9 9" stroke="#ffffff" stroke-linecap="butt"/>', /linecap/],
    ['<path d="M1 1C2 2 3 3 9 9" fill="none" stroke="#ffffff"/>', /unsupported commands/],
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

test("parser inherits fill/stroke/stroke-width from <g> and applies SVG defaults", () => {
  const doc = parseSvg(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><g id="k" fill="none" stroke="#112233" stroke-width="2"><line x1="0" y1="0" x2="1" y2="1"/><circle cx="1" cy="1" r="1" fill="#445566"/></g><rect width="2" height="2"/></svg>',
  );
  assert.equal(doc.shapes.length, 3);
  assert.deepEqual(doc.shapes[0], { kind: "polyline", id: undefined, group: "k", fill: null, stroke: [0x11, 0x22, 0x33], strokeWidth: 2, points: [[0, 0], [1, 1]], closed: false });
  assert.deepEqual(doc.shapes[1].fill, [0x44, 0x55, 0x66]);
  assert.deepEqual(doc.shapes[2].fill, [0, 0, 0], "default fill is black");
  assert.equal(doc.shapes[2].stroke, null);
  assert.equal(doc.shapes[2].group, undefined);
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
  assert.throws(() => flattenPathData("L1 1"), /before M/);
  assert.throws(() => flattenPathData("5 5"), /start with M/);
  assert.throws(() => flattenPathData("M0 0A1 1 45 0 1 2 2"), /rotated/);
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

const NIGHT = [...parseHexColor(TOKENS.night), 255];
const SIGNAL = [...parseHexColor(TOKENS.signal), 255];

test("icon.svg renders as a rounded tile with the glyph in the expected places (64 px)", () => {
  const img = render(parseSvg(read("icon.svg")), { width: 64, height: 64 });
  assert.deepEqual(px(img, 0, 0), [0, 0, 0, 0], "corner outside the rounded tile is transparent");
  assert.ok(near(px(img, 8, 8), NIGHT), "inside the tile is Night");
  assert.ok(near(px(img, 32, 39), SIGNAL), "dot centre is Signal");
  assert.ok(near(px(img, 32, 19), SIGNAL, 60), "arc apex is (mostly) Signal");
  assert.ok(near(px(img, 32, 58), NIGHT), "below the glyph is Night");
  assert.ok(near(px(img, 32, 29), NIGHT), "between arc and dot is Night");
});

test("bleed mode drops the tile, fills the background and scales the glyph about the centre", () => {
  const doc = parseSvg(read("icon.svg"));
  assert.deepEqual(tileIds(doc), ["dome-icon-tile"]);
  const img = render(doc, { width: 100, height: 100, background: parseHexColor(TOKENS.night), dropIds: tileIds(doc), glyphScale: 0.5 });
  assert.ok(near(px(img, 0, 0), NIGHT), "corner is background, no transparency");
  assert.ok(near(px(img, 50, 55), SIGNAL), "dot is still at (32,39)·(100/64) scaled 0.5 about (50,50) → (50, 55.5)");
  // arc centreline apex is y=22 user units → 34.4 px at 100 px; scaled 0.5 about the centre → 42.2 px
  assert.ok(near(px(img, 50, 42), SIGNAL, 40), "arc apex moved in by the glyph scale");
  assert.ok(near(px(img, 50, 34), NIGHT), "nothing left at the unscaled apex position");
  const img2 = render(doc, { width: 100, height: 100, background: parseHexColor(TOKENS.night), dropIds: tileIds(doc), glyphScale: 1 });
  assert.ok(near(px(img2, 50, 34), SIGNAL, 40), "unscaled apex position");
});

test("mono mode tints the glyph with currentColor on a transparent background", () => {
  const white = render(parseSvg(read("icon-mono.svg"), { currentColor: TOKENS.white }), { width: 64, height: 64 });
  assert.deepEqual(px(white, 0, 0), [0, 0, 0, 0]);
  assert.deepEqual(px(white, 32, 39), [255, 255, 255, 255]);
  const ink = render(parseSvg(read("icon-mono.svg"), { currentColor: TOKENS.ink }), { width: 64, height: 64 });
  assert.deepEqual(px(ink, 32, 39), [...parseHexColor(TOKENS.ink), 255]);
});

test("the glyph stays legible at 16 px: the dot and the arc differ clearly from the tile", () => {
  const img = render(parseSvg(read("icon.svg")), { width: 16, height: 16 });
  const dot = px(img, 8, 9);
  const tile = px(img, 2, 13);
  const diff = dot.slice(0, 3).reduce((s, v, i) => s + Math.abs(v - tile[i]), 0);
  assert.ok(diff > 250, `dot vs tile channel difference ${diff}`);
  const apex = px(img, 8, 5); // arc centreline apex: 22 user units × 16/64 = 5.5 px
  const apexDiff = apex.slice(0, 3).reduce((s, v, i) => s + Math.abs(v - tile[i]), 0);
  assert.ok(apexDiff > 150, `arc apex vs tile channel difference ${apexDiff}`);
  assert.deepEqual(px(img, 0, 0), [0, 0, 0, 0], "rounded corner still transparent at 16 px");
});

test("wordmark renders ink where the letters are and nothing where they are not", () => {
  const img = render(parseSvg(read("wordmark.svg")), { width: 252 });
  assert.equal(img.height, 64);
  assert.ok(near(px(img, 71, 32), [...parseHexColor(TOKENS.ink), 255]), "D stem");
  assert.ok(near(px(img, 161, 32), [...parseHexColor(TOKENS.ink), 255]), "M left stem");
  assert.ok(near(px(img, 226, 38), [...parseHexColor(TOKENS.ink), 255]), "e bar");
  assert.ok(near(px(img, 31, 40), [...parseHexColor(TOKENS.signalDeep), 255]), "glyph dot");
  assert.deepEqual(px(img, 132, 38), [0, 0, 0, 0], "inside the o is empty");
  assert.deepEqual(px(img, 2, 2), [0, 0, 0, 0], "clear space is empty");
});

test("recolor replaces a paint colour exactly", () => {
  const img = render(parseSvg(read("icon.svg")), { width: 64, height: 64, recolor: { [TOKENS.signal]: parseHexColor("#ff0000") } });
  assert.deepEqual(px(img, 32, 39), [255, 0, 0, 255]);
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
  const wanted = ["pwa/icon-192.png", "pwa/icon-512.png", "pwa/icon-192-maskable.png", "pwa/icon-512-maskable.png", "pwa/apple-touch-icon-180.png", "favicon/favicon-16.png", "favicon/favicon-32.png", "favicon/favicon.ico", "windows/tray-16.png", "windows/tray-32.png", "windows/tray-48.png", "windows/tray-256.png", "windows/dome.ico"];
  for (const w of wanted) assert.ok(outs.has(w), `plan includes ${w}`);
});

test("maskable and apple-touch exports are fully opaque; standard icons have transparent corners", () => {
  const byOut = Object.fromEntries(EXPORT_PLAN.map((e) => [e.out, e]));
  for (const out of ["pwa/icon-192-maskable.png", "pwa/icon-512-maskable.png", "pwa/apple-touch-icon-180.png"]) {
    const img = decodePng(renderEntry(byOut[out]));
    assert.deepEqual(px(img, 0, 0).slice(0, 3), NIGHT.slice(0, 3), out);
    for (let i = 3; i < img.data.length; i += 4) if (img.data[i] !== 255) assert.fail(`${out} has a non-opaque pixel`);
  }
  for (const out of ["pwa/icon-192.png", "pwa/icon-512.png", "windows/tray-256.png"]) assert.equal(px(decodePng(renderEntry(byOut[out])), 0, 0)[3], 0, out);
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

test("mobile-app/scripts/make-icons.mjs (the downstream consumer) only asks for exports the plan produces from icon.svg", (t) => {
  const consumer = resolve(BRAND_DIR, "..", "mobile-app", "scripts", "make-icons.mjs");
  if (!existsSync(consumer)) return t.skip("mobile-app consumer not present in this checkout");
  const text = readFileSync(consumer, "utf8");
  const wanted = [...text.matchAll(/\["((?:pwa|favicon|windows|wordmark)\/[^"]+)",\s*"[^"]+"\]/g)].map((m) => m[1]);
  assert.ok(wanted.length >= 8, `consumer lists export paths (${wanted.length})`);
  const byOut = new Map(EXPORT_PLAN.map((e) => [e.out, e]));
  for (const path of wanted) {
    const entry = byOut.get(path);
    assert.ok(entry, `mobile-app copies ${path}, which the export plan no longer produces (rename both together; BRAND.md §7)`);
    assert.equal(entry.source, "icon.svg", `${path}: the consumer requires icon.svg as the source`);
    assert.ok(existsSync(resolve(BRAND_DIR, "exports", path)), `${path} is committed`);
  }
  // the manifest fields the consumer reads
  const manifest = JSON.parse(readFileSync(resolve(BRAND_DIR, "exports", "manifest.json"), "utf8"));
  for (const f of manifest.files) assert.ok(typeof f.file === "string" && typeof f.source === "string", "manifest entries carry file + source");
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
