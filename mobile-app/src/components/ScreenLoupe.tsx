/**
 * The precision loupe (docs/design/screen-view.md §8.3): a 2.5× magnified circle above the finger with a crosshair at
 * the aim. It is a second `<video>` on the SAME MediaStream, scaled with a CSS transform: no canvas, no pixel read
 * (test/no-pixel-access.test.ts), nothing decoded twice by this code. `aim` is in surface-box px; the circle shows the
 * box content (with the current zoom/pan) magnified about that point.
 */
import { useEffect, useRef } from "react";

import { LOUPE_ZOOM } from "../lib/screenInput.ts";
import { viewTransform, type Box, type View } from "../lib/screenMap.ts";

export const LOUPE_DIAMETER = 132;
/** The circle sits this far above the finger so the finger never covers what it aims at. */
const LIFT = 96;

export function ScreenLoupe({ stream, aim, box, view }: { stream: MediaStream | null; aim: { x: number; y: number }; box: Box; view: View }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    v.srcObject = stream;
    if (stream) void v.play?.()?.catch(() => undefined);
    return () => {
      v.srcObject = null;
    };
  }, [stream]);
  const r = LOUPE_DIAMETER / 2;
  // keep the circle inside the box horizontally; flip below the finger near the top edge
  const left = Math.max(0, Math.min(box.width - LOUPE_DIAMETER, aim.x - r));
  const top = aim.y - LIFT - LOUPE_DIAMETER < 0 ? aim.y + LIFT / 2 : aim.y - LIFT - LOUPE_DIAMETER;
  const inner = `translate(${r - aim.x * LOUPE_ZOOM}px, ${r - aim.y * LOUPE_ZOOM}px) scale(${LOUPE_ZOOM}) ${viewTransform(view)}`;
  return (
    <div aria-hidden="true" data-testid="screen-loupe" className="pointer-events-none absolute z-20 overflow-hidden rounded-full border-2 border-white/90 bg-black shadow-[0_8px_28px_rgba(0,0,0,0.55)]" style={{ left, top, width: LOUPE_DIAMETER, height: LOUPE_DIAMETER }}>
      <div className="absolute left-0 top-0" style={{ width: box.width, height: box.height, transform: inner, transformOrigin: "0 0" }}>
        <video ref={ref} className="pointer-events-none h-full w-full object-contain" playsInline muted disablePictureInPicture />
      </div>
      <span className="absolute left-1/2 top-[18%] h-[64%] w-px -translate-x-1/2 bg-white/90 mix-blend-difference" />
      <span className="absolute left-[18%] top-1/2 h-px w-[64%] -translate-y-1/2 bg-white/90 mix-blend-difference" />
    </div>
  );
}
