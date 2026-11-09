/**
 * Keeping an open app current (DECISIONS 92, lib/appUpdate.ts + store/busy.ts). The check schedule: the start check,
 * a return to the front (visibilitychange to visible, pageshow from the back-forward cache) at most once a minute, every
 * 30 minutes while in front and never while hidden. A newer version that took control reloads the page at once when
 * nothing is busy; while Screen view, the touchpad, pairing, a payment return, a PC confirmation or the Assistant is
 * busy it waits (the notice's state names what for), and reloads once the busy state has ended for a moment. "Update
 * now" reloads at once. Fake timers and a fake registration only; nothing touches a network.
 */
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { AFTER_BUSY_DELAY_MS, AppUpdater, FOREGROUND_CHECK_GAP_MS, UPDATE_COPY, VISIBLE_CHECK_INTERVAL_MS, type AppUpdateView } from "../src/lib/appUpdate.ts";
import type { CommandRecord } from "../src/lib/commands.ts";
import { EMPTY_INPUT_STATE } from "../src/lib/input.ts";
import { clearLogs, recentLogs } from "../src/lib/log.ts";
import { EMPTY_SCREEN_STATE } from "../src/lib/screen.ts";
import { useAssistantStore, type ChatItem } from "../src/store/assistant.ts";
import { BUSY_ORDER, busyKinds, currentBusy, holdBusy, releaseBusy, resetBusyForTests, subscribeBusy, type BusyKind } from "../src/store/busy.ts";
import { useInputStore } from "../src/store/input.ts";
import { useLiveStore } from "../src/store/live.ts";
import { useScreenStore } from "../src/store/screen.ts";
import { FakeTimers } from "./helpers/fakeTimers.ts";

/** A document and a window that only carry events and a visibility state. */
class FakeDoc extends EventTarget {
  visibilityState: DocumentVisibilityState = "visible";
  show(state: DocumentVisibilityState): void {
    this.visibilityState = state;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}
class FakeWin extends EventTarget {
  pageshow(persisted: boolean): void {
    const e = new Event("pageshow");
    Object.defineProperty(e, "persisted", { value: persisted });
    this.dispatchEvent(e);
  }
}

let timers: FakeTimers;
let doc: FakeDoc;
let win: FakeWin;
let check: Mock<() => Promise<void>>;
let reload: Mock<() => void>;
let views: AppUpdateView[];
let updater: AppUpdater;

function makeUpdater(): AppUpdater {
  return new AppUpdater({
    check,
    reload,
    busy: currentBusy,
    subscribeBusy,
    onChange: (v) => views.push(v),
    now: () => timers.now,
    timers,
    doc: doc as unknown as Document,
    win: win as unknown as Window,
  });
}

function idle(): void {
  resetBusyForTests();
  useScreenStore.setState({ session: EMPTY_SCREEN_STATE });
  useInputStore.setState({ session: EMPTY_INPUT_STATE });
  useLiveStore.setState({ commands: [] });
  useAssistantStore.setState({ phase: "idle", items: [] });
}

/** A PC confirmation waiting for an answer (shut down, app close): the shape `pendingConfirmation` reads. */
const PENDING = { commandId: "c-1", pcId: "pc-1", action: "power.shutdown", confirmation: { decision: null }, terminal: null } as unknown as CommandRecord;
const RUNNING_PLAN = { id: 1, kind: "plan", status: "running" } as unknown as ChatItem;

/** Each busy kind: how the app enters it, and how it ends. */
const BUSY: Record<BusyKind, { label: string; enter: () => void; leave: () => void }[]> = {
  screen: [
    { label: "a live Screen view session", enter: () => useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "live" } }), leave: () => useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "ended" } }) },
    { label: "a Screen view session starting", enter: () => useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "starting" } }), leave: () => useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "failed" } }) },
    { label: "the Screen page open", enter: () => holdBusy("screen-page", "screen"), leave: () => releaseBusy("screen-page") },
  ],
  input: [
    { label: "a live touchpad or keyboard session", enter: () => useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase: "live" } }), leave: () => useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase: "ended" } }) },
    { label: "the Touchpad page open", enter: () => holdBusy("touchpad-page", "input"), leave: () => releaseBusy("touchpad-page") },
  ],
  pairing: [{ label: "the pairing page open", enter: () => holdBusy("pair-page", "pairing"), leave: () => releaseBusy("pair-page") }],
  checkout: [{ label: "a Stripe checkout return", enter: () => holdBusy("billing-page", "checkout"), leave: () => releaseBusy("billing-page") }],
  confirmation: [{ label: "an open PC confirmation", enter: () => useLiveStore.setState({ commands: [PENDING] }), leave: () => useLiveStore.setState({ commands: [{ ...PENDING, confirmation: { ...PENDING.confirmation!, decision: "approve" } }] }) }],
  assistant: [
    { label: "the Assistant thinking", enter: () => useAssistantStore.setState({ phase: "thinking" }), leave: () => useAssistantStore.setState({ phase: "idle" }) },
    { label: "a plan running", enter: () => useAssistantStore.setState({ items: [RUNNING_PLAN] }), leave: () => useAssistantStore.setState({ items: [{ ...RUNNING_PLAN, status: "done" } as ChatItem] }) },
    { label: "the microphone listening", enter: () => holdBusy("assistant-page", "assistant"), leave: () => releaseBusy("assistant-page") },
  ],
};

