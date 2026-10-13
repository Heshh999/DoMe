/** Product explanation. No testimonials, counts, certifications or platform claims that are not verified. */
import { Link } from "react-router";

import { Wordmark } from "../../components/brand.tsx";
import { Film } from "../../components/film/Film.tsx";
import { CONCEPT_NOTE } from "../../components/film/timeline.ts";
import { Icon, type IconName } from "../../components/icons.tsx";

const STEPS = [
  { title: "Install DoMe on your PC", text: "Sign in on your Windows PC and give it a name. It connects out to DoMe, so there are no ports to open and no router to change." },
  { title: "Pair your phone", text: "Scan the QR code the PC shows, check the six-digit code matches on both screens, and approve on the PC." },
  { title: "Sit back", text: "Play, pause, skip and set the volume from the couch, or from anywhere with internet." },
];

const CAPABILITIES: Array<{ icon: IconName; title: string; text: string; status?: string }> = [
  { icon: "youtube", title: "YouTube, the right tab", text: "Play, pause, skip and seek in the PC’s browser, even while another window has focus." },
  { icon: "volume", title: "Media and volume", text: "Pause or skip whatever Windows is playing, and set the exact system volume." },
  { icon: "apps", title: "Your approved apps", text: "Open, bring to front, minimise or close the apps you approved on the PC. Nothing else." },
  { icon: "lock", title: "Lock and power", text: "Lock Windows in one tap. Sleep, restart and shut down always ask you first." },
  { icon: "ask", title: "Just ask", text: "Say or type “skip this video” or “set the volume to 35”. Everyday requests need no AI." },
  { icon: "check", title: "Honest results", text: "Every control shows what the PC actually did. “Sent” is never shown as “done”." },
  { icon: "pc", title: "Screen view", text: "See your PC’s screen on your phone and tap what you see. In development: Free will get 10 minutes per 5 hours, Pro and Pro Plus unlimited.", status: "Coming" },
];

const TRUST: Array<{ title: string; text: React.ReactNode }> = [
  { title: "Nothing listens on your PC", text: "It opens one outbound, authenticated connection to the DoMe service." },
  { title: "Only phones you approved", text: "Each phone has its own signing key that never leaves it, approved on the PC with a matching code." },
  { title: "Actions, never scripts", text: "Phones send action names and checked settings: no file paths, arguments or scripts." },
  { title: "Big actions ask first", text: "Closing apps, sleep, restart and shut down need a fresh confirmation every time." },
  { title: "You stay in charge", text: "Revoke a phone or switch remote control off on the PC at any moment. Security is free for everyone." },
  {
    title: "Plain about privacy",
    text: (
      <>
        Encrypted in transit (TLS). The DoMe service can see the commands and PC state it routes; it is not end-to-end encrypted, as the{" "}
        <Link to="/privacy" className="font-semibold text-accent underline-offset-4 hover:underline">
          privacy notice
        </Link>{" "}
        says.
      </>
    ),
  },
];

/** The faint orbit behind the hero, ending in the logo's coral cursor. Decorative. */
function Orbit() {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 1200 640" preserveAspectRatio="xMidYMid slice" className="pointer-events-none absolute inset-0 hidden h-full w-full sm:block">
      <ellipse cx="600" cy="330" rx="640" ry="190" transform="rotate(-12 600 330)" fill="none" stroke="var(--dome-hero-orbit)" strokeWidth="1.5" />
      <ellipse cx="600" cy="330" rx="470" ry="132" transform="rotate(-12 600 330)" fill="none" stroke="var(--dome-hero-orbit)" strokeWidth="1" />
      <path d="M1052 106l16 7-5.6 2.8 3.8 7-3.3 1.4-3.8-7-4.7 3.8z" fill="#f96054" />
    </svg>
  );
}

