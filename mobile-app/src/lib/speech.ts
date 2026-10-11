/**
 * Speech in and out for the Assistant (docs/design/assistant.md §4.7, §4.8), behind small interfaces so the page and
 * the tests drive fakes.
 *
 * Recognition: the browser's own SpeechRecognition (Apple's on an iPhone; it may process the words on Apple's
 * servers; DoMe never records or uploads audio itself). It starts only from a tap (`start` is called in the talk
 * button's click handler), listens for one utterance, stops on a second tap, after 15 s of listening, and whenever the
 * page is hidden. A Home Screen app often has no web speech recognition at all: then, and after the errors that say
 * so, the page falls back to the keyboard's dictation key in a text field ("Dictate"), which always works.
 *
 * Synthesis: speechSynthesis with an English voice ranked for a British butler (en-GB first; novelty voices never
 * chosen by themselves). iOS speaks only after a gesture, so `unlock` speaks a silent space on taps that are not the
 * talk button, again on each such tap until speech has really started once; a line whose speech has not started 1.5 s
 * later is offered as "Tap to hear", and a line spoken from a tap ("Tap to hear", Test voice) starts inside that tap.
 * Nothing is spoken while listening, while muted or while the page is hidden (a reply waiting then is spoken when the
 * page shows again).
 */
import { isStandalone } from "./diagnostics.ts";

// --- recognition ------------------------------------------------------------------------------------------------------

