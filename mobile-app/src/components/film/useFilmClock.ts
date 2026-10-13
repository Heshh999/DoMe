/**
 * The film's one clock. Time advances with requestAnimationFrame while playing, stops at `total`, and can be
 * moved anywhere with `seek` (scene chips, the progress bar, the export's frame stepping). Everything on the
 * stage is drawn from this number alone (script.ts), so pausing, jumping and stepping are exact.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface FilmClock {
  /** Milliseconds on the film clock, 0..total. */
  t: number;
  playing: boolean;
  /** The clock reached `total` (and stopped there). */
  ended: boolean;
  play(): void;
  pause(): void;
  toggle(): void;
  seek(ms: number): void;
  /** Back to 0 and play. */
  replay(): void;
}

const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 16) as unknown as number;
const caf = typeof cancelAnimationFrame === "function" ? cancelAnimationFrame : (id: number) => clearTimeout(id);
const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

export function useFilmClock(total: number, initial = 0, autoplay = false): FilmClock {
  const [t, setT] = useState(() => Math.min(Math.max(initial, 0), total));
  const [playing, setPlaying] = useState(false);
  // where the running stretch started: film time at wall time
  const origin = useRef<{ t: number; wall: number } | null>(null);
  const tRef = useRef(t);
  tRef.current = t;
  const frame = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (frame.current !== null) caf(frame.current);
    frame.current = null;
    origin.current = null;
    setPlaying(false);
  }, []);

  const tick = useCallback(() => {
    const o = origin.current;
    if (!o) return;
    const next = o.t + (now() - o.wall);
    if (next >= total) {
      setT(total);
      stop();
      return;
    }
    setT(next);
    frame.current = raf(tick);
  }, [total, stop]);

  const play = useCallback(() => {
    if (origin.current) return;
    const from = tRef.current >= total ? 0 : tRef.current;
    origin.current = { t: from, wall: now() };
    setT(from);
    setPlaying(true);
    frame.current = raf(tick);
  }, [tick, total]);

  const seek = useCallback(
    (ms: number) => {
      const clamped = Math.min(Math.max(ms, 0), total);
      setT(clamped);
      if (origin.current) origin.current = { t: clamped, wall: now() };
    },
    [total],
  );

  const toggle = useCallback(() => {
    if (origin.current) stop();
    else play();
  }, [play, stop]);

  const replay = useCallback(() => {
    stop();
    setT(0);
    tRef.current = 0;
    play();
  }, [play, stop]);

  useEffect(() => {
    if (autoplay) play();
    return stop;
    // the first mount only: later changes are the page's own business
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { t, playing, ended: !playing && t >= total, play, pause: stop, toggle, seek, replay };
}