/** A picture of the app on a phone (always the dark Midnight look), described in one sentence for screen readers. */
function PhonePicture() {
  return (
    <div role="img" aria-label="The DoMe app on an iPhone: Living Room PC online, a YouTube video playing with pause and skip controls, the Windows volume at 40 percent, and quick actions for next video, back 10 seconds, lock and sleep." className="dome-dark relative mx-auto w-[288px] select-none sm:w-[304px]">
      <div className="absolute -inset-x-16 -inset-y-10 -z-10 bg-[radial-gradient(closest-side,rgb(1_66_251/0.4),transparent)]" />
      <div className="rounded-[54px] bg-[linear-gradient(160deg,#25335c,#0a1230_45%,#1b2a52)] p-[9px] shadow-[0_40px_60px_-30px_rgb(0_10_40/0.55),0_0_0_1px_rgb(255_255_255/0.08)_inset]">
        <div className="relative h-[600px] overflow-hidden rounded-[46px] bg-navy sm:h-[624px]">
          <div className="absolute inset-0 bg-[radial-gradient(28rem_18rem_at_50%_-6rem,rgb(1_66_251/0.5),transparent_70%)]" />
          <div className="absolute left-1/2 top-[10px] h-[28px] w-[96px] -translate-x-1/2 rounded-full bg-black" />
          <div className="relative flex h-full flex-col px-4 pt-[52px] text-text">
            <p className="text-[22px] font-bold tracking-[-0.02em]">Home</p>
            <div className="mt-3 flex items-center gap-3 rounded-[20px] border border-white/10 bg-white/[0.06] p-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/10">
                <Icon name="pc" size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12px] font-semibold">Living Room PC</span>
                <span className="block truncate text-[10px] text-text-muted">Connected</span>
              </span>
              <span className="inline-flex items-center gap-1 rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-semibold text-success">
                <span className="h-1.5 w-1.5 rounded-full bg-current" />
                Online
              </span>
            </div>
            <div className="mt-2.5 rounded-[22px] border border-white/10 bg-white/[0.06] p-3.5">
              <div className="flex items-center gap-3">
                <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,#2a62ff,#001441)] text-white">
                  <Icon name="youtube" size={22} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[10px] text-text-faint">YouTube · Edge</span>
                  <span className="block truncate text-[13px] font-semibold">Lo-fi beats to relax to</span>
                </span>
              </div>
              <div className="mt-3 h-1 rounded-full bg-white/15">
                <div className="h-1 w-[38%] rounded-full bg-white" />
              </div>
              <div className="mt-1 flex justify-between text-[9px] text-text-faint">
                <span>12:04</span>
                <span>31:40</span>
              </div>
              <div className="mt-2 flex items-center justify-center gap-5">
                <Icon name="prev" size={18} className="text-text-muted" />
                <span className="dome-primary flex h-12 w-12 items-center justify-center rounded-full">
                  <Icon name="pause" size={20} />
                </span>
                <Icon name="next" size={18} className="text-text-muted" />
              </div>
            </div>
            <div className="mt-2.5 rounded-[20px] border border-white/10 bg-white/[0.06] p-3">
              <div className="flex items-center justify-between text-[11px]">
                <span className="inline-flex items-center gap-1.5 font-semibold">
                  <Icon name="volume" size={14} />
                  Windows volume
                </span>
                <span className="font-semibold tabular-nums text-text-muted">40%</span>
              </div>
              <div className="mt-2.5 h-1.5 rounded-full bg-white/15">
                <div className="relative h-1.5 w-[40%] rounded-full bg-primary">
                  <span className="absolute -right-2 -top-[5px] h-4 w-4 rounded-full bg-white shadow" />
                </div>
              </div>
            </div>
            <div className="mt-2.5 grid grid-cols-2 gap-2">
              {(
                [
                  ["next", "Next video"],
                  ["back10", "Back 10 s"],
                  ["lock", "Lock"],
                  ["sleep", "Sleep"],
                ] as Array<[IconName, string]>
              ).map(([icon, label]) => (
                <span key={label} className="flex items-center gap-2 rounded-[18px] border border-white/10 bg-white/[0.06] px-3 py-2.5 text-[11px] font-semibold">
                  <Icon name={icon} size={15} className="text-accent" />
                  {label}
                </span>
              ))}
            </div>
            <div className="mt-auto -mx-4 grid grid-cols-5 border-t border-white/10 bg-[rgb(0_14_50/0.7)] px-2 pb-6 pt-2">
              {(
                [
                  ["home", "Home"],
                  ["remote", "Remote"],
                  ["touchpad", "Touchpad"],
                  ["ask", "Ask"],
                  ["more", "More"],
                ] as Array<[IconName, string]>
              ).map(([icon, label], i) => (
                <span key={label} className={`flex flex-col items-center gap-0.5 text-[9px] font-semibold ${i === 0 ? "text-text" : "text-text-faint"}`}>
                  <Icon name={icon} size={18} />
                  {label}
                  <span className={`h-1 w-1 rounded-full ${i === 0 ? "bg-coral" : "bg-transparent"}`} />
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="absolute -left-48 bottom-28 hidden items-center gap-2.5 rounded-2xl border border-white/10 bg-[rgb(11_33_86/0.82)] px-3.5 py-2.5 text-[13px] shadow-[0_20px_40px_-16px_rgb(0_0_0/0.6)] backdrop-blur-md lg:flex">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-success/20 text-success">
          <Icon name="check" size={14} strokeWidth={2.5} />
        </span>
        <span>
          <span className="block font-semibold">Volume set to 40%</span>
          <span className="block text-[12px] text-text-muted">Done on Living Room PC</span>
        </span>
      </div>
    </div>
  );
}

const SECTION = "mx-auto max-w-6xl px-5 lg:px-8";

export function LandingPage() {
  return (
    <div>
      <section className="relative overflow-x-clip">
        <Orbit />
        <div className={`${SECTION} relative grid items-center gap-12 pb-20 pt-14 sm:pb-16 sm:pt-20 lg:grid-cols-[1.1fr_1fr] lg:gap-10 lg:pb-12 lg:pt-24`}>
          <div className="animate-rise text-center lg:text-left">
            <p className="inline-flex items-center gap-2 rounded-full border border-border bg-bg-elevated px-3 py-1.5 text-xs font-semibold text-text-muted shadow-[var(--dome-shadow-card)]">
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-coral" />
              DoMe Remote for Windows and iPhone
            </p>
            <h1 className="mt-6 text-display font-bold">
              Your PC,
              <span className="block">in your hand.</span>
            </h1>
            <p className="mx-auto mt-6 max-w-[34rem] text-lg text-text-muted sm:text-xl lg:mx-0">Your iPhone becomes a simple, secure remote for your Windows PC. Free, no credit card.</p>
            <div className="mt-9 flex flex-col items-center gap-3 sm:flex-row sm:justify-center lg:justify-start">
              <Link to="/app" className="tap dome-primary dome-press inline-flex w-full items-center justify-center gap-2 rounded-full px-7 text-base font-semibold sm:w-auto sm:min-h-[52px]">
                Open the app
                <Icon name="arrow" size={18} />
              </Link>
              <Link to="/download" className="tap dome-press inline-flex w-full items-center justify-center gap-2 rounded-full border border-border-strong bg-bg-elevated px-7 text-base font-semibold hover:bg-surface sm:w-auto sm:min-h-[52px]">
                <Icon name="pc" size={18} />
                Get DoMe for Windows
              </Link>
            </div>
            <p className="mt-4 text-xs text-text-faint">DoMe is in development. The Windows app is not published yet: see Download for its status.</p>
          </div>
          <PhonePicture />
        </div>
      </section>

      <section aria-labelledby="see" className={`${SECTION} pt-[calc(var(--dome-section)*0.55)]`}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="dome-eyebrow">See it in action</p>
            <h2 id="see" className="mt-3 max-w-xl text-h2 font-bold">
              A minute on the couch with DoMe.
            </h2>
          </div>
          <Link to="/demo" className="tap dome-press inline-flex w-fit items-center gap-1.5 rounded-full border border-border-strong bg-bg-elevated px-5 text-[15px] font-semibold hover:bg-surface">
            Open the full demo
            <Icon name="arrow" size={16} />
          </Link>
        </div>
        <div className="mt-8">
          <Film autoplay />
        </div>
        <p className="mt-4 text-sm text-text-muted">{CONCEPT_NOTE}</p>
      </section>

      <section aria-labelledby="how" className={`${SECTION} py-[var(--dome-section)]`}>
        <p className="dome-eyebrow">How it works</p>
        <h2 id="how" className="mt-3 max-w-xl text-h2 font-bold">
          Ready in three steps.
        </h2>
        <ol className="mt-10 grid gap-4 md:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="dome-card p-6 lg:p-7">
              <span className="font-display text-sm font-bold tabular-nums text-accent">0{i + 1}</span>
              <h3 className="mt-3 text-title font-semibold md:mt-6">{s.title}</h3>
              <p className="mt-2 text-[15px] text-text-muted">{s.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="what" className={`${SECTION} pb-[var(--dome-section)]`}>
        <p className="dome-eyebrow">What you can do</p>
        <h2 id="what" className="mt-3 max-w-xl text-h2 font-bold">
          Everything within reach.
        </h2>
        <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map((c) =>
            c.status ? (
              // the one that is still being built: a full-width row with its status chip, never dressed as available
              <li key={c.title} className="dome-card flex gap-4 p-5 sm:col-span-2 sm:items-center sm:p-6 lg:col-span-3">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent/10 text-accent">
                  <Icon name={c.icon} size={22} />
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="flex flex-wrap items-center gap-2 text-lg font-semibold tracking-[-0.01em]">
                    {c.title}
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-semibold text-text-muted">
                      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-coral" />
                      {c.status}
                    </span>
                  </h3>
                  <p className="mt-1 max-w-3xl text-[15px] text-text-muted">{c.text}</p>
                </div>
                <Link to="/demo" className="tap dome-press hidden shrink-0 items-center gap-1 rounded-full border border-border-strong px-4 text-sm font-semibold hover:bg-surface sm:inline-flex">
                  See the preview
                  <Icon name="chevron" size={14} />
                </Link>
              </li>
            ) : (
              <li key={c.title} className="dome-card flex gap-4 p-5 sm:block sm:p-6">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent/10 text-accent">
                  <Icon name={c.icon} size={22} />
                </span>
                <div className="min-w-0">
                  <h3 className="text-lg font-semibold tracking-[-0.01em] sm:mt-5">{c.title}</h3>
                  <p className="mt-1 text-[15px] text-text-muted sm:mt-1.5">{c.text}</p>
                </div>
              </li>
            ),
          )}
        </ul>
      </section>

      <section aria-labelledby="security" className={`${SECTION} pb-[var(--dome-section)]`}>
        <div className="grid gap-10 lg:grid-cols-[1fr_1.5fr] lg:gap-16">
          <div>
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent/10 text-accent">
              <Icon name="shield" size={24} />
            </span>
            <h2 id="security" className="mt-6 text-h2 font-bold">
              Built to be trusted with your PC.
            </h2>
            <p className="mt-4 max-w-md text-lg text-text-muted">Security controls are part of every plan, and we say plainly what DoMe can and cannot see.</p>
          </div>
          <ul className="grid gap-x-8 gap-y-7 sm:grid-cols-2">
            {TRUST.map((t) => (
              <li key={t.title} className="flex gap-3.5">
                <span aria-hidden="true" className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-success/12 text-success">
                  <Icon name="check" size={14} strokeWidth={2.5} />
                </span>
                <span>
                  <span className="block font-semibold">{t.title}</span>
                  <span className="mt-1 block text-[15px] text-text-muted">{t.text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section aria-labelledby="free" className="px-3 pb-16 [--closing-fill:var(--dome-navy)] [--closing-glow:0_40px_80px_-40px_rgb(0_20_65/0.55)] sm:px-5 lg:px-8 lg:pb-24 dark:[--closing-fill:#0b2156] dark:[--closing-glow:0_0_0_1px_rgb(255_255_255/0.06),0_40px_120px_-30px_rgb(1_66_251/0.6)]">
        <div className="dome-dark relative mx-auto max-w-6xl overflow-hidden rounded-hero bg-[var(--closing-fill)] px-6 py-16 text-center shadow-[var(--closing-glow)] ring-1 ring-white/12 sm:px-12 lg:py-24">
          <div aria-hidden="true" className="absolute inset-0 bg-[radial-gradient(50rem_22rem_at_50%_-4rem,rgb(1_66_251/0.55),transparent_70%)]" />
          <div className="relative">
            <Wordmark className="mx-auto h-[72px] w-auto sm:h-[88px]" />
            <h2 id="free" className="mx-auto mt-10 max-w-3xl text-h2 font-bold">
              Free today. Pro and Pro Plus when they’re worth it.
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-lg text-text-muted">Remote control stays free, with 1 PC and 2 phones. Pro ($9.99 a month) and Pro Plus ($24.99 a month) add up to 5 PCs and 5 phones, unlimited Screen view when it ships, and more AI butler time. Both arrive at paid launch.</p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link to="/app" className="tap dome-primary dome-press inline-flex w-full items-center justify-center rounded-full px-7 text-base font-semibold sm:w-auto sm:min-h-[52px]">
                Open the app
              </Link>
              <Link to="/pricing" className="tap dome-press inline-flex w-full items-center justify-center gap-1.5 rounded-full border border-border-strong px-7 text-base font-semibold hover:bg-surface sm:w-auto sm:min-h-[52px]">
                Compare plans
                <Icon name="chevron" size={16} />
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