beforeEach(() => {
  idle();
  timers = new FakeTimers();
  doc = new FakeDoc();
  win = new FakeWin();
  check = vi.fn(async () => undefined);
  reload = vi.fn();
  views = [];
  updater = makeUpdater();
});

afterEach(() => {
  updater.stop();
  idle();
});

describe("the check schedule", () => {
  it("checks once at start; a return to the front checks at most once a minute; pageshow from the back-forward cache counts as a return", async () => {
    updater.start();
    expect(check).not.toHaveBeenCalled(); // the start check comes from pwa.ts once the registration is known
    updater.check("start");
    expect(check).toHaveBeenCalledTimes(1);

    doc.show("hidden");
    await timers.advance(20_000);
    doc.show("visible"); // 20 s after the start check: within the minute, nothing
    expect(check).toHaveBeenCalledTimes(1);

    doc.show("hidden");
    await timers.advance(FOREGROUND_CHECK_GAP_MS - 20_000);
    doc.show("visible"); // a minute after the last check
    expect(check).toHaveBeenCalledTimes(2);
    doc.show("hidden");
    doc.show("visible"); // straight back: capped
    expect(check).toHaveBeenCalledTimes(2);

    await timers.advance(FOREGROUND_CHECK_GAP_MS);
    win.pageshow(false); // an ordinary load: not a return
    expect(check).toHaveBeenCalledTimes(2);
    win.pageshow(true); // iOS brought the page back from its back-forward cache
    expect(check).toHaveBeenCalledTimes(3);
    win.pageshow(true);
    expect(check).toHaveBeenCalledTimes(3);
    expect(recentLogs().filter((l) => l.event === "pwa.update_check").map((l) => l.fields?.reason).slice(-3)).toEqual(["start", "foreground", "foreground"]);
  });

  it("checks every 30 minutes while in front; never while hidden; the 30 minutes count again from a return", async () => {
    updater.start();
    await timers.advance(VISIBLE_CHECK_INTERVAL_MS - 1);
    expect(check).not.toHaveBeenCalled();
    await timers.advance(1);
    expect(check).toHaveBeenCalledTimes(1);
    await timers.advance(VISIBLE_CHECK_INTERVAL_MS);
    expect(check).toHaveBeenCalledTimes(2);

    doc.show("hidden");
    await timers.advance(3 * VISIBLE_CHECK_INTERVAL_MS);
    expect(check).toHaveBeenCalledTimes(2); // hidden: the ticks do nothing

    await timers.advance(VISIBLE_CHECK_INTERVAL_MS / 2);
    doc.show("visible"); // the return checks (it is more than a minute since the last one) and restarts the count
    expect(check).toHaveBeenCalledTimes(3);
    await timers.advance(VISIBLE_CHECK_INTERVAL_MS / 2);
    expect(check).toHaveBeenCalledTimes(3); // the old phase's tick is gone
    await timers.advance(VISIBLE_CHECK_INTERVAL_MS / 2);
    expect(check).toHaveBeenCalledTimes(4);
  });

  it("a start while hidden checks nothing until the app is in front; a return iOS never announced still gets the 30-minute look", async () => {
    doc.visibilityState = "hidden";
    updater.start();
    await timers.advance(2 * VISIBLE_CHECK_INTERVAL_MS);
    expect(check).not.toHaveBeenCalled();
    doc.show("visible");
    expect(check).toHaveBeenCalledTimes(1);
    // hidden, then back without a visibilitychange: the timer still runs and looks once the page reads visible
    doc.show("hidden");
    doc.visibilityState = "visible";
    await timers.advance(VISIBLE_CHECK_INTERVAL_MS);
    expect(check).toHaveBeenCalledTimes(2);
  });

  it("a failed check (offline) never throws; once a newer version waits, nothing checks any more", async () => {
    check.mockRejectedValueOnce(new TypeError("Failed to update a ServiceWorker"));
    updater.start();
    expect(() => updater.check("start")).not.toThrow();
    await Promise.resolve();
    await Promise.resolve();
    expect(recentLogs().some((l) => l.event === "pwa.update_check_failed" && l.fields?.name === "TypeError")).toBe(true);

    holdBusy("screen-page", "screen");
    updater.onNewVersion();
    check.mockClear();
    await timers.advance(VISIBLE_CHECK_INTERVAL_MS * 2);
    doc.show("hidden");
    await timers.advance(FOREGROUND_CHECK_GAP_MS);
    doc.show("visible");
    expect(check).not.toHaveBeenCalled();
  });
});

