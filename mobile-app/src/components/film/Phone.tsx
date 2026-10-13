/**
 * The phone in the film: the real app's look (the brand mark, the Midnight cards, the tab bar with its coral dot),
 * authored at the real screen size (390 × 844) and scaled into the drawn handset. Pages: Home, Ask, Screen view
 * (coming) and Apps. The PC's mirror is a layer of its own so it can shrink into the corner while the page
 * underneath changes. Everything is positioned explicitly so the fingertip lands where it should at any size.
 */
import { memo, type ReactNode } from "react";

import { BrandMark, Wordmark } from "../brand.tsx";
import { Icon, type IconName } from "../icons.tsx";
import { FilmIcon } from "./filmIcons.tsx";
import { PHONE_SCREEN } from "./layout.ts";
import { EPISODES, PcScreen } from "./Pc.tsx";
import { MIRROR, TOUCH, type HandView, type PcView, type PhonePage, type PhoneView } from "./script.ts";
import { lerp } from "./timeline.ts";

type Box = { x: number; y: number; w?: number; h?: number };

/** An absolutely positioned block in phone-screen pixels. */
function At({ x, y, w, h, className = "", style, children }: Box & { className?: string; style?: React.CSSProperties; children?: ReactNode }) {
  return (
    <div className={`absolute ${className}`} style={{ left: x, top: y, width: w, height: h, ...style }}>
      {children}
    </div>
  );
}

const CARD = "rounded-[22px] border border-white/10 bg-white/[0.07] shadow-[0_1px_0_rgb(255_255_255/0.06)_inset,0_18px_40px_-22px_rgb(0_0_0/0.65)]";

