/**
 * The living-room PC's screen, authored at 1280 × 720 and scaled into its monitor (or into the phone's mirror):
 * a Windows-like desktop with a taskbar, a browser window playing "Night Drive" (a fictional series), the same
 * player full screen, a ten-foot TV app ("ZStream", one of the approved apps in the design preview), and the
 * small reactions the film needs (pause glyph, "More episodes" shelf, focus ring, Subscribe highlight, mute).
 */
import { memo } from "react";

import { BrandMark } from "../brand.tsx";
import { Icon } from "../icons.tsx";
import { FilmIcon } from "./filmIcons.tsx";
import { PC_SCREEN } from "./layout.ts";
import { EPISODES, type PcView } from "./script.ts";
import { lerp } from "./timeline.ts";

/** Playback time as m:ss. */
function clock(position: number, totalSeconds: number): string {
  const s = Math.round(position * totalSeconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const LENGTH_S = { 3: 1900, 4: 1752 } as const;

/** The episode itself: a night (or first-light) drive, drawn as vector art; the lane markers move while it plays. */
function NightDrive({ episode, position, className = "" }: { episode: 3 | 4; position: number; className?: string }) {
  const dawn = episode === 4;
  const offset = (position * 1400) % 1;
  const markers = Array.from({ length: 6 }, (_, i) => {
    const s = ((i / 6 + offset) % 1) ** 2.2;
    const y = 300 + 420 * s;
    const w = 6 + 60 * s;
    const h = 8 + 70 * s;
    return <rect key={i} x={640 - w / 2} y={y} width={w} height={h} rx={w / 3} fill="#f6e7b8" opacity={0.25 + 0.7 * s} />;
  });
  return (
    <svg viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid slice" className={`block h-full w-full ${className}`} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={`sky-${episode}`} x1="0" y1="0" x2="0" y2="1">
          {dawn ? (
            <>
              <stop offset="0" stopColor="#0d2a66" />
              <stop offset="0.55" stopColor="#3a4f9a" />
              <stop offset="1" stopColor="#f2a27a" />
            </>
          ) : (
            <>
              <stop offset="0" stopColor="#050c2a" />
              <stop offset="0.6" stopColor="#101a4f" />
              <stop offset="1" stopColor="#2c2b6d" />
            </>
          )}
        </linearGradient>
        <radialGradient id={`glow-${episode}`} cx="0.5" cy="1" r="0.6">
          <stop offset="0" stopColor={dawn ? "#ffd9a8" : "#ffe9a3"} stopOpacity="0.55" />
          <stop offset="1" stopColor="#000" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="1280" height="720" fill={`url(#sky-${episode})`} />
      {dawn ? <circle cx="880" cy="290" r="54" fill="#ffd2a0" opacity="0.9" /> : <circle cx="980" cy="120" r="34" fill="#f3f0dd" opacity="0.9" />}
      {dawn ? null : [120, 260, 410, 560, 700, 1090, 1180].map((x, i) => <circle key={x} cx={x} cy={40 + ((i * 53) % 150)} r={1.6 + (i % 3) * 0.5} fill="#fff" opacity="0.8" />)}
      {/* skyline */}
      {[
        [0, 230, 90],
        [90, 260, 60],
        [150, 200, 110],
        [260, 250, 70],
        [330, 180, 80],
        [410, 240, 120],
        [530, 270, 60],
        [690, 265, 90],
        [780, 215, 70],
        [850, 250, 130],
        [980, 230, 90],
        [1070, 190, 80],
        [1150, 245, 130],
      ].map(([x, y, w]) => (
        <rect key={x} x={x} y={y} width={w} height={310 - y!} fill={dawn ? "#2a2f6a" : "#0a1038"} />
      ))}
      {[180, 352, 465, 690, 890, 1010, 1190].map((x, i) => (
        <rect key={x} x={x} y={225 + (i % 4) * 14} width="6" height="4" fill="#ffe08a" opacity={dawn ? 0.5 : 0.9} />
      ))}
      <rect x="0" y="300" width="1280" height="420" fill={dawn ? "#2d2f4a" : "#0b0e24"} />
      <polygon points="640,300 1280,720 0,720" fill={dawn ? "#3a3c58" : "#1a1d36"} />
      <polygon points="640,300 1280,720 0,720" fill="url(#glow-${episode})" />
      {markers}
      <path d="M0 720 L0 640 Q640 560 1280 640 L1280 720 Z" fill={dawn ? "#141a3a" : "#06091c"} />
    </svg>
  );
}

/** A gradient poster tile for shelves and the TV app. */
function Poster({ i, title, w, h, focus = 0 }: { i: number; title: string; w: number; h: number; focus?: number }) {
  const hues = [
    ["#2a62ff", "#001441"],
    ["#f29d6a", "#4b2a6e"],
    ["#30c6a6", "#063a5a"],
    ["#ff8a8a", "#3b1058"],
    ["#86abff", "#1b2a52"],
    ["#ffc857", "#5a2a0e"],
  ];
  const [a, b] = hues[i % hues.length]!;
  return (
    <div className="relative shrink-0 overflow-hidden rounded-[14px]" style={{ width: w, height: h, background: `linear-gradient(135deg, ${a}, ${b})`, boxShadow: focus > 0 ? `0 0 0 ${4 * focus}px #fff, 0 18px 40px -16px rgb(0 0 0 / .7)` : "0 12px 30px -16px rgb(0 0 0 / .6)", transform: `scale(${1 + 0.05 * focus})` }}>
      <div className="absolute inset-x-0 bottom-0 bg-[linear-gradient(180deg,transparent,rgb(0_8_30/0.78))] px-3 pb-2 pt-6 text-[15px] font-semibold text-white">{title}</div>
    </div>
  );
}

function Taskbar() {
  return (
    <div className="absolute inset-x-0 bottom-0 flex h-12 items-center border-t border-white/10 bg-[rgb(10_22_60/0.82)] px-4 text-white/85">
      <div className="absolute left-1/2 flex -translate-x-1/2 items-center gap-3">
        <span className="grid h-8 w-8 grid-cols-2 gap-[3px] rounded-lg p-[7px]">
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className="rounded-[2px] bg-[#86abff]" />
          ))}
        </span>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg">
          <Icon name="search" size={18} />
        </span>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10">
          <span className="h-4 w-4 rounded-full border-[3px] border-[#4fc3ff]" />
        </span>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg">
          <FilmIcon name="tv" size={18} />
        </span>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg">
          <Icon name="music" size={18} />
        </span>
      </div>
      <div className="ml-auto flex items-center gap-3 text-[13px]">
        <span className="flex h-6 w-6 items-center justify-center text-[#86abff]">
          <BrandMark className="w-5" />
        </span>
        <Icon name="volume" size={16} />
        <span className="tabular-nums">21:42</span>
      </div>
    </div>
  );
}

