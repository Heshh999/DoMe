# Email to counsel: Privacy notice, Terms of service and launch questions

Status: a draft for Justin to send. It asks questions; it is not legal advice. Facts checked against the
repository on 2026-11-12; where each one comes from is listed at the end (do not send that part). The H.264
email (`h264-counsel-email.md`) is separate.

## For Justin only (do not send this part)

Send the email below to a US lawyer who advises small online businesses on privacy and consumer
(subscription) law. One licensed in New Jersey, where you are, is a good fit. If the same lawyer also handles
technology and IP licensing, they can take the H.264 email too; then send both together and say so in the
last paragraph.

The DoMe repository on GitHub is public: anyone can read this folder (`docs/LAUNCH_SETUP.md:66-69`;
`docs/SECURITY.md:312`). So copy the email into your own email program and replace [name] there; do not write
the lawyer's name, your answers or the lawyer's replies into the files in the repository.

Before you send it, fill in whatever you have already decided in your own downloaded copy of
`founder-decisions.md`, so the lawyer reviews your choices rather than blanks.

Attach:

- the Privacy notice and Terms drafts (links or PDFs; `README.md` says how to check you are sending the new
  version);
- `data-map.md` and `subprocessors.md` from this folder;
- `ai-consent-text.md` from this folder: the AI assistant's consent screen, word for word (question 6);
- the Windows licence terms that Setup shows, `pc-agent/packaging/installer/licence-terms.txt` (question 8).

Attach nothing secret (no passwords, keys, tokens, account pages, dashboard screenshots or customer data).
What to expect: a conflict check and an engagement letter first. This is a bigger job than the H.264 consult
(two documents, a short licence, seventeen questions and the [COUNSEL] notes in the drafts), so ask for a
fixed fee or an estimate that covers all of it before you agree, and ask for the answers in writing.

## The email

Subject: Pre-launch review: Privacy notice, Terms of service and subscription questions for my app

Hi [name],

I'm Justin Gaskins, a sole founder in New Jersey. Before I start selling DoMe, I'd like you to review my
draft Privacy notice and Terms of service and answer the questions below. The drafts are written to match
what the software does; [COUNSEL] marks points for your judgement, and [FOUNDER] marks details I still owe.

The product. DoMe Remote lets customers control their own Windows PC from their phone: media, volume, apps,
a touchpad and keyboard, and sleep, restart or shutdown after a confirmation. It has a Windows program, a
phone web app, an optional YouTube browser extension, and my server (Fly.io, in Virginia), which relays
commands encrypted in transit but not end-to-end; it can read them but does not store their content (only
the action's name, timing and outcome). Two optional features are built but not offered yet: Screen view
(beta), a live encrypted picture of the PC on the phone, sometimes through Cloudflare's relay (on only on my
test site); and an AI assistant (called "the AI butler" in the app and in the drafts) that, after the customer
consents, sends requests to Anthropic's API (not switched on anywhere). Plans: Free (no card); Pro
$9.99/month or $99.99/year; Pro Plus $24.99/month or $249.99/year; one-off AI top-ups ($2.99). Payment is
through Stripe, with DoMe as the seller and any tax included. Nothing is on sale yet, and DoMe will be small
at launch. I attach a data map and a vendor list, traced to the code, the AI consent screen word for word,
and the short licence terms my Windows installer shows.

My questions:
1. Renewal and cancellation. Plans renew automatically; customers cancel online in Stripe's portal, at
   period end. Under each subscribe button, and under each paid plan on my Pricing page, the app says in
   bold, larger type that the plan renews automatically at the price shown until cancelled, says how to
   cancel, and links to the Terms and the Privacy notice. There is still no tick box, and checkout records
   no acceptance. Is that wording and placement enough? What consent step, confirmation and yearly-renewal
   reminders do I need under federal, California, New Jersey and other state laws? Should I follow the
   strictest state everywhere? My own server sends no email and the app cannot show notices yet
   (only my sign-in provider sends sign-in emails); how must reminders and notices of price or terms changes
   be sent?