function Pill({ tone, children }: { tone: "success" | "neutral" | "coral" | "info"; children: ReactNode }) {
  const look = tone === "success" ? "bg-success/15 text-success" : tone === "coral" ? "bg-white/10 text-text" : tone === "info" ? "bg-accent/15 text-accent" : "bg-white/10 text-text-muted";
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-[12px] font-semibold ${look}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${tone === "coral" ? "bg-coral" : "bg-current"}`} />
      {children}
    </span>
  );
}

function Well({ icon, size = 44, iconSize = 20, className = "" }: { icon: IconName; size?: number; iconSize?: number; className?: string }) {
  return (
    <span style={{ width: size, height: size }} className={`flex shrink-0 items-center justify-center rounded-2xl bg-accent/15 text-accent ${className}`}>
      <Icon name={icon} size={iconSize} />
    </span>
  );
}

function ButlerAvatar({ size = 30 }: { size?: number }) {
  return (
    <span style={{ width: size, height: size }} className="flex shrink-0 items-center justify-center rounded-full bg-[linear-gradient(145deg,#2a62ff,#001441)] text-white shadow-[0_6px_16px_-6px_rgb(1_66_251/0.7)]">
      <BrandMark className="w-[66%]" />
    </span>
  );
}

function StatusBar() {
  return (
    <>
      <At x={28} y={16} className="text-[15px] font-semibold tabular-nums text-text">
        21:42
      </At>
      <At x={300} y={19} className="flex items-center gap-1.5 text-text">
        <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <rect key={i} x={i * 4.5} y={9 - i * 3} width="3" height={3 + i * 3} rx="0.8" fill="currentColor" />
          ))}
        </svg>
        <svg width="26" height="12" viewBox="0 0 26 12" aria-hidden="true">
          <rect x="0.5" y="0.5" width="21" height="11" rx="3" fill="none" stroke="currentColor" strokeOpacity="0.5" />
          <rect x="2" y="2" width="16" height="8" rx="1.6" fill="currentColor" />
          <rect x="23" y="4" width="2" height="4" rx="1" fill="currentColor" fillOpacity="0.5" />
        </svg>
      </At>
    </>
  );
}

function PageTitle({ title, sub, chip }: { title: string; sub?: string; chip?: ReactNode }) {
  return (
    <>
      <At x={20} y={56} className="text-[28px] font-bold leading-none tracking-[-0.03em] text-text">
        {title}
      </At>
      {sub ? (
        <At x={20} y={92} className="text-[13px] text-text-muted">
          {sub}
        </At>
      ) : null}
      {chip ? (
        <At x={0} y={60} w={374} className="flex justify-end">
          {chip}
        </At>
      ) : null}
    </>
  );
}

const TABS: Array<[IconName, string]> = [
  ["home", "Home"],
  ["remote", "Remote"],
  ["pc", "Screen"],
  ["ask", "Ask"],
  ["more", "More"],
];

function TabBar({ active }: { active: string }) {
  return (
    <At x={0} y={772} w={390} h={72} className="grid grid-cols-5 border-t border-white/10 bg-[rgb(0_14_50/0.9)] px-1 pt-1.5">
      {TABS.map(([icon, label]) => {
        const on = label === active;
        return (
          <span key={label} className={`relative flex flex-col items-center gap-[3px] text-[11px] font-semibold tracking-[-0.01em] ${on ? "text-text" : "text-text-faint"}`}>
            <Icon name={icon} size={24} strokeWidth={on ? 2 : 1.75} className={on ? "text-accent" : ""} />
            {label}
            <span className={`absolute -bottom-2 h-1 w-1 rounded-full ${on ? "bg-coral" : "bg-transparent"}`} />
          </span>
        );
      })}
    </At>
  );
}

function PcRow({ y = 112 }: { y?: number }) {
  return (
    <At x={16} y={y} w={358} h={84} className={`${CARD} flex items-center gap-3 px-4`}>
      <Well icon="pc" size={48} iconSize={22} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[17px] font-semibold tracking-[-0.02em] text-text">Living Room PC</span>
        <span className="block truncate text-[12px] text-text-muted">Connected · state just now</span>
      </span>
      <Pill tone="success">Online</Pill>
    </At>
  );
}

/* ── Home ────────────────────────────────────────────────────────────────────────────────────────────── */

function HomePage({ view, pc }: { view: PhoneView; pc: PcView }) {
  const ep = EPISODES[pc.episode];
  const playing = !pc.paused;
  return (
    <>
      <At x={20} y={62} className="text-text">
        <Wordmark compact title={null} className="h-[24px] w-auto" />
      </At>
      <At x={262} y={58} w={108} h={38} className="flex items-center justify-center gap-1.5 rounded-full border border-white/12 text-[14px] font-semibold text-text-muted">
        <Icon name="devices" size={18} />
        Devices
      </At>
      <PcRow />
      <At x={16} y={208} w={358} h={226} className={CARD}>
        <At x={16} y={16} className="text-[12px] font-medium text-text-faint">
          YouTube · Edge
        </At>
        <At x={262} y={14} className="flex items-center gap-0.5 text-[14px] font-semibold text-accent">
          Remote
          <Icon name="chevron" size={16} />
        </At>
        <At x={16} y={44} w={60} h={60} className="flex items-center justify-center rounded-[18px] bg-[linear-gradient(140deg,#3a72ff_0%,#0142fb_45%,#001441_100%)] text-white shadow-[0_14px_30px_-14px_rgb(1_66_251/0.8)]">
          <Icon name="youtube" size={26} />
        </At>
        <At x={88} y={42} w={254} className="text-[17px] font-semibold leading-snug tracking-[-0.01em] text-text">
          {ep.title.replace(" — ", " · ")}
        </At>
        <At x={88} y={92}>
          <Pill tone={playing ? "success" : "neutral"}>{playing ? "Playing" : "Paused"}</Pill>
        </At>
        <At x={16} y={126} w={326} h={4} className="rounded-full bg-white/15">
          <div className="h-full rounded-full bg-white" style={{ width: `${pc.position * 100}%` }} />
        </At>
        <At x={16} y={136} w={326} className="flex justify-between text-[11px] tabular-nums text-text-faint">
          <span>{Math.floor((pc.position * 1900) / 60)}:{String(Math.round(pc.position * 1900) % 60).padStart(2, "0")}</span>
          <span>{ep.length}</span>
        </At>
        <At x={0} y={168} w={358} h={48} className="flex items-center justify-center gap-10">
          <Icon name="prev" size={22} className="text-text-muted" />
          <span className="dome-primary flex h-12 w-12 items-center justify-center rounded-full" style={{ transform: `scale(${view.pausePress})` }}>
            <Icon name={playing ? "pause" : "play"} size={20} />
          </span>
          <Icon name="next" size={22} className="text-text-muted" />
        </At>
      </At>
      <At x={16} y={448} w={358} h={78} className={CARD}>
        <At x={16} y={14} className="flex items-center gap-1.5 text-[13px] font-semibold text-text">
          <Icon name="volume" size={16} />
          Windows volume
        </At>
        <At x={300} y={14} w={42} className="text-right text-[13px] font-semibold tabular-nums text-text-muted">
          40%
        </At>
        <At x={16} y={46} w={326} h={6} className="rounded-full bg-white/15">
          <div className="relative h-full w-[40%] rounded-full bg-primary">
            <span className="absolute -right-2.5 -top-[7px] h-5 w-5 rounded-full bg-white shadow" />
          </div>
        </At>
      </At>
      {(
        [
          ["next", "Next video", 16, 540],
          ["back10", "Back 10 s", 199, 540],
          ["lock", "Lock Windows", 16, 606],
          ["sleep", "Sleep…", 199, 606],
        ] as Array<[IconName, string, number, number]>
      ).map(([icon, label, x, y]) => (
        <At key={label} x={x} y={y} w={175} h={58} className={`${CARD} flex items-center gap-2.5 px-3 text-[14px] font-semibold text-text`}>
          <Well icon={icon} size={36} iconSize={18} className="rounded-xl" />
          {label}
        </At>
      ))}
      {view.toast > 0 ? (
        <At x={16} y={690} w={358} h={60} className="flex items-center gap-3 rounded-2xl border border-white/10 bg-[rgb(11_33_86/0.92)] px-4 shadow-[0_20px_40px_-16px_rgb(0_0_0/0.6)]" style={{ opacity: view.toast, transform: `translateY(${lerp(16, 0, view.toast)}px)` }}>
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-success/20 text-success">
            <Icon name="check" size={16} strokeWidth={2.5} />
          </span>
          <span className="min-w-0">
            <span className="block text-[14px] font-semibold text-text">Paused</span>
            <span className="block text-[12px] text-text-muted">Done on Living Room PC</span>
          </span>
        </At>
      ) : null}
      <TabBar active="Home" />
    </>
  );
}

/* ── Ask ─────────────────────────────────────────────────────────────────────────────────────────────── */

function Bubble({ side, children, y, x = 16, w = 358 }: { side: "owner" | "butler"; children: ReactNode; y: number; x?: number; w?: number }) {
  if (side === "owner") {
    return (
      <At x={x} y={y} w={w} className="flex justify-end">
        <p className="dome-primary max-w-[82%] rounded-[1.25rem] rounded-br-md px-4 py-2.5 text-[15px] leading-[1.4]">{children}</p>
      </At>
    );
  }
  return (
    <At x={x} y={y} w={w} className="flex items-end gap-2">
      <ButlerAvatar />
      <div className="max-w-[82%] rounded-[1.25rem] rounded-bl-md border border-white/10 bg-[rgb(255_255_255/0.08)] px-4 py-2.5 text-[15px] leading-[1.4] text-text">{children}</div>
    </At>
  );
}

function AskPage({ view }: { view: PhoneView }) {
  const label = view.listening ? "Listening… tap to stop" : view.thinking ? "Thinking…" : view.plan?.running && !view.plan.steps.every((s) => s.done) ? "Working…" : "Tap to talk";
  return (
    <>
      <At x={20} y={56} className="flex items-center gap-3">
        <ButlerAvatar size={40} />
        <span>
          <span className="block text-[26px] font-bold leading-none tracking-[-0.03em] text-text">Ask</span>
          <span className="mt-1 block text-[13px] text-text-muted">DoMe, your butler</span>
        </span>
      </At>
      <At x={16} y={112} className="flex items-center gap-2 text-[13px] text-text-muted">
        <Icon name="pc" size={16} />
        Living Room PC
        <span className="h-1.5 w-1.5 rounded-full bg-success" />
        Online
      </At>
      {view.ownerText ? (
        <Bubble side="owner" y={150}>
          {view.ownerText}
          {view.listening ? <span className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] bg-white/80" /> : null}
        </Bubble>
      ) : null}
      {view.butlerText ? (
        <Bubble side="butler" y={226}>
          {view.butlerText}
          {view.butlerText.endsWith(".") ? <span className="mt-1 block text-[11px] text-text-faint">Counts as 1 AI request</span> : null}
        </Bubble>
      ) : null}
      {view.plan ? (
        <At x={54} y={320} w={320} h={170} className={`${CARD} rounded-[18px] px-4 py-3.5`} style={{ opacity: 1 }}>
          <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-text-faint">
            <Icon name="pc" size={13} />
            On Living Room PC:
          </p>
          <ol className="mt-2 space-y-1.5 text-[15px] text-text">
            {view.plan.steps.map((s, i) => (
              <li key={s.label} className="flex items-center gap-2">
                <span className="w-4 text-text-faint">{i + 1}.</span>
                <strong>{s.label}</strong>
                {s.done ? (
                  <span className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-success">
                    <Icon name="check" size={14} strokeWidth={2.5} />
                    Done
                  </span>
                ) : view.plan?.running ? (
                  <span className="ml-auto text-[13px] text-text-muted">Sending…</span>
                ) : null}
              </li>
            ))}
          </ol>
          {view.plan.steps.every((s) => s.done) ? (
            <p className="absolute inset-x-4 bottom-4 flex h-12 items-center gap-2 text-[14px] font-semibold text-success">
              <Icon name="check" size={16} strokeWidth={2.5} />
              Done on Living Room PC
            </p>
          ) : (
            <div className="absolute inset-x-4 bottom-4 grid grid-cols-2 gap-2">
              <span className={`flex h-12 items-center justify-center rounded-[14px] text-[15px] font-semibold ${view.plan.running ? "bg-white/10 text-text-muted" : "dome-primary"}`} style={{ transform: `scale(${view.plan.runPress})` }}>
                {view.plan.running ? "Running" : "Run"}
              </span>
              <span className="flex h-12 items-center justify-center rounded-[14px] border border-white/12 text-[15px] font-semibold text-text">Cancel</span>
            </div>
          )}
        </At>
      ) : null}
      <At x={0} y={612} w={390} className="text-center text-[12px] text-text-faint">
        AI assistant is on · What DoMe can see
      </At>
      <At x={16} y={640} w={358} h={120}>
        <At x={36} y={12} w={48} h={48} className="flex items-center justify-center rounded-full border border-white/12 bg-white/8 text-text">
          <Icon name="keyboard" size={22} />
        </At>
        <At x={36} y={66} w={48} className="text-center text-[11px] font-medium text-text-muted">
          Type
        </At>
        <At x={143} y={0} w={72} h={72}>
          {view.listening ? (
            <>
              <span className="dome-ring absolute inset-0 rounded-full border-2 border-coral" />
              <span className="dome-ring absolute inset-0 rounded-full border-2 border-coral [animation-delay:0.9s]" />
            </>
          ) : null}
          <span className={`dome-primary relative flex h-[72px] w-[72px] items-center justify-center rounded-full ${view.listening ? "ring-2 ring-coral ring-offset-2 ring-offset-navy" : ""}`} style={{ transform: `scale(${view.micPress})` }}>
            <Icon name="mic" size={30} strokeWidth={1.9} />
            {view.listening ? <span className="absolute right-1.5 top-1.5 h-3 w-3 rounded-full border-2 border-white bg-coral" /> : null}
          </span>
        </At>
        <At x={80} y={80} w={198} className="text-center text-[13px] font-medium text-text-muted">
          {label}
        </At>
      </At>
      <TabBar active="Ask" />
    </>
  );
}

/* ── Screen view (coming) ────────────────────────────────────────────────────────────────────────────── */

function ScreenPage({ view }: { view: PhoneView }) {
  return (
    <>
      <PageTitle
        title="Screen view"
        sub="Living Room PC · Online"
        chip={
          <Pill tone="coral">
            <span className="text-text-muted">Coming</span>
          </Pill>
        }
      />
      <At x={16} y={334} w={358} className="flex gap-2">
        {["Tap to click", "Two fingers to scroll", "Type"].map((h) => (
          <span key={h} className="rounded-full border border-white/10 bg-white/6 px-3 py-1.5 text-[12px] font-medium text-text-muted">
            {h}
          </span>
        ))}
      </At>
      {view.spoken ? (
        <At x={16} y={388} w={358} h={52} className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/6 px-4">
          <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-coral/15 text-coral">
            <Icon name="mic" size={18} />
          </span>
          <span className="min-w-0">
            <span className="block text-[11px] font-semibold uppercase tracking-[0.1em] text-text-faint">You said</span>
            <span className="block text-[15px] font-semibold text-text">“{view.spoken}”</span>
          </span>
        </At>
      ) : null}
      {view.clickingText ? (
        <Bubble side="butler" y={456}>
          {view.clickingText}
        </Bubble>
      ) : null}
      <TabBar active="Screen" />
    </>
  );
}

/* ── Apps ────────────────────────────────────────────────────────────────────────────────────────────── */

function AppRow({ y, icon, name, detail, action, press = 1, iconEl }: { y: number; icon?: IconName; iconEl?: ReactNode; name: string; detail: string; action: string; press?: number }) {
  return (
    <At x={16} y={y} w={358} h={76} className={`${CARD} flex items-center gap-3 px-4`}>
      {iconEl ?? <Well icon={icon ?? "apps"} />}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[16px] font-semibold text-text">{name}</span>
        <span className="block truncate text-[12px] text-text-muted">{detail}</span>
      </span>
      <span className="flex h-10 items-center justify-center rounded-full border border-white/12 bg-white/8 px-4 text-[14px] font-semibold text-text" style={{ transform: `scale(${press})` }}>
        {action}
      </span>
    </At>
  );
}

function Dpad({ pressed }: { pressed: "right" | null }) {
  const key = (name: string, x: number, y: number, rotate: number) => (
    <span key={name} className={`absolute flex h-11 w-11 items-center justify-center rounded-full ${pressed === name ? "bg-primary text-white" : "text-text"}`} style={{ left: x - 22, top: y - 22, transform: `rotate(${rotate}deg) scale(${pressed === name ? 0.92 : 1})` }}>
      <Icon name="chevron" size={20} strokeWidth={2.25} />
    </span>
  );
  return (
    <div className="absolute h-[150px] w-[150px] rounded-full border border-white/12 bg-white/6" style={{ left: 104, top: 49 }}>
      {key("up", 75, 26, -90)}
      {key("right", 124, 75, 0)}
      {key("down", 75, 124, 90)}
      {key("left", 26, 75, 180)}
      <span className="absolute left-1/2 top-1/2 flex h-12 w-12 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white/12 text-[13px] font-bold text-text">OK</span>
    </div>
  );
}

function AppsPage({ view }: { view: PhoneView }) {
  return (
    <>
      <PageTitle title="Apps" sub="Approved on Living Room PC" />
      <AppRow y={120} name="ZStream" detail="TV and films" action="Open" press={view.zPress} iconEl={<span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[linear-gradient(145deg,#f29d6a,#4b2a6e)] text-white"><FilmIcon name="tv" size={22} /></span>} />
      <AppRow y={206} name="Edge" detail="Night Drive · Episode 4 · paused" action="Bring to front" iconEl={<span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent/15 text-accent"><span className="h-5 w-5 rounded-full border-[3px] border-current" /></span>} />
      <AppRow y={292} icon="music" name="Spotify" detail="Not running" action="Open" />
      {view.tvRemote > 0 ? (
        <At x={16} y={380} w={358} h={216} className={`${CARD} overflow-hidden`} style={{ opacity: view.tvRemote, transform: `translateY(${lerp(40, 0, view.tvRemote)}px)` }}>
          <At x={16} y={14} className="text-[15px] font-semibold text-text">
            ZStream · TV remote
          </At>
          <At x={16} y={36} className="text-[12px] text-text-muted">
            Arrow keys and OK on the PC
          </At>
          <Dpad pressed={view.dpadPress} />
          <At x={20} y={104} w={40} h={40} className="flex items-center justify-center rounded-full border border-white/12 text-text">
            <FilmIcon name="back" size={18} />
          </At>
          <At x={298} y={104} w={40} h={40} className="flex items-center justify-center rounded-full border border-white/12 text-text">
            <Icon name="home" size={18} />
          </At>
        </At>
      ) : null}
      {view.sound > 0 ? (
        <At x={16} y={608} w={358} h={56} className={`${CARD} flex items-center gap-3 px-4`} style={{ opacity: view.sound, transform: `translateY(${lerp(16, 0, view.sound)}px)` }}>
          <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${view.muted ? "bg-white/10 text-text-muted" : "bg-accent/15 text-accent"}`}>
            <FilmIcon name={view.muted ? "speakerMute" : "speaker"} size={20} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[14px] font-semibold text-text">PC audio</span>
            <span className="block text-[12px] text-text-muted">{view.muted ? "Muted on this phone" : "Playing on this phone"}</span>
          </span>
          <span className="flex items-end gap-[3px]" aria-hidden="true">
            {[10, 18, 14, 22, 12].map((h, i) => (
              <span key={i} className={`w-[3px] rounded-full ${view.muted ? "bg-white/20" : "bg-accent"}`} style={{ height: view.muted ? 6 : h }} />
            ))}
          </span>
          <span className={`ml-2 flex h-10 items-center justify-center rounded-full px-4 text-[14px] font-semibold ${view.muted ? "bg-white/8 text-text" : "bg-white/8 text-text"}`} style={{ transform: `scale(${view.mutePress})` }}>
            {view.muted ? "Unmute" : "Mute"}
          </span>
        </At>
      ) : null}
      <TabBar active="More" />
    </>
  );
}

