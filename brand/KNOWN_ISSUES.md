# brand — known issues (recorded, not fixed)

1. **No rendering was compared against a browser or OS rasterizer.** The exports come from the
   script's own signed-distance rasterizer (DECISIONS D2). Edges are anti-aliased with a linear
   coverage approximation, which is exact for straight edges and slightly soft (< 1 px) on curves;
   a browser rendering the same SVG will differ by sub-pixel amounts. To verify: open
   `exports/pwa/icon-512.png` beside `icon.svg` in a browser at 512 px and compare; on an iPhone,
   add the PWA to the home screen and look at the apple-touch and maskable results.
2. **Nothing is Windows-device-tested or iPhone-tested.** `.ico` files are structurally valid and
   open in Pillow and ImageMagick, but Explorer, the taskbar, PyInstaller's `icon=` embedding and
   installer wizards have not seen them. Tray mono glyphs at 16 px have not been judged on a real
   taskbar at 100 %/125 %/150 % scaling.
3. **The wordmark is not a font.** Any text that is not the lockup must use the system stack; there
   is no way to "type" DoMe in the wordmark style, by design (BRAND.md §3). If a future surface needs
   more lockups (e.g. "DoMe for YouTube"), they are drawn as paths in a new SVG within the subset.
4. **The subset parser is intentionally narrow.** No `transform`, `style`, `text`, gradients, filters,
   masks, cubic curves or rotated arcs. Editing an SVG in a design tool that writes those attributes
   will make `pnpm export` fail with a clear message; clean the file back to the subset (BRAND.md §8).
5. **Export time is ~6 s** (the 1008 px wordmark dominates: per-pixel minimum distance over ~500
   flattened segments). Acceptable for a manual step and the test suite; not optimised with a spatial
   index.
6. **Wiring: mobile-app is wired, pc-agent is not.** `mobile-app/scripts/make-icons.mjs`
   (`pnpm gen:icons`) reads `brand/exports/manifest.json` (fields `file`, `source`; requires
   `source: "icon.svg"`) and copies `pwa/icon-192.png`, `pwa/icon-512.png`, `pwa/icon-192-maskable.png`,
   `pwa/icon-512-maskable.png`, `pwa/apple-touch-icon-180.png`, `favicon/favicon-16.png`,
   `favicon/favicon-32.png`, `favicon/favicon.ico` and `icon.svg` (as `favicon.svg`) into
   `mobile-app/public/icons/`; the committed PNG/ICO copies there are byte-identical to these
   exports. A brand test fails if that script asks for a path the export plan no longer produces.
   Still unwired: `pc-agent/dome_agent/tray.py` draws its own shape and `pc-agent/packaging/*.spec`
   have `icon=None` (BRAND.md §7 has the file-to-destination table; the tray should use the colour
   tile, D15).
   **Action for the maintainer:** `brand/icon.svg` now carries file-prefixed ids (D14), so
   `mobile-app/public/icons/favicon.svg` (a copy of the previous `icon.svg`) differs from it in its
   ids only; re-run `pnpm gen:icons` in `mobile-app/` to refresh it. The rendered pixels are
   unchanged. This component may not write outside `brand/`.
7. **Installer-specific artwork** (wizard side banners, header bitmaps in BMP) is not produced; it
   depends on the installer toolkit chosen for `pc-agent` and can be made from the wordmark PNGs.
8. **The mono glyph can read as a Wi-Fi indicator at 16 px.** Mitigated by making the colour tile
   the tray default and documenting the mono files as a high-contrast/monochrome fallback (D15).
   Not done: changing the glyph itself (e.g. a short baseline under the arc so it reads as a dome
   rather than a signal bar). That would change the identity in every icon, export and the lockup
   and needs a design decision plus the shared-geometry test update; to be judged on a real Windows
   taskbar at 100/125/150 % scaling first (not yet verified on a device).
9. **`pnpm export` writes bytes that depend on Node's bundled zlib.** `--check` and the tests compare
   pixels (D13), so a different Node does not report drift, but re-running `pnpm export` on another
   Node may rewrite the PNG/ICO files with different compressed bytes and identical pixels. Commit
   such a rewrite only together with a real source change.