/** The parts of the Web Speech API recognizer DoMe uses (not in TypeScript's DOM library). */
export interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onaudiostart: ((ev: unknown) => void) | null;
  onresult: ((ev: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
  onend: ((ev: unknown) => void) | null;
}

export interface SpeechWindow {
  SpeechRecognition?: (new () => SpeechRecognitionLike) | undefined;
  webkitSpeechRecognition?: (new () => SpeechRecognitionLike) | undefined;
  speechSynthesis?: SpeechSynthesisLike | undefined;
  SpeechSynthesisUtterance?: (new (text: string) => UtteranceLike) | undefined;
  navigator: { language?: string; audioSession?: { type: string } | undefined };
  document: { visibilityState: string; addEventListener(type: string, fn: () => void): void; removeEventListener(type: string, fn: () => void): void };
  addEventListener(type: string, fn: () => void): void;
  removeEventListener(type: string, fn: () => void): void;
  localStorage?: Storage;
  sessionStorage?: Storage;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

/** Listening ends 15 s after the microphone went live; waiting for it gives up after 30 s (permission sheets). */
export const LISTEN_MAX_MS = 15_000;
export const READY_MAX_MS = 30_000;
const SR_UNAVAILABLE_KEY = (mode: "standalone" | "browser") => `dome.assistant.sr_unavailable.${mode}`;
const SR_FAILED_KEY = "dome.assistant.sr_failed";

export type RecognizerState = "idle" | "starting" | "listening";

/**
 * Why listening ended without words. `unavailable`: no web speech recognition here (remembered per display mode);
 * `denied`: microphone or speech recognition not allowed; `language`: not offered for this language; `transient`: a
 * call or another app held the microphone, or the network dropped (`disabled` after two in a row: voice off for this
 * session); `no_speech`: nothing heard; `not_ready`: the microphone never started.
 */
export type RecognitionProblem = { kind: "unavailable" | "denied" | "language" | "no_speech" | "not_ready" } | { kind: "transient"; disabled: boolean };

export interface RecognitionHandlers {
  onState(state: RecognizerState): void;
  onInterim(text: string): void;
  /** The words, and when the speech ended (ms). */
  onFinal(text: string, endedAt: number): void;
  onProblem(problem: RecognitionProblem): void;
}

export interface Recognizer {
  /** Web speech recognition exists here and was not found unusable on this phone (this display mode / session). */
  available(): boolean;
  /** Start listening; call only from a click handler (user activation). */
  start(handlers: RecognitionHandlers): void;
  /** The second tap: stop and deliver what was heard. */
  stop(): void;
  /** DoMe stops listening itself (hidden page, barge-in): nothing is delivered and no error is reported. */
  abort(): void;
  /** Forget that voice failed here ("Try voice again"). */
  tryAgain(): void;
  dispose(): void;
}

function store(win: SpeechWindow, kind: "local" | "session"): Storage | null {
  try {
    return (kind === "local" ? win.localStorage : win.sessionStorage) ?? null;
  } catch {
    return null;
  }
}

function flag(s: Storage | null, key: string): boolean {
  try {
    return s?.getItem(key) === "1";
  } catch {
    return false;
  }
}

function setFlag(s: Storage | null, key: string, on: boolean): void {
  try {
    if (on) s?.setItem(key, "1");
    else s?.removeItem(key);
  } catch {
    /* storage unavailable: in memory only */
  }
}

export function createRecognizer(win: SpeechWindow = window as unknown as SpeechWindow, options: { now?: () => number; standalone?: () => boolean } = {}): Recognizer {
  const Ctor = win.SpeechRecognition ?? win.webkitSpeechRecognition;
  const now = options.now ?? (() => Date.now());
  const mode = () => ((options.standalone ?? isStandalone)() ? "standalone" : "browser");
  let current: { rec: SpeechRecognitionLike; handlers: RecognitionHandlers; final: string; interim: string; aborted: boolean; problem: boolean; endedAt: number | null; timer: unknown } | null = null;
  let failures = 0;

  const clearTimer = () => {
    if (current?.timer !== undefined && current.timer !== null) win.clearTimeout(current.timer);
    if (current) current.timer = null;
  };

  const report = (problem: RecognitionProblem) => {
    if (!current) return;
    current.problem = true;
    current.handlers.onProblem(problem);
  };

  const transient = () => {
    failures++;
    const disabled = failures >= 2;
    if (disabled) setFlag(store(win, "session"), SR_FAILED_KEY, true);
    report({ kind: "transient", disabled });
  };

  const finish = () => {
    if (!current) return;
    const c = current;
    clearTimer();
    current = null;
    c.rec.onaudiostart = c.rec.onresult = c.rec.onerror = c.rec.onend = null;
    c.handlers.onState("idle");
    if (c.aborted || c.problem) return;
    const text = (c.final || c.interim).trim();
    if (text) {
      failures = 0;
      c.handlers.onFinal(text, c.endedAt ?? now());
    } else c.handlers.onProblem({ kind: "no_speech" });
  };

  const onHidden = () => {
    if (win.document.visibilityState === "hidden") api.abort();
  };
  const onPageHide = () => api.abort();
  win.document.addEventListener("visibilitychange", onHidden);
  win.addEventListener("pagehide", onPageHide);

  const api: Recognizer = {
    available() {
      return !!Ctor && !flag(store(win, "local"), SR_UNAVAILABLE_KEY(mode())) && !flag(store(win, "session"), SR_FAILED_KEY);
    },
    start(handlers) {
      if (current) api.abort();
      if (!Ctor) {
        handlers.onProblem({ kind: "unavailable" });
        return;
      }
      const rec = new Ctor();
      rec.lang = win.navigator.language?.startsWith("en-") ? win.navigator.language : "en-GB";
      rec.interimResults = true;
      rec.continuous = false;
      rec.maxAlternatives = 1;
      current = { rec, handlers, final: "", interim: "", aborted: false, problem: false, endedAt: null, timer: null };
      const c = current;
      // "Getting ready…" until the microphone is live; the permission sheets of the first use fit in READY_MAX_MS
      c.timer = win.setTimeout(() => {
        if (current !== c) return;
        report({ kind: "not_ready" });
        c.aborted = true;
        rec.abort();
        finish();
      }, READY_MAX_MS);
      rec.onaudiostart = () => {
        if (current !== c) return;
        clearTimer();
        c.timer = win.setTimeout(() => {
          if (current === c) rec.stop();
        }, LISTEN_MAX_MS);
        handlers.onState("listening");
      };
      rec.onresult = (ev) => {
        if (current !== c) return;
        let interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const result = ev.results[i]!;
          const words = result[0]?.transcript ?? "";
          if (result.isFinal) {
            c.final = `${c.final} ${words}`.trim();
            c.endedAt = now();
          } else interim += words;
        }
        c.interim = interim.trim();
        handlers.onInterim(`${c.final} ${c.interim}`.trim());
      };
      rec.onerror = (ev) => {
        if (current !== c) return;
        switch (ev.error) {
          case "service-not-allowed":
            setFlag(store(win, "local"), SR_UNAVAILABLE_KEY(mode()), true);
            report({ kind: "unavailable" });
            break;
          case "not-allowed":
            report({ kind: "denied" });
            break;
          case "language-not-supported":
            report({ kind: "language" });
            break;
          case "no-speech":
            report({ kind: "no_speech" });
            break;
          case "aborted":
            if (!c.aborted) transient();
            break;
          default: // audio-capture, network and anything new
            transient();
        }
      };
      rec.onend = () => {
        if (current === c) finish();
      };
      handlers.onState("starting");
      try {
        rec.start();
      } catch {
        transient();
        finish();
      }
    },
    stop() {
      if (!current) return;
      if (current.endedAt === null) current.endedAt = now();
      try {
        current.rec.stop();
      } catch {
        finish();
      }
    },
    abort() {
      if (!current) return;
      current.aborted = true;
      try {
        current.rec.abort();
      } catch {
        /* already ended */
      }
      finish();
    },
    tryAgain() {
      failures = 0;
      setFlag(store(win, "local"), SR_UNAVAILABLE_KEY(mode()), false);
      setFlag(store(win, "session"), SR_FAILED_KEY, false);
    },
    dispose() {
      api.abort();
      win.document.removeEventListener("visibilitychange", onHidden);
      win.removeEventListener("pagehide", onPageHide);
    },
  };
  return api;
}

// --- synthesis --------------------------------------------------------------------------------------------------------

export interface VoiceLike {
  name: string;
  lang: string;
  voiceURI: string;
  default?: boolean;
}

export interface UtteranceLike {
  text: string;
  voice: VoiceLike | null;
  lang: string;
  rate: number;
  pitch: number;
  volume: number;
  onstart: ((ev: unknown) => void) | null;
  onend: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export interface SpeechSynthesisLike {
  speak(u: UtteranceLike): void;
  cancel(): void;
  getVoices(): VoiceLike[];
  speaking?: boolean;
}

/** Preferred British voices, best first; their "(Enhanced)" / "(Premium)" downloads rank above them. */
const PREFERRED = ["daniel", "arthur"];
/** Never chosen automatically (Eloquence and novelty voices); still listed last in the picker. */
const DENIED = new Set(["eddy", "flo", "grandma", "grandpa", "reed", "rocko", "sandy", "shelley", "albert", "bad news", "bahh", "bells", "boing", "bubbles", "cellos", "good news", "jester", "organ", "superstar", "trinoids", "whisper", "wobble", "zarvox"]);

const langOf = (v: VoiceLike) => v.lang.replace(/_/g, "-").toLowerCase();
const baseName = (v: VoiceLike) =>
  v.name
    .replace(/\s*\(.*\)\s*$/, "")
    .trim()
    .toLowerCase();
const isEnglish = (v: VoiceLike) => langOf(v) === "en" || langOf(v).startsWith("en-");
export const isDeniedVoice = (v: VoiceLike) => DENIED.has(baseName(v));

function score(v: VoiceLike): number {
  if (isDeniedVoice(v)) return 1000;
  const gb = langOf(v) === "en-gb";
  const pref = PREFERRED.indexOf(baseName(v));
  const better = /\((?:enhanced|premium)\)/i.test(v.name);
  if (gb && pref >= 0) return pref * 2 + (better ? 0 : 1);
  if (gb) return 10 + (better ? 0 : 1);
  return 20 + (better ? 0 : 1);
}

/** English voices for the picker: en-GB first (Daniel and Arthur at the top), any other English, the deny list last. */
export function rankVoices(voices: readonly VoiceLike[]): VoiceLike[] {
  return voices
    .filter(isEnglish)
    .map((v, i) => ({ v, i }))
    .sort((a, b) => score(a.v) - score(b.v) || a.i - b.i)
    .map((x) => x.v);
}

/** The saved voice if this phone still offers it; else the best ranked one that is not denied; else null (default). */
export function pickVoice(voices: readonly VoiceLike[], savedURI: string | null): VoiceLike | null {
  if (savedURI) {
    const saved = voices.find((v) => v.voiceURI === savedURI);
    if (saved) return saved;
  }
  return rankVoices(voices).find((v) => !isDeniedVoice(v)) ?? null;
}

export const MIN_RATE = 0.8;
export const MAX_RATE = 1.2;
export const clampRate = (rate: number) => (Number.isFinite(rate) ? Math.min(MAX_RATE, Math.max(MIN_RATE, rate)) : 1);

/** The speech of a line has not started this long after `speak`: it is offered as "Tap to hear". */
export const START_TIMEOUT_MS = 1_500;
const VOICE_RETRIES = 3;
const VOICE_RETRY_MS = 250;
/**
 * Route speech as playback (audible with the Silent switch on) where Safari offers `navigator.audioSession`, and back
 * to "auto" before listening. To be confirmed on the owner's iPhone (docs/design/assistant.md §8.6); false leaves the
 * audio session alone.
 */
export const ROUTE_AUDIO_SESSION = true;

export interface SpeakOptions {
  /** Called inside a tap: speak at once (iOS only lets a gesture start speech), even before the voice list is read. */
  gesture?: boolean;
  onStart?(): void;
  onEnd?(): void;
  /** Not started within START_TIMEOUT_MS (speech still locked), or not spoken at all (muted, listening, no synthesiser). */
  onNotStarted?(): void;
}

export interface SpeakerPrefs {
  voiceURI: string | null;
  rate: number;
  muted: boolean;
}

export interface Speaker {
  /** A speech synthesiser exists here. */
  readonly available: boolean;
  /** iOS: speak a silent space from a gesture (never the talk button), so later replies may be spoken. */
  unlock(): void;
  /** Speech has really started at least once (iOS let it through): `unlock` is no longer needed. */
  readonly unlocked: boolean;
  speak(text: string, options?: SpeakOptions): void;
  cancel(): void;
  /** Listening: stop speaking and route audio for the microphone; nothing is spoken until it ends. */
  setListening(listening: boolean): void;
  setPrefs(prefs: SpeakerPrefs): void;
  /** English voices this phone offers, ranked (for the Settings picker). */
  voices(): VoiceLike[];
  dispose(): void;
}

export function createSpeaker(win: SpeechWindow = window as unknown as SpeechWindow, options: { audioSession?: boolean } = {}): Speaker {
  const synth = win.speechSynthesis;
  const Utterance = win.SpeechSynthesisUtterance;
  const routeAudio = options.audioSession ?? ROUTE_AUDIO_SESSION;
  let prefs: SpeakerPrefs = { voiceURI: null, rate: 1, muted: false };
  let unlocked = false;
  let listening = false;
  /** A line that arrived while the page was hidden: spoken when it shows again, if still the newest. */
  let waiting: { text: string; options: SpeakOptions } | null = null;
  let generation = 0;
  const available = !!synth && !!Utterance;

  const route = (type: "playback" | "auto") => {
    const session = win.navigator.audioSession;
    if (!routeAudio || !session) return;
    try {
      session.type = type;
    } catch {
      /* not settable here */
    }
  };

  const hidden = () => win.document.visibilityState === "hidden";

  const say = (text: string, opts: SpeakOptions, attempt: number) => {
    const mine = generation;
    const voices = synth!.getVoices();
    // inside a tap the line cannot wait: the gesture would be gone (the next line picks the ranked voice)
    if (voices.length === 0 && attempt < VOICE_RETRIES && !opts.gesture) {
      // Safari has not always fired voiceschanged: read the list again shortly instead of waiting for the event
      win.setTimeout(() => {
        if (mine === generation) say(text, opts, attempt + 1);
      }, VOICE_RETRY_MS);
      return;
    }
    const u = new Utterance!(text);
    const voice = pickVoice(voices, prefs.voiceURI);
    if (voice) {
      u.voice = voice;
      u.lang = voice.lang.replace(/_/g, "-");
    } else u.lang = "en-GB";
    u.rate = clampRate(prefs.rate);
    u.pitch = 1;
    u.volume = 1;
    let started = false;
    const timer = win.setTimeout(() => {
      if (!started) opts.onNotStarted?.();
    }, START_TIMEOUT_MS);
    u.onstart = () => {
      started = true;
      unlocked = true;
      win.clearTimeout(timer);
      opts.onStart?.();
    };
    u.onend = () => {
      win.clearTimeout(timer);
      opts.onEnd?.();
    };
    u.onerror = () => {
      win.clearTimeout(timer);
      if (!started) opts.onNotStarted?.();
      opts.onEnd?.();
    };
    route("playback");
    synth!.speak(u);
  };

  const onVisibility = () => {
    if (hidden()) {
      generation++;
      synth?.cancel();
      return;
    }
    const next = waiting;
    waiting = null;
    if (next && !listening && !prefs.muted) say(next.text, next.options, 0);
  };
  win.document.addEventListener("visibilitychange", onVisibility);

  return {
    available,
    get unlocked() {
      return unlocked;
    },
    unlock() {
      // tried on every tap until iOS really starts an utterance: a refused or ignored try leaves it locked
      if (unlocked || !available) return;
      const u = new Utterance!(" ");
      u.volume = 0;
      u.onstart = u.onend = () => {
        unlocked = true;
      };
      try {
        synth!.speak(u);
      } catch {
        /* still locked: the next tap tries again */
      }
    },
    speak(text, opts = {}) {
      const line = text.trim();
      if (!available || prefs.muted || listening || line === "") {
        opts.onNotStarted?.();
        return;
      }
      generation++;
      synth!.cancel(); // the newest line wins; never two at once
      if (hidden()) {
        waiting = { text: line, options: opts };
        return;
      }
      waiting = null;
      say(line, opts, 0);
    },
    cancel() {
      generation++;
      waiting = null;
      synth?.cancel();
    },
    setListening(on) {
      listening = on;
      if (on) {
        generation++;
        waiting = null;
        synth?.cancel();
        route("auto");
      }
    },
    setPrefs(next) {
      prefs = { ...next, rate: clampRate(next.rate) };
      if (prefs.muted) {
        generation++;
        waiting = null;
        synth?.cancel();
      }
    },
    voices() {
      return available ? rankVoices(synth!.getVoices()) : [];
    },
    dispose() {
      win.document.removeEventListener("visibilitychange", onVisibility);
      synth?.cancel();
    },
  };
}
