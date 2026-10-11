/** Status-only popup: connection state, profile label, ids and troubleshooting hints. No playback controls. */
import type { PopupToBackground, StatusReport } from "../shared/messages.ts";
import { POPUP_KIND } from "../shared/messages.ts";
import { describeDetail, describeStatus } from "./status.ts";

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
  const info = describeStatus(status);
  byId("state-title").textContent = info.title;
  byId("state-detail").textContent = describeDetail(status);
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
    else byId("state-title").textContent = "Status unavailable - press Retry connection below";
  } catch {
    byId("state-title").textContent = "The DoMe extension is not responding";
    byId("hint").textContent = "Open the browser's Extensions page and press Reload on DoMe for YouTube.";
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
