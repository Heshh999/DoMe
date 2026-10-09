# DoMe brand specification

Short, practical rules for the DoMe identity. One identity across the website, the PWA, the Windows
agent (tray, setup window, installer) and documents. Everything here is original work drawn as
geometry in the SVG sources of this directory; no font file, stock art or third-party mark is used.

## 1. The mark

**The dome glyph**: a thick semicircular arc opening downward with a dot at its centre. Read it as
*a PC at home under its dome, and the one tap on the phone that reaches it*. It is deliberately
plain: two shapes, one colour, no gradients, no text inside the icon.

Geometry (64-unit grid, identical in `icon.svg`, `icon-dark.svg` and `icon-mono.svg`): arc centre
(32, 39), radius 17, stroke 6 with round caps; dot centre (32, 39), radius 5.5. Visual bounds
12–52 × 19–44.5, so the glyph is centred in the tile. Maskable exports scale it by 0.85 about the
tile centre; its farthest bounding-box corner is then ≈ 20.3 units from the centre, inside the
25.6-unit (80 %) safe circle platform masks keep (asserted by `tests/brand.test.mjs`).

**The tile**: a rounded square (corner radius 14/64 = 22 %) in Night with the glyph in Signal.

**The wordmark**: a wordmark-weight glyph followed by the word **DoMe** set as monoline strokes
(stroke 6 on a 64-unit grid, cap height 40, x-height 28, baseline 52). The lockup glyph is its own
drawing, not the icon glyph scaled: arc centre (31, 40), radius 20, dot radius 6.5, stroke 6 so the
arc has the same stroke as the letters (scaling the icon glyph up to radius 20 would give a 7-unit
stroke, heavier than the letters). The icon glyph (radius 17, dot 5.5) is used alone, in tiles and the
tray; the lockup glyph only appears inside the wordmark. Capital D and M, lowercase o and e — the
name is written *DoMe*, never "Dome", "DOME" or "do me".

## 2. Colour tokens

Dark is the brand default (it matches the PWA's default theme and the app icon). Values are the
same ones the PWA already uses in `mobile-app/src/styles.css` (`--dome-color-*`), so brand and
product never disagree.

| Token | Hex | Used for |
| --- | --- | --- |
| **Night** | `#0b0f17` | Icon tile; dark page/app background; PWA `theme_color`/`background_color` |
| **Slate** | `#182033` | Elevated tile (`icon-dark.svg`), cards on dark surfaces |
| **Border strong** | `#37445f` | Tile outline in `icon-dark.svg`, borders on dark surfaces |
| **Signal** | `#5ee1c0` | The glyph and accents **on dark** surfaces |
| **Signal deep** | `#0c8f70` | The glyph and accents **on light** surfaces (graphics and large text, not body text) |
| **Paper** | `#f6f7fb` | Light page/app background |
| **Ink** | `#101522` | Wordmark letters and text on light surfaces; mono tray glyph for light taskbars (`tray-mono-on-light-*.png`) |
| **Cloud** | `#eef2f8` | Wordmark letters and text on dark surfaces |
| **White** | `#ffffff` | Mono tray glyph for dark taskbars (`tray-mono-on-dark-*.png`) |

Contrast (computed from the hex values with the WCAG relative-luminance formula, rounded; not
device-measured): Signal on Night ≈ 11.9:1, Cloud on Night ≈ 17.1:1, Ink on Paper ≈ 17.0:1,
Signal deep on Paper ≈ 3.8:1 (above the 3:1 non-text minimum, below the 4.5:1 body-text minimum —
so on light surfaces Signal deep is for the glyph, icons, large headings and controls, while body
text uses Ink). Signal (`#5ee1c0`) on Paper is only ≈ 1.5:1 — never use the dark-surface accent on
light backgrounds.

Status colours (success/warning/danger/info) are product UI tokens in `mobile-app/src/styles.css`,
not brand colours; the tray's connection-state colours stay as the agent defines them.

## 3. Type

No custom typeface. Product and website text use the platform system stack already set in the PWA:

```
-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif
```

and `ui-monospace, "SF Mono", Menlo, Consolas, monospace` for codes and identifiers. The wordmark
is not typed in any font — it is the path geometry in `wordmark.svg`, so it renders identically
everywhere. Do not retype "DoMe" in a display font to imitate the wordmark; in running text write
DoMe in the surrounding font.

## 4. Spacing and clear space

- **Icon tile**: nothing else inside the tile; the glyph's own margin (12 units on a 64 grid) is
  part of the design. Maskable exports scale the glyph to 85 % so platform masks never clip it.
- **Wordmark**: keep clear space of at least the dot's diameter (13 units on the 64-unit grid,
  ≈ 20 % of the lockup height) on every side. The exported PNGs (and the SVG viewBox) include
  8 units of built-in padding on the left, 9 on the right and 9 on top and bottom (measured from
  the path geometry including half the stroke; asserted by the tests); add the rest in the layout.
