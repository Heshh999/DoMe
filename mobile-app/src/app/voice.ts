/**
 * The app's one speech recognizer, one speaker and one Assistant controller (docs/design/assistant.md §4.7, §4.8,
 * §4.3), created on first use. The speaker follows the phone preferences (voice, rate, mute) and reports whether a
 * line is being spoken; `AppShell` unlocks it on taps that are not the talk button. Switching PC cancels the AI turn in
 * flight (the runtime clears the conversation; the controller then drops what that turn would have done). Tests install
 * fakes.
 */
import { api } from "../lib/api.ts";
import { createAssistantController, livePcAccess, type AssistantController } from "../lib/assistant.ts";
import { log } from "../lib/log.ts";
import { createRecognizer, createSpeaker, type Recognizer, type SpeakOptions, type Speaker } from "../lib/speech.ts";
import { useAssistantStore } from "../store/assistant.ts";
import { useDevicesStore } from "../store/devices.ts";

let recognizer: Recognizer | null = null;
let speaker: Speaker | null = null;
let controller: AssistantController | null = null;

/** Wraps a speaker so the store knows when a line is being spoken. */
function tracked(inner: Speaker): Speaker {
  const set = (on: boolean) => useAssistantStore.getState().setSpeaking(on);
  return {
    get available() {
      return inner.available;
    },
    unlock: () => inner.unlock(),
    get unlocked() {
      return inner.unlocked;
    },
    speak(text, options: SpeakOptions = {}) {
      inner.speak(text, {
        ...(options.gesture ? { gesture: true } : {}),
        onStart: () => {
          set(true);
          options.onStart?.();
        },
        onEnd: () => {
          set(false);
          options.onEnd?.();
        },
        onNotStarted: () => {
          set(false);
          options.onNotStarted?.();
        },
      });
    },
    cancel() {
      set(false);
      inner.cancel();
    },
    setListening(on) {
      if (on) set(false);
      inner.setListening(on);
    },
    setPrefs: (prefs) => inner.setPrefs(prefs),
    voices: () => inner.voices(),
    dispose: () => inner.dispose(),
  };
}

export function getSpeaker(): Speaker {
  if (!speaker) {
    speaker = tracked(createSpeaker());
    const apply = () => {
      const { voiceURI, rate, muted } = useAssistantStore.getState().prefs;
      speaker?.setPrefs({ voiceURI, rate, muted });
    };
    apply();
    useAssistantStore.subscribe((s, before) => {
      if (s.prefs !== before.prefs) apply();
    });
  }
  return speaker;
}

export function getRecognizer(): Recognizer {
  recognizer ??= createRecognizer();
  return recognizer;
}

let unsubscribePc: (() => void) | null = null;

export function getAssistant(): AssistantController {
  if (!controller) {
    controller = createAssistantController({ api: { turn: (body, signal) => api.assistantTurn(body, signal) }, pc: livePcAccess(), speaker: getSpeaker() });
    unsubscribePc ??= useDevicesStore.subscribe((s, before) => {
      if (s.selectedPcId !== before.selectedPcId) controller?.cancelTurn();
    });
  }
  return controller;
}

/** GET /v1/assistant into the store; a server without the assistant (404) or no connection leaves AI unused. */
export async function loadAssistantStatus(): Promise<void> {
  try {
    useAssistantStore.getState().setStatus(await api.assistantStatus());
  } catch (e) {
    const code = e && typeof e === "object" && "code" in e ? String((e as { code: unknown }).code) : "INTERNAL";
    log.info("assistant.status_unavailable", { code });
    useAssistantStore.getState().setStatus(null, code);
  }
}

/** Tests only. */
export function setVoiceForTests(parts: { recognizer?: Recognizer | null; speaker?: Speaker | null; controller?: AssistantController | null }): void {
  if ("recognizer" in parts) recognizer = parts.recognizer ?? null;
  if ("speaker" in parts) speaker = parts.speaker ?? null;
  if ("controller" in parts) controller = parts.controller ?? null;
}