describe("a newer version that took control", () => {
  it("at a safe moment it is loaded at once, without a notice", () => {
    updater.onNewVersion();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(views.at(-1)).toEqual({ ready: true, waitingFor: null });
    updater.onNewVersion(); // a second activation changes nothing
    updater.applyNow();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  for (const kind of BUSY_ORDER) {
    for (const way of BUSY[kind]) {
      it(`waits while ${way.label} (${kind}) and is loaded when it ends`, async () => {
        way.enter();
        expect(currentBusy()).toBe(kind);
        updater.onNewVersion();
        expect(reload).not.toHaveBeenCalled();
        expect(views.at(-1)).toEqual({ ready: true, waitingFor: kind });
        await timers.advance(10 * 60_000);
        expect(reload).not.toHaveBeenCalled();

        way.leave();
        expect(currentBusy()).toBeNull();
        await timers.advance(AFTER_BUSY_DELAY_MS - 1);
        expect(reload).not.toHaveBeenCalled(); // the session's own stop messages go out first
        await timers.advance(1);
        expect(reload).toHaveBeenCalledTimes(1);
        expect(views.at(-1)).toEqual({ ready: true, waitingFor: null });
      });
    }
  }

  it("busy again within the pause: it waits again, for the new reason", async () => {
    holdBusy("pair-page", "pairing");
    updater.onNewVersion();
    releaseBusy("pair-page");
    await timers.advance(AFTER_BUSY_DELAY_MS / 2);
    useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "connecting" } });
    await timers.advance(AFTER_BUSY_DELAY_MS * 4);
    expect(reload).not.toHaveBeenCalled();
    expect(views.at(-1)).toEqual({ ready: true, waitingFor: "screen" });
    useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "ended" } });
    await timers.advance(AFTER_BUSY_DELAY_MS);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("the second signal for the same arrival (Workbox's, after the browser's controllerchange) does not cut the pause short", async () => {
    clearLogs();
    holdBusy("screen-page", "screen");
    updater.onNewVersion(); // controllerchange
    releaseBusy("screen-page");
    await timers.advance(AFTER_BUSY_DELAY_MS / 2);
    updater.onNewVersion(); // Workbox's "activated", a moment later
    expect(reload).not.toHaveBeenCalled();
    await timers.advance(AFTER_BUSY_DELAY_MS / 2 - 1);
    expect(reload).not.toHaveBeenCalled();
    await timers.advance(1);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(recentLogs().filter((l) => l.event === "pwa.update_ready")).toHaveLength(1);
  });

  it("several busy at once: the notice names the first in order, and it is loaded only when all have ended", async () => {
    useAssistantStore.setState({ phase: "working" });
    useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase: "live" } });
    expect(busyKinds()).toEqual(["input", "assistant"]);
    updater.onNewVersion();
    expect(views.at(-1)?.waitingFor).toBe("input");
    useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase: "idle" } });
    expect(views.at(-1)?.waitingFor).toBe("assistant");
    await timers.advance(AFTER_BUSY_DELAY_MS * 2);
    expect(reload).not.toHaveBeenCalled();
    useAssistantStore.setState({ phase: "idle" });
    await timers.advance(AFTER_BUSY_DELAY_MS);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("'Update now' loads it at once, busy or not; before a newer version exists it does nothing", () => {
    updater.applyNow();
    expect(reload).not.toHaveBeenCalled();
    useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "live" } });
    updater.onNewVersion();
    expect(reload).not.toHaveBeenCalled();
    updater.applyNow();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(recentLogs().some((l) => l.event === "pwa.update_applied" && l.fields?.how === "now")).toBe(true);
  });

  it("an ended Screen view, touchpad or keyboard session (failed, ended, idle) is not busy; the other phases are", () => {
    for (const phase of ["idle", "ended", "failed"] as const) {
      useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase } });
      expect(currentBusy(), phase).toBeNull();
    }
    for (const phase of ["ice", "offering", "starting", "connecting", "live", "paused"] as const) {
      useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase } });
      expect(currentBusy(), phase).toBe("screen");
    }
    useScreenStore.setState({ session: EMPTY_SCREEN_STATE });
    for (const phase of ["idle", "ended", "failed"] as const) {
      useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase } });
      expect(currentBusy(), phase).toBeNull();
    }
    for (const phase of ["starting", "live", "suspended"] as const) {
      useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase } });
      expect(currentBusy(), phase).toBe("input");
    }
  });
});

describe("the notice's copy", () => {
  it("says what the update waits for and what 'Update now' ends, in plain words, for every busy kind", () => {
    expect(UPDATE_COPY.ready).toBe("A newer DoMe is ready.");
    expect(UPDATE_COPY.when.screen).toBe("It will update when you leave Screen view.");
    for (const kind of BUSY_ORDER) {
      expect(UPDATE_COPY.when[kind]).toMatch(/^It will update when /);
      expect(UPDATE_COPY.ends[kind]).toMatch(/^Updating now /);
    }
  });
});
