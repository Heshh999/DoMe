/**
 * The stage: a living room at dusk drawn as vector art, the PC on its sideboard, the phone in the foreground,
 * the link pulse between them, the caption band, scene 5's plan cards and the end card. It is drawn on a fixed
 * canvas (layout.ts) and scaled to its container by film.css; everything it shows is a function of `t`.
 */
import { memo } from "react";

import { Wordmark } from "../brand.tsx";
import { Icon } from "../icons.tsx";
import { FilmIcon } from "./filmIcons.tsx";
import type { StageLayout } from "./layout.ts";
import { PcScreen } from "./Pc.tsx";
import { Phone } from "./Phone.tsx";
import { handAt, linkAt, pcAt, phoneAt, stageAt } from "./script.ts";
import { CONCEPT_NOTE, lerp, PLAN_FACTS, sceneAt, seg, type StatusTone } from "./timeline.ts";

/* ── the room ────────────────────────────────────────────────────────────────────────────────────────── */

const Room = memo(function Room({ L }: { L: StageLayout }) {
  const wide = L.name === "wide";
  const floorY = wide ? 660 : 600;
  const win = wide ? { x: 1170, y: 60, w: 400, h: 430 } : { x: 150, y: 650, w: 600, h: 520 };
  const lamp = wide ? { x: 72, y: 268 } : { x: 60, y: 60 };
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
      {/* the window at dusk */}
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
      <rect x="0" y={floorY} width={L.w} height={L.h - floorY} fill="url(#fl-floor)" />
      <rect x="0" y={floorY - 1} width={L.w} height="2" fill="#1c2756" />
      {/* lamp */}
      <circle cx={lamp.x} cy={lamp.y} r={wide ? 300 : 260} fill="url(#fl-lamp)" />
      {wide ? (
        <g>
          <rect x={lamp.x - 3} y={lamp.y + 30} width="6" height={floorY - lamp.y - 30} fill="#1c2756" />
          <path d={`M${lamp.x - 44} ${lamp.y + 34} L${lamp.x - 28} ${lamp.y - 30} L${lamp.x + 28} ${lamp.y - 30} L${lamp.x + 44} ${lamp.y + 34} Z`} fill="#3a3f78" />
          <ellipse cx={lamp.x} cy={lamp.y + 34} rx="44" ry="7" fill="#ffd9a0" opacity="0.8" />
          <ellipse cx={lamp.x} cy={floorY + 2} rx="30" ry="6" fill="#1c2756" />
        </g>
      ) : null}
      {/* sideboard under the monitor */}
      <rect x={L.pc.x - 40} y={floorY - 64} width={L.pc.w + 80} height="64" rx="6" fill="url(#fl-wood)" />
      {[0.33, 0.66].map((f) => (
        <rect key={f} x={L.pc.x - 40 + f * (L.pc.w + 80)} y={floorY - 56} width="1.5" height="52" fill="#2a3670" />
      ))}
      <ellipse cx={L.pc.x + L.pc.w / 2} cy={floorY + 40} rx={L.pc.w * 0.5} ry="18" fill="#000" opacity="0.35" />
      {/* the glow the screen throws on the wall */}
      <ellipse cx={L.pc.x + L.pc.w / 2} cy={L.pc.y + L.pc.h / 2} rx={L.pc.w * 0.8} ry={L.pc.h * 0.95} fill="url(#fl-screenglow)" />
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
          <rect x="60" y="980" width="780" height="300" rx="40" fill="url(#fl-sofa)" />
          <rect x="90" y="1120" width="720" height="110" rx="30" fill="#162052" />
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
  const from = { x: L.phone.x + L.phone.w / 2, y: L.phone.y + 24 };
  const to = wide ? { x: L.pc.x + L.pc.w + 14, y: L.pc.y + L.pc.h * 0.45 } : { x: L.pc.x + L.pc.w / 2, y: L.pc.y + L.pc.h + 48 };
  const ctrl = wide ? { x: (from.x + to.x) / 2 + 40, y: Math.min(from.y, to.y) - 120 } : { x: from.x + 220, y: (from.y + to.y) / 2 };
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

function Captions({ L, t }: { L: StageLayout; t: number }) {
  const scene = sceneAt(t);
  const c = L.caption;
  const inAt = scene.n === 1 ? 3_200 : scene.start + 900;
  const visible = scene.id === "end" ? 0 : Math.min(seg(t, inAt, 600), 1 - seg(t, scene.end - 320, 320));
  return (
    <div className="absolute flex flex-col justify-end" style={{ left: c.x, top: c.y, width: c.w, height: c.h, alignItems: c.align === "center" ? "center" : "flex-start", textAlign: c.align }}>
      {/* the live region keeps the words even while the band is visually faded */}
      <div className="film-caption" role="status" aria-live="polite" aria-atomic="true" style={{ opacity: visible, transform: `translateY(${lerp(14, 0, visible)}px)`, fontSize: c.size }}>
        <p className="font-semibold leading-[1.2] tracking-[-0.015em] text-white [text-wrap:balance]">{scene.caption}</p>
        <p className="mt-[0.55em] inline-flex items-center gap-[0.45em] rounded-full border border-white/12 bg-white/[0.07] px-[0.9em] py-[0.42em] text-[0.5em] font-semibold text-[#dfe6f8]">
          <span className={`h-[0.55em] w-[0.55em] rounded-full ${TONE_DOT[scene.status.tone]}`} />
          {scene.status.label}
        </p>
      </div>
    </div>
  );
}

/* ── scene 5: the plans ──────────────────────────────────────────────────────────────────────────────── */

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
        return (
          <div key={plan.id} className="absolute rounded-[28px] border border-white/12 bg-[linear-gradient(180deg,rgb(255_255_255/0.09),rgb(255_255_255/0.04))] text-white" style={{ left: r.x, top: r.y, width: r.w, height: r.h, opacity: e, transform: `translateY(${lerp(44, 0, e)}px) scale(${lerp(0.97, 1, e)})`, boxShadow: `0 1px 0 rgb(255 255 255 / 0.08) inset, 0 40px 80px -40px rgb(0 0 0 / 0.8), 0 0 0 ${2 * glow}px rgb(66 129 250 / ${0.9 * glow}), 0 30px 90px -20px rgb(1 66 251 / ${0.75 * glow})` }}>
            {pro ? <div className="pointer-events-none absolute inset-0 rounded-[28px] bg-[radial-gradient(60%_50%_at_50%_0%,rgb(1_66_251/0.5),transparent_70%)]" style={{ opacity: glow }} /> : null}
            <div className={`relative flex h-full flex-col ${wide ? "p-9" : "p-8"}`}>
              <div className={wide ? "" : "flex items-center justify-between"}>
                <p className="whitespace-nowrap text-[30px] font-semibold tracking-[-0.02em]">{plan.name}</p>
                <p className={`flex items-baseline gap-2.5 ${wide ? "mt-4" : ""}`}>
                  <span className="font-display text-[52px] font-bold leading-none tracking-[-0.04em]">{plan.price}</span>
                  <span className="text-[18px] text-[#b2bfdd]">{plan.per}</span>
                </p>
              </div>
              <p className={wide ? "mt-5" : "mt-4"}>
                <span className="inline-flex items-center gap-2 rounded-full border border-white/14 bg-white/8 px-3.5 py-1.5 text-[16px] font-semibold text-[#dfe6f8]">
                  <span className={`h-2 w-2 rounded-full ${TONE_DOT[plan.tone]}`} />
                  {plan.status}
                </span>
              </p>
              <ul className={`mt-6 ${wide ? "space-y-2.5" : "grid grid-cols-2 gap-x-6 gap-y-2.5"} text-[18px]`}>
                {plan.lines.map((line) => (
                  <li key={line} className="flex items-center gap-3">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-success/15 text-success">
                      <Icon name="check" size={14} strokeWidth={2.75} />
                    </span>
                    {line}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        );
      })}
      <p className="absolute text-[20px] text-[#b2bfdd]" style={{ left: L.cards[0]!.x, width: wide ? L.cards[2]!.x + L.cards[2]!.w - L.cards[0]!.x : L.cards[0]!.w, top: wide ? 640 : 1206, textAlign: "center", opacity: s.footnote, transform: `translateY(${lerp(10, 0, s.footnote)}px)` }}>
        Everyday commands (“pause”, “volume 40”, “open Spotify”) are free and unlimited on every plan.
      </p>
    </>
  );
}

