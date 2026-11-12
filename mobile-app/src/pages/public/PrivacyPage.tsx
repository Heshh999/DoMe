/** Privacy notice — DRAFT for founder and counsel review. Describes what the software actually does today. Styled with .dome-prose; the text changes only in a legal round. */
import { Link } from "react-router";

import { DraftBanner } from "./DraftBanner.tsx";

export function PrivacyPage() {
  return (
    <article className="dome-prose">
      <header className="dome-page-intro animate-rise">
        <p className="dome-eyebrow">Legal</p>
        <h1 className="mt-3 text-h1 font-bold">Privacy notice</h1>
        <DraftBanner />
        <p className="mt-4">Effective date: [FOUNDER: date]. Items marked [COUNSEL] need a lawyer’s judgement; nothing in this draft is a legal conclusion.</p>
      </header>
      <section>
        <h2>Who we are</h2>
        <p>DoMe Remote (“DoMe”, “we”, “us”) is offered by [FOUNDER: legal name of the business], [FOUNDER: postal address]. For privacy questions and requests, write to [FOUNDER: contact email for privacy requests] or use the support form in the app once you are signed in.</p>
      </section>
      <section>
        <h2>What DoMe is</h2>
        <p>DoMe lets you control your own Windows PC from your phone through a service we operate (the “DoMe service”). It has four parts: the DoMe web app on your phone, the DoMe program for Windows on your PC, an optional browser extension for YouTube, and the DoMe service, which passes messages between your phone and your PC. This notice describes what data moves where. It is written to match the software as built; it is not a statement of legal compliance until reviewed.</p>
      </section>
      <section>
        <h2>What we collect and why</h2>
        <p>We collect what DoMe needs to sign you in, connect your devices, keep them secure, take payments and answer support requests. [COUNSEL: whether a statement of the legal basis for each use is needed for any launch region.]</p>
        <ul>
          <li>
            <strong>Account.</strong> From your sign-in provider: an identifier for your sign-in, your email address and your display name (the name the provider gives, or the first part of your email address). We also keep your plan, when the account was created and, if you turn on the AI butler, which version of its consent you accepted and when. DoMe lets you in only after you have confirmed your email address. Why: to sign you in, show your account and apply your plan.
          </li>
          <li>
            <strong>Sign-in sessions.</strong> When you sign in, your browser gets a session cookie (see Cookies). On our side we keep a keyed hash of the session’s identifier (never the identifier itself), a security token that protects your requests, a one-way hash of your browser’s user-agent string (the text a browser sends to say which browser and system it is), and when the session started, was last used and expires. Session records hold no IP address. Why: to keep you signed in and to sign you out.
          </li>
          <li>
            <strong>Your PCs.</strong> The name you give each PC (or “My PC”), its public key, its platform, the DoMe program version and protocol versions it runs, whether it is enabled, when it was linked and last connected, and the last power action requested (only which action and when). DoMe never reads your Windows computer name or Windows user name. Why: to show your PCs and send commands to the right one.
          </li>
          <li>
            <strong>Your phones.</strong> The name of each phone you pair, its public key, when it was last seen, and the permissions you gave it on each PC. Why: so your PCs accept only phones you approved.
          </li>
          <li>
            <strong>Linking and pairing.</strong> When you link a PC, the short linking code (valid for 10 minutes) is kept with a keyed hash of the network address (IP address) that asked for it. Pairing codes are never stored; we keep only one-way hashes of them. Why: to connect a PC and a phone to your account safely.
          </li>
          <li>
            <strong>Command history.</strong> For each command: which phone, which PC, the action’s name, its outcome, when it happened, how long it took and any error code. Not the parameters, results, typed text or media titles. Why: so we can investigate problems. No app screen shows this history yet.
          </li>
          <li>
            <strong>Security events.</strong> Sign-ins, account creation, linking and unlinking PCs, pairing and revoking phones, AI butler consent given or withdrawn, Screen view sessions, plan changes, support requests and refused requests. For sign-ins, failed pairing attempts and failed link-code look-ups we add a keyed hash of your IP address, never the address itself. Why: to protect your account and investigate problems.
          </li>
          <li>
            <strong>Support requests.</strong> If you use the support form: the topic, your message (before we store it, we mask anything that looks like a security key or token, a pairing code or a network address), the app version and, only if you choose to attach it, a diagnostics report that you can read before sending. The form needs you to be signed in. Why: to answer you.
          </li>
          <li>
            <strong>Billing (when Pro and Pro Plus go on sale).</strong> Stripe’s identifiers for you and your subscription, your plan, monthly or yearly billing, the subscription’s state and current period, and for AI top-ups the number of requests bought, the amount and the currency. DoMe’s billing records hold no card details and no email address. Why: to give you the plan you paid for.
          </li>
          <li>
            <strong>AI butler usage (only if you use it).</strong> Counts only: the AI requests used in each 5-hour and weekly window, your one-time Free requests and top-up balance, and for each request its cost, the amount of text processed (tokens), timing, whether it was charged and the AI provider’s reference for it. Never the words. Why: to apply your plan’s allowance and control cost.
          </li>
          <li>
            <strong>Screen view usage (only if you use it).</strong> Session identifiers, start and end times, seconds used, why a session ended, the user name of the temporary relay credential issued to your phone, and how much data the relay carried for your account each day. Never the picture, the connection set-up or a network address. Why: to apply the Free allowance and control relay cost.
          </li>
        </ul>
      </section>
      <section>
        <h2>Data that passes through the DoMe service</h2>
        <p>To route commands, the service receives signed commands from your phone (including anything you type with the Touchpad keyboard and the words of YouTube searches), the results your PC sends back (for example the names of the apps you approved and the titles of their windows, and YouTube search results with their titles and channels) and the current state your PC reports: what is playing (YouTube tab titles and video ids, media titles and artists, playback position), the app in front and its window title, browser profile labels and volume. It forwards them between your devices and keeps the latest PC state in memory only while the PC is connected; it does not store this content. Connections are encrypted in transit (TLS). The service can read what it routes: DoMe is not end-to-end encrypted and we do not claim otherwise.</p>
        <p>Our hosting provider, Fly.io, receives all traffic between your devices and the DoMe service before it reaches our app. [FOUNDER: confirm with Fly.io what its network proxy logs about this traffic and for how long.]</p>
      </section>
      <section>
        <h2>On your phone</h2>
        <ul>
          <li>A signing key that cannot be exported, your controller identifier, your selected PC and the public keys of your paired PCs (used to check Screen view) are stored in the browser’s storage on the phone. No passwords or long-lived tokens are stored.</li>
          <li>Your preferences (theme, touchpad, Screen view and AI butler settings, which explanations you dismissed, and whether speech recognition works on this phone) are kept in the browser’s local storage. If a sign-in fails, the reason is kept in the browser tab for about a minute so the app can explain it.</li>
          <li>The app does not use analytics, advertising or session-replay scripts, loads no scripts or fonts from other companies, and does not cache your PC’s data for offline use.</li>
          <li>The camera is used only on the pairing page while you scan a code; the picture is read on the phone and not sent anywhere. The app does not use your location and sends no push notifications.</li>
          <li>“Forget this installation” in Settings deletes the phone’s signing key, its selected PC and the saved PC keys.</li>
        </ul>
      </section>
      <section>
        <h2>On your PC</h2>
        <p>The DoMe app keeps its data in your Windows user folder (%LOCALAPPDATA%\DoMe): its key material protected by Windows, the link to your account, the list of phones you approved, the apps you approved, a journal of the last 5,000 commands it received, its last 500 local security events, its Screen view history and its logs. Logs are redacted; each log file rotates at 2 MB and DoMe keeps at most about 8 MB of it. A crash log may hold file paths or other details; it stays on the PC unless you send it. Diagnostics files are created only when you ask for them, and DoMe never uploads them.</p>
        <p>About once a day the program checks GitHub, where DoMe publishes its Windows installer, for a newer version. The check sends only the program’s name and version, so GitHub sees your PC’s IP address. A new version is downloaded only when you ask, and you start the installer yourself.</p>
        <p>When you uninstall DoMe, the uninstaller asks whether to delete this data too; if you answer No, it stays on the PC. Your PC stays listed in your account until you unlink it in the app.</p>
        <p>The browser extension only runs on YouTube pages and does not collect browsing history. It keeps a random identifier for the browser and the label you give the browser profile, passes the state of your YouTube tabs to the DoMe app on the same PC, and makes no internet requests of its own. It is not yet published in the Chrome Web Store.</p>
      </section>
      <section>
        <h2>The AI butler (optional)</h2>
        <ul>
          <li>Talking to DoMe uses your iPhone’s own speech recognition, provided by Apple, which may process your words on Apple’s servers. DoMe listens only after you tap the microphone, until you tap again or finish speaking (at most 15 seconds), and never records, uploads or keeps audio.</li>
          <li>Requests DoMe understands by itself are handled on your phone and go nowhere else.</li>
          <li>Only if you turn on the AI butler (an explicit consent, which you can withdraw in Settings at any time), requests DoMe can’t work out by itself are sent through DoMe’s server to Anthropic, which provides the Claude AI model: those words (including any YouTube search words in them), your earlier requests to the butler and its replies from the past 10 minutes, a short note of what DoMe did by itself in that time (without naming apps, players, videos or searches), the butler’s name and form of address you chose, the time of day (morning, afternoon, evening or night), whether you spoke, typed or tapped one of the butler’s suggested choices, and a few facts about your PC (whether it is online, whether something is playing, volume levels, how many search results are showing, which kinds of actions your phone may perform). DoMe sends Anthropic no account identifier, email address or name.</li>
          <li>Never sent to Anthropic: audio, your screen, the list of apps on your PC or their names, player names, video or window titles, the words of commands DoMe handled by itself, anything typed with the Touchpad keyboard, your clipboard or files.</li>
          <li>DoMe does not store what you say or what the butler answers. It keeps counts (requests and tokens, and their cost) for each 5-hour and weekly allowance window to apply your plan’s allowance. Anthropic’s API data terms apply to the text it receives. [FOUNDER: confirm Anthropic’s API data terms before the butler is switched on; a web search on 2026-11-12 summarised them as deleting API inputs and outputs within 30 days, with exceptions, and other sources conflict. Not verified by counsel.]</li>
          <li>Withdrawing consent applies at once to all your phones. Giving and withdrawing consent are recorded as security events.</li>
          <li>The conversation on your phone is kept in memory only and is cleared when you sign out, switch PC, after 10 minutes without a request, or when the app reloads. Your butler preferences (name, form of address, voice) are stored on the phone.</li>
          <li>The AI butler is not switched on for DoMe accounts yet.</li>
        </ul>
      </section>
      <section>
        <h2>Screen view (beta, optional)</h2>
        <ul>
          <li>Only when the PC’s owner allowed it in a DoMe window on that PC, your PC’s picture goes straight to your phone, encrypted between the two devices. It never passes through the DoMe service, and nothing is recorded or stored. The AI butler never sees it. While Screen view is on, the PC shows a “Screen view on” bar on every screen.</li>
          <li>To connect the two devices, the DoMe service forwards the signed connection set-up between them without reading, storing or logging it. It keeps the session’s timing and the seconds counted toward your plan, never the picture or a network address.</li>
          <li>When your phone cannot reach the PC directly (often on mobile data), the connection may go through a relay server run by Cloudflare. To issue your phone a temporary relay credential, we give Cloudflare the random identifier of your DoMe account (not your name or email), and Cloudflare reports to us how much data its relay carried for each account. Cloudflare sees both devices’ network addresses, connection times and how much data passed; the picture stays encrypted and Cloudflare cannot see it. A Cloudflare lookup server tells each device its own public address, even when the relay is not used.</li>
          <li>The PC keeps a local list of its last 20 Screen view sessions (which phone, when, how long, why it ended, and refused requests); you can clear it on the PC.</li>
          <li>Screen view is switched on only on our test site so far.</li>
        </ul>
      </section>
      <section>
        <h2>What we never collect</h2>
        <ul>
          <li>Your password: you type it only on our sign-in provider’s pages.</li>
          <li>Your card details: you type them only on Stripe’s pages.</li>
          <li>Recordings: DoMe never records audio, and never records or stores the Screen view picture.</li>
          <li>Your browsing history, contacts, photos or location.</li>
          <li>Your Windows computer name or Windows user name.</li>
          <li>Advertising or analytics data: DoMe has no advertising, analytics or session-replay code, and we do not sell your personal information or share it for targeted advertising. [COUNSEL: confirm that no vendor arrangement counts as a “sale” or “sharing” under state law.]</li>
          <li>The content of what you do: our server does not store the words of commands, what you type, YouTube searches, titles of what is playing, or what the AI butler is asked or answers.</li>
        </ul>
      </section>
      <section>
        <h2>Cookies</h2>
        <p>DoMe sets only two cookies. Both are DoMe’s own, both are needed for sign-in, your browser’s scripts cannot read them, and neither is used for advertising or tracking.</p>
        <ul>
          <li>
            <strong>dome_session</strong>: a random identifier that keeps you signed in. Your browser keeps it for 30 days from sign-in. On our side a session also ends after 30 days without use, and after 90 days at most.
          </li>
          <li>
            <strong>dome_reprompt</strong>: holds only the value “1”. It is set for one day when a sign-in is refused because your email address is not yet confirmed, so that the next sign-in shows the sign-in page again. It is sent only to DoMe’s sign-in addresses, and the next successful sign-in removes it.
          </li>
        </ul>
        <p>The pages of our sign-in provider and of Stripe are run by those companies, and any cookies they set are covered by their own policies. [COUNSEL: whether to describe them.] The app also keeps settings in your browser’s storage (see On your phone). Because DoMe does not track you across other websites, it works the same whether or not your browser sends a “Do Not Track” signal. [COUNSEL: confirm this wording.]</p>
      </section>
      <section>
        <h2>Who receives data (sub-processors)</h2>
        <p>We use these companies to run DoMe. Each receives only what it needs for the job described. [COUNSEL: which of them act for us and which act on their own account (for example Stripe for payments), and which data processing agreements to accept.]</p>
        <ul>
          <li><strong>Fly.io</strong> hosts the DoMe service, the relay and this website in its Ashburn, Virginia (USA) region, and runs our production database (Fly Managed Postgres) in the same region. It handles all traffic between your devices and the DoMe service and stores the records described above.</li>
          <li><strong>Auth0</strong> runs sign-in (US region). It holds your email address, your password and your sign-in details, and sends the emails that confirm your address or reset your password. DoMe never sees your password.</li>
          <li><strong>Resend</strong> delivers Auth0’s emails from mail.domeremote.com. It receives your email address and the message.</li>
          <li><strong>Stripe</strong> takes payments when paid plans go on sale. It receives your email address and your DoMe account identifier; you give your card details to Stripe directly.</li>
          <li><strong>Cloudflare</strong> runs the domain-name service for domeremote.com (your app traffic does not pass through Cloudflare) and redirects other DoMe web addresses to domeremote.com. For Screen view it runs the connection relay and lookup servers (connection addresses, timing and byte counts; never the picture), as described under Screen view.</li>
          <li><strong>Anthropic</strong> provides the AI model for the AI butler, only for accounts that turned the butler on. It receives what is listed under The AI butler, and no account identifier.</li>
          <li><strong>Neon</strong> hosts the database of our test site, staging.domeremote.com (in Amazon Web Services’ US East region). It holds the records of people who sign up on the test site, not those of domeremote.com. [COUNSEL: whether the test site needs its own notice.]</li>
        </ul>
        <p>Your devices also talk directly to some companies, under those companies’ own terms: Apple (speech recognition on iPhone), YouTube (searches and playback happen in your own browser) and GitHub (the PC program’s update check, described under On your PC).</p>
        <p>DoMe’s servers and databases are in the United States. [FOUNDER: confirm where Stripe, Resend, Cloudflare and Anthropic process data.] [COUNSEL: whether a statement on transfers outside the United States is needed.]</p>
      </section>
      <section>
        <h2>How long we keep data</h2>
        <p>Our server does not yet run an automatic clean-up. Until it does, the records below are kept for the life of your account unless a period is marked “automatic”; periods marked “planned” are our design and are not yet enforced. [FOUNDER: confirm each period.] [COUNSEL: confirm the periods and the reasons for keeping billing records.]</p>
        <ul>
          <li>Account, PCs, phones and permissions: for the life of the account. Planned: deleted 30 days after the account is deleted.</li>
          <li>Sign-in sessions: end after 30 days without use or 90 days at most (automatic). Planned: the records are deleted 30 days after a session ends.</li>
          <li>PC linking codes: valid 10 minutes (automatic). Planned: deleted 24 hours after they expire.</li>
          <li>Sign-in attempts in progress: deleted when the sign-in finishes; abandoned ones are deleted when someone next starts a sign-in (automatic).</li>
          <li>Command history: planned 30 days.</li>
          <li>Security events: planned 12 months.</li>
          <li>Support requests: for the life of the account; no shorter period is set yet.</li>
          <li>Billing records: planned for the life of the account plus 7 years, as financial records. Stripe keeps its own records under its own terms.</li>
          <li>AI butler usage: per-request records planned 400 days; allowance counts for the life of the account.</li>
          <li>Screen view: daily relay data counts 62 days (automatic); session records planned 90 days; Free allowance windows planned 30 days after each window ends.</li>
          <li>Server logs (redacted): kept by Fly.io for a short time; planned 30 days. [FOUNDER: confirm Fly.io’s log retention.]</li>
          <li>Database backups: as our database host keeps them. [FOUNDER: confirm the backup period and encryption at rest with Fly.io.]</li>
          <li>On your PC and phone: as described above; you control that data.</li>
        </ul>
      </section>
      <section>
        <h2>Deleting your account</h2>
        <p>The app does not have a button to delete your account yet. To delete it, send a request through the support form (topic “Account / sign-in”) or write to [FOUNDER: contact email for privacy requests], and we will delete it by hand. [FOUNDER: whether to build self-serve deletion before launch, and the time within which a request is completed.]</p>
        <p>As DoMe’s database is built, deleting an account deletes every record tied to it: PCs, phones, permissions, sessions, command history, security events, support requests and usage counts. Records of payment events are kept without the link to your account. PC linking codes are also kept without the link to your account until they are cleaned up; each holds the code, the PC’s name and public key and a keyed hash of the network address that asked for it. DoMe can cancel the account’s subscription at Stripe at once when the account is deleted. [COUNSEL: today, deleting an account also deletes DoMe’s own copy of its subscription and top-up records, while the plan above keeps billing records 7 years; confirm which to follow.] [FOUNDER: whether deletion also removes the sign-in at Auth0.]</p>
        <p>Deleting the account does not touch the data on your PC or phone: uninstall DoMe on the PC (answer Yes to delete its data) and use “Forget this installation” on the phone.</p>
      </section>
      <section>
        <h2>Your choices</h2>
        <ul>
          <li>Revoke any phone or unlink any PC at any time from the app; disable remote control locally on the PC (it is off until you turn it on).</li>
          <li>Turn the AI butler off, or withdraw its consent in Settings, at any time.</li>
          <li>Screen view can be allowed only on the PC itself, and you can clear its history there.</li>
          <li>Diagnostics are only created when you ask for them, and you see the file before sharing it.</li>
          <li>Ask us for a copy of your data, a correction or deletion (see Your privacy rights in US states and Deleting your account).</li>
        </ul>
      </section>
      <section>
        <h2>Security</h2>
        <ul>
          <li>Every connection between your devices and DoMe is encrypted in transit (TLS, the encryption behind HTTPS).</li>
          <li>Your phone signs every command with its own key, which cannot be exported from the browser, and your PC accepts only phones you approved on the PC itself.</li>
          <li>Outside Screen view, the PC program never listens for connections from the internet: it makes one outgoing connection to DoMe and accepts only actions from DoMe’s fixed list. During a Screen view session it opens temporary network ports that answer only that session’s phone, and closes them when the session ends.</li>
          <li>Shutting down, restarting, sleeping and closing apps need your confirmation of the exact action; power actions wait 10 seconds by default, and you can cancel them. The “Disable remote control” switch on the PC overrides everything remote.</li>
          <li>We keep secrets only as hashes, remove sensitive values from logs, and keep the PC’s key protected by Windows.</li>
          <li>Our service can read commands, typing and PC status while it routes them; only the Screen view picture is encrypted between your devices.</li>
          <li>No system is perfectly secure. If a security breach affects your personal information, we will tell you as the law requires. [COUNSEL: breach-notice duties, including New Jersey’s, and what an incident plan should contain.]</li>
        </ul>
      </section>
      <section>
        <h2>Children</h2>
        <p>DoMe is not meant for children. You must be at least [FOUNDER: minimum age; suggested default for counsel: 18, because paid plans are contracts and DoMe can shut down a PC] to create an account. We do not knowingly collect personal information from children under 13; if we learn that a child under 13 has an account, we will delete it. [COUNSEL: confirm the minimum age and the wording on children under 13 and on teenagers.]</p>
      </section>
      <section>
        <h2>Your privacy rights in US states</h2>
        <p>[COUNSEL: confirm which state laws apply (for example New Jersey’s and California’s), from what size of business, and adjust this section.] Depending on where you live, you may have the right to:</p>
        <ul>
          <li>know what personal information we hold about you and get a copy of it;</li>
          <li>have it corrected;</li>
          <li>have it deleted;</li>
          <li>opt out of the sale of your personal information, targeted advertising and profiling (DoMe does none of these; [COUNSEL: confirm]);</li>
          <li>appeal if we refuse a request;</li>
          <li>not be treated differently for using these rights.</li>
        </ul>
        <p>To make a request, write to [FOUNDER: contact email for privacy requests] or use the support form. We will check that the request comes from the account holder (for example through your DoMe sign-in) and reply within [COUNSEL: the period the applicable laws require]. Where the law allows, someone you authorise may ask for you. [COUNSEL: how to verify an authorised agent.]</p>
      </section>
      <section>
        <h2>Changes to this notice</h2>
        <p>We will post any change on this page and update the effective date at the top. If a change affects how we use information we already hold, we will tell you in the app or by email before it takes effect. [FOUNDER: DoMe has no way yet to send customers notices by email or in the app; decide how notices will be sent before launch.] [COUNSEL: which changes need your consent and how much notice to give.]</p>
      </section>
      <section>
        <h2>Contact</h2>
        <p>
          [FOUNDER: legal name of the business], [FOUNDER: postal address], [FOUNDER: contact email for privacy requests]. Signed-in users can also use the <Link to="/support">support form</Link>. See also our <Link to="/terms">Terms of service</Link>.
        </p>
      </section>
      <section>
        <h2>Open items for the founder and counsel</h2>
        <ul>
          <li>Business identity: legal name, entity type, postal address and a contact email for privacy requests.</li>
          <li>Which state privacy laws apply, and whether DoMe accepts customers outside the United States (GDPR, UK GDPR); checkout restricts no country today.</li>
          <li>Vendor confirmations: Anthropic’s API data terms; Fly.io’s proxy and log retention, backup period and encryption at rest; where Stripe, Resend, Cloudflare and Anthropic process data; data processing agreements with each sub-processor.</li>
          <li>Retention: confirm each period, build the automatic clean-up, and settle the conflict between deleting billing records with an account and keeping them 7 years. docs/DATA_RETENTION.md is the engineering source for this list; it does not yet cover support requests, top-up purchases, AI allowance tables or some PC and phone files.</li>
          <li>Account deletion and access: no delete button exists; DoMe’s service can list an account’s sessions, command history and security events, but no app screen shows them yet (relevant to access requests).</li>
          <li>Notices: a way to send customers notices (by email or in the app) is not built; it is needed for notices of changes to this notice, of price changes and terms changes, and for renewal reminders.</li>
          <li>The AI butler’s consent text in the app is marked for lawyer review; the copy of it in the design document (docs/design/assistant.md) still says “monthly allowance”, while the allowance uses 5-hour and weekly windows. The consent text does not yet say that the time of day and whether a request was spoken, typed or tapped are sent to Anthropic, as this notice does; it needs the same addition when it is reviewed.</li>
          <li>The test site’s database (Neon) holds real sign-ups (at least the founder’s own account and a test sign-up); whether other people can sign up there is to be confirmed.</li>
          <li>A Chrome Web Store listing for the extension will need a privacy policy link and in-product disclosure.</li>
        </ul>
      </section>
    </article>
  );
}
