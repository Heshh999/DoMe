/**
 * Content script entry (https://www.youtube.com/*, top frame only). Generates a fresh tab_token for
 * this attachment, answers ops from the service worker and streams player state to it. It never
 * talks to the page's JavaScript, never inserts anything into the DOM and has no network access of
 * its own.
 */
import { isBackgroundToContent, type ContentReply, type ContentToBackground } from "../shared/messages.ts";
import { randomToken22 } from "../shared/token.ts";
import { createPlayerAdapter } from "./adapter.ts";
import { createEmitter } from "./emitter.ts";

export interface ContentRuntime {
  id: string;
  onMessage: {
    addListener(cb: (message: unknown, sender: chrome.runtime.MessageSender, sendResponse: (reply: ContentReply) => void) => boolean | undefined | void): void;
  };
  sendMessage(message: ContentToBackground): Promise<unknown>;
}

export function startContentScript(runtime: ContentRuntime, doc: Document, win: Window): { token: string; stop: () => void } {
  const token = randomToken22();
  const adapter = createPlayerAdapter({ doc, win, token });
  const emitter = createEmitter({
    doc,
    win,
    token,
    snapshot: () => adapter.snapshot(),
    send: async (message) => {
      await runtime.sendMessage(message);
    },
  });

  runtime.onMessage.addListener((message, sender, sendResponse) => {
    // Only our own service worker may drive the player: same extension id and not from a tab.
    if (sender.id !== runtime.id || sender.tab !== undefined) return undefined;
    if (!isBackgroundToContent(message)) return undefined;
    if (message.type === "probe") {
      sendResponse({ ok: true, token, state: adapter.snapshot() });
      return undefined;
    }
    void adapter.run(message.op, message.args, message.deadline_ms).then(sendResponse);
    return true; // keep the channel open for the asynchronous reply
  });

  emitter.start();
  return { token, stop: () => emitter.stop() };
}

if (typeof chrome !== "undefined" && chrome.runtime?.id && typeof document !== "undefined" && window.top === window) {
  startContentScript(chrome.runtime as unknown as ContentRuntime, document, window);
}
