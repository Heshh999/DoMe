/**
 * The Screen view readout, test builds only (device round 2, 2026-10-27: the founder's phone sat on "Connecting…" for
 * 217 s and nothing on it said why). It sits in the stage's corner over the picture — or over the black stage when no
 * picture exists, which is the case it is for — and lists, once a second, the gates a picture has to pass in order
 * (the first "no" is the stage that failed), then the numbers behind them. Everything comes from the session client:
 * the whitelisted `getStats()` snapshot, the `<video>` element's own metadata and the stage timeline
 * (lib/screenStats.ts); nothing here reads a pixel or an address.
 *
 * Only the development, test, CI and staging builds render it (`screenReadoutEnabled`, an allow-list: an unset or
 * unknown channel renders nothing). Shown by default; "Hide readout" is remembered on this phone. The panel lets taps
 * through to the picture except on its buttons, and sits under the page's status and "Tap to show the picture"
 * overlays so neither is ever covered. Copy puts the text on the clipboard; where Safari refuses, a small box with
 * the text as it was at that moment takes touches so it can be selected, until "Done".
 */
import { useEffect, useState } from "react";

import type { ScreenSessionState } from "../lib/screen.ts";
import { readoutLines, screenReadoutEnabled, type ScreenClientFacts } from "../lib/screenStats.ts";

const HIDDEN_KEY = "dome.screen.readout.hidden";
const REFRESH_MS = 1000;
const BUTTON = "pointer-events-auto inline-flex h-11 items-center rounded-md border border-white/30 bg-white/10 px-2 text-[11px] font-semibold text-white";
/** Under the status overlay (z-10) and the "Tap to show the picture" overlay (z-20), over the picture. */
const PANEL = "pointer-events-none absolute left-2 top-2 z-[5] max-w-[70%] rounded-md bg-black/70 p-1.5 font-mono text-[10px] leading-tight text-white";

/** What the readout reads from the session client (`ScreenSessionClient` satisfies it). */
export interface ScreenReadoutSource {
  readonly facts: ScreenClientFacts;
  readFacts(): Promise<ScreenClientFacts>;
}

function readHidden(): boolean {
  try {
    return localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function writeHidden(hidden: boolean): void {
  try {
    if (hidden) localStorage.setItem(HIDDEN_KEY, "1");
    else localStorage.removeItem(HIDDEN_KEY);
  } catch {
    /* storage unavailable: the choice lasts until the page is left */
  }
}

export function ScreenReadout(props: { source: ScreenReadoutSource; state: ScreenSessionState }) {
  if (!screenReadoutEnabled()) return null;
  return <Readout {...props} />;
}

function Readout({ source, state }: { source: ScreenReadoutSource; state: ScreenSessionState }) {
  const [shown, setShown] = useState(() => !readHidden());
  const [facts, setFacts] = useState<ScreenClientFacts>(() => source.facts);
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  /** The text as it was when the copy was refused, in a box that takes touches (it does not change under a selection). */
  const [toSelect, setToSelect] = useState<string | null>(null);
  // a new stage (verified, a stream, DTLS, live…) is read at once rather than at the next tick
  const stage = `${state.phase}|${state.verified}|${state.transportConnected}|${state.stream !== null}`;

  useEffect(() => {
    if (!shown) return;
    let alive = true;
    const refresh = () => {
      source.readFacts().then(
        (f) => {
          if (alive) setFacts(f);
        },
        () => undefined,
      );
    };
    refresh();
    const h = setInterval(refresh, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, [source, shown, stage]);

  const text = readoutLines(state, facts).join("\n");

  const toggle = () => {
    const next = !shown;
    setShown(next);
    setCopy("idle");
    setToSelect(null);
    writeHidden(!next);
  };

  // writeText runs inside the tap itself (Safari wants the gesture); a refusal leaves the text to select by hand
  const copyText = () => {
    const refused = () => {
      setCopy("failed");
      setToSelect(text);
    };
    let pending: Promise<void>;
    try {
      pending = navigator.clipboard.writeText(text);
    } catch {
      refused();
      return;
    }
    pending.then(() => {
      setCopy("copied");
      setToSelect(null);
    }, refused);
  };
  const doneSelecting = () => {
    setCopy("idle");
    setToSelect(null);
  };

  return (
    <div data-testid="screen-readout" className={PANEL}>
      <div className="flex gap-1.5">
        <button type="button" className={BUTTON} aria-expanded={shown} onClick={toggle}>
          {shown ? "Hide readout" : "Readout"}
        </button>
        {shown ? (
          <button type="button" className={BUTTON} onClick={copyText}>
            Copy
          </button>
        ) : null}
      </div>
      {shown ? (
        <>
          <pre data-testid="screen-readout-text" className="mt-1 whitespace-pre-wrap break-words font-mono">
            {text}
          </pre>
          {copy !== "idle" ? (
            <p role="status" className="mt-1 font-sans text-[11px]">
              {copy === "copied" ? "Copied" : "Copy failed: press and hold the text below to select it"}
            </p>
          ) : null}
          {toSelect !== null ? (
            // 16 px: iOS zooms the page into a focused text field set smaller than that
            <>
              <textarea readOnly value={toSelect} aria-label="Readout text to select" data-testid="screen-readout-select" className="pointer-events-auto mt-1 block h-20 w-full resize-none select-text rounded border border-white/30 bg-black/80 p-1 font-mono text-base leading-snug text-white" />
              <button type="button" className={`${BUTTON} mt-1`} onClick={doneSelecting}>
                Done
              </button>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