2. Refunds and card disputes. Today: no partial refunds, refunds by hand, no trial, and top-ups never expire.
   What refund policy should I publish, and are never-expiring credits a problem? When a customer disputes a
   card payment (a chargeback), my system only flags it for me; access is not paused. What should my Terms
   say about access while a payment is disputed, and what records should I keep to answer a dispute?
3. Privacy laws. Which state laws apply at launch, and from what size (New Jersey's, California's,
   others)? What must the notice contain regardless? The app has no analytics or advertising code; could a
   vendor arrangement still count as a "sale" or "sharing"? Is a keyed hash of an IP address personal
   information?
4. Account deletion. Customers can delete their account in the app (Settings, Delete account). They type
   a phrase and, unless they did so in the last 10 minutes, sign in again with their password. The
   subscription is cancelled at once with no automatic refund, unused AI top-up requests are lost, every
   phone and PC is disconnected, and the email and name are removed at once; the account is erased 30
   days later (later while a payment matter is still open). My own payment records are kept without the
   name or email; Stripe keeps its own records. The sign-in login at Auth0 is not deleted automatically: I
   delete it by hand on request. Is this enough? How fast must I handle a deletion request sent to
   support, and how long must I keep billing records?
5. Minimum age. The drafts propose 18, because paid plans are contracts and the app can shut down a PC. Is
   that right, and what must I do about children under 13?
6. The AI assistant. It proposes up to three steps from a fixed list of actions; simple ones such as pause
   run without a tap. Are my consent screen (attached word for word, since it is not switched on anywhere
   you could see it) and "the AI can make mistakes" wording enough? Do state chatbot laws or Anthropic's
   usage policy require more?
7. Misuse. Are my acceptable-use terms enough against someone controlling or watching another person's PC,
   given computer-misuse and wiretap laws and that the screen can show other people's messages? Screen view
   is also planned to carry the PC's sound later. What should I do when told about misuse, or asked by law
   enforcement?
8. Liability, law and disputes. Do the "as is" disclaimer and a cap at 12 months' fees work under New
   Jersey's TCCWNA? Which governing law, venue and dispute terms (arbitration, class waiver) do you advise?
   The short Windows licence terms that Setup shows (attached) should match the Terms on all of this:
   what should their liability and governing-law wording say?
9. Business entity. Sole proprietor or LLC before launch? My Windows code signing is in my own name. Should
   vendor accounts move to an LLC, and what insurance do I need?
10. Trademark. Could you run or recommend a clearance search for "DoMe" and "DoMe Remote" for software and
    online services? My browser extension is named "DoMe for YouTube"; is using "YouTube" in its name a
    problem (question 15)?
11. Vendors. Which of Fly.io, Neon, Auth0, Resend, Stripe, Cloudflare, Anthropic and GitHub act as my
    processors, and which data processing agreements should I accept or sign?
12. Export and sanctions. DoMe uses standard encryption and will be downloadable from anywhere. Must I
    classify it or file anything under US export rules, and should I sell only in the US or block
    sanctioned countries?
13. Source code and ownership. All of DoMe's source code (server, Windows program, web app, and these
    drafts and emails) is in a public GitHub repository with no licence file. It stays public because, on
    GitHub's plans below Enterprise, a private repository loses the approval step my deploys rely on. The
    repository also holds the FFmpeg build scripts my H.264 email asks about. My drafts call DoMe's code
    closed-source. What licence or notice should the repository carry? Is "closed-source" the right
    wording? Does the public code change your answer to question 12? Do I, or a future LLC, clearly own all
    of it, given that it was written with an AI coding tool (every change in the repository's history names
    the tool as a co-author)? Separately, my phone web app includes open-source libraries (React, React
    Router, zustand and Ajv under the MIT licence, idb under the ISC licence and jsQR under the Apache
    License 2.0, plus the smaller libraries these use), and my browser extension includes Ajv; neither shows a
    notices page. Do they need one?