function UpNext() {
  const items = ["Episode 5: Tunnel", "Episode 6: The Bridge", "Episode 7: Coast Road", "Episode 8: Home"];
  return (
    <div className="absolute right-6 top-6 w-[352px]">
      <p className="text-[13px] font-semibold uppercase tracking-[0.12em] text-white/55">Up next</p>
      <ul className="mt-3 space-y-2.5">
        {items.map((title, i) => (
          <li key={title} className="flex items-center gap-3">
            <Poster i={i + 2} title="" w={128} h={72} />
            <span className="min-w-0">
              <span className="block truncate text-[15px] font-semibold text-white">{title}</span>
              <span className="block text-[13px] text-white/55">Night Drive · 29 min</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The video page in a window: tab strip, address bar, the video, its title, the channel row with Subscribe. */
function BrowserPage({ view }: { view: PcView }) {
  const ep = EPISODES[view.episode];
  const ring = view.highlight;
  return (
    <div className="absolute left-8 top-6 h-[648px] w-[1216px] overflow-hidden rounded-[12px] bg-[#121a3a] shadow-[0_30px_60px_-30px_rgb(0_0_0/0.8)] ring-1 ring-white/12">
      <div className="flex h-10 items-end gap-2 bg-[#0c1330] px-3">
        <div className="flex h-8 items-center gap-2 rounded-t-lg bg-[#1a2450] px-3 text-[13px] text-white/90">
          <span className="h-3.5 w-3.5 rounded-sm bg-[#ff5c5c]" />
          <span className="truncate">{ep.title}</span>
          <span className="ml-2 text-white/40">×</span>
        </div>
        <span className="mb-1 text-[18px] leading-none text-white/40">+</span>
      </div>
      <div className="flex h-11 items-center gap-3 bg-[#1a2450] px-3 text-white/60">
        <FilmIcon name="back" size={16} />
        <span className="h-7 flex-1 rounded-full bg-white/8 px-3 text-[13px] leading-7">watch.example/night-drive/episode-{view.episode}</span>
      </div>
      <div className="relative h-[564px] bg-[#0f1530]">
        <div className="absolute left-6 top-6 h-[432px] w-[768px] overflow-hidden rounded-[14px] bg-black">
          <NightDrive episode={view.episode} position={view.position} />
          <PauseGlyph flash={view.pauseFlash} paused={view.paused} />
          <div className="absolute inset-x-0 bottom-0 h-1.5 bg-white/20">
            <div className="h-full bg-[#ff3b3b]" style={{ width: `${view.position * 100}%` }} />
          </div>
        </div>
        <p className="absolute left-6 top-[472px] w-[768px] truncate text-[20px] font-semibold text-white">{ep.title}</p>
        <div className="absolute left-6 top-[506px] flex w-[768px] items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[linear-gradient(145deg,#f29d6a,#4b2a6e)] text-[13px] font-bold text-white">N</span>
          <span className="text-[15px] font-semibold text-white">Night Drive</span>
          <span className="text-[13px] text-white/50">Series</span>
          <span className="relative ml-3">
            {ring > 0 ? <span className="pointer-events-none absolute -inset-2 rounded-full border-[3px] border-[#4281fa] shadow-[0_0_0_6px_rgb(66_129_250/0.25)]" style={{ opacity: ring, transform: `scale(${lerp(1.3, 1, ring)})` }} /> : null}
            <span className={`relative inline-flex h-9 items-center gap-1.5 rounded-full px-4 text-[14px] font-semibold ${view.subscribed ? "bg-white/12 text-white" : "bg-white text-[#0f1530]"}`}>
              {view.subscribed ? <FilmIcon name="bell" size={16} /> : null}
              {view.subscribed ? "Subscribed" : "Subscribe"}
            </span>
          </span>
          {ring > 0 ? (
            <span className="absolute left-[392px] top-[6px] whitespace-nowrap rounded-md bg-[#4281fa] px-2.5 py-1 text-[13px] font-semibold text-white shadow-[0_8px_20px_-8px_rgb(66_129_250/0.8)]" style={{ opacity: ring, transform: `translateX(${lerp(-8, 0, ring)}px)` }}>
              Button name from Windows: “Subscribe”
            </span>
          ) : null}
        </div>
        <UpNext />
      </div>
    </div>
  );
}

function PauseGlyph({ flash, paused }: { flash: number; paused: boolean }) {
  if (flash <= 0 && !paused) return null;
  const o = flash > 0 ? flash : 0.55;
  const s = flash > 0 ? lerp(1.25, 1, flash) : 1;
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center" style={{ opacity: o }}>
      <span className="flex h-24 w-24 items-center justify-center rounded-full bg-black/45 text-white backdrop-blur-sm" style={{ transform: `scale(${s})` }}>
        <Icon name="pause" size={44} />
      </span>
    </div>
  );
}

/** The player filling the screen (after "make it full screen"); title and controls show while paused. */
function FullscreenPlayer({ view }: { view: PcView }) {
  const ep = EPISODES[view.episode];
  const c = view.controls;
  const shelf = view.shelf;
  return (
    <div className="absolute inset-0 overflow-hidden bg-black">
      <NightDrive episode={view.episode} position={view.position} />
      <PauseGlyph flash={view.pauseFlash} paused={view.paused && c > 0} />
      <div className="absolute inset-x-0 top-0 bg-[linear-gradient(180deg,rgb(0_0_0/0.7),transparent)] px-10 pb-16 pt-7 text-white" style={{ opacity: c, transform: `translateY(${lerp(-12, 0, c)}px)` }}>
        <p className="text-[26px] font-semibold tracking-[-0.01em]">{ep.title}</p>
        <p className="mt-1 text-[15px] text-white/70">Night Drive · Series</p>
      </div>
      <div className="absolute inset-x-0 bottom-0 bg-[linear-gradient(0deg,rgb(0_0_0/0.75),transparent)] px-10 pb-7 pt-16 text-white" style={{ opacity: c, transform: `translateY(${lerp(12, 0, c)}px)` }}>
        <div className="h-1.5 rounded-full bg-white/25">
          <div className="relative h-full rounded-full bg-[#ff3b3b]" style={{ width: `${view.position * 100}%` }}>
            <span className="absolute -right-2 -top-[5px] h-4 w-4 rounded-full bg-[#ff3b3b]" />
          </div>
        </div>
        <div className="mt-4 flex items-center gap-5">
          <Icon name={view.paused ? "play" : "pause"} size={30} />
          <Icon name="next" size={26} />
          <Icon name="volume" size={24} />
          <span className="text-[15px] tabular-nums">
            {clock(view.position, LENGTH_S[view.episode])} / {ep.length}
          </span>
          <span className="ml-auto flex items-center gap-5">
            <Icon name="theater" size={24} />
            <FilmIcon name="compress" size={24} />
          </span>
        </div>
      </div>
      {shelf > 0 ? (
        <div className="absolute inset-x-0 bottom-0 bg-[rgb(6_10_30/0.92)] px-10 pb-8 pt-6 backdrop-blur-md" style={{ transform: `translateY(${lerp(100, 0, shelf)}%)`, opacity: shelf }}>
          <p className="text-[15px] font-semibold uppercase tracking-[0.12em] text-white/60">More episodes</p>
          <div className="mt-4 flex gap-5">
            {["Episode 5: Tunnel", "Episode 6: The Bridge", "Episode 7: Coast Road", "Episode 8: Home"].map((title, i) => (
              <Poster key={title} i={i + 2} title={title} w={280} h={158} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** ZStream: a ten-foot TV app, steered with the phone's D-pad. */
function TvApp({ focus }: { focus: number }) {
  const row = ["Night Drive S2 E5", "Harbour", "The Long Way", "Coast Road", "Home Again"];
  return (
    <div className="absolute inset-0 overflow-hidden bg-[#070d2a] text-white">
      <div className="absolute inset-y-0 left-0 flex w-[72px] flex-col items-center gap-7 bg-[rgb(255_255_255/0.04)] pt-9 text-white/55">
        <Icon name="search" size={22} />
        <Icon name="home" size={22} className="text-white" />
        <FilmIcon name="tv" size={22} />
        <FilmIcon name="grid" size={22} />
        <FilmIcon name="star" size={22} />
      </div>
      <div className="absolute left-[72px] right-0 top-0 h-[400px] bg-[linear-gradient(100deg,#0a1a5a_0%,#2a62ff_45%,#f29d6a_100%)]">
        <div className="absolute inset-0 bg-[linear-gradient(90deg,rgb(7_13_42/0.9)_20%,transparent_70%)]" />
        <div className="absolute left-14 top-16">
          <p className="text-[14px] font-semibold uppercase tracking-[0.18em] text-white/70">Continue watching</p>
          <p className="mt-3 text-[56px] font-bold leading-none tracking-[-0.03em]">Night Drive</p>
          <p className="mt-3 text-[20px] text-white/80">Season 2 · Episode 4 · First Light</p>
          <span className="mt-7 inline-flex h-12 items-center gap-2 rounded-full bg-white px-6 text-[17px] font-semibold text-[#070d2a]">
            <Icon name="play" size={20} />
            Resume
          </span>
        </div>
        <span className="absolute right-10 top-8 flex items-center gap-2 text-[15px] font-semibold text-white/80">
          <FilmIcon name="tv" size={18} />
          ZStream
        </span>
      </div>
      <p className="absolute left-[128px] top-[436px] text-[17px] font-semibold text-white/80">Continue watching</p>
      <div className="absolute left-[128px] top-[472px] flex gap-6">
        {row.map((title, i) => (
          <Poster key={title} i={i} title={title} w={200} h={112} focus={i === focus ? 1 : 0} />
        ))}
      </div>
    </div>
  );
}

export const PcScreen = memo(function PcScreen({ view, width }: { view: PcView; width: number }) {
  const scale = width / PC_SCREEN.w;
  return (
    <div className="relative overflow-hidden" style={{ width, height: PC_SCREEN.h * scale }}>
      <div className="absolute left-0 top-0 origin-top-left bg-[linear-gradient(160deg,#0a2a7a_0%,#061a4e_50%,#1a2b6d_100%)]" style={{ width: PC_SCREEN.w, height: PC_SCREEN.h, transform: `scale(${scale})`, opacity: view.on }}>
        <div className="absolute inset-0 bg-[radial-gradient(60rem_30rem_at_30%_20%,rgb(66_129_250/0.35),transparent_70%)]" />
        {view.app === "tv" ? (
          <TvApp focus={view.focus} />
        ) : view.fullscreen >= 1 ? (
          <FullscreenPlayer view={view} />
        ) : (
          <>
            <BrowserPage view={view} />
            <Taskbar />
            {view.fullscreen > 0 ? (
              <div className="absolute inset-0" style={{ opacity: view.fullscreen }}>
                <FullscreenPlayer view={view} />
              </div>
            ) : null}
          </>
        )}
        {view.muteOsd > 0 ? (
          <div className="absolute left-6 top-6 flex items-center gap-3 rounded-[12px] bg-[rgb(20_30_70/0.92)] px-4 py-3 text-white shadow-[0_20px_40px_-20px_rgb(0_0_0/0.8)] ring-1 ring-white/10" style={{ opacity: view.muteOsd, transform: `translateY(${lerp(-8, 0, view.muteOsd)}px)` }}>
            <FilmIcon name="speakerMute" size={22} />
            <span className="h-1.5 w-40 rounded-full bg-white/20">
              <span className="block h-full w-[40%] rounded-full bg-white/40" />
            </span>
            <span className="text-[14px] font-semibold">Muted</span>
          </div>
        ) : null}
      </div>
      {/* the panel's glass: a faint reflection */}
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(115deg,rgb(255_255_255/0.07),transparent_38%)]" />
    </div>
  );
});