- **Glyph next to text** (e.g. tray tooltip, setup window title): gap ≥ half the glyph height.

## 5. Minimum sizes

| Asset | Minimum | Note |
| --- | --- | --- |
| Tile icon (`icon.svg`, PNG exports) | 16 × 16 px | At 16 px the arc is 1.5 px thick and the dot 2.75 px wide: still a dome and a dot, verified by rendering and by the pixel test in `tests/brand.test.mjs`, **not yet verified** on a real device screen. |
| Mono glyph (`icon-mono.svg`) | 16 × 16 px | Fallback for monochrome / high-contrast contexts only (§7): at 16 px a lone arc over a dot can be mistaken for a one-bar Wi-Fi indicator. White on dark taskbars, Ink on light ones. |
| Wordmark lockup | 126 × 32 px (height 32) | Below that, use the tile icon alone. |

Never draw the glyph thinner than 6/64 of the tile, and never add a drop shadow, outline or
gradient to make it "pop" at small sizes — use the matching surface variant instead.

## 6. Do / don't

Do:
- Use `icon.svg` (Night tile) everywhere an app icon is expected; it is self-contained on light
  **and** dark surfaces.
- Use `icon-dark.svg` only when the Night tile would sit on a Night-coloured background and
  disappear (dark website sections, dark dialogs); it adds a Slate tile with a thin border.
- Use `wordmark.svg` on light backgrounds and `wordmark-dark.svg` on dark ones.
- Use `icon-mono.svg` only where the platform requires a single colour (high-contrast mode,
  monochrome print); the Windows tray uses the colour tile by default (§7).
- Keep the glyph and letters exactly as drawn in each file; scale each file uniformly. Do not
  swap the icon glyph into the lockup or the lockup glyph into a tile (§1).

Don't:
- Recolour the glyph outside the tokens above; no gradients, shadows, bevels or outlines.
- Rotate, skew, stretch, mirror, or separate the dot from the arc.
- Put the dark-surface accent (`#5ee1c0`) on light backgrounds.
- Place the wordmark on photographs or busy backgrounds; put it on a Night or Paper block first.
- Use the tile as a bullet, a button or part of another logo.
- Attach claims to the mark ("#1", "most secure", comparisons with other products). Advertised
  differentiators stay concrete and verifiable: straightforward setup, clear media targeting,
  useful Free controls, guided recovery, optional paid convenience.

## 7. Where each asset is used (exact files)

Sources are the five SVGs at the top level of `brand/`. Exports are regenerated by
`node brand/scripts/export.mjs` (no dependencies) into `brand/exports/`; `brand/exports/manifest.json`
lists every file with its source and render mode. Other components **copy** what they need;
nothing outside `brand/` is modified by the brand build.

**File-name convention**: a `-dark` / `on-dark` suffix always names the background the file is
*for* (DECISIONS D4): `wordmark-dark.*` has light letters, `tray-mono-on-dark-*.png` is a white glyph
for a dark taskbar, `tray-mono-on-light-*.png` an Ink glyph for a light one. The tests check every
suffixed export's paint luminance against its suffix.

**Downstream consumer — rename in both places**: the PWA and favicon rows below are copied by
`mobile-app/scripts/make-icons.mjs` (`pnpm gen:icons` in `mobile-app/`), which reads
`exports/manifest.json` (fields `file` and `source`, and requires `source: "icon.svg"`) and copies
`pwa/icon-192.png`, `pwa/icon-512.png`, `pwa/icon-192-maskable.png`, `pwa/icon-512-maskable.png`,
`pwa/apple-touch-icon-180.png`, `favicon/favicon-16.png`, `favicon/favicon-32.png`,
`favicon/favicon.ico`, plus `icon.svg` as `favicon.svg`. Renaming any of these exports, or a manifest
field, must be done together with that script (a brand test fails if the script asks for a path the
plan no longer produces). The Windows rows are not wired yet (`pc-agent/dome_agent/tray.py` draws
its own shape; `pc-agent/packaging/*.spec` have `icon=None`).

