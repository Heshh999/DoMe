Prepared from the code on 2026-11-12 for counsel; not legal advice; not reviewed by counsel.
Checked against commit 297f991 of the repository; every open point is marked "for counsel to confirm".

# DoMe outside services (sub-processors)

## What this is

This is one row per outside company that DoMe uses, or that customer data reaches because of DoMe. Each row
says what the company does for DoMe, what customer data it receives, its region, whether it is in use, and the
repository file and line that shows it (`path:line`). What each kind of data is, and how long DoMe keeps it,
is in `docs/legal/data-map.md`.

Line numbers refer to commit 297f991, except those in the draft Privacy page
(`mobile-app/src/pages/public/PrivacyPage.tsx`), which refer to the draft as rewritten on 2026-11-12, the
version sent to counsel with this file, and except those re-pointed on 2026-11-13 (see "How this was checked").

Whether each company is DoMe's processor (acting on DoMe's instructions) or acts on its own account, and
whether it belongs on a published sub-processor list, is for counsel to confirm. The repository records no data
processing agreement (DPA) with any of these companies.

Status words:

- **Live on staging**: in use for https://staging.domeremote.com, which holds real sign-ups (at least the
  founder's own account and a test sign-up; whether anyone else has signed up is not recorded)
  (`docs/PROGRESS.md:220-223`; `docs/LAUNCH_SETUP.md:835-837`).
- **Live in production**: in use for https://domeremote.com, live since 2026-10-15
  (`docs/PROGRESS.md:117-118,1061-1065`). It ran commit 35a203f from 2026-10-20 [GitHub Actions Deploy run
  39464803237, 2026-10-20] and was updated to commit bf83141 on 2026-11-13 [GitHub Actions Deploy run
  43508425555, 2026-11-13] (`docs/PROGRESS.md:132-141`); the founder signed in there after the update
  [founder-reported 2026-11-13]. It holds real accounts: at least the founder's own (`docs/PROGRESS.md:1067-1069`);
  whether anyone else has signed up is not recorded (`docs/PROGRESS.md:121-122`).
- **Planned for production**: set up in the code and guides for domeremote.com, not yet in use there (Stripe's
  live mode, the Screen view relay).
- **Development only**: used by engineers, never with customer data.
- **Not used**: nothing in the shipped code calls it.

Outside research. The privacy and DPA page addresses in section 3 come from web searches made on 2026-11-12.
The pages themselves were not opened in full, and none of it has been checked by counsel.

## Summary for counsel

1. Seven companies receive customer data today, on staging, in production (live at https://domeremote.com since
   2026-10-15, `docs/PROGRESS.md:117-118,1061-1065`) or both: Fly.io (hosting for both, and production's
   database), Neon (staging database), Auth0 (sign-in for both, in one tenant), Resend (sign-in emails for
   both), Stripe (payments; test mode on staging only), Cloudflare (DNS for both, and the Screen view relay on
   staging) and GitHub (downloads of the Windows program since 2026-11-13, row 10). Anthropic (AI) is built in
   but switched off. Whether Google receives sign-ins on staging is unknown (row 5).
2. Fly.io's proxy handles every request between the customers' devices and DoMe's server, so it sees all of it in
   readable form at that point. What it logs, and for how long, is not recorded (row 1).
3. The Screen view relay at Cloudflare is given DoMe's account id with each relay login, and sees both
   devices' network addresses, times and data amounts (row 8).
4. The draft Privacy page rewritten on 2026-11-12 names Fly.io, Auth0, Resend, Stripe, Cloudflare, Anthropic
   and Neon as the companies DoMe uses (`mobile-app/src/pages/public/PrivacyPage.tsx:136-142`), and Apple, YouTube and
   GitHub as companies the customer's devices talk to directly (`PrivacyPage.tsx:144`). It does not mention
   Google sign-in (row 5), or Microsoft, which signs the Windows program and receives no customer data
   (section 2).
5. No analytics, advertising, error-reporting or monitoring company is used (section 2).

## 1. Companies that receive customer data

