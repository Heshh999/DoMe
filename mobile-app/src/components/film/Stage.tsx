/**
 * The stage: a living room at dusk drawn as vector art, the PC on its sideboard, the phone in the foreground,
 * the link pulse between them, the caption band, scene 5's plan cards and the end card. It is drawn on a fixed
 * canvas (layout.ts) and scaled to its container by film.css; everything it shows is a function of `t`.
 */
import { memo } from "react";
import { Link as RouterLink, useInRouterContext } from "react-router";

import { Wordmark } from "../brand.tsx";
import { Icon } from "../icons.tsx";
import { endCardRows, lockupHeight, type StageLayout } from "./layout.ts";
import { PcScreen } from "./Pc.tsx";
import { Phone } from "./Phone.tsx";
import { handAt, linkAt, pcAt, phoneAt, stageAt } from "./script.ts";
import { beatAt, CONCEPT_NOTE, lerp, PLAN_FACTS, sceneAt, seg, type StatusTone } from "./timeline.ts";

/* ── the room ────────────────────────────────────────────────────────────────────────────────────────── */

const Room = memo(function Room({ L, lamp: lit, screen }: { L: StageLayout; lamp: number; screen: number }) {
  const wide = L.name === "wide";
  const floorY = wide ? 660 : 600;
  const win = { x: 1170, y: 60, w: 400, h: 430 };
  const lamp = { x: 72, y: 268 };
  const plant = wide ? { x: 1085, y: 600 } : { x: 790, y: 600 };
  return (
    <svg viewBox={`0 0 ${L.w} ${L.h}`} className="absolute inset-0 h-full w-full" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="fl-wall" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#040d2e" />
          <stop offset="1" stopColor="#0b1b52" />
        </linearGradient>
        <linearGradient id="fl-floor" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#0a1544" />
          <stop offset="1" stopColor="#030a24" />
        </linearGradient>
        <linearGradient id="fl-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#15245e" />
          <stop offset="0.55" stopColor="#5b3d7c" />
          <stop offset="1" stopColor="#e9865c" />
        </linearGradient>
        <radialGradient id="fl-lamp" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#ffb86b" stopOpacity="0.42" />
          <stop offset="1" stopColor="#ffb86b" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="fl-screenglow" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#4281fa" stopOpacity="0.35" />
          <stop offset="1" stopColor="#4281fa" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="fl-wood" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1c2756" />
          <stop offset="0.08" stopColor="#2a3670" />
          <stop offset="0.1" stopColor="#141d47" />
          <stop offset="1" stopColor="#0c1335" />
        </linearGradient>
        <linearGradient id="fl-sofa" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1a2458" />
          <stop offset="1" stopColor="#0d1540" />
        </linearGradient>
      </defs>
      <rect width={L.w} height={L.h} fill="url(#fl-wall)" />
      {/* the window at dusk (wide only: the vertical frame has no wall beside the monitor) */}
      {wide ? (
      <g>
        <rect x={win.x - 14} y={win.y - 14} width={win.w + 28} height={win.h + 28} rx="10" fill="#0a1442" />
        <rect x={win.x} y={win.y} width={win.w} height={win.h} fill="url(#fl-sky)" />
        <g fill="#1a1f4e">
          {[0, 60, 110, 190, 250, 320, 370, 440, 500, 560].map((dx, i) => {
            const h = 40 + ((i * 37) % 70);
            return <rect key={dx} x={win.x + (dx * win.w) / 600} y={win.y + win.h - h} width={(45 * win.w) / 600} height={h} />;
          })}
        </g>
        {[0.1, 0.22, 0.4, 0.58, 0.73, 0.87].map((f, i) => (
          <rect key={f} x={win.x + f * win.w} y={win.y + win.h - 30 - (i % 3) * 18} width="4" height="4" fill="#ffe08a" opacity="0.85" />
        ))}
        <circle cx={win.x + win.w * 0.72} cy={win.y + win.h * 0.22} r="14" fill="#fbead0" opacity="0.9" />
        <rect x={win.x + win.w / 2 - 4} y={win.y} width="8" height={win.h} fill="#0a1442" />
        <rect x={win.x} y={win.y + win.h / 2 - 4} width={win.w} height="8" fill="#0a1442" />
        <rect x={win.x - 14} y={win.y - 14} width={win.w + 28} height={win.h + 28} rx="10" fill="none" stroke="#2a3670" strokeWidth="2" />
      </g>
      ) : null}
      <rect x="0" y={floorY} width={L.w} height={L.h - floorY} fill="url(#fl-floor)" />
      <rect x="0" y={floorY - 1} width={L.w} height="2" fill="#1c2756" />
      {/* the reading lamp: its pool of light comes on first (wide only: the vertical frame has no room for the
          lamp, and a glow with no lamp under it reads as a stain in the corner) */}
      {wide ? (
        <g>
          <circle cx={lamp.x} cy={lamp.y} r="300" fill="url(#fl-lamp)" style={{ opacity: lit }} />
          <rect x={lamp.x - 3} y={lamp.y + 30} width="6" height={floorY - lamp.y - 30} fill="#1c2756" />
          <path d={`M${lamp.x - 44} ${lamp.y + 34} L${lamp.x - 28} ${lamp.y - 30} L${lamp.x + 28} ${lamp.y - 30} L${lamp.x + 44} ${lamp.y + 34} Z`} fill="#3a3f78" />
          <ellipse cx={lamp.x} cy={lamp.y + 34} rx="44" ry="7" fill="#ffd9a0" style={{ opacity: 0.25 + 0.6 * lit }} />
          <ellipse cx={lamp.x} cy={floorY + 2} rx="30" ry="6" fill="#1c2756" />
        </g>
      ) : null}
      {/* sideboard under the monitor */}
      <rect x={L.pc.x - 40} y={floorY - 64} width={L.pc.w + 80} height="64" rx="6" fill="url(#fl-wood)" />
      {[0.33, 0.66].map((f) => (
        <rect key={f} x={L.pc.x - 40 + f * (L.pc.w + 80)} y={floorY - 56} width="1.5" height="52" fill="#2a3670" />
      ))}
      <ellipse cx={L.pc.x + L.pc.w / 2} cy={floorY + 40} rx={L.pc.w * 0.5} ry="18" fill="#000" opacity="0.35" />
      {/* the glow the screen throws on the wall, once the PC is awake */}
      <ellipse cx={L.pc.x + L.pc.w / 2} cy={L.pc.y + L.pc.h / 2} rx={L.pc.w * 0.8} ry={L.pc.h * 0.95} fill="url(#fl-screenglow)" style={{ opacity: screen }} />
      {/* plant */}
      <g transform={`translate(${plant.x} ${plant.y})`}>
        <path d="M-26 0 L26 0 L20 56 L-20 56 Z" fill="#1c2756" />
        <rect x="-30" y="-6" width="60" height="10" rx="3" fill="#2a3670" />
        {[-70, -40, -10, 25, 55].map((a, i) => (
          <ellipse key={a} cx="0" cy="-70" rx="14" ry="62" fill={i % 2 ? "#17563f" : "#1f6e4c"} transform={`rotate(${a} 0 -8)`} />
        ))}
      </g>
      {/* the sofa the viewer sits on, under the phone */}
      {wide ? (
        <g>
          <rect x="1140" y="540" width="520" height="130" rx="30" fill="url(#fl-sofa)" />
          <rect x="1162" y="566" width="230" height="90" rx="22" fill="#1f2a62" />
          <rect x="1408" y="566" width="230" height="90" rx="22" fill="#1f2a62" />
          <rect x="1120" y="650" width="560" height="64" rx="22" fill="#162052" />
          <rect x="1104" y="600" width="44" height="118" rx="18" fill="#1b2560" />
        </g>
      ) : (
        <g>
          {/* the sofa, seen from the front: back, two cushions, seat, arms; a rug under it */}
          <ellipse cx="450" cy="1215" rx="430" ry="40" fill="#0d1744" />
          <rect x="70" y="930" width="760" height="170" rx="36" fill="url(#fl-sofa)" />
          <rect x="100" y="958" width="340" height="118" rx="26" fill="#1f2a62" />
          <rect x="460" y="958" width="340" height="118" rx="26" fill="#1f2a62" />
          <rect x="46" y="1086" width="808" height="92" rx="30" fill="#162052" />
          <rect x="24" y="1010" width="58" height="168" rx="22" fill="#1b2560" />
          <rect x="818" y="1010" width="58" height="168" rx="22" fill="#1b2560" />
        </g>
      )}
    </svg>
  );
});

