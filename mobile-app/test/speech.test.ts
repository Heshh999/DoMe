/**
 * Speech in and out (src/lib/speech.ts) with fake browser APIs. Recognition: settings per start, "Getting ready…"
 * until the microphone is live, interim and final words, the 15 s cap counted from audiostart, the 30 s give-up,
 * stopping when the page is hidden, and each error path (remembered per display mode, per session after two transient
 * failures, DoMe's own abort ignored). Synthesis: the silent unlock once, "Tap to hear" when speech does not start,
 * the audio-session route both ways, the voice ranking and deny list, the getVoices retry, rate bounds, mute, barge-in
 * and a reply that waits for the page to be visible.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clampRate, createRecognizer, createSpeaker, isDeniedVoice, LISTEN_MAX_MS, pickVoice, rankVoices, READY_MAX_MS, START_TIMEOUT_MS, type RecognitionHandlers, type SpeechRecognitionLike, type SpeechWindow, type UtteranceLike, type VoiceLike } from "../src/lib/speech.ts";

class FakeRecognition implements SpeechRecognitionLike {
  static all: FakeRecognition[] = [];
  lang = "";
  interimResults = false;
  continuous = true;
  maxAlternatives = 5;
  started = 0;
  stopped = 0;
  aborted = 0;
  throwOnStart = false;
  onaudiostart: ((ev: unknown) => void) | null = null;
  onresult: SpeechRecognitionLike["onresult"] = null;
  onerror: ((ev: { error: string }) => void) | null = null;
  onend: ((ev: unknown) => void) | null = null;
  constructor() {
    FakeRecognition.all.push(this);
  }
  start() {
    if (this.throwOnStart) throw new Error("InvalidStateError");
    this.started++;
  }
  stop() {
    this.stopped++;
    this.onend?.({});
  }
  abort() {
    this.aborted++;
    this.onerror?.({ error: "aborted" });
    this.onend?.({});
  }
  audio() {
    this.onaudiostart?.({});
  }
  say(words: string, isFinal: boolean) {
    const result = Object.assign([{ transcript: words }], { isFinal });
    this.onresult?.({ resultIndex: 0, results: [result] });
  }
  fail(error: string) {
    this.onerror?.({ error });
    this.onend?.({});
  }
}

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, String(v)), removeItem: (k) => void m.delete(k), clear: () => m.clear(), key: () => null, get length() { return m.size; } } as Storage;
}

function fakeWindow(extra: Partial<SpeechWindow> = {}) {
  const docListeners = new Map<string, Set<() => void>>();
  const winListeners = new Map<string, Set<() => void>>();
  const doc = {
    visibilityState: "visible",
    addEventListener: (t: string, fn: () => void) => void (docListeners.get(t) ?? docListeners.set(t, new Set()).get(t)!).add(fn),
    removeEventListener: (t: string, fn: () => void) => void docListeners.get(t)?.delete(fn),
  };
  const win: SpeechWindow & { fire(t: string): void; hide(hidden: boolean): void } = {
    SpeechRecognition: FakeRecognition,
    navigator: { language: "en-US" },
    document: doc,
    addEventListener: (t, fn) => void (winListeners.get(t) ?? winListeners.set(t, new Set()).get(t)!).add(fn),
    removeEventListener: (t, fn) => void winListeners.get(t)?.delete(fn),
    localStorage: memoryStorage(),
    sessionStorage: memoryStorage(),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
    fire: (t) => winListeners.get(t)?.forEach((fn) => fn()),
    hide(hidden) {
      doc.visibilityState = hidden ? "hidden" : "visible";
      docListeners.get("visibilitychange")?.forEach((fn) => fn());
    },
    ...extra,
  };
  return win;
}

function handlers() {
  const log: string[] = [];
  const h: RecognitionHandlers = {
    onState: (s) => void log.push(`state:${s}`),
    onInterim: (t) => void log.push(`interim:${t}`),
    onFinal: (t) => void log.push(`final:${t}`),
    onProblem: (p) => void log.push(`problem:${p.kind}${"disabled" in p ? `:${p.disabled}` : ""}`),
  };
  return { h, log };
}

beforeEach(() => {
  FakeRecognition.all = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe("recognition", () => {
  it("is unavailable without SpeechRecognition (dictation instead)", () => {
    const r = createRecognizer(fakeWindow({ SpeechRecognition: undefined }));
    expect(r.available()).toBe(false);
    const { h, log } = handlers();
    r.start(h);
    expect(log).toEqual(["problem:unavailable"]);
  });

  it("uses webkitSpeechRecognition, one utterance, interim words, en-GB unless the phone speaks another English", () => {
    const win = fakeWindow({ SpeechRecognition: undefined, webkitSpeechRecognition: FakeRecognition, navigator: { language: "fr-FR" } });
    const r = createRecognizer(win);
    r.start(handlers().h);
    const rec = FakeRecognition.all[0]!;
    expect([rec.lang, rec.interimResults, rec.continuous, rec.maxAlternatives]).toEqual(["en-GB", true, false, 1]);
    const r2 = createRecognizer(fakeWindow());
    r2.start(handlers().h);
    expect(FakeRecognition.all[1]!.lang).toBe("en-US");
  });

  it("'Getting ready…' until the microphone is live, then listening; interim then final words", () => {
    const r = createRecognizer(fakeWindow());
    const { h, log } = handlers();
    r.start(h);
    const rec = FakeRecognition.all[0]!;
    expect(log).toEqual(["state:starting"]);
    rec.audio();
    rec.say("pause", false);
    rec.say("pause youtube", true);
    rec.onend?.({});
    expect(log).toEqual(["state:starting", "state:listening", "interim:pause", "interim:pause youtube", "state:idle", "final:pause youtube"]);
  });

  it("a second tap stops and delivers what was heard", () => {
    const r = createRecognizer(fakeWindow());
    const { h, log } = handlers();
    r.start(h);
    const rec = FakeRecognition.all[0]!;
    rec.audio();
    rec.say("open note", false);
    r.stop();
    expect(rec.stopped).toBe(1);
    expect(log.at(-1)).toBe("final:open note");
  });

  it("listening stops 15 s after audiostart, not after the start tap (permission sheets do not eat it)", () => {
    const r = createRecognizer(fakeWindow());
    r.start(handlers().h);
    const rec = FakeRecognition.all[0]!;
    vi.advanceTimersByTime(10_000);
    rec.audio();
    vi.advanceTimersByTime(LISTEN_MAX_MS - 1);
    expect(rec.stopped).toBe(0);
    vi.advanceTimersByTime(1);
    expect(rec.stopped).toBe(1);
  });

  it("gives up when the microphone never starts within 30 s", () => {
    const r = createRecognizer(fakeWindow());
    const { h, log } = handlers();
    r.start(h);
    vi.advanceTimersByTime(READY_MAX_MS);
    expect(log).toEqual(["state:starting", "problem:not_ready", "state:idle"]);
    expect(FakeRecognition.all[0]!.aborted).toBe(1);
  });

  it("never listens in the background: hidden page and pagehide abort without delivering", () => {
    const win = fakeWindow();
    const r = createRecognizer(win);
    const { h, log } = handlers();
    r.start(h);
    FakeRecognition.all[0]!.audio();
    FakeRecognition.all[0]!.say("lock my", false);
    win.hide(true);
    expect(FakeRecognition.all[0]!.aborted).toBe(1);
    expect(log.some((l) => l.startsWith("final") || l.startsWith("problem"))).toBe(false);
    r.start(h);
    win.fire("pagehide");
    expect(FakeRecognition.all[1]!.aborted).toBe(1);
  });

  it("service-not-allowed: unavailable, remembered for this display mode only, until 'Try voice again'", () => {
    const win = fakeWindow();
    let standalone = true;
    const r = createRecognizer(win, { standalone: () => standalone });
    const { h, log } = handlers();
    r.start(h);
    FakeRecognition.all[0]!.fail("service-not-allowed");
    expect(log).toContain("problem:unavailable");
    expect(r.available()).toBe(false);
    expect(win.localStorage!.getItem("dome.assistant.sr_unavailable.standalone")).toBe("1");
    standalone = false;
    expect(r.available()).toBe(true);
    standalone = true;
    r.tryAgain();
    expect(r.available()).toBe(true);
  });

  it.each([
    ["not-allowed", "problem:denied"],
    ["language-not-supported", "problem:language"],
    ["no-speech", "problem:no_speech"],
  ])("%s → %s, reported once", (error, problem) => {
    const r = createRecognizer(fakeWindow());
    const { h, log } = handlers();
    r.start(h);
    FakeRecognition.all[0]!.fail(error);
    expect(log.filter((l) => l.startsWith("problem"))).toEqual([problem]);
    expect(r.available()).toBe(true);
  });

  it("audio-capture and network: try again; voice is off for the session only after two in a row", () => {
    const win = fakeWindow();
    const r = createRecognizer(win);
    const { h, log } = handlers();
    r.start(h);
    FakeRecognition.all[0]!.fail("audio-capture");
    expect(log.at(-2)).toBe("problem:transient:false");
    expect(r.available()).toBe(true);
    r.start(h);
    FakeRecognition.all[1]!.fail("network");
    expect(log.at(-2)).toBe("problem:transient:true");
    expect(r.available()).toBe(false);
    expect(win.sessionStorage!.getItem("dome.assistant.sr_failed")).toBe("1");
    r.tryAgain();
    expect(r.available()).toBe(true);
  });

  it("a success in between resets the count", () => {
    const r = createRecognizer(fakeWindow());
    const { h, log } = handlers();
    r.start(h);
    FakeRecognition.all[0]!.fail("audio-capture");
    r.start(h);
    FakeRecognition.all[1]!.say("hello", true);
    FakeRecognition.all[1]!.onend?.({});
    r.start(h);
    FakeRecognition.all[2]!.fail("audio-capture");
    expect(log.filter((l) => l.startsWith("problem"))).toEqual(["problem:transient:false", "problem:transient:false"]);
  });

  it("DoMe's own abort is not an error; an abort DoMe did not ask for is treated like audio-capture", () => {
    const r = createRecognizer(fakeWindow());
    const { h, log } = handlers();
    r.start(h);
    r.abort();
    expect(log.some((l) => l.startsWith("problem"))).toBe(false);
    r.start(h);
    FakeRecognition.all[1]!.fail("aborted");
    expect(log.at(-2)).toBe("problem:transient:false");
  });

  it("an exception from start() is treated like audio-capture", () => {
    const win = fakeWindow({
      SpeechRecognition: class extends FakeRecognition {
        override start() {
          throw new Error("busy");
        }
      },
    });
    const r = createRecognizer(win);
    const { h, log } = handlers();
    r.start(h);
    expect(log).toEqual(["state:starting", "problem:transient:false", "state:idle"]);
  });

  it("ending with no words says nothing was heard", () => {
    const r = createRecognizer(fakeWindow());
    const { h, log } = handlers();
    r.start(h);
    FakeRecognition.all[0]!.onend?.({});
    expect(log.at(-1)).toBe("problem:no_speech");
  });
});

// --- synthesis ------------------------------------------------------------------------------------------------------------

class FakeUtterance implements UtteranceLike {
  voice: VoiceLike | null = null;
  lang = "";
  rate = 1;
  pitch = 1;
  volume = 1;
  onstart: ((ev: unknown) => void) | null = null;
  onend: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(public text: string) {}
}

const V = (name: string, lang: string): VoiceLike => ({ name, lang, voiceURI: `uri:${name}:${lang}` });
const VOICES = [V("Samantha", "en-US"), V("Eddy (English (UK))", "en-GB"), V("Daniel", "en_GB"), V("Arthur", "en-GB"), V("Daniel (Enhanced)", "en-GB"), V("Kate", "en-GB"), V("Zarvox", "en-US"), V("Thomas", "fr-FR")];

function synth(voices: VoiceLike[] = VOICES) {
  const spoken: FakeUtterance[] = [];
  let list = voices;
  return {
    spoken,
    setVoices: (v: VoiceLike[]) => (list = v),
    s: {
      speak: vi.fn((u: UtteranceLike) => void spoken.push(u as FakeUtterance)),
      cancel: vi.fn(),
      getVoices: vi.fn(() => list),
    },
  };
}

describe("voices", () => {
  it("ranks en-GB first with Daniel and Arthur on top (enhanced first), other English next, the deny list last", () => {
    expect(rankVoices(VOICES).map((v) => v.name)).toEqual(["Daniel (Enhanced)", "Daniel", "Arthur", "Kate", "Samantha", "Eddy (English (UK))", "Zarvox"]);
    expect(isDeniedVoice(V("Grandpa (English (UK))", "en-GB"))).toBe(true);
  });

  it("picks the saved voice when still offered, never a denied voice by itself", () => {
    expect(pickVoice(VOICES, "uri:Kate:en-GB")?.name).toBe("Kate");
    expect(pickVoice(VOICES, "uri:gone")?.name).toBe("Daniel (Enhanced)");
    expect(pickVoice([V("Zarvox", "en-US"), V("Eddy", "en-GB")], null)).toBeNull();
    expect(pickVoice(VOICES, "uri:Zarvox:en-US")?.name).toBe("Zarvox"); // the owner chose it
  });

  it("rate is kept between 0.8 and 1.2", () => {
    expect([clampRate(0.1), clampRate(1.1), clampRate(9), clampRate(Number.NaN)]).toEqual([0.8, 1.1, 1.2, 1]);
  });
});

describe("speaker", () => {
  it("without speechSynthesis: text only", () => {
    const sp = createSpeaker(fakeWindow());
    const notStarted = vi.fn();
    sp.speak("Hello.", { onNotStarted: notStarted });
    expect(sp.available).toBe(false);
    expect(notStarted).toHaveBeenCalled();
  });

  it("unlocks with a silent space, tried again on every tap until speech really starts", () => {
    const f = synth();
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    // iOS ignored the first tries (no start event): still locked, so the next tap tries again
    sp.unlock();
    sp.unlock();
    expect(sp.unlocked).toBe(false);
    expect(f.spoken.map((u) => [u.text, u.volume])).toEqual([
      [" ", 0],
      [" ", 0],
    ]);
    f.spoken[1]!.onstart?.({});
    expect(sp.unlocked).toBe(true);
    sp.unlock();
    expect(f.spoken).toHaveLength(2);
  });

  it("a try that throws stays locked; any line that starts unlocks", () => {
    const f = synth();
    f.s.speak.mockImplementationOnce(() => {
      throw new Error("not allowed");
    });
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    sp.unlock();
    expect(sp.unlocked).toBe(false);
    sp.speak("Very good.");
    f.spoken.at(-1)!.onstart?.({});
    expect(sp.unlocked).toBe(true);
  });

  it("speaks with the ranked voice, its language, the rate and pitch 1", () => {
    const f = synth();
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    sp.setPrefs({ voiceURI: null, rate: 1.5, muted: false });
    sp.speak("Very good.");
    const u = f.spoken[0]!;
    expect([u.text, u.voice?.name, u.lang, u.rate, u.pitch]).toEqual(["Very good.", "Daniel (Enhanced)", "en-GB", 1.2, 1]);
  });

  it("'Tap to hear' when speech has not started after 1.5 s; not when it started", () => {
    const f = synth();
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    const notStarted = vi.fn();
    sp.speak("One.", { onNotStarted: notStarted });
    vi.advanceTimersByTime(START_TIMEOUT_MS);
    expect(notStarted).toHaveBeenCalledTimes(1);
    const later = vi.fn();
    sp.speak("Two.", { onNotStarted: later });
    f.spoken[1]!.onstart?.({});
    vi.advanceTimersByTime(START_TIMEOUT_MS);
    expect(later).not.toHaveBeenCalled();
  });

  it("reads the voices again while the list is empty (Safari has not always fired voiceschanged)", () => {
    const f = synth([]);
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    sp.speak("Hello.");
    expect(f.spoken).toHaveLength(0);
    f.setVoices(VOICES);
    vi.advanceTimersByTime(250);
    expect(f.spoken[0]!.voice?.name).toBe("Daniel (Enhanced)");
    const g = synth([]);
    const sp2 = createSpeaker(fakeWindow({ speechSynthesis: g.s, SpeechSynthesisUtterance: FakeUtterance }));
    sp2.speak("Hello.");
    vi.advanceTimersByTime(1_000);
    expect(g.spoken[0]!.voice).toBeNull(); // the default voice after three tries
    expect(g.spoken[0]!.lang).toBe("en-GB");
  });

  it("inside a tap a line is spoken at once, even before the voice list is read; the next line gets the ranked voice", () => {
    const f = synth([]);
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    sp.speak("Good evening.", { gesture: true });
    expect(f.spoken).toHaveLength(1);
    expect([f.spoken[0]!.voice, f.spoken[0]!.lang]).toEqual([null, "en-GB"]);
    f.setVoices(VOICES);
    sp.speak("Very good.", { gesture: true });
    expect(f.spoken[1]!.voice?.name).toBe("Daniel (Enhanced)");
  });

  it("muted: nothing is spoken (and speech in progress stops)", () => {
    const f = synth();
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    sp.setPrefs({ voiceURI: null, rate: 1, muted: true });
    expect(f.s.cancel).toHaveBeenCalled();
    sp.speak("Hello.");
    expect(f.spoken).toHaveLength(0);
  });

  it("listening cancels speech, routes audio back to auto, and nothing is spoken until it ends", () => {
    const f = synth();
    const session = { type: "auto" };
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance, navigator: { language: "en-GB", audioSession: session } }), { audioSession: true });
    sp.speak("Hello.");
    expect(session.type).toBe("playback");
    sp.setListening(true);
    expect(f.s.cancel).toHaveBeenCalled();
    expect(session.type).toBe("auto");
    sp.speak("Not now.");
    expect(f.spoken.map((u) => u.text)).toEqual(["Hello."]);
    sp.setListening(false);
    sp.speak("Now.");
    expect(f.spoken.map((u) => u.text)).toEqual(["Hello.", "Now."]);
  });

  it("leaves the audio session alone when routing is off or the API is missing", () => {
    const f = synth();
    const session = { type: "auto" };
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance, navigator: { language: "en-GB", audioSession: session } }), { audioSession: false });
    sp.speak("Hello.");
    expect(session.type).toBe("auto");
    const sp2 = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }), { audioSession: true });
    expect(() => sp2.speak("Hello.")).not.toThrow();
  });

  it("a new line cancels the one speaking (barge-in); cancel() stops it", () => {
    const f = synth();
    const sp = createSpeaker(fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance }));
    sp.speak("One.");
    sp.speak("Two.");
    expect(f.s.cancel).toHaveBeenCalledTimes(2);
    sp.cancel();
    expect(f.s.cancel).toHaveBeenCalledTimes(3);
  });

  it("hidden: speech stops, and a reply that arrives meanwhile is spoken when the page shows again (the newest only)", () => {
    const f = synth();
    const win = fakeWindow({ speechSynthesis: f.s, SpeechSynthesisUtterance: FakeUtterance });
    const sp = createSpeaker(win);
    win.hide(true);
    expect(f.s.cancel).toHaveBeenCalled();
    sp.speak("Old.");
    sp.speak("Newest.");
    expect(f.spoken).toHaveLength(0);
    win.hide(false);
    expect(f.spoken.map((u) => u.text)).toEqual(["Newest."]);
  });
});