| # | Company | What it does for DoMe | Customer data it receives | Region | Status | Source |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Fly.io | Runs DoMe's server: the website and phone app, the API and the relay, as one app per environment. Its proxy turns on HTTPS, passes each request to the app and tells the app the client's address. Holds the app's secrets and its log output | Everything sent to or from the server, readable at Fly's proxy, because the app itself listens without encryption behind that proxy: cookies, commands, typed text, PC state, the PC's command results, AI requests, client IP addresses. Server logs (redacted, `docs/legal/data-map.md` section 2). What Fly's proxy itself logs, and for how long, is to confirm | iad (Ashburn, Virginia, USA) | Live on staging and in production (Fly app `domeremote-production`, since 2026-10-15; `docs/PROGRESS.md:117-118,1063`; [GitHub Actions Deploy run 39464803237, 2026-10-20]) | `deploy/fly.toml:18-19,53-58,67-80`; `deploy/launch/environments.toml:9-11,24-58`; `docs/OPERATIONS.md:23-26`; `docs/DATA_RETENTION.md:79-82` |
| 2 | Fly.io Managed Postgres | Production database: Basic plan, 10 GB, Postgres 16, in the same region as the app. In place since the first production deploy on 2026-10-15; a later deploy finds it and keeps it, because "create the database" acts only when the database is missing (`deploy/launch/dome_deploy.py:2709-2712,2828-2832`) | The whole server database (`docs/legal/data-map.md` section 1). Encryption at rest and backup retention are "not yet verified" | iad (Ashburn, Virginia, USA) | Live in production since 2026-10-15 (`docs/PROGRESS.md:117-118,1063`); the deploys of 2026-10-20 and 2026-11-13 found it already connected [GitHub Actions Deploy run 39464803237, 2026-10-20; GitHub Actions Deploy run 43508425555, 2026-11-13] | `deploy/launch/environments.toml:13-18,50-54`; `docs/LAUNCH_SETUP.md:318-329`; `docs/DATA_RETENTION.md:37-39,93-95` |
| 3 | Neon | Staging database on Neon's free plan. Restorable for 6 hours only; no other copy | The whole staging database, including real sign-ups (at least the founder's own account and a test sign-up; whether anyone else has signed up is not recorded). Encryption at rest is "not yet verified" | AWS us-east-1 (northern Virginia, USA) | Live on staging only; production does not use it | `deploy/launch/environments.toml:11,33-34`; `docs/LAUNCH_SETUP.md:178-218,326-329`; `docs/OPERATIONS.md:170-172`; `docs/DATA_RETENTION.md:39-41` |
| 4 | Auth0 (an Okta company) | Sign-in provider (OpenID Connect). Customers type their password on Auth0's pages. Auth0 sends verification and password-reset emails. One tenant (the free plan's only one) with a separate application for staging and for production (cloud-api DECISIONS #124) | Email address, password (DoMe never sees it), name, and whatever Auth0 records about sign-ins (to confirm). It returns to DoMe a subject id, email, verified flag and name. A login made on one site can sign in on the other, where it starts a separate, empty account (cloud-api DECISIONS #124) | One tenant, created with region "US"; the exact US location is to confirm | Live on staging (application "DoMe staging") and in production (application "DoMe") since 2026-10-15 (`docs/PROGRESS.md:117-118,123-124,1065`), both in the one tenant the free plan allows. On 2026-11-13 the founder re-checked the production application's settings and chose to keep sharing the tenant rather than pay for a second one [founder-reported, 2026-11-13]; cloud-api DECISIONS #124 | `docs/LAUNCH_SETUP.md:148-176,274-317`; `docs/adr/0001-foundational-decisions.md:36-39`; `cloud-api/dome_api/auth/oidc.py:277-297`; `deploy/fly.toml:63` |
| 5 | Google (sign-in through Auth0's Google connection) | "Sign in with Google", if that connection is on in Auth0 | If on, Google takes part in the sign-in | To confirm | Whether it is on for the staging application is not recorded. Off for the production application (its `google-oauth2` connection is off) [founder-reported, 2026-11-13]; whether it was on in production before 2026-11-13 is not recorded (`cloud-api/DECISIONS.md:1019-1020`); it stays off until DoMe has its own Google keys (`docs/LAUNCH_SETUP.md:292-294`) | `docs/LAUNCH_SETUP.md:292-294,806-807` |
| 6 | Resend | Email delivery for Auth0's verification and password-reset emails, from the sending domain mail.domeremote.com (free plan: 3,000 a month, 100 a day). DoMe's server sends no email itself | Recipient email addresses and the content of those emails (including the verification or reset link) | To confirm in the repository. Outside research says Resend keeps account data and email logs in the USA (section 3) | Live since 2026-10-20 as the email provider of DoMe's one Auth0 tenant (`docs/PROGRESS.md:1019-1024`), so it sends the emails of both staging and production (cloud-api DECISIONS #124). On 2026-11-12 the Auth0 dashboard's resend-verification button did not reach Resend; cause unknown | `docs/LAUNCH_SETUP.md:803-807,826-837`; `docs/PROGRESS.md:1019-1027`; search of `cloud-api/dome_api`: no email-sending code |
| 7 | Stripe | Payments, subscriptions and the customer billing portal. Checkout and card entry happen on Stripe's own pages. DoMe is the seller (Stripe's Managed Payments is turned off); Stripe Tax is off | The account email, sent once when DoMe creates the Stripe customer, with DoMe's account id as a label; DoMe's account id again on each checkout. Card details and everything customers type on Stripe's pages | To confirm | Test mode on staging (a test purchase and cancellation on 2026-10-26). Staging refuses live keys. The live account was activated and set up on 2026-11-13 (products, a webhook for domeremote.com, the customer portal) [founder-reported 2026-11-13], but payments are not switched on in production: no live values are in GitHub (`docs/LAUNCH_SETUP.md:380-392`) | `cloud-api/dome_api/billing/stripe_provider.py:7,199-207,222-239`; `cloud-api/dome_api/settings.py:147`; `docs/LAUNCH_SETUP.md:375-397,413-415,785-786` |
| 8 | Cloudflare | (a) DNS for domeremote.com; the app's records are "DNS only", so app traffic does not pass through Cloudflare. (b) The redirect-only name www.domeremote.com passes through Cloudflare so it can redirect it; domeremote.app and www.domeremote.app are planned the same way but parked today (`docs/PROGRESS.md:1066`). (c) A STUN server for Screen view, given to both devices. (d) The TURN relay for Screen view; DoMe gets a relay login for the phone only. (e) Hourly usage figures for the relay, read by DoMe | (a) DNS look-ups only. (b) Requests to the redirect names. (c) Both devices' public network addresses. (d) Both devices' network addresses, connection times, data amounts and the encrypted picture (which it cannot see); DoMe passes its account id as the label of each relay login. (e) Data amounts per account id, relay login and day | To confirm (Cloudflare's network is worldwide; the repository states no location) | DNS: live for staging's address and, since 2026-10-15, for domeremote.com itself (apex A and AAAA records, DNS only) and the redirect of www.domeremote.com (`docs/PROGRESS.md:117-118,1063-1065`); domeremote.app is parked (`docs/PROGRESS.md:1066`). TURN and STUN: live on staging since 2026-11-09. Screen view stays off in production until its launch conditions are met; production needs its own relay key | (a, b) `docs/LAUNCH_SETUP.md:248-257,335-339,636-662`. (c) `cloud-api/dome_api/settings.py:190-191`; `pc-agent/dome_agent/screen/worker/rtc.py:176`. (d) `cloud-api/dome_api/screen/turn.py:1-3,67-68,235-240,572`; `shared/protocol/version.json:76`; `mobile-app/src/pages/public/PrivacyPage.tsx:102,140`. (e) `cloud-api/dome_api/screen/turn.py:293-310`. Status: `docs/LAUNCH_SETUP.md:550-554,569-571,844-848` |
| 9 | Anthropic | The AI assistant's language model, through Anthropic's API | Only when the assistant is on and the account has consented: the person's words and whether they were spoken, typed or a tapped choice, up to six recent lines from the last 10 minutes, the assistant's chosen name and form of address, and coarse PC facts (connection state, playback state, volumes, number of search results, part of day, capabilities). No account id, email or user identifier is sent. The API address is fixed in the code | To confirm | Not used: switched off by default, and no deployment sets a key ("the assistant stays off"). Confirming Anthropic's data terms is a release condition | `cloud-api/dome_api/settings.py:149-152`; `docs/LAUNCH_SETUP.md:784`; `cloud-api/dome_api/assistant/service.py:124-142`; `cloud-api/dome_api/assistant/anthropic_provider.py:15-17,50,191-202`; `docs/DATA_RETENTION.md:84-91`; `docs/design/assistant.md:2088-2093` |
| 10 | GitHub | Hosts the source code, automated tests, the Deploy and Release workflows and their secrets. Its public Releases host the Windows installer and the signed update files: DoMe for Windows 0.1.0 since 2026-11-13 | People who download the installer download it from GitHub, so GitHub sees their IP address. Once the PC app's update check is started, it fetches two small files from GitHub about daily, sending only "DoMe-agent/" plus the version, so GitHub sees each PC's IP address and DoMe version. The website itself cannot contact GitHub | To confirm | Source code and workflows: in use. Releases: in use since 2026-11-13 (release v0.1.0, published by the founder before counsel's answers) [GitHub Actions Release run 43513385142, 2026-11-13]. Update check: in the PC app's code, but not started by version 0.1.0 | `pc-agent/dome_agent/update_check.py:16-21,59-65,74-76,292`; `mobile-app/src/content/windowsRelease.ts:1-6,47-50`; `mobile-app/src/content/windows-release.json:6,10`; `docs/LAUNCH_SETUP.md:761-784,868-889,1038-1046`; `docs/PROGRESS.md:23-30`; update check not started `docs/WINDOWS_INSTALL.md:745` |

## 2. Other outside services, and those not used

| Company or kind | Role | Customer data | Status | Source |
| --- | --- | --- | --- | --- |
| Microsoft (Azure Artifact Signing), with its identity partner AU10TIX | Signs DoMe's Windows programs inside the Release workflow, signing in with a short-lived token and no stored Azure password. Basic plan, region East US. Microsoft checked the founder's identity through AU10TIX (ID document and selfie); the certificate shows the founder's legal name and is printed in every signed file | None. The founder's own identity only | Steps 1 to 6 done on 2026-10-30 (signed test builds); signed DoMe for Windows 0.1.0, published on GitHub on 2026-11-13 [GitHub Actions Release run 43513385142, 2026-11-13] (`docs/LAUNCH_SETUP.md:868-889`) | `docs/LAUNCH_SETUP.md:857-863,904-911,917-921,925-931,934-939,943-944`; `.github/workflows/release.yml:271,373` |
| Cloudflare Registrar | Registrar for domeremote.com and domeremote.app, on the founder's account since 2026-10-13 | None | In use | `docs/HANDOFF.md:84-87` |
| Apple (iPhone speech recognition) | Turns spoken requests into text on the customer's iPhone; Apple may process the words on its servers. DoMe does not engage Apple; it uses the browser's built-in feature | The customer's voice, handled by Apple under Apple's terms. DoMe receives only the text | Part of the assistant, which is switched off | `mobile-app/src/lib/speech.ts:5-7`; `mobile-app/src/pages/public/PrivacyPage.tsx:87`; `docs/DATA_RETENTION.md:149` |
| YouTube (Google) | The site the extension controls in the customer's own browser | YouTube searches and viewing go from the customer's browser, as with any YouTube use. DoMe's server sends nothing to YouTube | In use where the extension is installed | `browser-extension/src/shared/youtubeUrl.ts:44-53`; `shared/protocol/version.json:70`; `browser-extension/public/manifest.json:8,10` |
| Chrome Web Store and Edge Add-ons | Would list the extension | None today | Not used yet; the extension is loaded by hand for development, and release builds leave it out | `docs/PRODUCT_AND_PLANS.md:169`; `docs/WINDOWS_INSTALL.md:748` |
| Cloudflare quick tunnels | An engineer's test kit uses them to reach a test server | None (test data only) | Development only | `docs/HANDOFF.md:208-213`; `cloud-api/KNOWN_ISSUES.md:78-80` |
| Analytics, advertising, session replay, error reporting (for example Sentry) | — | — | Not used. The phone app's only runtime libraries are its own UI and helper libraries; its security policy lets it load scripts and connect only to DoMe's own server; fonts are the device's own | `mobile-app/package.json:18-27`; `cloud-api/dome_api/security/headers.py:19-24`; `mobile-app/src/styles.css:239-241`; `mobile-app/src/pages/public/PrivacyPage.tsx:72` |
| Monitoring (Grafana Cloud, Better Stack) | Appear only as cost assumptions | — | Not used; no outside uptime monitor and no log shipping are set up | `docs/COST_MODEL.md:55-57`; `docs/LAUNCH_SETUP.md:795`; `docs/OPERATIONS.md:412` |

## 3. Vendors' public privacy and DPA pages (outside research)

Source: web searches made on 2026-11-12. The addresses come from search results; the pages were not opened in
full and may have changed. Nothing here is verified by counsel.

| Company | Public pages | What the search summaries said |
| --- | --- | --- |
| Fly.io | https://fly.io/legal/privacy/ ; https://fly.io/legal/sub-processors/ ; https://fly.io/mpg | Fly.io has a DPA and lists its own sub-processors, with 30 days' notice before a new one handles customer personal data. Fly Managed Postgres advertises encryption at rest and 10-day rolling backups. The DPA's full text was not found |
| Neon | https://neon.com/subprocessors | Neon's sub-processor list (dated 2026-03-02) forms part of its DPA and terms; a DPA is said to be built into its terms of service. The result for Neon's DPA page showed other content, so the DPA's address is to confirm |
| Auth0 (Okta) | https://www.okta.com/legal/privacy-policy/ | Okta's policy covers Auth0 and says Okta acts as a processor for its customers' data. The current DPA should be taken from Okta's own trust and compliance pages (the copy found was on a third-party site). Auth0's documents show no exact location within the US for US tenants (https://community.auth0.com/t/customer-data-locations/104690) |
| Resend | https://resend.com/legal/privacy-policy ; https://resend.com/legal/dpa ; https://resend.com/legal/subprocessors ; https://resend.com/docs/dashboard/domains/regions | Resend publishes a DPA and acts as a processor for customer data. Account data and email logs are kept in the United States whatever the sending region |
| Stripe | https://stripe.com/legal/privacy-center ; https://stripe.com/legal/dpa | The DPA is part of the Stripe Services Agreement. Businesses in the Americas contract with Stripe, LLC. Stripe says it is a processor when it handles payments for a business, and a controller in other contexts |
| Cloudflare | https://www.cloudflare.com/cloudflare-customer-dpa/ ; https://developers.cloudflare.com/realtime/turn/faq/ | The DPA applies where Cloudflare processes personal data as a processor; copies showed different versions, so the current one is to confirm. The TURN FAQ says Cloudflare processes only the information needed to run the relay |
| Anthropic | https://www.anthropic.com/legal/commercial-terms ; https://www.anthropic.com/legal/data-processing-addendum ; https://privacy.claude.com/en/articles/7996866 | The DPA is part of the Commercial Terms. Anthropic's privacy pages say API inputs and outputs are deleted within 30 days, with exceptions (zero-retention agreements, legal requirements, and usage-policy flags kept up to 2 years). One third-party page claims 7 days. Training on commercial customers' content is reportedly off by default |
| GitHub | https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement | GitHub publishes a privacy statement and a standard data protection agreement; which version applies to DoMe's plan is to confirm |
| Microsoft | https://www.microsoft.com/licensing/docs/view/Microsoft-Products-and-Services-Data-Protection-Addendum-DPA | The Products and Services DPA covers Azure online services; the newest copy found was dated April 2025 |
| Apple | https://www.apple.com/legal/privacy/data/en/ask-siri-dictation/ | Apple's page on Siri and dictation. Whether Safari's built-in speech recognition follows the same rules was not confirmed |
| Google and YouTube | https://policies.google.com/privacy ; https://www.youtube.com/t/terms | Google's privacy policy covers YouTube |
| Chrome Web Store | https://developer.chrome.com/docs/webstore/program-policies/limited-use/ | A listed extension that handles user data needs an accurate privacy policy and clear in-product disclosure (relevant only once the extension is listed) |

## 4. Open questions for counsel

Each of these is for counsel to confirm; none is settled.

1. Which of these companies are DoMe's processors and which act on their own account? Candidates for the
   second group include Stripe (payment data), Apple (speech on the customer's phone), Google and YouTube, and
   GitHub (update checks and downloads).
2. Which DPAs should the founder accept or sign? Are the vendors' click-through terms enough? None is recorded
   in the repository.
3. The repository states no region for Stripe, Anthropic, Cloudflare's relay, GitHub or Resend, nor Auth0's exact
   US location. Does any of these matter for the customers DoMe will accept?
4. What does Fly.io's proxy log about customer requests, and for how long? Fly's proxy sees all customer
   traffic before DoMe's app does.
5. What do Cloudflare's relay and STUN servers keep (addresses, times, DoMe's account id), and for how long? Is
   passing DoMe's account id as the relay label acceptable, or should a different label be used?
6. What does Anthropic keep, and for how long (30 days in one source, 7 in another), before the assistant is
   switched on?
7. The draft Privacy page names the companies in summary item 4 (`mobile-app/src/pages/public/PrivacyPage.tsx:136-144`),
   including Cloudflare, which the launch checklist requires before Screen view goes on in production
   (`mobile-app/src/pages/public/PrivacyPage.tsx:102,140`; `docs/LAUNCH_SETUP.md:844-848`). Is that list complete and
   right? Must Google be named if Google sign-in is on (row 5)? Should Microsoft (section 2) be named?
8. Two engineering notes are out of date: one says the relay code was "Never run against Cloudflare"
   (`cloud-api/KNOWN_ISSUES.md:126`), the other that "nothing here has been run against the real service"
   (`cloud-api/dome_api/screen/turn.py:28-29`). Staging has used Cloudflare's relay since 2026-11-09
   (`docs/LAUNCH_SETUP.md:569-571`). Counsel should rely on the later record.

## How this was checked

Every repository fact above was re-read against the file and line it cites on 2026-11-12, at commit 297f991:
22 statements, each with at least one `path:line` citation (some also rest on a recorded search). After the
Privacy draft was rewritten that day, every citation of it was re-pointed to the rewritten draft and re-read,
and summary item 4 and question 7 were rewritten to match it. A script also confirmed that every cited file
exists and every cited line is inside it. "Search" means a repository-wide text
search, recorded as the source where no single line can show an absence. Section 3 (12 rows) is the only
outside research in this file; it is labelled with its source and date and is not verified by counsel.

On 2026-11-13 the status words, summary item 1 and rows 1, 2, 4, 5, 6 and 8 were corrected: production has been
live since 2026-10-15, and there has only ever been one Auth0 tenant (cloud-api DECISIONS #124). They were
rewritten from `docs/PROGRESS.md`, the record of the production Deploy of 2026-10-20 [GitHub Actions Deploy run
39464803237, 2026-10-20] and the founder's check of the production application's settings [founder-reported,
2026-11-13]. Every citation of `docs/PROGRESS.md`, `docs/LAUNCH_SETUP.md` and `docs/HANDOFF.md`, which changed
that day, was re-pointed to the changed files by matching the cited text (the citations of `docs/PROGRESS.md` had
still given the lines of commit 297f991); the monitoring row in section 2 now cites the monitor line of
`docs/OPERATIONS.md` section 12. The same script confirmed again that every cited file exists and every cited
line is inside it. After a review later that day, each go-live date also cites the entry that states it
(`docs/PROGRESS.md:117-118`), row 5 says that the Google connection's state in production before 2026-11-13 is not
recorded, and every citation into a file changed by that review was re-pointed again.

Later on 2026-11-13 production was updated to commit bf83141. The status word "Live in production" now says so
instead of "being updated", and row 2 adds that day's deploy, which found the database already connected. Both
were rewritten from the records of that day's staging and production Deploys [GitHub Actions Deploy run
43408148197, 2026-11-13; GitHub Actions Deploy run 43508425555, 2026-11-13] and the founder's sign-in after the
update [founder-reported 2026-11-13]; the update cites `docs/PROGRESS.md:132-141`. Stripe and the Screen view relay
stay off in production [GitHub Actions Deploy run 43508425555, 2026-11-13]. The top entry of `docs/PROGRESS.md`
grew by 14 lines, so every citation of it past line 41 was moved down by 14 and re-read against the cited text.
The same script confirmed again that every cited file exists and every cited line is inside it.

Later still on 2026-11-13 DoMe for Windows 0.1.0 was published on GitHub. Summary item 1 and rows 10 (GitHub) and
Microsoft (section 2) now say so; they were rewritten from the record of the release run [GitHub Actions Release
run 43513385142, 2026-11-13] and the release files copied into the website
(`mobile-app/src/content/windows-release.json`). `docs/PROGRESS.md` gained a new top entry, and
`docs/LAUNCH_SETUP.md`, `docs/WINDOWS_INSTALL.md` and `docs/HANDOFF.md` changed, so every citation into them was
re-pointed to the same text (compared with the previous commit) and the checker script was run again.
