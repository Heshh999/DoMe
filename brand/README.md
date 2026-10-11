# brand — DoMe identity

The owner's DoMe Remote logo as clean vector sources, a dependency-free export script that renders
every PNG/ICO the other components need, and `BRAND.md` (the logo, palette and recommended light/dark
surface tokens, type, clear space, minimum sizes, usage rules, where each asset is used). Decisions:
`DECISIONS.md`; deferred items: `KNOWN_ISSUES.md`; contract notes: `CONTRACT_ISSUES.md`.

```
reference/dome-remote-logo-v2.webp  the owner's logo (raster, 1536 × 1024): what the vectors were traced from
wordmark.svg                 full lockup "DoMe" + REMOTE, Navy letters, for light backgrounds
wordmark-dark.svg            full lockup, white letters, for dark backgrounds
wordmark-compact.svg         "DoMe" without REMOTE, Navy (headers, small sizes)
wordmark-compact-dark.svg    "DoMe" without REMOTE, white
mark.svg                     the ringed o with the sky tip and the coral cursor; the ring is currentColor
icon.svg                     app icon: DoMe Blue rounded tile + white ring + light-blue tip + coral cursor
icon-mono.svg                the mark as one currentColor silhouette (high-contrast / monochrome fallback)
BRAND.md                     the brand specification
scripts/export.mjs           SVG-subset parser + rasterizer + PNG/ICO writers + the export plan (Node 22, no deps)
scripts/sync-consumers.mjs   copies the extension icons and the tray PNG into browser-extension/ and pc-agent/
exports/                     generated: pwa/, favicon/, windows/, extension/, wordmark/, manifest.json (committed; drift-tested)
tests/brand.test.mjs         node:test suite (sources, one-drawing check, parser, renderer, containers, plan, consumers, drift)
```

Consumers (all wired; the tests fail when a committed copy no longer matches its export):

- **mobile-app**: `mobile-app/scripts/make-icons.mjs` (`pnpm gen:icons` in `mobile-app/`) copies the
  PWA icons, apple-touch icon and favicons from `exports/` (it reads `exports/manifest.json` and
  requires `source: "icon.svg"`) plus `icon.svg` as `favicon.svg` into `mobile-app/public/icons/`.
- **browser-extension**: `pnpm sync` here copies `exports/extension/icon-{16,32,48,128}.png` into
  `browser-extension/public/icons/`; `public/manifest.json` names them in `icons` and
  `action.default_icon`.
- **pc-agent**: `pnpm sync` copies `exports/windows/tray-256.png` into `pc-agent/dome_agent/assets/`;
  `dome_agent/tray.py` loads it with Pillow and draws the connection state as a small badge (and
  draws a simplified tile itself if the PNG cannot be read). `packaging/dome-agent.spec` bundles the
  `assets` folder.

## Commands

Prerequisites: Node 22 (nothing to install in `brand/`; lint and typecheck borrow
`../mobile-app/node_modules`, so run `pnpm install` in `mobile-app/` once). `xmllint` for the SVG
well-formedness check (`libxml2-utils`); without it, the subset parser in the tests still validates the files.

```sh
cd brand
pnpm export          # render every file in exports/ from the SVG sources (< 1 s); removes images no longer in the plan
pnpm export:check    # exit 1 if any export's pixels differ from a fresh render, the manifest differs,
                     # or exports/ holds an image the plan does not produce (CI / pre-commit use)
pnpm sync            # copy the extension icons and the tray PNG into browser-extension/ and pc-agent/
pnpm sync:check      # exit 1 if one of those copies is missing or stale
pnpm test            # node --test
pnpm lint            # xmllint on the seven SVGs + eslint (recommended rules, no-eval, no-console)
pnpm typecheck       # tsc --checkJs with strict on scripts/ and tests/
pnpm check           # all of the above
```

After editing an SVG: `pnpm export`, `pnpm sync`, then `pnpm gen:icons` in `mobile-app/`, and commit
the regenerated files.

Last run here (Linux, Node 22.22): `xmllint` clean on all seven sources, `eslint` clean, `tsc` clean,
**33 tests passed, 0 failed** (`node --test`, about 4 s), `export:check` reports 30 files +
`manifest.json` up to date, `sync:check` 5 copies up to date. Not run on another Node major or on
Windows.

## What was generated

All of it is **unit-tested** by `tests/brand.test.mjs` unless marked otherwise.

