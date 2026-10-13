/**
 * The film's one clock. Time advances with requestAnimationFrame while playing, stops at `total`, and can be
 * moved anywhere with `seek` (scene chips, the progress bar, the export's frame stepping). Everything on the
 * stage is drawn from this number alone (script.ts), so pausing, jumping and stepping are exact.
 *
 * The clock itself is a small closure-based engine created once per mount; React only mirrors its time and
 * its playing flag into state, so nothing reads or writes a ref during render.
 */
import { useEffect, useState } from "react";

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

type Engine = Pick<FilmClock, "play" | "pause" | "toggle" | "seek" | "replay">;

const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 16) as unknown as number;
const caf = typeof cancelAnimationFrame === "function" ? cancelAnimationFrame : (id: number) => clearTimeout(id);
const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

function createEngine(total: number, initial: number, setT: (ms: number) => void, setPlaying: (on: boolean) => void): Engine {
  const clamp = (ms: number) => Math.min(Math.max(ms, 0), total);
  let t = clamp(initial);
  // where the running stretch started: film time at wall time
  let origin: { t: number; wall: number } | null = null;
  let frame: number | null = null;

  const render = (ms: number) => {
    t = ms;
    setT(ms);
  };
  const stop = () => {
    if (frame !== null) caf(frame);
    frame = null;
    origin = null;
    setPlaying(false);
  };
  const tick = () => {
    if (!origin) return;
    const next = origin.t + (now() - origin.wall);
    if (next >= total) {
      render(total);
      stop();
      return;
    }
    render(next);
    frame = raf(tick);
  };
  const play = () => {
    if (origin) return;
    const from = t >= total ? 0 : t;
    origin = { t: from, wall: now() };
    render(from);
    setPlaying(true);
    frame = raf(tick);
  };
  const seek = (ms: number) => {
    const c = clamp(ms);
    render(c);
    if (origin) origin = { t: c, wall: now() };
  };
  return {
    play,
    pause: stop,
    seek,
    toggle: () => (origin ? stop() : play()),
    replay: () => {
      stop();
      render(0);
      play();
    },
  };
}

export function useFilmClock(total: number, initial = 0, autoplay = false): FilmClock {
  const [t, setT] = useState(() => Math.min(Math.max(initial, 0), total));
  const [playing, setPlaying] = useState(false);
  // the engine lives for the component's whole life (total is a constant of the film)
  const [engine] = useState(() => createEngine(total, initial, setT, setPlaying));

  useEffect(() => {
    if (autoplay) engine.play();
    return engine.pause;
  }, [engine, autoplay]);

  return { t, playing, ended: !playing && t >= total, play: engine.play, pause: engine.pause, toggle: engine.toggle, seek: engine.seek, replay: engine.replay };
}
