# DoMe brand specification

**Name.** The product's full name is **DoMe Remote** (app install name, store listings, legal text).
Everyone calls it **DoMe**: that is the short name (Home Screen label, tray, everyday copy). Write it
*DoMe*, never "Dome", "DOME" or "do me". The spaced capitals "R E M O T E" in the logo are lettering,
not how to write the name in text.

**Source of truth.** The owner's logo, `reference/dome-remote-logo-v2.webp` (1536 × 1024 raster), was
traced into the vector files in this directory (method: DECISIONS D18). From now on the SVGs are the
source: every PNG and ICO in `exports/` is rendered from them by `scripts/export.mjs`, and the copies
other components ship are checked against those renders by the tests.

## 1. The logo

**The mark** is the "o" of DoMe drawn as a ringed planet: a heavy white ring, crossed in front by a
thin orbit band. The band starts at the lower left in a **sky-blue tip** and ends at the upper right in
a **coral mouse cursor** pointing up and to the left. Read it as *your PC at home, and the pointer
that reaches it from anywhere*. Thin gaps (keylines) separate the band from the ring; they are cut
out of the drawing, so they always show the background.

**The wordmark** is "DoMe" with the mark as its "o": a D with a diagonal notch at the top left, the
mark, an M with rounded joins and a rounded e. The **full lockup** adds "REMOTE" in spaced capitals
under "Do", aligned with the D's left edge. The **compact lockup** is the same drawing without REMOTE.

