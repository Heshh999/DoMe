# brand — DoMe identity

Original, simple DoMe wordmark and app icon as editable SVG sources, a dependency-free export script
that renders every PNG/ICO other components need, and `BRAND.md` (colour tokens, type, spacing,
minimum sizes, do/don't, where each asset is used). Spec: `docs/spec/MASTER_PROMPT.md` §11 last
paragraph; brief: `docs/design/input-control.md` §6. Decisions: `DECISIONS.md`; deferred items:
`KNOWN_ISSUES.md`; contract notes: `CONTRACT_ISSUES.md`.

```
icon.svg             app icon: Night rounded tile + Signal dome glyph (default everywhere)
icon-dark.svg        same glyph on a Slate tile with a thin border, for Night-coloured backgrounds
icon-mono.svg        glyph only, currentColor (high-contrast / monochrome fallback; the tray default is the tile)
wordmark.svg         lockup for light backgrounds: glyph (Signal deep) + "DoMe" (Ink), letters as paths
wordmark-dark.svg    lockup for dark backgrounds: glyph (Signal) + "DoMe" (Cloud)
BRAND.md             the brand specification
scripts/export.mjs   SVG-subset parser + SDF rasterizer + PNG/ICO writers + the export plan (Node 22, no deps)
exports/             generated: pwa/, favicon/, windows/, wordmark/, manifest.json (committed; drift-tested)
tests/brand.test.mjs node:test suite (sources, parser subset, renderer, containers, plan, drift)
```

Nothing outside `brand/` is modified by this component. Downstream consumers:

- **mobile-app (wired)**: `mobile-app/scripts/make-icons.mjs` (`pnpm gen:icons`) reads
  `exports/manifest.json` (`file`, `source`) and copies `pwa/*` and `favicon/favicon-16.png`,
  `-32.png`, `favicon.ico` plus `icon.svg` into `mobile-app/public/icons/`. Those paths are an
  interface: rename an export only together with that script (a test here fails otherwise; full list
  in BRAND.md §7 and KNOWN_ISSUES 6).
- **pc-agent (not wired)**: `pc-agent/dome_agent/tray.py` and `pc-agent/packaging/*.spec` (`icon=None`)
  do not use the brand files yet; BRAND.md §7 has the file-to-destination table.

## Commands

Prerequisites: Node 22 (nothing to install in `brand/`; lint and typecheck borrow
`../mobile-app/node_modules`, so run `pnpm install` in `mobile-app/` once). `xmllint` for the SVG
well-formedness check (`libxml2-utils`); without it, the subset parser in the tests still validates the files.

```sh
cd brand
pnpm export          # render every file in exports/ from the SVG sources (~6 s); removes images no longer in the plan
pnpm export:check    # exit 1 if any export's pixels differ from a fresh render, the manifest differs,
                     # or exports/ holds an image the plan does not produce (CI / pre-commit use)
pnpm test            # node --test
pnpm lint            # xmllint on the five SVGs + eslint (recommended rules, no-eval, no-console)
pnpm typecheck       # tsc --checkJs with strict on scripts/ and tests/
pnpm check           # all of the above
```

Last run here (Linux, Node 22.22): `xmllint` clean on all five sources, `eslint` clean, `tsc` clean,
**29 tests passed, 0 failed** (`node --test`, ~17 s because the suite re-renders every export),
`export:check` reports 26 files + `manifest.json` up to date. Not run on another Node major or on
Windows.

## What was generated

All of it is **unit-tested** by `tests/brand.test.mjs` unless marked otherwise.

| Files | Mode | Verified |
| --- | --- | --- |
| `exports/pwa/icon-192.png`, `icon-512.png` | rounded tile, transparent corners | PNG signature + IHDR dimensions by the script's own decoder; opened by Pillow 12 and ImageMagick 6 `identify` in this environment |
| `exports/pwa/icon-192-maskable.png`, `icon-512-maskable.png` | full-bleed Night, glyph scaled to 85 % (inside the 80 % safe zone) | as above; test asserts every pixel is opaque |
| `exports/pwa/apple-touch-icon-180.png` | full-bleed Night, glyph unscaled (iOS applies its own corner mask) | as above |
| `exports/favicon/favicon-16.png`, `-32.png`, `-48.png`, `favicon.ico` (16/32/48 as 32-bit DIB + AND mask) | tile | PNGs as above; `.ico` directory/offsets/DIB header checked by test, opened by Pillow (`sizes {16,32,48}`) and ImageMagick |
| `exports/windows/tray-16/32/48/256.png` | colour tile; the recommended tray icon (DECISIONS D15) | as above |
| `exports/windows/tray-mono-on-dark-*.png` (white glyph, for dark taskbars) and `tray-mono-on-light-*.png` (Ink glyph, for light taskbars), 16/32/48/256 | glyph only, transparent; high-contrast/monochrome fallback | as above; tests assert the tint, the transparent background, and that the paint luminance matches the `on-dark`/`on-light` suffix (DECISIONS D4, D12) |
| `exports/windows/dome.ico` (16/32/48 DIB + 256 PNG entry) | tile | as `favicon.ico`; ImageMagick lists the 256 entry as PNG |
| `exports/wordmark/wordmark-504.png`, `-1008.png`, `wordmark-dark-504.png`, `-1008.png` | lockup, transparent | as above; test samples letter stems, the e bar, the glyph dot and empty counters |
| `exports/manifest.json` | list of every file with source, mode, size, background/colour | drift-tested (text equality) |

**Not generated / not verified:**

- No render was compared against a browser or OS rasterizer: this machine has no SVG renderer
  (no `sharp`/`resvg` in `mobile-app/node_modules`, ImageMagick's `rsvg-convert` delegate absent,
  no cairo), so `scripts/export.mjs` carries its own rasterizer for a deliberately small SVG subset
  (DECISIONS D2). Visual inspection of the PNGs at 16–512 px was done here by eye from a contact
  sheet; the files are **not yet verified** on an iPhone home screen, in iOS Safari's favicon slot,
  on a Windows taskbar/tray, or in Explorer.
- `favicon.ico`/`dome.ico` are structurally valid and open in two independent readers here, but
  **Windows-device-tested: no** (Explorer, PyInstaller `icon=`, shortcut icons).
- No installer banner/wizard bitmap (those depend on the installer the maintainer picks; the
  wordmark PNGs are the input for them).
- Contrast figures in BRAND.md are computed from the hex values, not measured on a device.

## How the exports are made (honest summary)

`scripts/export.mjs` parses each SVG with a strict parser that accepts only `rect` (optional `rx`),
`circle`, `line`, `path` (`M L H V A Z`, `fill="none"`), `g` for paint inheritance, `#rrggbb`/`none`/
`currentColor`, round caps and joins, double-quoted attributes, and throws on anything else (no
`transform`, `style`, `text`, gradients, filters). Shapes are rendered by evaluating a signed distance
per pixel (rounded rect, circle, capsule distance to flattened polylines; arcs flattened at 1° steps),
converted to coverage with `clamp(0.5 − d, 0, 1)` and composited source-over in document order.
Output is 8-bit RGBA PNG (own encoder, zlib from Node) or ICO (own writer). Everything the script
produces is re-read by the script's own decoder before it is written, and the tests cross-check pixel
positions against the SVG geometry. Because the exports are derived from the SVG files, editing an
SVG and running `pnpm export` keeps every surface consistent; `pnpm test` fails on drift. Drift is
judged on decoded pixels (PNG RGBA; ICO directory + every DIB/PNG entry), not on compressed bytes,
so a different zlib build does not report false drift (DECISIONS D13).

## Evidence tags

- **unit-tested**: parser subset enforcement (including duplicate/invalid ids), path/arc flattening,
  renderer pixel checks at 16/64/100 px and for the wordmark, PNG round trip and CRC detection, ICO
  structure and full DIB/PNG entry decoding, export plan integrity, opaque/transparent invariants,
  source/BRAND.md token sync, file-prefixed ids unique across all five sources, `-dark`/`-light`
  suffix vs. paint luminance, icon glyph bounds and the maskable safe circle after the 0.85 scale,
  wordmark glyph geometry and built-in padding, pixel-based drift (a re-deflated PNG/ICO is not
  drift, one changed pixel is), orphaned exports, export paths named in BRAND.md/README exist.
- **integration-tested**: none. One static cross-check: a test parses `mobile-app/scripts/make-icons.mjs`
  and asserts every export it copies is produced by the plan from `icon.svg` (it does not run that
  script).
- **Windows-device-tested / iPhone-tested**: none.