/* ── scene 6: the end card ───────────────────────────────────────────────────────────────────────────── */

function EndCard({ L, t }: { L: StageLayout; t: number }) {
  const s = stageAt(t);
  if (s.lockup <= 0) return null;
  const { cx, cy, w } = L.lockup;
  const h = w * (408 / 1345);
  return (
    <div className="absolute inset-0 text-white">
      <div className="absolute" style={{ left: cx - w / 2, top: cy - h / 2, width: w, opacity: s.lockup, transform: `scale(${lerp(0.9, 1, s.lockup)})`, filter: `drop-shadow(0 0 ${40 * s.lockup}px rgb(66 129 250 / 0.45))` }}>
        <Wordmark title={null} className="h-auto w-full text-white" />
      </div>
      <p className="absolute w-full text-center text-[52px] font-bold tracking-[-0.03em]" style={{ top: cy + h / 2 + 50, opacity: s.tagline, transform: `translateY(${lerp(14, 0, s.tagline)}px)` }}>
        Your PC, in your hand.
      </p>
      <div className="absolute flex w-full items-center justify-center gap-4" style={{ top: cy + h / 2 + 150, opacity: s.buttons, transform: `translateY(${lerp(14, 0, s.buttons)}px)` }}>
        <span className="dome-primary inline-flex h-[64px] items-center gap-2 rounded-full px-9 text-[22px] font-semibold">
          Open the app
          <Icon name="arrow" size={22} />
        </span>
        <span className="inline-flex h-[64px] items-center gap-1.5 rounded-full border border-white/25 px-9 text-[22px] font-semibold">
          Compare plans
          <Icon name="chevron" size={20} />
        </span>
      </div>
      <p className="absolute w-full text-center text-[20px] text-[#8f9dc4]" style={{ top: cy + h / 2 + 250, opacity: s.concept }}>
        {CONCEPT_NOTE}
      </p>
    </div>
  );
}

