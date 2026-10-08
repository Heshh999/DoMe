/** Status-only popup: connection state, profile label, ids and troubleshooting hints. No playback controls. */
import type { ConnectionStateName, PopupToBackground, StatusReport } from "../shared/messages.ts";
import { POPUP_KIND } from "../shared/messages.ts";

const HINTS: Record<ConnectionStateName, { title: string; hint: string; cls: string }> = {
  connected: { title: "Connected to the DoMe agent", hint: "", cls: "connected" },
  connecting: { title: "Connecting...", hint: "", cls: "" },
  disconnected: { title: "Not connected", hint: "The agent closed the connection. The extension retries automatically; use Retry to try now.", cls: "warn" },
  agent_not_running: { title: "DoMe agent is not running", hint: "Start DoMe from the Start menu (look for the tray icon), then press Retry.", cls: "warn" },
  host_missing: { title: "DoMe agent is not installed for this browser", hint: "Install the DoMe agent on this PC, or run its 'Register browser' step (dome-agent install-native-host), then press Retry.", cls: "error" },
  host_forbidden: { title: "This extension is not registered with the agent", hint: "The agent only accepts registered extension ids. For a development build, set DOME_AGENT_DEV_EXTENSION_ID to the Extension id shown below and re-run dome-agent install-native-host.", cls: "error" },
  incompatible: { title: "Update needed", hint: "The extension and the DoMe agent speak different protocol versions. Update whichever is older.", cls: "error" },
  error: { title: "Connection error", hint: "Something went wrong talking to the agent. Press Retry; if it persists, restart the browser.", cls: "error" },
};

function byId<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`popup element missing: ${id}`);
  return el as T;
}

async function ask(message: PopupToBackground): Promise<unknown> {
  return chrome.runtime.sendMessage(message);
}

function isStatus(v: unknown): v is StatusReport {
  return typeof v === "object" && v !== null && typeof (v as StatusReport).browser_instance_id === "string" && typeof (v as StatusReport).connection === "object";
}

function render(status: StatusReport): void {
  const info = HINTS[status.connection.state] ?? HINTS.error;
  byId("state-title").textContent = info.title;
  byId("state-detail").textContent = status.connection.message ? status.connection.message : "";
  byId("hint").textContent = info.hint;
  const dot = byId("indicator");
  dot.className = `dot ${info.cls}`.trim();
  byId("instance-id").textContent = status.browser_instance_id;
  byId("extension-id").textContent = status.extension_id;
  byId("agent-version").textContent = status.connection.agent_version ? `v${status.connection.agent_version} (protocol ${status.connection.protocol_version ?? "?"})` : "-";
  byId("tabs-count").textContent = String(status.attached_tabs);
  const input = byId<HTMLInputElement>("profile-label");
  if (document.activeElement !== input) input.value = status.profile_label;
}

async function refresh(): Promise<void> {
  try {
    const status = await ask({ kind: POPUP_KIND, type: "status" });
    if (isStatus(status)) render(status);
    else byId("state-title").textContent = "Status unavailable";
  } catch {
    byId("state-title").textContent = "Background worker not responding";
    byId("hint").textContent = "Reload the extension from the browser's extensions page.";
  }
}

byId("save-label").addEventListener("click", () => {
  const value = byId<HTMLInputElement>("profile-label").value;
  void ask({ kind: POPUP_KIND, type: "set_profile_label", value }).then(refresh);
});
byId("reconnect").addEventListener("click", () => {
  void ask({ kind: POPUP_KIND, type: "reconnect" }).then(() => setTimeout(() => void refresh(), 600));
});

void refresh();
setInterval(() => void refresh(), 2000);
