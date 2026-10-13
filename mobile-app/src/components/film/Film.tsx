/**
 * The DoMe demo film: a web animation of the product (SVG + CSS + the design tokens, no video files, no
 * external assets), about a minute long, muted and captioned. See timeline.ts for the scenes and script.ts
 * for every beat.
 *
 * - One clock (useFilmClock) drives the whole stage; every frame is a function of the time, so stills and
 *   jumps are exact.
 * - Controls: play/pause, scene chips, a thin progress bar, replay; all keyboard operable (the frame itself
 *   takes Space, the arrow keys, Home and 1–6). Captions are a polite aria-live region inside the stage.
 * - Autoplay only while at least half of the frame is on screen (85 % in the vertical layout, so its caption band
 *   is in view too); paused while the page is hidden. Before the first play the frame shows scene 1's still as
 *   its poster. The caption region stays silent until the visitor starts the film or picks a scene.
 * - `prefers-reduced-motion`: a storyboard of still frames with Previous / Next instead of animation.
 * - `exportMode`: no controls or chrome, and `window.__domeFilm` (seek / play / pause) for the export script.
 */
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import "../../styles/film.css";
import { Icon } from "../icons.tsx";
import { endCardRows, layoutFor, VERTICAL_BELOW_PX, type FilmLayout } from "./layout.ts";
import { CAPTION_ID, FilmLink, Stage } from "./Stage.tsx";
import { clamp, SCENES, sceneAt, sceneByNumber, TOTAL_MS } from "./timeline.ts";
import { useFilmClock } from "./useFilmClock.ts";

export interface FilmProps {
  /** Start playing once at least half of the frame is on screen (and resume when it comes back). */
  autoplay?: boolean;
  /** The film clock's starting point in ms (a scene's `still` for QA). */
  initialTime?: number;
  /** Force "wide" (16:9) or "vertical" (9:16); otherwise the frame restacks below VERTICAL_BELOW_PX. */
  layout?: FilmLayout;
  /** The export's chrome-less, deterministic mode (see scripts/export-film.mjs). */
  exportMode?: boolean;
  /** Overrides the device's reduced-motion preference (tests and QA). */
  reducedMotion?: boolean;
  /** An extra class on the root. */
  className?: string;
}

/** What the export script drives (installed on window only in exportMode). */
export interface FilmHandle {
  total: number;
  scenes: Array<{ n: number; title: string; start: number; end: number; still: number }>;
  seek(ms: number): void;
  play(): void;
  pause(): void;
  /** The time currently rendered. */
  time(): number;
}

declare global {
  interface Window {
    __domeFilm?: FilmHandle;
  }
}