/* ── the monitor ─────────────────────────────────────────────────────────────────────────────────────── */

function Monitor({ L, t }: { L: StageLayout; t: number }) {
  const r = L.pc;
  const pc = pcAt(t);
  const bezel = 12;
  return (
    <div className="absolute" style={{ left: r.x - bezel, top: r.y - bezel, width: r.w + bezel * 2, height: r.h + bezel * 2 }}>
      <div className="absolute inset-0 rounded-[16px] bg-[linear-gradient(160deg,#1d2a5e,#0a1234)] shadow-[0_40px_70px_-30px_rgb(0_0_0/0.8),0_0_0_1px_rgb(255_255_255/0.08)_inset]" />
      <div className="absolute overflow-hidden rounded-[8px] bg-[#030818]" style={{ left: bezel, top: bezel, width: r.w, height: r.h }}>
        <PcScreen view={pc} width={r.w} />
      </div>
      <div className="absolute left-1/2 h-[22px] w-[60px] -translate-x-1/2 bg-[#121b45]" style={{ top: r.h + bezel * 2 - 2 }} />
      <div className="absolute left-1/2 h-[8px] w-[220px] -translate-x-1/2 rounded-full bg-[#1c2756]" style={{ top: r.h + bezel * 2 + 18 }} />
      <span className="absolute left-1/2 top-[calc(100%-7px)] h-1 w-1 -translate-x-1/2 rounded-full bg-[#4fdc97]" style={{ opacity: pc.on }} />
    </div>
  );
}