14. Security and incidents. DoMe can restart or shut down a customer's PC (after the customer confirms), my
    server can read commands and typed text while routing them, and the Windows program offers updates that
    must carry a signature made with my update key, which is kept as a secret of my release workflow on
    GitHub. The program never installs anything by itself; the customer starts any new installer. If there
    were a breach (for example someone reading traffic on my server, or a forged update), what must I do,
    and how fast, under New Jersey's and other states' notice laws? What should my written incident plan
    and security program contain? I have only an outline of an incident procedure, never run. Do the Terms
    or the Windows licence need wording about updates?
15. YouTube. My optional browser extension, named "DoMe for YouTube", controls YouTube's player in the
    customer's own browser (play, pause, volume, search; it never skips ads). Do YouTube's terms or brand
    rules, or the Chrome Web Store's policies, require changes to its name, to the extension or to my Terms?
16. Advertising claims. My pages describe paid Screen view as "without the Free plan's time limit
    (fair-use limits apply)", with an example (a monthly limit on relay connections per account), and
    everyday commands as free and "never counted against AI limits", next to a fair-use limit of 120
    commands a minute from each phone. Nothing is called "unlimited". My FAQ says Screen view is not in the
    app yet, and that hearing the PC's sound and a TV-style remote are coming later. AI requests are also
    shown as minutes. Is this wording enough, and what else do I need?
17. Accessibility. My website and phone app aim for WCAG AA contrast and 44-pixel touch targets, and handle
    VoiceOver for the assistant, but publish no accessibility statement. Do I need one, or a particular standard, under US disability law?

Beyond these questions, the drafts carry [COUNSEL] notes on: notice of changes to the terms, to the notice
and to prices; notice before suspension, and refunds if I close a paid account; cookies set by my sign-in
and payment providers, and Do Not Track; transfers outside the US; response times and authorised agents for
privacy requests; which changes to the privacy notice need consent; standard licence limits; whether my
rate limits must be published; whether a test site needs its own notice; and the liability wording of the
Windows licence terms. Please include them in your review and your estimate.

My H.264 patent and FFmpeg (LGPL) questions for Screen view are in a separate email.

Written answers I can keep with my launch records would be ideal. My code repository is public, so I will
not keep your advice there: where and how would you suggest I keep it? Could you tell me your fee or an
estimate up front?

Thanks,
Justin Gaskins
Founder, DoMe
New Jersey

## Where the facts come from (for Justin and an engineer; do not send)

Paths are from the top of the repository; line numbers as of 2026-11-12, with the Privacy and Terms drafts as
rewritten that day.

- Founder in New Jersey: `docs/legal/h264-counsel-email.md:24`.
- The repository is public: `docs/LAUNCH_SETUP.md:66-69`; `docs/SECURITY.md:312`; `.github/workflows/deploy.yml:13`.
- What DoMe controls, confirmation before power actions: `docs/PRODUCT_AND_PLANS.md:31-33`;
  `pc-agent/dome_agent/authz.py:279-291`; `pc-agent/dome_agent/actions/power.py:86-95`.
- Four parts, server in Virginia on Fly.io: `mobile-app/src/pages/public/PrivacyPage.tsx:21`;
  `deploy/fly.toml:18-19`.
- Encrypted in transit, not end-to-end: `mobile-app/src/pages/public/PrivacyPage.tsx:64`;
  `docs/adr/0001-foundational-decisions.md:167-178`. The server keeps a record of each command (action name,
  state, error code, times and a hash), not its content: `cloud-api/dome_api/db/models.py:320-344`;
  `mobile-app/src/pages/public/PrivacyPage.tsx:43`.
- Screen view encrypted, Cloudflare relay: `shared/protocol/version.json:76-77`;
  `cloud-api/dome_api/screen/turn.py:1-3`. On only on staging: `cloud-api/dome_api/settings.py:175`;
  `docs/LAUNCH_SETUP.md:492-499,514-516`.