/* ── the mirror: the PC's screen inside the phone ────────────────────────────────────────────────────── */

function Mirror({ view, pc }: { view: PhoneView; pc: PcView }) {
  if (view.mirror <= 0) return null;
  const f = MIRROR.full;
  const m = MIRROR.mini;
  const p = view.mini;
  const x = lerp(f.x, m.x, p);
  const y = lerp(f.y, m.y, p);
  const w = lerp(f.w, m.w, p);
  const h = lerp(f.h, m.h, p);
  const live = view.mirror >= 1;
  const hold = view.hold;
  return (
    <div className="absolute overflow-hidden rounded-[16px] bg-navy shadow-[0_24px_50px_-20px_rgb(0_0_0/0.8)] ring-1 ring-white/15" style={{ left: x, top: y, width: w, height: h }}>
      <div style={{ opacity: live ? 1 : lerp(0.2, 1, view.mirror) }}>
        <PcScreen view={pc} width={w} />
      </div>
      {!live ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-[12px] font-medium text-text-muted">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-white/15 border-t-accent" />
          Connecting to Living Room PC…
        </div>
      ) : null}
      {p < 1 ? (
        <span className="absolute right-3 top-2.5 inline-flex items-center gap-1.5 rounded-full bg-black/45 px-2 py-0.5 text-[11px] font-semibold text-white backdrop-blur-sm" style={{ opacity: 1 - p }}>
          <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-success" : "bg-white/40"}`} />
          Live
        </span>
      ) : null}
      {hold > 0 ? (
        <span className="pointer-events-none absolute rounded-full border-2 border-sky" style={{ left: TOUCH.subscribe.x - f.x - 32, top: TOUCH.subscribe.y - f.y - 32, width: 64, height: 64, opacity: hold * 0.9, transform: `scale(${lerp(0.5, 1, hold)})`, boxShadow: "0 0 0 8px rgb(66 129 250 / 0.25)" }} />
      ) : null}
      {view.nameTag > 0 ? (
        <span className="absolute rounded-md bg-sky px-2 py-1 text-[12px] font-semibold text-white shadow-[0_8px_20px_-8px_rgb(66_129_250/0.9)]" style={{ left: TOUCH.subscribe.x - f.x + 22, top: TOUCH.subscribe.y - f.y - 44, opacity: view.nameTag, transform: `translateY(${lerp(6, 0, view.nameTag)}px)` }}>
          Subscribe
        </span>
      ) : null}
    </div>
  );
}