| Surface | File(s) in `brand/` | Destination / use |
| --- | --- | --- |
| PWA manifest icons (`any`) | `exports/pwa/icon-192.png`, `exports/pwa/icon-512.png` | `mobile-app/public/icons/`, referenced from the `vite-plugin-pwa` manifest (`/icons/icon-192.png`, `/icons/icon-512.png`) |
| PWA manifest icon (`maskable`) | `exports/pwa/icon-192-maskable.png`, `exports/pwa/icon-512-maskable.png` | same, `purpose: "maskable"` |
| iPhone home screen | `exports/pwa/apple-touch-icon-180.png` | `<link rel="apple-touch-icon">` in `mobile-app/index.html` (full-bleed; iOS rounds the corners) |
| Favicon (website + PWA) | `icon.svg` (as `favicon.svg`), `exports/favicon/favicon-32.png`, `favicon-16.png`, `favicon-48.png`, `favicon.ico` (16/32/48) | `<link rel="icon">` entries; `.ico` for legacy and Windows "pin to taskbar" |
| Website header / footer | `wordmark.svg`, `wordmark-dark.svg` (inline SVG preferred); `exports/wordmark/wordmark-504.png`, `wordmark-dark-504.png`, `-1008` for 2×/4× | public pages |
| Windows tray (default) | `exports/windows/tray-16.png`, `tray-32.png`, `tray-48.png`, `tray-256.png` (Night tile + Signal glyph, RGBA) | `pc-agent/dome_agent/tray.py` (`pystray.Icon` accepts RGBA images); the tile stands out on light **and** dark taskbars and is clearly distinct from the Wi-Fi/network indicator in the same notification area (judged from the renders here; **not yet verified** on a Windows taskbar). Status can be shown as a small badge over a corner of the tile. |
| Windows tray (fallback: high-contrast / monochrome only) | `exports/windows/tray-mono-on-dark-16.png` … `-256.png` (white glyph, for dark taskbars) and `tray-mono-on-light-*.png` (Ink glyph, for light taskbars) | same; only when Windows asks for a single-colour icon. Not the default: at 16 px a lone arc over a dot resembles a one-bar Wi-Fi indicator. |
| Windows executable / installer | `exports/windows/dome.ico` (16/32/48 DIB + 256 PNG) | PyInstaller `icon=` in `pc-agent/packaging/*.spec`, installer and shortcut icon |
| Setup window title / about | `icon.svg` or `exports/windows/tray-48.png` + the wordmark PNG for dark or light theme | `pc-agent/dome_agent/ui.py` |
| Documents, slides, support | `wordmark*.svg` or the 1008 px PNGs | — |
| PWA `theme_color` / `background_color` | token values, not files | Night `#0b0f17` (dark), Paper `#f6f7fb` (light meta theme-color) — already what `mobile-app` ships |

## 8. Editing the sources

Keep edits within the subset the export script understands (it fails loudly otherwise):
`rect` (optional `rx`), `circle`, `line`, `path` with `M L H V A Z` and `fill="none"`, `g` for
inheritance only (no `transform`), colours as `#rrggbb`/`none`/`currentColor`, round caps and
joins only, double-quoted attributes, no `style`, `text`, gradients, filters or masks.

**Ids**: every id is prefixed with the file name (`dome-icon-…`, `dome-wordmark-dark-…`) so that
several brand SVGs can be inlined on one page (e.g. both wordmarks for a theme switch, or the
wordmark next to the icon) without duplicate ids, and each `aria-labelledby` resolves to its own
`<title>`. When inlining a brand SVG, keep the ids unique on the page (do not inline the same file
twice without renaming them). The export script finds the tile and the glyph group by the `-tile`
and `-glyph` suffixes; the parser rejects duplicate ids and the tests reject ids without the file
prefix. After an
edit run `pnpm export` in `brand/` and commit the regenerated `exports/`; `pnpm test` fails on
drift. `BRAND.md` must list every colour the sources use (also enforced by the tests).