- Assistant only after consent, not switched on: `cloud-api/dome_api/settings.py:150`;
  `docs/LAUNCH_SETUP.md:724`; `mobile-app/src/pages/public/PrivacyPage.tsx:89`. Called "the AI butler":
  `mobile-app/src/pages/public/TermsPage.tsx:120`; `mobile-app/src/pages/public/PrivacyPage.tsx:85`;
  `mobile-app/src/components/AssistantConsentSheet.tsx:24`.
- Prices and top-up: `shared/protocol/plans.json:149-164`. Free needs no card: `docs/PRODUCT_AND_PLANS.md:18`.
- DoMe the seller, tax included: `cloud-api/dome_api/billing/stripe_provider.py:222-237`;
  `cloud-api/DECISIONS.md:522-531`; `shared/protocol/plans.json:166`.
- Nothing on sale: `docs/LAUNCH_SETUP.md:334-342`.
- Question 1: renewal and cancellation in Stripe's portal `mobile-app/src/pages/public/TermsPage.tsx:82,93-94`;
  `docs/LAUNCH_SETUP.md:409-411`. The renewal statement (bold, larger), how to cancel and the Terms and
  Privacy links `mobile-app/src/content/SubscriptionTerms.tsx:1-42`; `mobile-app/src/content/plans.ts:161-178`;
  under each subscribe button `mobile-app/src/pages/app/BillingPage.tsx:400` and each paid plan on Pricing
  `mobile-app/src/pages/public/PricingPage.tsx:212`. No tick box and no acceptance at checkout
  `cloud-api/dome_api/billing/stripe_provider.py:222-237`; `mobile-app/src/pages/public/TermsPage.tsx:15,85`.
  No email sent by DoMe's server: search of `cloud-api/dome_api` (no email-sending code); Auth0's emails go
  through Resend `docs/LAUNCH_SETUP.md:743-745`. No in-app notice feature: search of `mobile-app/src`. The
  drafts' notice promises and [FOUNDER] notes: `TermsPage.tsx:34,108,157`; `PrivacyPage.tsx:213`.
- Question 2: `mobile-app/src/pages/public/TermsPage.tsx:102`; `docs/BILLING.md:298,334-335,408`;
  `cloud-api/KNOWN_ISSUES.md:107-109`; `shared/protocol/plans.json:2`. Disputes flagged, access not paused:
  `docs/BILLING.md:49,264,337-338`.
- Question 3: no analytics code `mobile-app/package.json:18-27`; `cloud-api/dome_api/security/headers.py:19-24`.
  Keyed IP hash `cloud-api/dome_api/security/tokens.py:41-44`.
- Question 4: the button and what it says `mobile-app/src/components/DeleteAccountSheet.tsx:80-100`; the route,
  its phrase and the 10-minute password check `cloud-api/dome_api/routes/account.py:28-58`;
  `cloud-api/dome_api/account_deletion.py:64`; cloud-api DECISIONS #121, #123; erased 30 days later, not while
  billing is open `cloud-api/dome_api/purge.py:69,194-214`; payment records kept without name or email
  `cloud-api/KNOWN_ISSUES.md:210-218`; `docs/BILLING.md:375-395`; the Auth0 login deleted by hand
  `cloud-api/KNOWN_ISSUES.md:210-218`; `mobile-app/src/pages/public/PrivacyPage.tsx:168`.
- Question 5: `mobile-app/src/pages/public/TermsPage.tsx:25`; `mobile-app/src/pages/public/PrivacyPage.tsx:196`.
- Question 6: three steps, fixed list `docs/design/assistant.md:409-445`; simple actions run at once
  `mobile-app/src/content/plans.ts:254`; consent screen `mobile-app/src/components/AssistantConsentSheet.tsx:1-5,24-48`,
  copied in `docs/legal/ai-consent-text.md`; mistakes wording `mobile-app/src/pages/public/TermsPage.tsx:124`.