/* ── the fingertip ───────────────────────────────────────────────────────────────────────────────────── */

function Fingertip({ hand }: { hand: HandView }) {
  if (hand.visible <= 0) return null;
  const tip = (dx: number) => (
    <span className="absolute h-14 w-14 rounded-full border border-white/70 bg-white/35 shadow-[0_10px_30px_-10px_rgb(0_0_0/0.7)] backdrop-blur-[2px]" style={{ left: hand.x - 28 + dx, top: hand.y - 28 }}>
      <span className="absolute inset-[9px] rounded-full bg-white/70" />
    </span>
  );
  return (
    <div className="pointer-events-none absolute inset-0" style={{ opacity: hand.visible }}>
      {hand.ripple > 0 && hand.ripple < 1 ? <span className="absolute rounded-full border-2 border-white" style={{ left: hand.x - 28, top: hand.y - 28, width: 56, height: 56, transform: `scale(${lerp(1, 2.4, hand.ripple)})`, opacity: 1 - hand.ripple }} /> : null}
      {tip(0)}
      {hand.second ? tip(52) : null}
    </div>
  );
}

/* ── the handset ─────────────────────────────────────────────────────────────────────────────────────── */

function previousPage(page: PhonePage, t: number): PhonePage {
  if (page === "ask") return "home";
  if (page === "screen") return t >= 40_000 ? "apps" : "ask";
  if (page === "apps") return "screen";
  return "home";
}

