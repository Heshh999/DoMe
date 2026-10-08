/**
 * VolumeSlider wiring over the throttle: the first value goes out immediately, at most one value per
 * 250 ms while dragging, the release value is flushed on pointerup/pointercancel/blur, and the
 * emitter survives a re-render with a new onChange (the latest callback receives pending values).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VolumeSlider } from "../../src/components/VolumeSlider.tsx";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function setValue(v: number) {
  fireEvent.change(screen.getByRole("slider"), { target: { value: String(v) } });
}

describe("VolumeSlider", () => {
  it("emits the first value at once, then at most one per 250 ms, plus the release value", () => {
    const onChange = vi.fn();
    render(<VolumeSlider label="PC volume" scopeHint="Windows" value={20} muted={false} disabled={false} onChange={onChange} />);
    setValue(30);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(30);
    vi.advanceTimersByTime(50);
    setValue(40);
    vi.advanceTimersByTime(50);
    setValue(50);
    expect(onChange).toHaveBeenCalledTimes(1); // coalesced
    vi.advanceTimersByTime(150); // 250 ms since the first emit
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith(50); // the latest, not an intermediate
    vi.advanceTimersByTime(20);
    setValue(60);
    expect(screen.getByText("60%")).toBeInTheDocument(); // while dragging: the finger position
    fireEvent.pointerUp(screen.getByRole("slider"));
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(onChange).toHaveBeenLastCalledWith(60);
    vi.advanceTimersByTime(1000);
    expect(onChange).toHaveBeenCalledTimes(3); // nothing left pending
    expect(screen.getByText("20%")).toBeInTheDocument(); // released: follows the PC's reported value until it reports the change
  });

  it("over a long drag the number of commands never exceeds one per 250 ms plus the final one", () => {
    const onChange = vi.fn();
    render(<VolumeSlider label="PC volume" scopeHint="Windows" value={0} muted={false} disabled={false} onChange={onChange} />);
    // 100 input events over 1 s (every 10 ms)
    for (let i = 1; i <= 100; i++) {
      setValue(i);
      vi.advanceTimersByTime(10);
    }
    fireEvent.pointerUp(screen.getByRole("slider"));
    expect(onChange.mock.calls.length).toBeLessThanOrEqual(1000 / 250 + 1 + 1);
    expect(onChange).toHaveBeenLastCalledWith(100);
  });

  it("pointercancel and blur also flush the pending value; a release with nothing pending sends nothing", () => {
    const onChange = vi.fn();
    render(<VolumeSlider label="PC volume" scopeHint="Windows" value={20} muted={false} disabled={false} onChange={onChange} />);
    setValue(30); // immediate
    setValue(35); // pending
    fireEvent.pointerCancel(screen.getByRole("slider"));
    expect(onChange.mock.calls.map((c) => c[0])).toEqual([30, 35]);
    fireEvent.pointerUp(screen.getByRole("slider"));
    expect(onChange).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(300);
    setValue(40);
    setValue(45);
    fireEvent.blur(screen.getByRole("slider"));
    expect(onChange.mock.calls.map((c) => c[0])).toEqual([30, 35, 40, 45]);
  });

  it("a re-render with a new onChange mid-drag keeps the emitter and delivers the pending value to the latest callback", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<VolumeSlider label="PC volume" scopeHint="Windows" value={20} muted={false} disabled={false} onChange={first} />);
    setValue(30);
    setValue(70); // pending inside the 250 ms window
    rerender(<VolumeSlider label="PC volume" scopeHint="Windows" value={30} muted={false} disabled={false} onChange={second} />);
    vi.advanceTimersByTime(250);
    expect(first.mock.calls.map((c) => c[0])).toEqual([30]);
    expect(second.mock.calls.map((c) => c[0])).toEqual([70]); // not dropped, not duplicated
    // still dragging: the slider shows the finger position, not the PC's older value
    expect(screen.getByText("70%")).toBeInTheDocument();
  });

  it("follows the PC's reported value when not dragging and shows '—' when unknown", () => {
    const { rerender } = render(<VolumeSlider label="PC volume" scopeHint="Windows" value={null} muted={null} disabled onChange={() => undefined} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByRole("slider")).toBeDisabled();
    rerender(<VolumeSlider label="PC volume" scopeHint="Windows" value={55} muted={true} disabled={false} onChange={() => undefined} />);
    expect(screen.getByText("55% · muted")).toBeInTheDocument();
  });
});
