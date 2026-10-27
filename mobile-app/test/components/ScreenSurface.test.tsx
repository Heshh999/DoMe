/**
 * The `<video>`'s metadata probe (device round 2, 2026-10-27): ScreenSurface registers a probe with the session client
 * that reports what the element and the stream say about themselves — ready state, size, the last play() outcome, the
 * frame-callback ticks (requestVideoFrameCallback, or `timeupdate` where it is missing), the stream's and the track's
 * flags — and unregisters it when the picture goes. Over a fake video element; nothing reads a pixel.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScreenSurface } from "../../src/components/ScreenSurface.tsx";
import type { ScreenMediaFacts } from "../../src/lib/screenStats.ts";

type Probe = (() => ScreenMediaFacts) | null;

const stream = { id: "s", active: true, getVideoTracks: () => [{ readyState: "live", muted: false, enabled: true }] } as unknown as MediaStream;

function renderSurface(onProbe: (p: Probe) => void, onFrame = vi.fn()) {
  return render(<ScreenSurface stream={stream} mode="direct" canSend={false} scrollDirection="natural" sensitivity={1} dragMode={false} geometry={() => ({ geometry: null, stale: false })} onInput={() => undefined} onViewOnlyTap={() => undefined} onFrame={onFrame} onResize={() => undefined} onPlayBlocked={() => undefined} playEpoch={0} label="Picture" onProbe={onProbe} />);
}

/** What a real element would report once frames play (jsdom's own element reports nothing useful). */
function looksPlaying(v: HTMLElement) {
  for (const [k, value] of Object.entries({ readyState: 4, networkState: 2, paused: false, videoWidth: 1280, videoHeight: 720, currentTime: 2.04 })) Object.defineProperty(v, k, { configurable: true, value });
  Object.defineProperty(v, "getVideoPlaybackQuality", { configurable: true, value: () => ({ totalVideoFrames: 61, droppedVideoFrames: 1 }) });
}

afterEach(() => {
  vi.restoreAllMocks();
  delete (HTMLVideoElement.prototype as { requestVideoFrameCallback?: unknown }).requestVideoFrameCallback;
});

describe("ScreenSurface: the <video> probe", () => {
  it("registers a probe that reports the element, play() resolved, timeupdate ticks (no rVFC in this browser) and the stream's flags; unregisters on unmount", async () => {
    HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
    let probe: Probe = null;
    const onProbe = vi.fn((p: Probe) => (probe = p));
    const onFrame = vi.fn();
    const view = renderSurface(onProbe, onFrame);
    await act(async () => undefined);
    expect(probe).not.toBeNull();
    const video = screen.getByTestId("screen-video");
    looksPlaying(video);
    fireEvent(video, new Event("timeupdate"));
    fireEvent(video, new Event("timeupdate"));
    expect(onFrame).toHaveBeenCalledTimes(2);
    const facts = probe!();
    expect(facts).toMatchObject({ ready_state: 4, network_state: 2, paused: false, width: 1280, height: 720, current_time: 2, error: null, total_frames: 61, dropped_frames: 1, play: "resolved", rvfc: false, frame_ticks: 2, stream_active: true, track_state: "live", track_muted: false, track_enabled: true });
    expect(facts.first_tick_ms).toEqual(expect.any(Number));
    view.unmount();
    expect(onProbe).toHaveBeenLastCalledWith(null);
  });

  it("a refused play() is reported by the exception's name; requestVideoFrameCallback ticks are counted where it exists", async () => {
    HTMLMediaElement.prototype.play = vi.fn(() => Promise.reject(new DOMException("needs a gesture", "NotAllowedError")));
    const callbacks: Array<() => void> = [];
    (HTMLVideoElement.prototype as { requestVideoFrameCallback?: unknown }).requestVideoFrameCallback = vi.fn((cb: () => void) => callbacks.push(cb));
    let probe: Probe = null;
    renderSurface((p) => (probe = p));
    await act(async () => undefined);
    expect(probe!()).toMatchObject({ play: "rejected:NotAllowedError", rvfc: true, frame_ticks: 0, first_tick_ms: null });
    act(() => callbacks.shift()!());
    act(() => callbacks.shift()!());
    expect(probe!()).toMatchObject({ frame_ticks: 2 });
  });

  it("a play() that throws at once is reported as such", async () => {
    HTMLMediaElement.prototype.play = vi.fn(() => {
      throw new TypeError("no");
    });
    let probe: Probe = null;
    renderSurface((p) => (probe = p));
    await act(async () => undefined);
    expect(probe!().play).toBe("threw:TypeError");
  });
});