function Page({ page, view, pc }: { page: PhonePage; view: PhoneView; pc: PcView }) {
  if (page === "home") return <HomePage view={view} pc={pc} />;
  if (page === "ask") return <AskPage view={view} />;
  if (page === "screen") return <ScreenPage view={view} />;
  return <AppsPage view={view} />;
}

export interface PhoneProps {
  t: number;
  view: PhoneView;
  pc: PcView;
  hand: HandView;
  /** The handset's body width in canvas px; the screen scales to fit inside the bezel. */
  width: number;
  bezel: number;
}

export const Phone = memo(function Phone({ t, view, pc, hand, width, bezel }: PhoneProps) {
  const scale = (width - bezel * 2) / PHONE_SCREEN.w;
  const screenH = PHONE_SCREEN.h * scale;
  const prev = previousPage(view.page, t);
  const fading = view.pageIn < 1;
  const slide = view.page === "apps";
  return (
    <div className="dome-dark relative" style={{ width, height: screenH + bezel * 2 }}>
      <div className="absolute inset-0 rounded-[46px] bg-[linear-gradient(160deg,#2b3a66,#0a1230_45%,#1b2a52)] shadow-[0_50px_80px_-36px_rgb(0_10_40/0.8),0_0_0_1px_rgb(255_255_255/0.1)_inset]" />
      <div className="absolute overflow-hidden rounded-[38px] bg-navy" style={{ left: bezel, top: bezel, width: width - bezel * 2, height: screenH }}>
        <div className="absolute left-0 top-0 origin-top-left" style={{ width: PHONE_SCREEN.w, height: PHONE_SCREEN.h, transform: `scale(${scale})` }}>
          <div className="absolute inset-0 bg-[radial-gradient(28rem_18rem_at_50%_-6rem,rgb(1_66_251/0.5),transparent_70%)]" />
          {fading ? (
            <div className="absolute inset-0" style={{ opacity: 1 - view.pageIn, transform: slide ? `translateX(${lerp(0, -40, view.pageIn)}px)` : `scale(${lerp(1, 0.98, view.pageIn)})` }}>
              <Page page={prev} view={view} pc={pc} />
            </div>
          ) : null}
          <div className="absolute inset-0" style={{ opacity: view.pageIn, transform: slide ? `translateX(${lerp(390, 0, view.pageIn)}px)` : undefined }}>
            <Page page={view.page} view={view} pc={pc} />
          </div>
          <Mirror view={view} pc={pc} />
          <StatusBar />
          <div className="absolute left-1/2 top-[11px] h-[30px] w-[100px] -translate-x-1/2 rounded-full bg-black" />
          <Fingertip hand={hand} />
        </div>
      </div>
    </div>
  );
});
