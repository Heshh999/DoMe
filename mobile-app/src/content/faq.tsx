/**
 * The FAQ's questions and answers, grouped by topic. Every answer describes the software as built: the AI
 * butler exists (optional, off until turned on); Screen view is coming; point-and-talk is planned; the AI
 * never sees the screen; nothing is recorded; speech is not stored. Rendered by src/pages/public/FaqPage.tsx.
 */
import type { ReactNode } from "react";
import { Link } from "react-router";

import type { IconName } from "../components/icons.tsx";
import type { PublicIconName } from "../pages/public/publicIcons.tsx";
import { AI_RULES, COMING_AT_PAID_LAUNCH, FREE_SCREEN_VIEW_MINUTES, FREE_TASTE_REQUESTS, planById, POINT_AND_TALK_STATUS, SCREEN_VIEW_STATUS, TOP_UP, usd, WARN_AT_PERCENT, WINDOW_HOURS } from "./plans.ts";

export type FaqEntry = { q: string; a: ReactNode };
export type FaqGroup = { id: string; title: string; icon: PublicIconName | Extract<IconName, "shield" | "ask">; status?: string; entries: FaqEntry[] };

const pro = planById("pro");
const proPlus = planById("pro_plus");

const link = (to: string, label: string) => (
  <Link to={to} className="font-semibold text-accent underline decoration-accent/35 underline-offset-[3px] hover:decoration-current">
    {label}
  </Link>
);

