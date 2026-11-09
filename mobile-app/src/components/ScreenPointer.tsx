/**
 * The pointer drawn by the phone over the Screen view picture (protocol 1.5, rules.screen_cursor; design screen-view-2a
 * §4.4, D17). The PC leaves its pointer out of the picture and reports where it is and what it looks like; this
 * overlay draws it where `PointerModel` says, so it moves the moment the finger does and the picture catches up.
 *
 * - Rendered by `ScreenSurface` as a sibling AFTER the zoom/pan layer, never inside it: a pinch never scales it. It is
 *   always 24 CSS px tall (the named glyphs of `lib/cursorGlyphs.ts`); a custom bitmap from the PC is scaled down to
 *   24 px when it is taller, never up, its hotspot with it.
 * - No React state per message: a `requestAnimationFrame` loop reads `pointer.sample(now)` (which drains the client's
 *   one-slot `cursor_pos`) and writes `transform` and `opacity` straight onto the element. Only a new shape (a few a
 *   second at most) re-renders the glyph.
 * - Placed through the letterbox fit (`fitRef`) and the zoom (`viewRef`) with `fromSurface`, the hotspot on the
 *   reported point. Hidden (opacity 0) before the session's first report, while the PC says the pointer is not
 *   visible, without a fit (no frame laid out yet), and whenever the surface says so (`hidden`: the loupe is open, the
 *   PC paused, a switch of screens, just after a tap's ripple). Not rendered at all unless the session is in `overlay`
 *   mode (`enabled`): in a `drawn` or 1.4 session the pointer is in the picture.
 *
 * DOM only (an `<svg>` or an `<img>` from a `data:` URL); nothing here reads or draws a pixel
 * (test/no-pixel-access.test.ts). A custom bitmap is kept in memory as the current shape only, never logged.
 */
import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

import { CURSOR_GLYPH_SIZE, CURSOR_GLYPHS } from "../lib/cursorGlyphs.ts";
import type { CursorShape, PointerModel } from "../lib/pointer.ts";
import { fromSurface, type Fit, type View } from "../lib/screenMap.ts";

/** The pointer's height in CSS px at every zoom. */
export const POINTER_CSS_PX = CURSOR_GLYPH_SIZE;

export interface ScreenPointerProps {
  pointer: PointerModel;
  /** The picture's letterbox fit in the surface box (null: no frame laid out yet, so nothing is drawn). */
  fitRef: RefObject<Fit | null>;
  /** The zoom and pan of the picture. */
  viewRef: RefObject<View>;
  /** The session draws its pointer on the phone (`cursorMode === "overlay"`). */
  enabled: boolean;
  /** The surface hides it for now (loupe, PC paused, switching screens, a tap's ripple). */
  hidden: boolean;
}

/** Where a shape's hotspot sits in its drawn box, and the box's size, in CSS px (a missing shape is the arrow). */
function pointerLayout(shape: CursorShape | null): { width: number; height: number; hx: number; hy: number } {
  if (shape && shape.kind === "custom") {
    const k = Math.min(1, POINTER_CSS_PX / shape.height);
    return { width: shape.width * k, height: shape.height * k, hx: shape.hotspotX * k, hy: shape.hotspotY * k };
  }
  const glyph = CURSOR_GLYPHS[shape?.kind ?? "arrow"];
  return { width: POINTER_CSS_PX, height: POINTER_CSS_PX, hx: glyph.hotspot.x, hy: glyph.hotspot.y };
}

/** Constant, so a re-render (a new shape) never resets what the frame loop wrote. Easing lives in the model. */
const INITIAL_STYLE: CSSProperties = { opacity: 0, transform: "translate3d(0px, 0px, 0)", transition: "opacity 80ms" };
const SHADOW: CSSProperties = { filter: "drop-shadow(0 1px 1px rgba(0,0,0,.6))" };

function Glyph({ shape }: { shape: CursorShape | null }) {
  if (shape && shape.kind === "custom") {
    const l = pointerLayout(shape);
    return <img src={shape.src} width={l.width} height={l.height} draggable={false} alt="" data-testid="screen-pointer-image" style={SHADOW} />;
  }
  const kind = shape?.kind ?? "arrow";
  const glyph = CURSOR_GLYPHS[kind];
  const spinAt = glyph.spinAt;
  return (
    <svg width={POINTER_CSS_PX} height={POINTER_CSS_PX} viewBox={`0 0 ${CURSOR_GLYPH_SIZE} ${CURSOR_GLYPH_SIZE}`} data-testid="screen-pointer-glyph" data-kind={kind} className={glyph.animate === "spin" ? "animate-spin motion-reduce:animate-none" : undefined} style={SHADOW} aria-hidden="true">
      {glyph.paths.map((p, i) => {
        const path = <path key={i} d={p.d} fill={p.fill} fillRule={p.evenOdd ? "evenodd" : undefined} stroke="black" strokeWidth={1} strokeLinejoin="round" />;
        return p.spin && spinAt ? (
          <g key={i} className="animate-spin motion-reduce:animate-none" style={{ transformOrigin: `${spinAt.x}px ${spinAt.y}px` }}>
            {path}
          </g>
        ) : (
          path
        );
      })}
    </svg>
  );
}

export function ScreenPointer({ pointer, fitRef, viewRef, enabled, hidden }: ScreenPointerProps) {
  const el = useRef<HTMLDivElement>(null);
  const [shape, setShape] = useState<CursorShape | null>(null);
  const hiddenRef = useRef(hidden);
  useEffect(() => {
    hiddenRef.current = hidden;
  }, [hidden]);

  useEffect(() => {
    const node = el.current;
    if (!enabled || !node || typeof requestAnimationFrame !== "function") return;
    let handle = 0;
    let shown: CursorShape | null | undefined;
    let transform = "";
    let opacity = "";
    const frame = () => {
      // one clock for the model: performance.now() (the gestures that feed it use the same)
      const s = pointer.sample(performance.now());
      if (s.shape !== shown) {
        shown = s.shape;
        setShape(s.shape);
      }
      const fit = fitRef.current;
      const visible = s.visible && !hiddenRef.current && fit !== null;
      if (visible) {
        const { px, py } = fromSurface(s.x, s.y, fit, viewRef.current);
        const l = pointerLayout(s.shape);
        const next = `translate3d(${px - l.hx}px, ${py - l.hy}px, 0)`;
        if (next !== transform) node.style.transform = transform = next;
      }
      const nextOpacity = visible ? "1" : "0";
      if (nextOpacity !== opacity) node.style.opacity = opacity = nextOpacity;
      handle = requestAnimationFrame(frame);
    };
    handle = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(handle);
      node.style.opacity = "0";
    };
  }, [enabled, pointer, fitRef, viewRef]);

  if (!enabled) return null;
  return (
    <div ref={el} aria-hidden="true" data-testid="screen-pointer" className="pointer-events-none absolute left-0 top-0 z-[15] will-change-transform" style={INITIAL_STYLE}>
      <Glyph shape={shape} />
    </div>
  );
}
