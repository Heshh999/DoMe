/**
 * /demo: the demo film full-width on a Navy stage, with a short introduction and the plan links. Query flags:
 *   ?scene=N&paused=1   render scene N's representative still (QA and the poster)
 *   ?t=<ms>&paused=1    any moment of the film clock
 *   ?layout=vertical    the 9:16 restack (phones and social)
 *   ?export=1           chrome-less, controls hidden, deterministic from t = 0 (scripts/export-film.mjs)
 * The page sits inside PublicLayout's reading column, so the stage breaks out to the viewport's width.
 */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Film } from "../../components/film/Film.tsx";
import type { FilmLayout } from "../../components/film/layout.ts";
import { CONCEPT_NOTE, PLAN_FACTS, sceneByNumber, SCENES } from "../../components/film/timeline.ts";
import { Icon } from "../../components/icons.tsx";

/** The document's client width (no scrollbar), so a full-bleed band never causes sideways scroll. */
function useViewportWidth(): number | null {
  const [w, setW] = useState<number | null>(null);
  useEffect(() => {
    const update = () => setW(document.documentElement.clientWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return w;
}

export function DemoPage() {
  const [params] = useSearchParams();
  const exportMode = params.get("export") === "1";
  const paused = params.get("paused") === "1" || exportMode;
  const sceneParam = Number(params.get("scene"));
  const tParam = Number(params.get("t"));
  const initialTime = Number.isFinite(tParam) && params.has("t") ? tParam : sceneParam >= 1 ? sceneByNumber(sceneParam).still : 0;
  const layoutParam = params.get("layout");
  const layout: FilmLayout | undefined = layoutParam === "vertical" ? "vertical" : layoutParam === "wide" ? "wide" : undefined;
  const vw = useViewportWidth();

  if (exportMode) {
    return (
      <div className="film-export" data-testid="film-export">
        <Film exportMode initialTime={initialTime} layout={layout ?? "wide"} reducedMotion={false} />
      </div>
    );
  }

  const bleed = vw ? { width: vw, marginLeft: `calc(50% - ${vw / 2}px)` } : undefined;
  return (
    <div>
      {/* short on phones, so the frame and its caption band fit in the first screen */}
      <header className="animate-rise mx-auto max-w-6xl px-5 pb-3 pt-5 sm:pb-10 sm:pt-16 lg:px-8 lg:pt-20">
        <p className="dome-eyebrow hidden sm:block">Demo</p>
        <h1 className="text-h2 font-bold sm:mt-4 sm:text-h1">See DoMe in action.</h1>
        <p className="mt-5 hidden max-w-2xl text-lg text-text-muted sm:block sm:text-xl">About a minute: your PC across the room, the AI butler handling a request in your own words, Screen view, point-and-talk, and the plans. Muted, with captions.</p>
      </header>

      <section aria-label="Demo film" className="dome-dark relative pb-4 pt-3 sm:py-10 lg:py-14" style={bleed}>
        <div aria-hidden="true" className="absolute inset-0 bg-[linear-gradient(180deg,#03195a,#001441_40%,#001441)] ring-1 ring-inset ring-white/8" />
        <div aria-hidden="true" className="absolute inset-0 bg-[radial-gradient(60rem_26rem_at_50%_-6rem,rgb(1_66_251/0.5),transparent_70%)]" />
        <div className="relative mx-auto max-w-6xl px-4 sm:px-6 lg:px-8">
          <Film autoplay={!paused} initialTime={initialTime} {...(layout ? { layout } : {})} />
          <p className="mt-5 text-center text-sm text-text-muted">{CONCEPT_NOTE}</p>
          <p className="mt-2 text-center text-sm text-text-muted sm:hidden">About a minute, muted, with captions: the AI butler, Screen view, point-and-talk and the plans.</p>
        </div>
      </section>

      <section aria-labelledby="demo-plans" className="mx-auto max-w-6xl px-5 pb-20 pt-12 lg:px-8 lg:pb-24 lg:pt-16">
        <div className="max-w-3xl">
          <h2 id="demo-plans" className="text-h2 font-bold">
            What you saw, by plan.
          </h2>
          <p className="mt-3 text-text-muted">Today only Free exists. Pro and Pro Plus are shown with their prices and contents; they arrive at paid launch. Everyday commands never count against an AI limit, on any plan.</p>
          <ul className="mt-8 grid gap-4 sm:grid-cols-3">
            {PLAN_FACTS.map((plan) => (
              <li key={plan.id} className="dome-card p-5">
                <h3 className="text-lg font-semibold">{plan.name}</h3>
                <p className="mt-2 flex flex-wrap items-baseline gap-x-1.5">
                  <span className="font-display text-3xl font-bold tracking-[-0.03em]">{plan.price}</span>
                  {plan.per ? <span className="text-sm text-text-muted">{plan.per}</span> : null}
                </p>
                <p className="mt-1 text-sm text-text-muted">{plan.sub}</p>
                <p className="mt-3">
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 text-xs font-semibold text-text-muted">
                    <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${plan.tone === "now" ? "bg-success" : "bg-text-faint"}`} />
                    {plan.status}
                  </span>
                </p>
                <ul className="mt-4 space-y-1.5 text-sm text-text-muted">
                  {plan.lines.map((line) => (
                    <li key={line.text}>
                      {line.text}
                      {line.status ? (
                        <>
                          {" "}
                          <span className="ml-0.5 inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-surface px-2 py-px align-middle text-[11px] font-semibold text-text-muted">
                            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-coral" />
                            {line.status}
                          </span>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
          <div className="mt-8 flex flex-col items-start gap-3 sm:flex-row">
            <Link to="/pricing" className="tap dome-primary dome-press inline-flex items-center gap-2 rounded-full px-6 font-semibold">
              Compare plans
              <Icon name="arrow" size={18} />
            </Link>
            <Link to="/app" className="tap dome-press inline-flex items-center gap-2 rounded-full border border-border-strong bg-bg-elevated px-6 font-semibold hover:bg-surface">
              Open the app
            </Link>
          </div>
          <p className="mt-8 text-xs text-text-faint">
            Scenes: {SCENES.map((s) => `${s.n} ${s.title}`).join(" · ")}. Nothing is recorded; the AI never sees your screen. DoMe is in development: the Windows app is not published yet.
          </p>
        </div>
      </section>
    </div>
  );
}
