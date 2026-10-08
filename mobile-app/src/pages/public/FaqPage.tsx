import { Link } from "react-router";

const FAQ: Array<{ q: string; a: React.ReactNode }> = [
  { q: "Do I need to open ports, set up a VPN or install anything on my router?", a: "No. The PC makes one outgoing, authenticated connection to the DoMe service, and your phone talks to the same service. Nothing on your PC listens on the internet." },
  { q: "Does it work when I am away from home?", a: "Yes, over mobile data or any Wi-Fi, as long as both your phone and your PC have internet. There is no local-only mode yet: if the DoMe service is unreachable, controls pause rather than pretending to work." },
  { q: "Can DoMe control YouTube when the browser is in the background?", a: "Yes. The DoMe browser extension in Chrome or Edge on the PC controls the player in the chosen tab directly, so another window can have focus. The extension only asks for access to YouTube pages." },
  { q: "Why does Next sometimes say “no next video”?", a: "DoMe only reports success when the player really moved to a different video. Ads, live streams, Shorts and some playlists behave differently; DoMe tells you instead of guessing." },
  { q: "Why can’t fullscreen be turned on from my phone?", a: "Browsers only allow fullscreen from a click on the PC itself. DoMe reports this honestly rather than faking it. Theater mode is offered as a separate, working control." },
  { q: "Is this end-to-end encrypted?", a: "Not yet. Connections are encrypted in transit (TLS) and every command is signed by your phone so only phones you approved on the PC are accepted — but the DoMe service can see the commands and PC state it routes. Stronger payload encryption is a possible later improvement; we will not claim it before it exists." },
  { q: "What happens if my PC is asleep or off?", a: "The PC shows as offline and commands are not stored for later — nothing will run hours later by surprise. Waking a PC remotely (Wake-on-LAN) is not offered yet." },
  { q: "Can someone with my password control my PC?", a: "Not without pairing: a new phone must be approved on the PC itself with a matching verification code. Account recovery never silently approves a phone. If you lose a phone, revoke it from Devices or from the PC." },
  { q: "What if I clear my browser data or reinstall?", a: "Each browser or home-screen installation has its own key. Clearing site data removes the key, and that installation must pair again on the PC. There is deliberately no shortcut." },
  { q: "Does DoMe use AI?", a: "Typed commands are understood on your phone with fixed rules — no AI service and nothing to subscribe to. Optional cloud AI interpretation may come later as a separate, clearly disclosed feature." },
  {
    q: "What does the Windows app see or change?",
    a: (
      <>
        It runs as you (no administrator rights), shows a tray icon, and performs only the actions in the registry: media, volume, approved apps, lock, and confirmed power actions. It never bypasses the Windows lock screen or UAC. See <Link to="/privacy" className="text-accent font-semibold">Privacy</Link>.
      </>
    ),
  },
];

export function FaqPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Frequently asked questions</h1>
      </header>
      <dl className="divide-y divide-border rounded-card border border-border">
        {FAQ.map((f) => (
          <details key={f.q} className="group p-4">
            <summary className="cursor-pointer font-semibold list-none flex justify-between gap-3">
              <dt>{f.q}</dt>
              <span aria-hidden="true" className="text-text-faint group-open:rotate-45 transition-transform">
                +
              </span>
            </summary>
            <dd className="text-sm text-text-muted mt-2">{f.a}</dd>
          </details>
        ))}
      </dl>
    </div>
  );
}