function usePrefersReducedMotion(override: boolean | undefined): boolean {
  const [reduced, setReduced] = useState(() => {
    if (override !== undefined) return override;
    try {
      return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    if (override !== undefined || typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    mq.addEventListener?.("change", update);
    return () => mq.removeEventListener?.("change", update);
  }, [override]);
  return override ?? reduced;
}

/** "wide" or "vertical" from the root's measured width (unless forced), and that width (null until measured). */
function useResponsiveLayout(forced: FilmLayout | undefined, root: React.RefObject<HTMLDivElement | null>): { layout: FilmLayout; width: number | null } {
  const [width, setWidth] = useState<number | null>(null);
  const [measured, setMeasured] = useState<FilmLayout>(() => (typeof window !== "undefined" && window.innerWidth < VERTICAL_BELOW_PX ? "vertical" : "wide"));
  useEffect(() => {
    const el = root.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? el.clientWidth;
      setWidth(w);
      setMeasured(w < VERTICAL_BELOW_PX ? "vertical" : "wide");
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [root]);
  return { layout: forced ?? measured, width };
}

function mmss(ms: number): string {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* ── the storyboard (reduced motion) ─────────────────────────────────────────────────────────────────── */

function Storyboard({ layout, initial, onAnimate }: { layout: FilmLayout; initial: number; onAnimate: () => void }) {
  const [i, setI] = useState(() => clamp(SCENES.findIndex((s) => s.n === sceneAt(initial).n), 0, SCENES.length - 1));
  const scene = SCENES[i]!;
  return (
    <div className="film-storyboard">
      <div className="film-frame" data-layout={layout} data-testid="film-frame" role="group" aria-label={`Storyboard, scene ${scene.n} of ${SCENES.length}: ${scene.title}`} aria-describedby={CAPTION_ID}>
        <Stage t={scene.still} layout={layoutFor(layout)} endActions="links" />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" className="film-button" onClick={() => setI((k) => Math.max(0, k - 1))} disabled={i === 0} aria-label="Previous scene">
          <Icon name="chevron" size={18} className="rotate-180" />
          Previous
        </button>
        <button type="button" className="film-button" onClick={() => setI((k) => Math.min(SCENES.length - 1, k + 1))} disabled={i === SCENES.length - 1} aria-label="Next scene">
          Next
          <Icon name="chevron" size={18} />
        </button>
        <p className="ml-1 text-sm text-text-muted">
          Scene {scene.n} of {SCENES.length} · {scene.title}
        </p>
        <button type="button" className="film-button ml-auto" onClick={onAnimate}>
          <Icon name="play" size={16} />
          Play the animation
        </button>
      </div>
      <SceneChips current={scene.n} onPick={(n) => setI(SCENES.findIndex((s) => s.n === n))} />
    </div>
  );
}

/** Scrolls the chip row (never the page) so `chip` sits in the middle of it. */
function centreChip(row: HTMLElement, chip: HTMLElement, smooth: boolean) {
  if (row.scrollWidth <= row.clientWidth) return;
  const left = chip.offsetLeft - row.offsetLeft - (row.clientWidth - chip.offsetWidth) / 2;
  const to = Math.max(0, Math.min(left, row.scrollWidth - row.clientWidth));
  if (typeof row.scrollTo === "function") row.scrollTo({ left: to, behavior: smooth ? "smooth" : "auto" });
  else row.scrollLeft = to;
}

function prefersReduced(): boolean {
  try {
    return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function SceneChips({ current, onPick }: { current: number; onPick: (n: number) => void }) {
  const row = useRef<HTMLUListElement>(null);
  // the row follows the film: the current scene's chip is always in view on a phone
  useEffect(() => {
    const ul = row.current;
    const chip = ul?.querySelector<HTMLElement>(`[data-scene="${current}"]`);
    if (ul && chip) centreChip(ul, chip, !prefersReduced());
  }, [current]);
  return (
    <ul ref={row} className="film-chips no-scrollbar mt-3 flex gap-2 overflow-x-auto py-0.5" aria-label="Scenes">
      {SCENES.map((s) => (
        <li key={s.n} className="shrink-0" data-scene={s.n}>
          <button type="button" className="film-chip" aria-current={s.n === current ? "true" : undefined} aria-label={`Scene ${s.n}: ${s.title}`} onClick={() => onPick(s.n)} onFocus={(e) => row.current && centreChip(row.current, e.currentTarget.parentElement as HTMLElement, false)}>
            <span className="film-chip-n" aria-hidden="true">
              {s.n}
            </span>
            {s.title}
          </button>
        </li>
      ))}
    </ul>
  );
}

/* ── the animated film ───────────────────────────────────────────────────────────────────────────────── */

function Animated({ layout, initialTime, autoplay, exportMode }: { layout: FilmLayout; initialTime: number; autoplay: boolean; exportMode: boolean }) {
  const clock = useFilmClock(TOTAL_MS, initialTime, false);
  const frameRef = useRef<HTMLDivElement>(null);
  const { play, pause, seek, toggle, replay, t, playing, ended } = clock;
  // whether the person pressed pause themselves: then visibility never resumes the film
  const userPaused = useRef(false);
  const inView = useRef(false);
  const wantsAutoplay = autoplay && !exportMode;
  // the caption region speaks only once the visitor has started the film, picked a scene or used the keyboard
  const [engaged, setEngaged] = useState(false);
  const engage = useCallback(() => setEngaged(true), []);

  // autoplay only while in view (in the vertical layout, with its caption band in view too); pause while hidden
  useEffect(() => {
    const el = frameRef.current;
    if (!wantsAutoplay || !el || typeof IntersectionObserver === "undefined") return;
    const need = layout === "vertical" ? 0.85 : 0.5;
    const io = new IntersectionObserver(
      (entries) => {
        const visible = (entries[0]?.intersectionRatio ?? 0) >= need;
        inView.current = visible;
        if (visible && !userPaused.current && document.visibilityState !== "hidden") play();
        else if (!visible) pause();
      },
      { threshold: [0, 0.5, 0.85, 1] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [wantsAutoplay, layout, play, pause]);
  useEffect(() => {
    if (exportMode) return;
    const onVisibility = () => {
      if (document.visibilityState === "hidden") pause();
      else if (wantsAutoplay && inView.current && !userPaused.current) play();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [exportMode, wantsAutoplay, play, pause]);

  // the export script's handle
  useEffect(() => {
    if (!exportMode) return;
    const handle: FilmHandle = {
      total: TOTAL_MS,
      scenes: SCENES.map((s) => ({ n: s.n, title: s.title, start: s.start, end: s.end, still: s.still })),
      seek,
      play,
      pause,
      time: () => Number(frameRef.current?.querySelector("[data-film-time]")?.getAttribute("data-film-time") ?? 0),
    };
    window.__domeFilm = handle;
    return () => {
      if (window.__domeFilm === handle) delete window.__domeFilm;
    };
  }, [exportMode, seek, play, pause]);

  const onToggle = useCallback(() => {
    userPaused.current = playing;
    setEngaged(true);
    toggle();
  }, [playing, toggle]);
  const jump = useCallback(
    (n: number) => {
      setEngaged(true);
      seek(sceneByNumber(n).start);
    },
    [seek],
  );
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === " " || e.key === "Enter" || e.key.toLowerCase() === "k") {
      e.preventDefault();
      onToggle();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      engage();
      seek(t + 5_000);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      engage();
      seek(t - 5_000);
    } else if (e.key === "Home") {
      e.preventDefault();
      engage();
      seek(0);
    } else if (/^[1-6]$/.test(e.key)) {
      e.preventDefault();
      jump(Number(e.key));
    }
  };

  const scene = sceneAt(t);
  const L = layoutFor(layout);
  // before the first play the frame shows scene 1's lit room and phone as its poster (the clock still reads 0)
  const poster = !exportMode && !playing && t === 0;
  return (
    <div data-testid="film">
      <div ref={frameRef} className="film-frame" data-layout={layout} data-testid="film-frame" role="group" aria-label="DoMe demo film" aria-describedby={CAPTION_ID} tabIndex={exportMode ? -1 : 0} onKeyDown={exportMode ? undefined : onKey} onClick={exportMode ? undefined : (e) => (e.target === e.currentTarget || (e.target as HTMLElement).closest(".film-canvas")) && onToggle()}>
        <Stage t={poster ? SCENES[0]!.still : t} clock={t} layout={L} endActions={exportMode ? "drawn" : ended ? "hidden" : "links"} announce={exportMode || engaged} />
        {poster ? (
          <button type="button" className="film-start dome-press" aria-label="Play the film" data-testid="film-start" onClick={(e) => (e.stopPropagation(), onToggle())}>
            <Icon name="play" size={34} strokeWidth={2} />
          </button>
        ) : null}
        {ended && !exportMode ? (
          // the end card's calls to action as full-size controls, next to Watch again
          <div className="film-end-actions" style={{ top: `${(endCardRows(L).actions / L.h) * 100}%` }} onClick={(e) => e.stopPropagation()}>
            <FilmLink to="/app" className="film-button dome-primary is-primary dome-press">
              Open the app
              <Icon name="arrow" size={16} />
            </FilmLink>
            <FilmLink to="/pricing" className="film-button dome-press">
              Compare plans
            </FilmLink>
            <button type="button" className="film-button dome-press" onClick={() => replay()}>
              <Icon name="play" size={16} />
              Watch again
            </button>
          </div>
        ) : null}
      </div>
      {exportMode ? null : (
        <div className="mt-3">
          <input type="range" className="film-progress" min={0} max={TOTAL_MS} step={100} value={Math.round(t)} aria-label="Film position" aria-valuetext={`${mmss(t)} of ${mmss(TOTAL_MS)}, scene ${scene.n}: ${scene.title}`} style={{ "--fill": `${(t / TOTAL_MS) * 100}%` } as React.CSSProperties} onChange={(e) => (engage(), seek(Number(e.currentTarget.value)))} />
          <div className="mt-1 flex items-center gap-2">
            <button type="button" className="film-button dome-press" aria-label={ended ? "Replay" : playing ? "Pause" : "Play"} onClick={ended ? replay : onToggle} data-testid="film-play">
              <Icon name={playing ? "pause" : "play"} size={18} />
              <span className="hidden sm:inline">{ended ? "Replay" : playing ? "Pause" : "Play"}</span>
            </button>
            <span className="text-sm tabular-nums text-text-muted" aria-hidden="true">
              {mmss(t)} / {mmss(TOTAL_MS)}
            </span>
            <span className="ml-auto text-sm text-text-muted" aria-hidden="true">
              <span className="sm:hidden">
                Scene {scene.n} of {SCENES.length}
              </span>
              <span className="hidden sm:inline">
                Scene {scene.n} · {scene.title}
              </span>
            </span>
          </div>
          <SceneChips current={scene.n} onPick={jump} />
        </div>
      )}
    </div>
  );
}

export function Film({ autoplay = false, initialTime = 0, layout: forced, exportMode = false, reducedMotion, className = "" }: FilmProps) {
  const root = useRef<HTMLDivElement>(null);
  const { layout, width } = useResponsiveLayout(forced, root);
  const prefersStills = usePrefersReducedMotion(reducedMotion);
  const [animateAnyway, setAnimateAnyway] = useState(false);
  const stills = prefersStills && !animateAnyway && !exportMode;
  // the canvas scale: measured when possible (film.css has a CSS-only fallback for the first paint)
  const style = width ? ({ "--film-scale": width / layoutFor(layout).w } as React.CSSProperties) : undefined;
  return (
    <div ref={root} className={`film-root ${className}`} data-layout={layout} style={style}>
      {stills ? <Storyboard layout={layout} initial={initialTime} onAnimate={() => setAnimateAnyway(true)} /> : <Animated layout={layout} initialTime={initialTime} autoplay={autoplay || animateAnyway} exportMode={exportMode} />}
    </div>
  );
}
