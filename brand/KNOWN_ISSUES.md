# brand — known issues (recorded, not fixed)

1. **Nothing is Windows-device-tested or iPhone-tested.** The `.ico` files are structurally valid and
   decode back to the exact pixels in the script's own reader, but Explorer, the taskbar, PyInstaller's
   `icon=` embedding and installer wizards have not seen them. The tray tile with its connection badge
   has not been judged on a real taskbar at 100 %/125 %/150 % scaling, and the apple-touch and maskable
   icons have not been seen on a phone's home screen.
2. **The only raster of the logo is 1536 × 1024.** The traced outlines agree with it to about 0.3 px
   of that image (DECISIONS D18), so the vector files are as accurate as the source allows; details
   smaller than that (the exact shape of the cursor's notches, the last fraction of a pixel of each
   keyline) are the tracer's best reading, not the designer's file. If the owner can obtain the
   original vector artwork from whoever made the logo, compare it with these sources.
3. **The icon's mark is the wordmark's drawing.** The owner's icon was rendered separately and its ring
   is 1–1.5 px (of 221 px) bolder in places; the sources use one drawing everywhere (D18). Visible only
   when the two are overlaid at large size.
4. **At 16 px the band and its keylines merge** into the ring. The ring, the coral cursor pixel and
   the transparent corners still read (pixel-tested), which is the usual compromise for a detailed
   mark; a hand-hinted 16 px variant was not made. To judge on real screens (browser tab, Windows tray).
5. **`mobile-app/vite.config.ts` and `index.html` still declare the previous theme colours** for the
   PWA manifest (`theme_color`, `background_color`) and the `theme-color` meta tags. They belong to the
   app's theme work (BRAND.md §2 recommends the page backgrounds and DoMe Blue); this component did not
   change them.
6. **PyInstaller `icon=` is still `None`** in `pc-agent/packaging/*.spec`: the executable and
   shortcut icon do not use `exports/windows/dome.ico` yet (copy it next to the spec and set `icon=`;
   needs a Windows build to verify). The tray PNG is bundled (`dome_agent/assets`).
7. **The wordmark is not a font.** Any text that is not the lockup uses the system stack; there is no
   way to "type" DoMe in the logo's letters, by design (BRAND.md §3). New lockups (e.g. "DoMe for
   YouTube") are drawn as paths in a new SVG within the subset.
8. **The subset parser is intentionally narrow.** No `transform`, `style`, `text`, gradients, filters,
   masks, clip paths or rotated arcs. Editing an SVG in a design tool that writes those will make
   `pnpm export` fail with a clear message; flatten the file back to the subset (BRAND.md §7).
9. **Installer-specific artwork** (wizard side banners, header bitmaps in BMP) is not produced; it
   depends on the installer toolkit chosen for `pc-agent` and can be made from the wordmark PNGs.
10. **`pnpm export` writes bytes that depend on Node's bundled zlib.** `--check` and the tests compare
    pixels (D13), so a different Node does not report drift, but re-running `pnpm export` on another
    Node may rewrite the PNG/ICO files with different compressed bytes and identical pixels. Commit
    such a rewrite only together with a real source change.