- Question 7: PC sound planned `docs/design/screen-view.md:75-77`; `mobile-app/src/content/faq.tsx:121`.
- Question 8: `mobile-app/src/pages/public/TermsPage.tsx:145`; `pc-agent/packaging/installer/licence-terms.txt:21-24`.
- Question 9: signing in your own name `docs/LAUNCH_SETUP.md:792-797,814-817`.
- Question 10: the extension's name `browser-extension/public/manifest.json:3`.
- Question 11: `docs/legal/subprocessors.md` section 1.
- Question 12: encryption `mobile-app/src/pages/public/PrivacyPage.tsx:100,185`; public downloads on GitHub
  `docs/LAUNCH_SETUP.md:950-957`; checkout restricts no country `mobile-app/src/pages/public/TermsPage.tsx:26`.
- Question 13: public repository, as above; no licence file at the top of the repository (`git ls-files`: the
  only licence texts are third-party ones under `pc-agent/packaging/licenses`); "closed-source"
  `mobile-app/src/pages/public/TermsPage.tsx:56`; `pc-agent/packaging/THIRD_PARTY_NOTICES.txt:21-22`; the
  FFmpeg build script is in the repository `pc-agent/packaging/THIRD_PARTY_NOTICES.txt:45-46`
  (`.github/workflows/ffmpeg-lgpl.yml`); every one of the 62 commits up to 297f991 names an AI coding tool
  as co-author (`git log`); the web app's and extension's libraries `mobile-app/package.json:18-27`;
  `browser-extension/package.json:15-18`; their licences from the `license` field of each package as
  installed at those pinned versions (read on 2026-11-12); no notices file in `mobile-app/public` or `browser-extension/public`
  (directory listing).
- Question 14: power actions need confirmation `mobile-app/src/pages/public/PrivacyPage.tsx:188`; the server
  reads what it routes `PrivacyPage.tsx:64,190`; signed updates and no automatic install
  `pc-agent/dome_agent/update_check.py:1-25,66-69`; publishing a release `docs/LAUNCH_SETUP.md:950-957`;
  incident outline "procedure, not yet run" `docs/OPERATIONS.md:340-345,425`; the breach note
  `PrivacyPage.tsx:191`.
- Question 15: `browser-extension/public/manifest.json:3,7-10`; no ad skipping
  `browser-extension/src/content/adapter.ts:30,102`; `mobile-app/src/pages/public/TermsPage.tsx:58`; not
  yet listed in the Chrome Web Store `docs/WINDOWS_INSTALL.md:737`.
- Question 16: the paid Screen view and everyday-command wording `mobile-app/src/content/plans.ts:109,113,209`;
  `mobile-app/src/lib/pricing.ts:44-45`; no "unlimited" on any rendered public page
  `mobile-app/test/public/honestClaims.test.tsx`; the per-phone command limits
  `shared/protocol/plans.json:28-33`; `docs/PRODUCT_AND_PLANS.md:47`; Screen view relay limits
  `mobile-app/src/pages/public/TermsPage.tsx:116`; features not built `mobile-app/src/content/faq.tsx:123`;
  AI requests as minutes `mobile-app/src/content/plans.ts:125-131,150-155`; the Terms' limits wording
  `TermsPage.tsx:134,140`.
- Question 17: `mobile-app/README.md:61`; `mobile-app/DECISIONS.md:481`; `docs/design/assistant.md:2139`; no
  accessibility statement (search of `mobile-app/src`).
- The [COUNSEL] notes paragraph: every note, with the question or review that covers it, is listed in
  `docs/legal/founder-decisions.md` section 8.
- The legal topics behind the questions (subscription law, state privacy laws, children's privacy, chatbot
  laws, computer-misuse and wiretap laws, TCCWNA, export and sanctions, trademark, DPAs, breach notice,
  copyright in AI-assisted code, open-source notices, platform terms, advertising law and accessibility)
  come from web-search summaries read on 2026-11-12, not from the full pages, and are not verified by
  counsel, or are general topics a lawyer is asked about. The email asks questions about them; it states
  no conclusion.
