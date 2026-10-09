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
6. **Wiring is pending.** `mobile-app/public/icons/*`, `mobile-app/index.html` (apple-touch-icon,
   favicon links, `favicon.svg`), `pc-agent/dome_agent/tray.py` and `pc-agent/packaging/*.spec`
   (`icon=`) still use the previous placeholder mark or no icon; this component does not modify them.
   BRAND.md §7 has the exact file-to-destination table.
7. **Installer-specific artwork** (wizard side banners, header bitmaps in BMP) is not produced; it
   depends on the installer toolkit chosen for `pc-agent` and can be made from the wordmark PNGs.
