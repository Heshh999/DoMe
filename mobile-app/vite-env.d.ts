/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

interface ImportMetaEnv {
  /** Empty = same origin (production). Development proxies /v1 and /ws instead. */
  readonly VITE_DOME_API_ORIGIN?: string;
  readonly VITE_DOME_RELEASE_CHANNEL?: string;
  /** Optional support contact URL (mailto: or https:). Unset = "not configured yet" copy. */
  readonly VITE_DOME_SUPPORT_URL?: string;
}

/** Injected by Vite `define` from package.json (see vite.config.ts). */
declare const __APP_VERSION__: string;
/** Injected by Vite `define` from DOME_BUILD_COMMIT (scripts/build-commit.ts): a git commit, or "" for a local build. */
declare const __BUILD_COMMIT__: string;
