# brand — contract / shared-library issues

The brand component does not touch `shared/protocol` or the shared libraries, and nothing under
`shared/` was modified. There are no wire-contract issues. Notes about the interfaces with other
components:

## 1. Export paths are an interface with three consumers

`brand/exports/` is copied, not rendered, by the components that ship the logo:

- `mobile-app/scripts/make-icons.mjs` (`pnpm gen:icons`) reads `brand/exports/manifest.json`
  (`file`, `source`; it requires `source: "icon.svg"`) and copies `pwa/icon-192.png`,
  `pwa/icon-512.png`, `pwa/icon-192-maskable.png`, `pwa/icon-512-maskable.png`,
  `pwa/apple-touch-icon-180.png`, `favicon/favicon-16.png`, `favicon/favicon-32.png`,
  `favicon/favicon.ico` and `icon.svg` (as `favicon.svg`) into `mobile-app/public/icons/`.
- `brand/scripts/sync-consumers.mjs` (`pnpm sync` in `brand/`) copies `extension/icon-16.png`,
  `icon-32.png`, `icon-48.png`, `icon-128.png` into `browser-extension/public/icons/` (named by the
  extension manifest's `icons` and `action.default_icon`) and `windows/tray-256.png` into
  `pc-agent/dome_agent/assets/` (loaded by `pc-agent/dome_agent/tray.py`; bundled by
  `pc-agent/packaging/dome-agent.spec`).

Renaming an export or a manifest field must be done together with those scripts;
`tests/brand.test.mjs` fails if a consumer asks for a path the plan no longer produces or if a
committed copy no longer matches its export.

## 2. No image library is available in the repository's toolchain

`mobile-app/node_modules` contains no `sharp`/`resvg` and the machine has no system SVG rasterizer,
which is why `brand/scripts/export.mjs` carries its own (README "How the exports are made"; DECISIONS
D2, D19). The SVG sources are standard SVG and render the same in browsers (compared with Chromium,
README); the subset restrictions exist only so that the self-contained script stays correct.
