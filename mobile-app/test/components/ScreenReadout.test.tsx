/**
 * The test-build readout over the Screen view stage (device round 2, 2026-10-27): shown only in the dev, test, CI and
 * staging builds (an allow-list: unset, unknown and production channels render nothing), expanded by default, even
 * without a stream (the case it exists for); the gate checklist leads; it refreshes once a second from the session
 * client; "Hide readout" is remembered on the phone (and survives storage that throws); taps pass through it except on
 * its buttons and it sits under the page's overlays; Copy writes the text to the clipboard, and a refused copy opens a
 * small box with the text to select, the readout itself still letting taps through. Protocol 1.5 adds three lines at
 * the end: the pointer, the screens and the zoom (design screen-view-2a §4.11).
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ScreenReadout, type ScreenReadoutSource } from "../../src/components/ScreenReadout.tsx";
import { EMPTY_SCREEN_STATE, type ScreenSessionState } from "../../src/lib/screen.ts";
import { EMPTY_POINTER_FACTS, EMPTY_SURFACE_FACTS, EMPTY_ZOOM_FACTS, emptyTimeline, type ScreenClientFacts } from "../../src/lib/screenStats.ts";

const KEY = "dome.screen.readout.hidden";

function facts(over: Partial<ScreenClientFacts> = {}): ScreenClientFacts {
  return { stats: null, media: null, timeline: emptyTimeline(), channel: { open: false, hb_sent: 0, received: 0, dropped: 0 }, transport_connected: false, codec_fallback: null, ...over };
}

function source(initial: ScreenClientFacts = facts()): ScreenReadoutSource & { reads: number; next: ScreenClientFacts } {
  const s = {
    reads: 0,
    next: initial,
    get facts() {
      return s.next;
    },
    readFacts: async () => {
      s.reads += 1;
      return s.next;
    },
  };
  return s;
}

const CONNECTING: ScreenSessionState = { ...EMPTY_SCREEN_STATE, phase: "connecting", verified: true };

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_DOME_RELEASE_CHANNEL", "staging");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("ScreenReadout", () => {
  it("renders on a non-production build, expanded, with the gate lines even while no stream exists; taps pass through except on its buttons", async () => {
    const src = source();
    render(<ScreenReadout source={src} state={CONNECTING} />);
    const panel = screen.getByTestId("screen-readout");
    expect(panel).toHaveClass("pointer-events-none", "absolute", "left-2", "top-2", "z-[5]"); // under the z-10 / z-20 overlays
    const text = screen.getByTestId("screen-readout-text");
    for (const line of ["verified: yes", "track seen: no", "stream set: no", "DTLS: no", "data channel: no, hb 0, in 0", "first RTP: no", "frames decoded: -", "play(): -", "rVFC ticks: -", "phase: connecting"]) expect(text).toHaveTextContent(line);
    expect(text.textContent!.indexOf("verified:")).toBeLessThan(text.textContent!.indexOf("rtp pkts"));
    expect(screen.getByRole("button", { name: "Hide readout" })).toHaveClass("pointer-events-auto");
    expect(screen.getByRole("button", { name: "Copy" })).toHaveClass("pointer-events-auto");
    await act(async () => undefined);
    expect(src.reads).toBe(1);
  });

  it("refreshes once a second from the client", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const src = source();
    render(<ScreenReadout source={src} state={CONNECTING} />);
    await act(async () => undefined);
    expect(src.reads).toBe(1);
    src.next = facts({ timeline: { ...emptyTimeline(), dtls_connected: 1300 }, channel: { open: true, hb_sent: 3, received: 1, dropped: 0 } });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(src.reads).toBe(2);
    expect(screen.getByTestId("screen-readout-text")).toHaveTextContent("DTLS: yes +1300 ms");
    expect(screen.getByTestId("screen-readout-text")).toHaveTextContent("data channel: open, hb 3, in 1");
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(src.reads).toBe(5);
  });

  it("is absent from production builds, and from any build whose channel is unset or not on the list", () => {
    for (const channel of ["production", "", "beta", "prod", "Staging"]) {
      vi.stubEnv("VITE_DOME_RELEASE_CHANNEL", channel);
      const src = source();
      const view = render(<ScreenReadout source={src} state={CONNECTING} />);
      expect(screen.queryByTestId("screen-readout"), channel).toBeNull();
      expect(src.reads).toBe(0);
      view.unmount();
    }
    vi.stubEnv("VITE_DOME_RELEASE_CHANNEL", undefined); // a plain `vite build`
    expect(import.meta.env.VITE_DOME_RELEASE_CHANNEL).toBeUndefined();
    render(<ScreenReadout source={source()} state={CONNECTING} />);
    expect(screen.queryByTestId("screen-readout")).toBeNull();
  });

  it("is shown in the dev, test-kit, CI and staging builds", () => {
    for (const channel of ["dev", "test", "ci", "staging"]) {
      vi.stubEnv("VITE_DOME_RELEASE_CHANNEL", channel);
      const view = render(<ScreenReadout source={source()} state={CONNECTING} />);
      expect(screen.getByTestId("screen-readout"), channel).toBeInTheDocument();
      view.unmount();
    }
  });

  it("'Hide readout' is remembered on this phone; 'Readout' shows it again; a hidden readout reads nothing", async () => {
    const src = source();
    const view = render(<ScreenReadout source={src} state={CONNECTING} />);
    await userEvent.click(screen.getByRole("button", { name: "Hide readout" }));
    expect(screen.queryByTestId("screen-readout-text")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    expect(localStorage.getItem(KEY)).toBe("1");
    view.unmount();
    const reads = src.reads;
    render(<ScreenReadout source={src} state={CONNECTING} />);
    expect(screen.getByRole("button", { name: "Readout" })).toHaveAttribute("aria-expanded", "false");
    await act(async () => undefined);
    expect(src.reads).toBe(reads);
    await userEvent.click(screen.getByRole("button", { name: "Readout" }));
    expect(screen.getByTestId("screen-readout-text")).toBeInTheDocument();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("storage that throws (private mode, blocked site data) still shows the readout and lets it be hidden", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "QuotaExceededError");
    });
    render(<ScreenReadout source={source()} state={CONNECTING} />);
    expect(screen.getByTestId("screen-readout-text")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide readout" }));
    expect(screen.queryByTestId("screen-readout-text")).toBeNull();
  });

  it("Copy writes the readout's text to the clipboard", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<ScreenReadout source={source()} state={CONNECTING} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = (writeText.mock.calls[0] as unknown as [string])[0];
    expect(copied).toBe(screen.getByTestId("screen-readout-text").textContent);
    expect(copied.split("\n")[0]).toBe("verified: yes");
    expect(await screen.findByRole("status")).toHaveTextContent("Copied");
  });

  it("a refused copy opens a small box with the text to select; the readout itself keeps letting taps through; Done closes it", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: () => Promise.reject(new DOMException("no", "NotAllowedError")) } });
    const view = render(<ScreenReadout source={source()} state={CONNECTING} />);
    expect(screen.queryByTestId("screen-readout-select")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Copy failed: press and hold the text below to select it");
    expect(screen.getByTestId("screen-readout-text")).not.toHaveClass("pointer-events-auto"); // taps on the picture still work
    expect(screen.getByTestId("screen-readout")).toHaveClass("pointer-events-none");
    const box = screen.getByTestId("screen-readout-select");
    expect(box).toHaveClass("pointer-events-auto", "select-text", "h-20");
    expect(box).toHaveAttribute("readonly");
    expect((box as HTMLTextAreaElement).value).toBe(screen.getByTestId("screen-readout-text").textContent);
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByTestId("screen-readout-select")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    view.unmount();
    // no clipboard at all (an insecure page): the same line
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    render(<ScreenReadout source={source()} state={CONNECTING} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Copy failed");
  });

  it("ends with the three 1.5 lines: the pointer, the screens, the zoom (numbers and short words only)", async () => {
    const src = source(
      facts({
        pointer: { ...EMPTY_POINTER_FACTS, mode: "overlay", shapes: 3, custom: 1, rejected: 0, positions: 412, unreliable: 400, droppedGen: 2, lastKind: "ibeam", predErrMaxPx: 9 },
        surfaces: { count: 2, showing: 2, switches: 1, switchFailures: 0, gone: 0 },
        zoom: { maxScale: 2.64, followPans: 83 },
      }),
    );
    render(<ScreenReadout source={src} state={CONNECTING} />);
    await act(async () => undefined);
    const lines = screen.getByTestId("screen-readout-text").textContent!.split("\n");
    expect(lines.slice(-3)).toEqual(["pointer: overlay · shapes 3 custom 1 rejected 0 · pos 412 old-gen 2 · pred err max 9 px", "screens: 2 · showing 2 · switches 1 failed 0 · gone 0", "zoom: max 2.6x · follow pans 83"]);
  });

  it("the pointer line says when the pointer is in the picture or the PC speaks 1.4; unknown numbers read '-'", async () => {
    const src = source(facts({ pointer: { ...EMPTY_POINTER_FACTS, mode: "drawn" }, surfaces: EMPTY_SURFACE_FACTS, zoom: EMPTY_ZOOM_FACTS }));
    const view = render(<ScreenReadout source={src} state={CONNECTING} />);
    await act(async () => undefined);
    let text = screen.getByTestId("screen-readout-text");
    expect(text).toHaveTextContent("pointer: in picture · shapes 0 custom 0 rejected 0 · pos 0 old-gen 0 · pred err max -");
    expect(text).toHaveTextContent("screens: 0 · showing - · switches 0 failed 0 · gone 0");
    expect(text).toHaveTextContent("zoom: max 1.0x · follow pans 0");
    view.unmount();
    // facts without the 1.5 groups (a client that never started one): the same lines with their defaults
    render(<ScreenReadout source={source()} state={CONNECTING} />);
    await act(async () => undefined);
    text = screen.getByTestId("screen-readout-text");
    const lines = text.textContent!.split("\n");
    expect(lines.slice(-3)).toEqual(["pointer: none (PC speaks 1.4) · shapes 0 custom 0 rejected 0 · pos 0 old-gen 0 · pred err max -", "screens: 0 · showing - · switches 0 failed 0 · gone 0", "zoom: max 1.0x · follow pans 0"]);
    expect(text.textContent).not.toMatch(/png/i);
  });
});