/* ── the link pulse between phone and PC ─────────────────────────────────────────────────────────────── */

function Link({ L, t }: { L: StageLayout; t: number }) {
  const link = linkAt(t);
  if (link.visible <= 0) return null;
  const wide = L.name === "wide";
  // wide: from the phone's top across to the monitor's right edge; vertical: the phone stands in front of the
  // monitor, so the pulse arcs from its top-right corner up onto the screen
  const from = wide ? { x: L.phone.x + L.phone.w / 2, y: L.phone.y + 24 } : { x: L.phone.x + L.phone.w - 30, y: L.phone.y + 40 };
  const to = wide ? { x: L.pc.x + L.pc.w + 14, y: L.pc.y + L.pc.h * 0.45 } : { x: L.pc.x + L.pc.w * 0.8, y: L.pc.y + L.pc.h * 0.62 };
  const ctrl = wide ? { x: (from.x + to.x) / 2 + 40, y: Math.min(from.y, to.y) - 120 } : { x: from.x + 150, y: (from.y + to.y) / 2 };
  const p = link.pulse;
  const q = 1 - p;
  const dot = { x: q * q * from.x + 2 * q * p * ctrl.x + p * p * to.x, y: q * q * from.y + 2 * q * p * ctrl.y + p * p * to.y };
  return (
    <svg viewBox={`0 0 ${L.w} ${L.h}`} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" focusable="false" style={{ opacity: link.visible }}>
      <path d={`M${from.x} ${from.y} Q${ctrl.x} ${ctrl.y} ${to.x} ${to.y}`} fill="none" stroke="#86abff" strokeOpacity="0.35" strokeWidth="2" strokeDasharray="4 10" strokeLinecap="round" />
      <circle cx={dot.x} cy={dot.y} r="18" fill="#4281fa" opacity="0.25" />
      <circle cx={dot.x} cy={dot.y} r="7" fill="#ffffff" />
      {p >= 1 ? <circle cx={to.x} cy={to.y} r={lerp(8, 40, seg(t, t, 0))} fill="none" stroke="#86abff" strokeWidth="2" opacity="0.5" /> : null}
    </svg>
  );
}