| Files | Mode | Verified |
| --- | --- | --- |
| `exports/pwa/icon-192.png`, `icon-512.png` | rounded tile, transparent corners | PNG signature + IHDR dimensions by the script's own decoder; colour regions sampled at known points |
| `exports/pwa/icon-192-maskable.png`, `icon-512-maskable.png` | full-bleed DoMe Blue, mark scaled to 82 % (inside the 80 % safe circle) | as above; every pixel opaque; safe-circle geometry |
| `exports/pwa/apple-touch-icon-180.png` | full-bleed DoMe Blue, mark unscaled (iOS applies its own corner mask) | as above |
| `exports/favicon/favicon-16.png`, `-32.png`, `-48.png`, `favicon.ico` (16/32/48 as 32-bit DIB + AND mask) | tile | `.ico` directory/offsets/DIB entries decoded back to the exact pixels; 16 px legibility check |
| `exports/windows/tray-16/32/48/256.png` | colour tile; the tray default (DECISIONS D15) | as above |
| `exports/windows/tray-mono-on-dark-*.png` (white) and `tray-mono-on-light-*.png` (Ink), 16/32/48/256 | silhouette only, transparent; high-contrast/monochrome fallback | tint, transparency, suffix vs paint luminance (D4, D12) |
| `exports/windows/dome.ico` (16/32/48 DIB + 256 PNG entry) | tile | as `favicon.ico` |
| `exports/extension/icon-16.png`, `icon-32.png`, `icon-48.png`, `icon-128.png` | tile; 128 with a 16 px transparent margin (Chrome Web Store artwork size 96 px) | margin and tile checked |
| `exports/wordmark/wordmark-512.png`, `wordmark-1024.png`, `wordmark-dark-512.png`, `wordmark-dark-1024.png` | full lockup, transparent, 1x/2x | letters, cursor and empty counters sampled; dark = light with white letters |
| `exports/manifest.json` | list of every file with source, mode, size, background/colour/inset | drift-tested (text equality) |

**Compared with a browser** (not part of the test suite, done once here with the bundled headless
Chromium 141.0.7390.37, `chromium_headless_shell-1194`): each export above was rendered again by Chromium from
the same SVG at the same size and compared pixel by pixel (premultiplied RGBA). Mean absolute
difference: 0.10/255 for `icon-512.png`, 0.15/255 for `tray-256.png`, 0.07/255 for
`tray-mono-on-light-256.png`, 0.17–0.23/255 for the wordmarks, 1.4/255 at 32 px and 2.4/255 at 16 px
(small sizes differ only in how the two rasterizers sample partial pixels; side-by-side zooms look the
same). How the sources were traced and how they compare with the owner's raster: DECISIONS D18.

**Not verified:**

- Nothing is **Windows-device-tested or iPhone-tested**: the `.ico` files are structurally valid and
  decode in the script's own reader, but Explorer, the taskbar, PyInstaller's `icon=` and installer
  wizards have not seen them; the tray icon with its status badge has not been judged on a real
  taskbar at 100/125/150 % scaling; the apple-touch and maskable icons have not been seen on a home
  screen.
- No installer banner/wizard bitmap (those depend on the installer the maintainer picks; the
  wordmark PNGs are the input for them).
- Contrast figures in BRAND.md are computed from the hex values, not measured on a device.

## How the exports are made (honest summary)

`scripts/export.mjs` parses each SVG with a strict parser that accepts filled and stroked `path`s
(`M L H V C S Q T A Z`, `fill-rule` nonzero/evenodd), `rect` (optional `rx`), `circle`, `line`, `g`
for paint inheritance, `#rrggbb`/`none`/`currentColor`, round caps and joins, double-quoted
attributes, and throws on anything else (no `transform`, `style`, `text`, gradients, filters, masks).
Curves are flattened to within 0.02 user units (a bound on the second derivative picks the chord
count). Filled paths are scan-converted: 16 sub-scanlines per pixel row, and on each the exact
horizontal overlap of every inside span with every pixel, so coverage is the covered area up to the
vertical sampling (a half-covered edge pixel gets alpha 128, tested). Rects, circles and strokes keep
the signed-distance anti-aliasing. Shapes are composited source-over in document order. Output is
8-bit RGBA PNG (own encoder, zlib from Node) or ICO (own writer); everything is re-read by the
script's own decoder before it is written. Drift is judged on decoded pixels (PNG RGBA; ICO directory
+ every DIB/PNG entry), not on compressed bytes, so a different zlib build does not report false drift
(DECISIONS D13). Why not a headless browser: DECISIONS D19.

## Evidence tags

- **unit-tested**: parser subset enforcement (including duplicate/invalid ids and fill rules), path
  flattening for every command (deviation from the true Bézier within the tolerance; S/T reflection;
  relative forms), exact area coverage and both fill rules, renderer colour checks on the icon at
  16/64/512 px and on the wordmarks, bleed/inset/recolor/mono modes, PNG round trip and CRC detection,
  ICO structure and full DIB/PNG entry decoding, export plan integrity, opaque/transparent invariants,
  source/BRAND.md token sync, file-prefixed ids unique across all seven sources, one drawing in every
  file (uniform scale + shift), mono silhouette vs colour mark overlap, icon tile geometry and the
  maskable safe circle, tight viewBoxes, `-dark`/`-light` suffix vs paint luminance, pixel-based drift,
  orphaned exports, export paths named in BRAND.md/README exist, consumer copies in mobile-app,
  browser-extension and pc-agent match their exports, the extension manifest names its icons.
- **integration-tested**: none here (the pc-agent tray tests load the copied PNG and check the badge;
  the browser-extension manifest test checks the icon files).
- **Windows-device-tested / iPhone-tested**: none.