export const FAQ_GROUPS: FaqGroup[] = [
  {
    id: "basics",
    title: "Getting started",
    icon: "help",
    entries: [
      { q: "Do I need to open ports, set up a VPN or install anything on my router?", a: "No. The PC makes one outgoing, authenticated connection to the DoMe service, and your phone talks to the same service. Nothing on your PC listens on the internet." },
      { q: "Does it work when I am away from home?", a: "Yes, over mobile data or any Wi-Fi, as long as both your phone and your PC have internet. There is no local-only mode yet: if the DoMe service is unreachable, controls pause rather than pretending to work." },
      { q: "Can DoMe control YouTube when the browser is in the background?", a: "Yes. The DoMe browser extension in Chrome or Edge on the PC controls the player in the chosen tab directly, so another window can have focus. The extension only asks for access to YouTube pages." },
      { q: "Why does Next sometimes say “no next video”?", a: "DoMe only reports success when the player really moved to a different video. Ads, live streams, Shorts and some playlists behave differently; DoMe tells you instead of guessing." },
      { q: "Why can’t fullscreen be turned on from my phone?", a: "Browsers only allow fullscreen from a click on the PC itself. DoMe reports this honestly rather than faking it. Theater mode is offered as a separate, working control." },
      { q: "What happens if my PC is asleep or off?", a: "The PC shows as offline and commands are not stored for later — nothing will run hours later by surprise. Waking a PC remotely (Wake-on-LAN) is not offered yet." },
    ],
  },
  {
    id: "butler",
    title: "The AI butler",
    icon: "ask",
    entries: [
      {
        q: "What is the AI butler?",
        a: "Ask is DoMe’s optional assistant. Say or type what you want in your own words. When DoMe cannot work it out by itself, the AI interprets your request and proposes safe PC actions from DoMe’s fixed list — media, volume, your approved apps, lock and confirmed power — and shows them to you before anything runs. It is off until you turn it on in Settings, and you can turn it off again at any time.",
      },
      {
        q: "Does DoMe use AI for everything?",
        a: "No. Typed and spoken commands DoMe understands by itself (“pause”, “volume 40”, “open Spotify”) are handled on your phone with fixed rules — no AI service involved, nothing to subscribe to. Only requests DoMe cannot work out go to the AI, and only if you turned the butler on.",
      },
      {
        q: "What counts as an AI request?",
        a: (
          <>
            <p>{AI_RULES.request}</p>
            <p>{AI_RULES.everyday}</p>
            <p>{AI_RULES.taste}</p>
          </>
        ),
      },
      {
        q: "What happens when I reach a limit?",
        a: (
          <>
            <p>Everything that does not need the AI keeps working: every everyday command, the touchpad and keyboard, Screen view when it arrives. Only new AI requests wait.</p>
            <p>{AI_RULES.windows}</p>
            <p>
              On Pro and Pro Plus you can also add {TOP_UP.requests} requests for {usd(TOP_UP.priceCents)} as a top-up. You choose to buy it; nothing is added or charged automatically.
            </p>
          </>
        ),
      },
      {
        q: "Does the AI see my screen?",
        a: (
          <>
            <p>Never. DoMe takes no screenshots and sends no pictures anywhere. The AI receives only the words of your request, your recent requests to the butler, and a few facts about your PC (whether it is online, whether something is playing, the volume). It never receives your screen, the names of your apps, window or video titles, or anything you type with the keyboard.</p>
            <p>Point-and-talk (“click that”) is {POINT_AND_TALK_STATUS.toLowerCase()}, not built. When it comes it will use the names of on-screen elements only, as Windows exposes them for accessibility — still no screenshots, ever.</p>
          </>
        ),
      },
      {
        q: "Is my speech stored?",
        a: "No. Talking to DoMe uses your iPhone’s own speech recognition, provided by Apple. DoMe listens only while you hold the microphone button and never records, uploads or keeps audio. DoMe does not store what you say, your search words or what the butler answers; it keeps counts of requests to apply your allowance. The conversation on your phone is cleared after 10 minutes without a request, when you sign out or switch PC, or when the app reloads.",
      },
    ],
  },
  {
    id: "screen-view",
    title: "Screen view",
    icon: "screen",
    status: SCREEN_VIEW_STATUS,
    entries: [
      {
        q: "What is Screen view, and when is it coming?",
        a: (
          <>
            <p>Screen view shows your PC’s screen live on your phone so you can tap what you see, scroll with two fingers, and keep a floating mini view while you use another app. It is in development and not in the app yet; this page will say so until it ships.</p>
            <p>
              Free will include {FREE_SCREEN_VIEW_MINUTES} minutes per {WINDOW_HOURS}-hour window; Pro and Pro Plus will have unlimited Screen view.
            </p>
          </>
        ),
      },
      { q: "Is anything recorded in Screen view?", a: "No. The picture streams from your PC to your phone while you watch it and is not recorded or stored anywhere, and the AI never receives it. Screen view and the AI butler are separate: the butler works from your words, never from the picture." },
    ],
  },
  {
    id: "plans",
    title: "Plans and billing",
    icon: "wallet",
    entries: [
      {
        q: "What do Pro and Pro Plus include?",
        a: (
          <>
            <p>
              Both add up to {pro.pcs} PCs and {pro.phones} phones, unlimited Screen view when it arrives, and custom remote layouts and saved routines at paid launch. The difference is the AI butler allowance: Pro has {pro.ai.perWindow?.requests} requests per {WINDOW_HOURS}-hour window (about {pro.ai.perWindow?.minutes} minutes), up to {pro.ai.perWeek?.requests} a week; Pro Plus has {proPlus.ai.perWindow?.requests} per window (about {proPlus.ai.perWindow?.minutes} minutes), up to {proPlus.ai.perWeek?.requests} a week.
            </p>
            <p>
              Pro is {usd(pro.price!.monthlyCents)} a month or {usd(pro.price!.annualCents)} a year; Pro Plus is {usd(proPlus.price!.monthlyCents)} a month or {usd(proPlus.price!.annualCents)} a year, in US dollars before tax. Both are {COMING_AT_PAID_LAUNCH.toLowerCase()}: nothing can be bought in this version. See {link("/pricing", "Pricing")} for the full comparison.
            </p>
          </>
        ),
      },
      {
        q: "What is a top-up?",
        a: `${AI_RULES.topUp} A top-up is a one-off purchase, not a subscription, and the app warns you at ${WARN_AT_PERCENT} % of a window before you need one.`,
      },
      {
        q: "How do I cancel?",
        a: "From Billing in the DoMe web app, at any time, once paid plans launch. Access continues until the end of the period you paid for and your settings are kept. If Pro ends, you keep every PC, phone and setting and choose which PC and phones stay enabled on Free. Revocation and emergency stop always work, on every plan.",
      },
      {
        q: "Can I buy Pro today?",
        a: `Not yet. Only Free exists today; Pro and Pro Plus are ${COMING_AT_PAID_LAUNCH.toLowerCase()}. Free includes a one-time taste of ${FREE_TASTE_REQUESTS} AI requests, so you can try the butler before then.`,
      },
    ],
  },
  {
    id: "security",
    title: "Security and privacy",
    icon: "shield",
    entries: [
      { q: "Is this end-to-end encrypted?", a: "Not yet. Connections are encrypted in transit (TLS) and every command is signed by your phone so only phones you approved on the PC are accepted — but the DoMe service can see the commands and PC state it routes. Stronger payload encryption is a possible later improvement; we will not claim it before it exists." },
      { q: "Can someone with my password control my PC?", a: "Not without pairing: a new phone must be approved on the PC itself with a matching verification code. Account recovery never silently approves a phone. If you lose a phone, revoke it from Devices or from the PC." },
      { q: "What if I clear my browser data or reinstall?", a: "Each browser or home-screen installation has its own key. Clearing site data removes the key, and that installation must pair again on the PC. There is deliberately no shortcut." },
      {
        q: "What does the Windows app see or change?",
        a: <>It runs as you (no administrator rights), shows a tray icon, and performs only the actions in the registry: media, volume, approved apps, lock, and confirmed power actions. It never bypasses the Windows lock screen or UAC. See {link("/privacy", "Privacy")}.</>,
      },
    ],
  },
];