/* ── captions ────────────────────────────────────────────────────────────────────────────────────────── */

const TONE_DOT: Record<StatusTone, string> = { now: "bg-success", coming: "bg-coral", planned: "bg-sky", info: "bg-[#8f9dc4]" };

/** The id of the caption region, which the film's frame points at with aria-describedby. */
export const CAPTION_ID = "dome-film-caption";

function Captions({ L, t, announce }: { L: StageLayout; t: number; announce: boolean }) {
  const scene = sceneAt(t);
  const beat = beatAt(t);
  const c = L.caption;
  const vertical = L.name === "vertical";
  const inAt = scene.n === 1 ? 3_200 : scene.start + 900;
  const out = 1 - seg(t, scene.end - 320, 320);
  const visible = scene.id === "end" ? 0 : Math.min(seg(t, inAt, 600), out);
  const beatIn = beat ? Math.min(seg(t, beat.at, 420), out) : 0;
  return (
    <>
        {/* a soft navy floor under the caption band, so the words never sit on the furniture or the phone's edge */}
      <div aria-hidden="true" className="absolute bottom-0 left-0 bg-[linear-gradient(180deg,rgb(0_20_65/0),rgb(0_20_65/0.88)_38%,#001441)]" style={{ top: c.y - 40, right: vertical ? 0 : L.w - L.phone.x - 40, opacity: Math.max(visible, beatIn), ...(vertical ? {} : { WebkitMaskImage: "linear-gradient(90deg,#000 70%,transparent)", maskImage: "linear-gradient(90deg,#000 70%,transparent)" }) }} />
    <div className="absolute flex flex-col justify-end" style={{ left: c.x, top: c.y, width: c.w, height: c.h, alignItems: vertical ? "center" : "flex-start", textAlign: c.align }}>
      {/* the live region keeps the words even while the band is visually faded; it stays silent until the visitor
          has started the film themselves, so an autoplaying film never talks over the page */}
      <div id={CAPTION_ID} className="film-caption" role="status" aria-live={announce ? "polite" : "off"} aria-atomic="false" style={{ opacity: visible, transform: `translateY(${lerp(14, 0, visible)}px)`, fontSize: c.size }}>
        <p className={`font-semibold tracking-[-0.015em] text-white [text-wrap:balance] ${vertical ? "leading-[1.15]" : "leading-[1.2]"}`}>{scene.caption}</p>
        {beat ? (
          <p className={`mt-[0.4em] font-medium leading-[1.3] text-[#c9d4f0] [text-wrap:balance] ${vertical ? "text-[0.78em]" : "text-[0.62em]"}`} style={{ opacity: beatIn }} data-film-beat="">
            {beat.speech ? `“${beat.text}”` : beat.text}
          </p>
        ) : null}
        <p className={`inline-flex items-center gap-[0.45em] rounded-[1em] border border-white/14 bg-white/[0.08] px-[0.9em] font-semibold leading-[1.25] text-[#dfe6f8] ${vertical ? "mt-[0.45em] max-w-full py-[0.36em] text-[0.72em]" : "mt-[0.55em] py-[0.42em] text-[0.5em]"}`}>
          <span className={`h-[0.55em] w-[0.55em] shrink-0 rounded-full ${TONE_DOT[scene.status.tone]}`} />
          <span>{scene.status.label}</span>
        </p>
      </div>
    </div>
    </>
  );
}

/* ── scene 5: the plans ──────────────────────────────────────────────────────────────────────────────── */

function StatusPill({ tone, children, size }: { tone: StatusTone; children: React.ReactNode; size: number }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border border-white/14 bg-white/8 px-3 py-1 font-semibold text-[#dfe6f8]" style={{ fontSize: size }}>
      <span className={`h-2 w-2 rounded-full ${TONE_DOT[tone]}`} />
      {children}
    </span>
  );
}

