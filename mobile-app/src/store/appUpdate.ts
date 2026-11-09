/**
 * React-visible state of a newer DoMe waiting to be loaded (lib/appUpdate.ts `AppUpdater`, wired in pwa.ts): whether
 * one is ready and what the reload waits for. The notice (components/AppUpdateNotice.tsx) reads it; nothing persists.
 */
import { create } from "zustand";

import { NO_UPDATE, type AppUpdateView } from "../lib/appUpdate.ts";

export const useAppUpdateStore = create<AppUpdateView>()(() => NO_UPDATE);
