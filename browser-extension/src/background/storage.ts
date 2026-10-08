/** Persistent facts in chrome.storage.local — the only state assumed to survive worker termination. */
import type { ConnectionState } from "../shared/messages.ts";
import { sanitizeLabel } from "../shared/sanitize.ts";
import { BROWSER_INSTANCE_ID_RE, randomToken22 } from "../shared/token.ts";
import type { ExtensionApi } from "./api.ts";

export const KEY_INSTANCE_ID = "browser_instance_id";
export const KEY_PROFILE_LABEL = "profile_label";
export const KEY_CONNECTION_STATE = "connection_state";

export class ExtensionStorage {
  private instanceIdPromise: Promise<string> | null = null;

  constructor(private readonly api: ExtensionApi) {}

  /** Random id generated once per installation; identifies this browser profile to the agent. */
  browserInstanceId(): Promise<string> {
    this.instanceIdPromise ??= (async () => {
      const stored = (await this.api.storage.local.get(KEY_INSTANCE_ID))[KEY_INSTANCE_ID];
      if (typeof stored === "string" && BROWSER_INSTANCE_ID_RE.test(stored)) return stored;
      const fresh = randomToken22();
      await this.api.storage.local.set({ [KEY_INSTANCE_ID]: fresh });
      return fresh;
    })();
    return this.instanceIdPromise;
  }

  async profileLabel(): Promise<string> {
    const stored = (await this.api.storage.local.get(KEY_PROFILE_LABEL))[KEY_PROFILE_LABEL];
    return sanitizeLabel(stored);
  }

  async setProfileLabel(value: unknown): Promise<string> {
    const label = sanitizeLabel(value);
    await this.api.storage.local.set({ [KEY_PROFILE_LABEL]: label });
    return label;
  }

  async connectionState(): Promise<ConnectionState | null> {
    const stored = (await this.api.storage.local.get(KEY_CONNECTION_STATE))[KEY_CONNECTION_STATE];
    if (typeof stored !== "object" || stored === null) return null;
    const s = stored as Partial<ConnectionState>;
    if (typeof s.state !== "string" || typeof s.since !== "string") return null;
    return s as ConnectionState;
  }

  async setConnectionState(state: ConnectionState): Promise<void> {
    await this.api.storage.local.set({ [KEY_CONNECTION_STATE]: state });
  }
}