**The app icon** is the mark on a **DoMe Blue rounded tile**: 512-unit grid, corner radius 108
(21.1 %, measured as circular corners of 21.0 % on the owner's icon). The mark keeps the owner's
placement and scale (0.529 × the wordmark mark, fitted to the owner's icon by overlap): it spans
35.1–461.6 × 98.4–420.9 units, about 7 % from the left edge, 10 % from the right, 19 % from the top
and 18 % from the bottom. In the icon the tip is the lighter **Sky light** so it separates from the
blue tile, as on the owner's icon.

One drawing is used everywhere: the mark in the lockups, `mark.svg` and `icon.svg` differ only by a
uniform scale and a shift (asserted by the tests to within the 0.1-unit rounding of the files).

| File | viewBox | What it is | Use it for |
| --- | --- | --- | --- |
| `wordmark.svg` | 1345 × 408 | Full lockup, Navy letters, Sky tip, Coral cursor | Light backgrounds: website hero, documents, installer |
| `wordmark-dark.svg` | 1345 × 408 | Full lockup, white letters, Sky tip, Coral cursor | Dark backgrounds (Navy or the dark theme) |
| `wordmark-compact.svg` | 1345 × 328 | "DoMe" without REMOTE, Navy | Light headers and navigation bars, small sizes |
| `wordmark-compact-dark.svg` | 1345 × 328 | "DoMe" without REMOTE, white | Dark headers and navigation bars, small sizes |
| `mark.svg` | 349 × 264 | The mark alone; the ring is `currentColor`, tip Sky, cursor Coral | Inline in the UI: the ring follows the text colour, so one file works in both themes |
| `icon.svg` | 512 × 512 | App icon: DoMe Blue tile, white ring, Sky light tip, Coral cursor | App icon, favicon (`favicon.svg`), tray, extension, anywhere a square icon is expected |
| `icon-mono.svg` | 512 × 512 | The mark as one silhouette in `currentColor` (tip and cursor merged into the ring, keylines kept), placed as in `icon.svg` | Monochrome and high-contrast contexts only (Windows high-contrast tray, one-colour print) |

Geometry facts the tests check: the icon tile is `rect` 512 × 512, `rx` 108, DoMe Blue; the mark's
farthest outline point is 234.0 units from the tile centre (the tip of the cursor's right wing), so
maskable exports scale the mark by 0.82 about the centre (to 191.9 units, inside the 204.8-unit safe
circle, 80 % of the tile, that Android and other platform masks keep).

## 2. Colour

### Brand palette

Measured from the owner's logo; these are the only colours the SVG sources use (enforced by the tests;
the script's `TOKENS` holds the same values).

| Token | Hex | Role |
| --- | --- | --- |
| **Navy** | `#001441` | The brand's dark: the logo background, dark brand panels, the letters of the light lockups, primary text on light surfaces |
| **DoMe Blue** | `#0142fb` | The icon tile; primary buttons and links on light surfaces; maskable / apple-touch icon background; PWA theme colour |
| **Sky** | `#4281fa` | The ring tip in the lockups and `mark.svg`; accents on dark surfaces |
| **Sky light** | `#63a1fe` | The ring tip on the DoMe Blue tile (`icon.svg`) only |
| **Coral** | `#f96054` | The cursor. A small accent only (see §5) |
| **White** | `#ffffff` | Letters and ring on dark surfaces; the ring in the icon; mono glyph for dark taskbars (`tray-mono-on-dark-*.png`) |
| **Ink** | `#000000` | The one-colour lockup on light paper (the owner's black lockup); mono glyph for light taskbars (`tray-mono-on-light-*.png`) |

Contrast (WCAG 2 formula on the hex values, rounded; not device-measured):

| Pair | Ratio | Verdict |
| --- | --- | --- |
| White on Navy / Navy on White | 17.9 : 1 | any text |
| White on DoMe Blue / DoMe Blue on White | 6.6 : 1 | any text (AA), including button labels |
| Coral on Navy | 5.8 : 1 | text AA on Navy |
| Sky on Navy | 4.9 : 1 | text AA on Navy |
| Sky on White | 3.7 : 1 | graphics and large text only |
| Coral on White | 3.1 : 1 | graphics only (the cursor); never body text, never white text on a Coral fill |
| Sky light on DoMe Blue | 2.5 : 1 | the icon tip only (decorative, next to the white ring) |

### Recommended UI surface tokens (website and app, both themes)

Derived neutrals are Navy-tinted greys so the interface reads as one family with the logo. The PWA
implements its own theme in `mobile-app/src/styles.css`; these are the brand's recommendation for it
and for the website's light/dark switch (which should also follow `prefers-color-scheme`). The shipped
theme differs in places, mostly in dark; see "As shipped" below.

| Token | Dark theme | Light theme | Notes |
| --- | --- | --- | --- |
| Page background | `#060b1d` | `#f6f8fc` | Dark is near-black Navy, not pure black; light is a cool off-white |
| Surface (cards, sheets) | `#0d1631` | `#ffffff` | Light cards separate by a soft Navy-tinted shadow, not by borders alone |
| Raised surface (menus, dialogs) | `#142046` | `#ffffff` | |
| Subtle fill (inputs, chips, pressed) | `#111c3b` | `#eef2f9` | |
| Hairline border | `#22305a` | `#dce3ef` | Decorative separation only (1.5 : 1 / 1.3 : 1); never the only cue for a control's edge |
| Text primary | `#f4f6fb` (18.1 : 1 on the page) | Navy `#001441` (16.8 : 1) | |
| Text secondary | `#a9b4cf` (9.4 : 1; 8.6 : 1 on a surface) | `#4a5878` (6.7 : 1 on the page) | |
| Text tertiary (captions) | `#7d89a8` (5.6 : 1; 5.1 : 1 on a surface) | `#647191` (4.6 : 1 on the page, 4.9 : 1 on white) | AA for normal text in both themes |
| Accent (links, focus ring, selected) | Sky 300 `#6e9bff` (7.3 : 1; 6.6 : 1 on a surface) | DoMe Blue `#0142fb` (6.2 : 1 on the page) | Sky `#4281fa` also passes on the dark page (5.4 : 1) |
| Primary button | DoMe Blue fill, white label (6.6 : 1) | DoMe Blue fill, white label (6.6 : 1) | Same in both themes: the brand colour is the action colour |
| Brand panel / hero band | Navy `#001441` with `wordmark-dark.svg` | White or the page colour with `wordmark.svg` | |
| Coral | the cursor; at most a small dot or a single highlight per screen | same | 6.4 : 1 on the dark page; 3.1 : 1 on white, so never text on light surfaces |

### As shipped: the website and PWA ("Midnight Studio", 2026-10-12)

The design the owner chose (mobile-app DECISIONS 55 and 60) uses **Navy itself as the dark page**
instead of the near-black above, with translucent "glass" cards and a soft DoMe Blue glow, so the dark
theme is the logo's own background; the design review judged a near-black page generic. Light changed
only slightly. The values in `mobile-app/src/styles.css` are the ones in use; this table lists where they
differ from the recommendation. Contrast is computed with the WCAG 2 formula on composited values, not
device-measured.

| Token | Dark: recommended → shipped | Light: recommended → shipped |
| --- | --- | --- |
| Page background | `#060b1d` → Navy `#001441` | `#f6f8fc` (unchanged) |
| Surface (cards) | `#0d1631` → white at 7.5–8 % over the page (composites to about `#142750`) | `#ffffff` (unchanged); filled controls use `#eff3fa` |
| Raised surface | `#142046` → `#0b2156` | `#ffffff` (unchanged) |
| Subtle / sunken fill | `#111c3b` → `#000c2e` | `#eef2f9` → `#edf1f8` |
| Hairline border | `#22305a` → white at 9 % | `#dce3ef` → Navy at 8 % |
| Text primary | `#f4f6fb` → `#f3f6fd` (16.5 : 1 on the page, 13.5 : 1 on a card) | Navy (unchanged) |
| Text secondary | `#a9b4cf` → `#b2bfdd` (9.7 : 1; 7.9 : 1) | `#4a5878` → `#44527a` (7.2 : 1 on the page) |
| Text tertiary | `#7d89a8` → `#8f9dc4` (6.6 : 1; 5.4 : 1) | `#647191` → `#5d6b8e` (5.0 : 1 on the page, 4.8 : 1 on `#eff3fa`) |
| Accent (links, focus, selected) | `#6e9bff` → `#86abff` (7.9 : 1; 6.5 : 1) | DoMe Blue (unchanged) |
| Primary button | unchanged: DoMe Blue fill, white label (6.6 : 1) | unchanged |
| `theme-color` | Navy `#001441` | `#f6f8fc` |

Coral stays a small accent in the shipped UI: the logo's cursor, the active tab's dot, the listening
ring and status dots; never a fill and never text.

Status colours (success/warning/danger) are product UI tokens, not brand colours; the tray's
connection-state badge colours stay as the agent defines them.

## 3. Type

No custom typeface is required. Product and website text use the platform system stack:

```
-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif
```

and `ui-monospace, "SF Mono", Menlo, Consolas, monospace` for codes and identifiers. The wordmark is
not typed in any font: it is path geometry, so it renders identically everywhere. Do not retype
"DoMe" or "REMOTE" in a display font to imitate the logo; in running text write DoMe (or DoMe Remote)
in the surrounding font. If a bundled typeface is ever added, it must carry an open licence (for
example Inter, SIL OFL) and be served from the app itself (the CSP allows no external fonts).

## 4. Clear space and minimum sizes

The SVG files have **no built-in clear space**: each viewBox fits the drawing (at most 1 unit of
slack per side, asserted by the tests), so layouts can align the logo's visible edges to their grid.
Add the clear space in the layout:

- **Lockups**: keep free space of at least **25 % of the lockup's height** on every side (for the
  full lockup that is about the height of the coral cursor plus the gap above it). Nothing else, no
  text and no edge of the screen, comes closer.
- **Mark alone**: at least 20 % of its width on every side.
- **App icon**: nothing inside the tile but the mark; outside, at least 10 % of the icon size to
  neighbouring elements. Platform icon slots handle their own spacing.

| Asset | Minimum | Below that |
| --- | --- | --- |
| Full lockup (`wordmark*.svg`) | 72 px tall (REMOTE is then about 8 px tall) | Use the compact lockup |
| Compact lockup (`wordmark-compact*.svg`) | 20 px tall | Use the icon or the mark |
| Mark (`mark.svg`) | 20 px wide | Use the icon |
| App icon (`icon.svg`, PNG exports) | 16 × 16 px | At 16 px the ring, the coral cursor pixel and the transparent corners still read (pixel-tested); the keylines and the band merge, which is acceptable at that size. Not yet verified on a real device screen. |
| Mono silhouette (`icon-mono.svg`) | 16 × 16 px | Fallback only (§6) |

## 5. Usage rules

Do:
- Use the full lockup where the brand is introduced (website hero, splash, installer, documents);
  the compact lockup in headers and navigation; the icon wherever a square icon is expected; the mark
  for compact UI moments (empty states, loading, the assistant's avatar).
- Use `wordmark.svg` / `wordmark-compact.svg` on light backgrounds and the `-dark` files on dark ones;
  inline `mark.svg` and let the ring take the text colour (Navy in the light theme, white in the dark).
- Keep Coral rare: it is the cursor. One small Coral accent per screen at most (a status dot, a
  highlight), never a large fill, never body text, never the colour of buttons.
- Put the lockup on Navy, White, DoMe Blue (icon tile only) or the recommended page backgrounds.
- Scale every file uniformly.

Don't:
- Recolour the logo outside the palette; add gradients, shadows, glows, outlines or bevels.
- Rotate, skew, stretch or mirror it; move the cursor, the tip or REMOTE; change the letter spacing.
- Fill the keylines between the band and the ring with another colour: they must show the background.
- Use the light lockup on dark backgrounds or the dark lockup on light ones.
- Place the logo on photographs or busy backgrounds; put it on a Navy or white panel first.
- Use the tile as a bullet, a button or part of another logo; put text inside the tile.
- Attach claims to the mark ("#1", "most secure", comparisons with other products). Advertised
  differentiators stay concrete and verifiable: straightforward setup, clear media targeting,
  useful Free controls, guided recovery, optional paid convenience.

## 6. Where each asset is used (exact files)

Sources are the seven SVGs at the top level of `brand/`. Exports are regenerated by
`node brand/scripts/export.mjs` (`pnpm export`; no dependencies) into `brand/exports/`;
`brand/exports/manifest.json` lists every file with its source and render mode. Other components
**copy** what they need and commit the copies; the brand tests fail when a copy no longer matches its
export:

- **mobile-app**: `mobile-app/scripts/make-icons.mjs` (`pnpm gen:icons` in `mobile-app/`) reads
  `exports/manifest.json` (fields `file` and `source`; requires `source: "icon.svg"`) and copies
  `pwa/icon-192.png`, `pwa/icon-512.png`, `pwa/icon-192-maskable.png`, `pwa/icon-512-maskable.png`,
  `pwa/apple-touch-icon-180.png`, `favicon/favicon-16.png`, `favicon/favicon-32.png`,
  `favicon/favicon.ico`, plus `icon.svg` as `favicon.svg`, into `mobile-app/public/icons/`.
- **browser-extension and pc-agent**: `scripts/sync-consumers.mjs` (`pnpm sync` in `brand/`) copies
  `extension/icon-16.png` … `icon-128.png` into `browser-extension/public/icons/` and
  `windows/tray-256.png` into `pc-agent/dome_agent/assets/`.

Renaming an export or a manifest field must be done together with those scripts (a test fails if a
consumer asks for a path the plan no longer produces).

**File-name convention**: a `-dark` / `on-dark` suffix always names the background the file is *for*
(DECISIONS D4): `wordmark-dark.*` has white letters, `tray-mono-on-dark-*.png` is a white glyph for a
dark taskbar, `tray-mono-on-light-*.png` an Ink glyph for a light one. The tests check every suffixed
export's paint luminance against its suffix.

| Surface | File(s) in `brand/` | Destination / use |
| --- | --- | --- |
| PWA manifest icons (`any`) | `exports/pwa/icon-192.png`, `exports/pwa/icon-512.png` | `mobile-app/public/icons/`, referenced from the `vite-plugin-pwa` manifest (`/icons/icon-192.png`, `/icons/icon-512.png`) |
| PWA manifest icon (`maskable`) | `exports/pwa/icon-192-maskable.png`, `exports/pwa/icon-512-maskable.png` | same, `purpose: "maskable"`; full-bleed DoMe Blue, mark at 82 % |
| iPhone home screen | `exports/pwa/apple-touch-icon-180.png` | `<link rel="apple-touch-icon">` in `mobile-app/index.html` (full-bleed DoMe Blue; iOS rounds the corners) |
| Favicon (website + PWA) | `icon.svg` (as `favicon.svg`), `exports/favicon/favicon-32.png`, `favicon-16.png`, `favicon-48.png`, `favicon.ico` (16/32/48) | `<link rel="icon">` entries; `.ico` for legacy browsers and Windows "pin to taskbar" |
| Website / app header | `wordmark-compact.svg`, `wordmark-compact-dark.svg` (inline SVG; swap with the theme) or `mark.svg` + the name in text | public pages and the app shell |
| Website hero, splash, documents | `wordmark.svg`, `wordmark-dark.svg`; `exports/wordmark/wordmark-512.png`, `wordmark-1024.png`, `wordmark-dark-512.png`, `wordmark-dark-1024.png` (1x / 2x) | public pages, slides, support documents, installer pages |
| Browser extension | `exports/extension/icon-16.png`, `icon-32.png`, `icon-48.png`, `icon-128.png` (128 has the Chrome Web Store's 16 px transparent margin) | `browser-extension/public/icons/`, manifest `icons` and `action.default_icon` |
| Windows tray (default) | `exports/windows/tray-16.png`, `tray-32.png`, `tray-48.png`, `tray-256.png` (the colour tile, RGBA) | `pc-agent/dome_agent/tray.py` loads its copy of `tray-256.png` and draws the connection state as a small badge (status-coloured dot in a white ring) in the bottom-right corner; Windows scales it to the tray size. If the PNG cannot be read, the tray draws a simplified tile itself. |
| Windows tray (fallback: high-contrast / monochrome only) | `exports/windows/tray-mono-on-dark-16.png` … `-256.png` (white silhouette, for dark taskbars) and `tray-mono-on-light-*.png` (Ink silhouette, for light taskbars) | only when Windows asks for a single-colour icon |
| Windows executable / installer | `exports/windows/dome.ico` (16/32/48 DIB + 256 PNG) | PyInstaller `icon=` in `pc-agent/packaging/dome-agent.spec` (both programs), the installer's `SetupIconFile`, the uninstaller and the Start menu shortcut (wired 2026-10-13; first Windows build pending, see KNOWN_ISSUES 6) |
| PWA `theme_color` / `background_color` | token values, not files | the shipped page backgrounds (§2 "As shipped"): `<meta name="theme-color">` is `#f6f8fc` in light and Navy `#001441` in dark; the web manifest uses Navy for both; DoMe Blue for the install splash if a brand-coloured splash is wanted |

## 7. Editing the sources

The export script renders a strict SVG subset and fails loudly on anything else:
`path` (commands `M L H V C S Q T A Z`, absolute or relative; arcs without x-axis rotation; filled
with `fill-rule` `nonzero` or `evenodd`, and/or stroked with round caps and joins), `rect` (optional
`rx`), `circle`, `line`, `g` for paint inheritance only (no `transform`), colours as `#rrggbb`,
`none` or `currentColor`, double-quoted attributes; no `style`, `text`, gradients, filters, masks or
clip paths. Design tools export transforms and styles: flatten them before saving.

Keep the conventions the tests enforce:
- **Ids** are prefixed with the file name (`dome-icon-…`, `dome-wordmark-dark-…`) so several brand SVGs
  can be inlined on one page without duplicate ids, and each `aria-labelledby` resolves to its own
  `<title>`. The export script finds the tile and the mark by the `-tile` and `-glyph` suffixes.
- **One drawing**: the ring, tip and cursor paths in the lockups, `mark.svg` and `icon.svg` keep the
  same commands and differ only by scale and position. Change all of them together.
- **Dark files** differ from their light files only in colour (Navy becomes White), ids and wording.
- **Colours** come from §2 and every one appears in this file.

After an edit run, in `brand/`: `pnpm export` (renders `exports/`), `pnpm sync` (extension and tray
copies), then `pnpm gen:icons` in `mobile-app/`; commit everything. `pnpm check` (lint, typecheck,
tests, `export:check`, `sync:check`) fails on any drift.