function PlanCards({ L, t }: { L: StageLayout; t: number }) {
  const s = stageAt(t);
  if (s.cards.every((c) => c <= 0)) return null;
  const wide = L.name === "wide";
  return (
    <>
      {PLAN_FACTS.map((plan, i) => {
        const r = L.cards[i]!;
        const e = s.cards[i]!;
        const pro = plan.id === "pro";
        const glow = pro ? s.proGlow : 0;
        // the vertical card keeps the two lines that matter most, in a size a phone can read
        const lines = wide ? plan.lines : plan.lines.filter((l) => l.key);
        return (
          // an opaque navy base under the glass, so nothing of the room can read through a card
          <div key={plan.id} className="absolute rounded-[28px] border border-white/12 bg-[#0a1c50] bg-[linear-gradient(180deg,rgb(255_255_255/0.09),rgb(255_255_255/0.04))] text-white" style={{ left: r.x, top: r.y, width: r.w, height: r.h, opacity: e, transform: `translateY(${lerp(44, 0, e)}px) scale(${lerp(0.97, 1, e)})`, boxShadow: `0 1px 0 rgb(255 255 255 / 0.08) inset, 0 40px 80px -40px rgb(0 0 0 / 0.8), 0 0 0 ${2 * glow}px rgb(66 129 250 / ${0.9 * glow}), 0 30px 90px -20px rgb(1 66 251 / ${0.75 * glow})` }}>
            {pro ? <div className="pointer-events-none absolute inset-0 rounded-[28px] bg-[radial-gradient(60%_50%_at_50%_0%,rgb(1_66_251/0.5),transparent_70%)]" style={{ opacity: glow }} /> : null}
            {wide ? (
              <div className="relative flex h-full flex-col p-9">
                <p className="whitespace-nowrap text-[30px] font-semibold tracking-[-0.02em]">{plan.name}</p>
                <p className="mt-4 flex items-baseline gap-2.5">
                  <span className="font-display text-[52px] font-bold leading-none tracking-[-0.04em]">{plan.price}</span>
                  {plan.per ? <span className="text-[18px] text-[#b2bfdd]">{plan.per}</span> : null}
                </p>
                <p className="mt-2 whitespace-nowrap text-[17px] text-[#b2bfdd]">{plan.sub}</p>
                <p className="mt-5">
                  <StatusPill tone={plan.tone} size={16}>
                    {plan.status}
                  </StatusPill>
                </p>
                <ul className="mt-6 space-y-2.5 text-[18px] leading-[1.35]">
                  {lines.map((line) => (
                    <li key={line.text} className="flex items-start gap-3">
                      <span className="mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-success/15 text-success">
                        <Icon name="check" size={14} strokeWidth={2.75} />
                      </span>
                      <span>
                        {line.text}
                        {line.status ? (
                          <span className="ml-2 inline-flex translate-y-[-1px] items-center gap-1.5 whitespace-nowrap rounded-full border border-white/14 bg-white/8 px-2 py-px align-middle text-[13px] font-semibold text-[#dfe6f8]">
                            <span className="h-1.5 w-1.5 rounded-full bg-coral" />
                            {line.status}
                          </span>
                        ) : null}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="relative flex h-full flex-col justify-between p-8">
                <div className="flex items-start justify-between gap-6">
                  <div>
                    <p className="whitespace-nowrap text-[36px] font-semibold leading-none tracking-[-0.02em]">{plan.name}</p>
                    <p className="mt-4">
                      <StatusPill tone={plan.tone} size={22}>
                        {plan.status}
                      </StatusPill>
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="flex items-baseline justify-end gap-2">
                      <span className="font-display text-[56px] font-bold leading-none tracking-[-0.04em]">{plan.price}</span>
                      {plan.per ? <span className="text-[22px] text-[#b2bfdd]">{plan.per}</span> : null}
                    </p>
                    <p className="mt-2 whitespace-nowrap text-[22px] text-[#b2bfdd]">{plan.sub}</p>
                  </div>
                </div>
                <ul className="space-y-2 text-[30px] font-medium leading-[1.3]">
                  {lines.map((line) => (
                    <li key={line.text} className="flex items-center gap-4">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-success/15 text-success">
                        <Icon name="check" size={20} strokeWidth={2.75} />
                      </span>
                      <span className="min-w-0">{line.text}</span>
                      {line.status ? (
                        <StatusPill tone="coming" size={20}>
                          {line.status}
                        </StatusPill>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        );
      })}
      <p className={`absolute text-[#b2bfdd] ${wide ? "text-[20px]" : "text-[26px] leading-[1.35]"}`} style={{ left: L.cards[0]!.x, width: wide ? L.cards[2]!.x + L.cards[2]!.w - L.cards[0]!.x : L.cards[0]!.w, top: wide ? 642 : 1140, textAlign: "center", opacity: s.footnote, transform: `translateY(${lerp(10, 0, s.footnote)}px)` }}>
        Everyday commands (“pause”, “volume 40”, “open Spotify”) are free and unlimited on every plan.
      </p>
    </>
  );
}

/* ── scene 6: the end card ───────────────────────────────────────────────────────────────────────────── */

/** How the end card's two calls to action are drawn: as real links (website), as pictures (export), or not at all
 *  (when the film has ended and the frame shows them as full-size buttons over the stage). */
export type EndActions = "links" | "drawn" | "hidden";

/** A link that uses client-side routing inside the website, and a plain link where the film renders on its own. */
export function FilmLink({ to, className, onClick, children }: { to: string; className: string; onClick?: (e: React.MouseEvent) => void; children: React.ReactNode }) {
  const routed = useInRouterContext();
  return routed ? (
    <RouterLink to={to} className={className} onClick={onClick}>
      {children}
    </RouterLink>
  ) : (
    <a href={to} className={className} onClick={onClick}>
      {children}
    </a>
  );
}

const END_PRIMARY = "dome-primary inline-flex h-[64px] items-center gap-2 rounded-full px-9 text-[22px] font-semibold";
const END_SECONDARY = "inline-flex h-[64px] items-center gap-1.5 rounded-full border border-white/25 px-9 text-[22px] font-semibold text-white";

function EndCard({ L, t, actions }: { L: StageLayout; t: number; actions: EndActions }) {
  const s = stageAt(t);
  if (s.lockup <= 0) return null;
  const { cx, cy, w } = L.lockup;
  const h = lockupHeight(L);
  const rows = endCardRows(L);
  const vertical = L.name === "vertical";
  // clicks on the links must not reach the frame, which toggles play/pause
  const stop = (e: React.MouseEvent) => e.stopPropagation();
  const live = actions === "links" && s.buttons > 0.5;
  return (
    <div className="pointer-events-none absolute inset-0 text-white">
      <div aria-hidden="true" className="absolute" style={{ left: cx - w / 2, top: cy - h / 2, width: w, opacity: s.lockup, transform: `scale(${lerp(0.9, 1, s.lockup)})`, filter: `drop-shadow(0 0 ${40 * s.lockup}px rgb(66 129 250 / 0.45))` }}>
        <Wordmark title={null} className="h-auto w-full text-white" />
      </div>
      <p aria-hidden="true" className="absolute w-full text-center text-[52px] font-bold tracking-[-0.03em]" style={{ top: rows.tagline, opacity: s.tagline, transform: `translateY(${lerp(14, 0, s.tagline)}px)` }}>
        Your PC, in your hand.
      </p>
      {actions === "hidden" ? null : (
        <div className="absolute flex w-full items-center justify-center gap-4" style={{ top: rows.actions, opacity: s.buttons, transform: `translateY(${lerp(14, 0, s.buttons)}px)` }}>
          {live ? (
            <>
              <FilmLink to="/app" className={`${END_PRIMARY} pointer-events-auto`} onClick={stop}>
                Open the app
                <Icon name="arrow" size={22} />
              </FilmLink>
              <FilmLink to="/pricing" className={`${END_SECONDARY} pointer-events-auto hover:bg-white/10`} onClick={stop}>
                Compare plans
                <Icon name="chevron" size={20} />
              </FilmLink>
            </>
          ) : (
            <>
              <span aria-hidden="true" className={END_PRIMARY}>
                Open the app
                <Icon name="arrow" size={22} />
              </span>
              <span aria-hidden="true" className={END_SECONDARY}>
                Compare plans
                <Icon name="chevron" size={20} />
              </span>
            </>
          )}
        </div>
      )}
      <p aria-hidden="true" className={`absolute w-full px-12 text-center leading-[1.45] text-[#8f9dc4] ${vertical ? "text-[28px]" : "text-[20px]"}`} style={{ top: rows.concept, opacity: s.concept }}>
        {CONCEPT_NOTE}
      </p>
    </div>
  );
}

/* ── the stage ───────────────────────────────────────────────────────────────────────────────────────── */

export interface StageProps {
  t: number;
  layout: StageLayout;
  /** The end card's calls to action (default: drawn, as the export and stills show them). */
  endActions?: EndActions;
  /** Whether the caption region announces changes (off until the visitor starts the film). */
  announce?: boolean;
  /** The time the film's clock reads, when the stage shows another moment (the poster); defaults to t. */
  clock?: number;
}

/** The stage at time t on a fixed canvas: wrap it in `.film-frame` (film.css) to scale it to any width. */
export const Stage = memo(function Stage({ t, layout: L, endActions = "drawn", announce = true, clock }: StageProps) {
  const s = stageAt(t);
  const phone = phoneAt(t);
  const pc = pcAt(t);
  const hand = handAt(t);
  const rise = lerp(70, 0, phone.entrance);
  // a gentle float once the phone has arrived, so it reads as held rather than pinned
  const float = phone.entrance >= 1 ? Math.sin(t / 1900) * 5 : 0;
  // the room and the devices fade out under the veil, so nothing of them shows through the plan cards
  const clear = 1 - s.veil;
  return (
    <div className="film-canvas dome-dark" style={{ width: L.w, height: L.h, fontSize: 16 }} data-film-time={Math.round(clock ?? t)}>
      {/* the picture: decorative for assistive technology (the caption region below says what happens) */}
      <div aria-hidden="true" className="absolute inset-0">
        {clear > 0 ? (
          <div className="absolute inset-0" style={{ opacity: s.room * clear }}>
            <Room L={L} lamp={s.lamp} screen={pc.on} />
          </div>
        ) : null}
        {clear > 0 ? (
          <div className="absolute inset-0" style={{ opacity: s.devices * clear, transform: `scale(${lerp(0.96, 1, s.devices)})`, transformOrigin: "50% 60%" }}>
            <Monitor L={L} t={t} />
            <Link L={L} t={t} />
            <div className="absolute" style={{ left: L.phone.x, top: L.phone.y, opacity: phone.entrance, transform: `translateY(${rise + float}px) rotate(${lerp(3, 0, phone.entrance)}deg)`, transformOrigin: "50% 100%" }}>
              <Phone t={t} view={phone} pc={pc} hand={hand} width={L.phone.w} bezel={L.bezel} />
            </div>
          </div>
        ) : null}
        {s.veil > 0 ? <div className="absolute inset-0 bg-[radial-gradient(70%_50%_at_50%_-10%,rgb(1_66_251/0.5),transparent_70%),linear-gradient(180deg,#02113f,#001441)]" style={{ opacity: s.veil }} /> : null}
        <PlanCards L={L} t={t} />
        {/* the DoMe wordmark, quietly, in the corner */}
        <span className="absolute" style={{ left: 36, top: 30, opacity: s.lockup > 0 ? 0 : 0.8 }}>
          <Wordmark compact title={null} className="h-[22px] w-auto text-white" />
        </span>
      </div>
      <EndCard L={L} t={t} actions={endActions} />
      <Captions L={L} t={t} announce={announce} />
    </div>
  );
});