/* ── the stage ───────────────────────────────────────────────────────────────────────────────────────── */

export interface StageProps {
  t: number;
  layout: StageLayout;
}

/** The stage at time t on a fixed canvas: wrap it in `.film-frame` (film.css) to scale it to any width. */
export const Stage = memo(function Stage({ t, layout: L }: StageProps) {
  const s = stageAt(t);
  const phone = phoneAt(t);
  const pc = pcAt(t);
  const hand = handAt(t);
  const rise = lerp(70, 0, phone.entrance);
  // a gentle float once the phone has arrived, so it reads as held rather than pinned
  const float = phone.entrance >= 1 ? Math.sin(t / 1900) * 5 : 0;
  return (
    <div className="film-canvas dome-dark" style={{ width: L.w, height: L.h, fontSize: 16 }} data-film-time={Math.round(t)}>
      <div className="absolute inset-0" style={{ opacity: s.room }}>
        <Room L={L} />
      </div>
      <div className="absolute inset-0" style={{ opacity: s.devices, transform: `scale(${lerp(0.96, 1, s.devices)})`, transformOrigin: "50% 60%" }}>
        <Monitor L={L} t={t} />
        <Link L={L} t={t} />
        <div className="absolute" style={{ left: L.phone.x, top: L.phone.y, opacity: phone.entrance, transform: `translateY(${rise + float}px) rotate(${lerp(3, 0, phone.entrance)}deg)`, transformOrigin: "50% 100%" }}>
          <Phone t={t} view={phone} pc={pc} hand={hand} width={L.phone.w} bezel={L.bezel} />
        </div>
      </div>
      {s.veil > 0 ? <div className="absolute inset-0 bg-[radial-gradient(70%_50%_at_50%_-10%,rgb(1_66_251/0.5),transparent_70%),linear-gradient(180deg,#02113f,#001441)]" style={{ opacity: s.veil }} /> : null}
      <PlanCards L={L} t={t} />
      <EndCard L={L} t={t} />
      <Captions L={L} t={t} />
      {/* the DoMe mark, quietly, in the corner */}
      <span className="absolute flex items-center gap-2 text-[15px] font-semibold text-white/55" style={{ left: 36, top: 30, opacity: s.lockup > 0 ? 0 : 1 }}>
        <FilmIcon name="cast" size={16} />
        DoMe Remote
      </span>
    </div>
  );
});
