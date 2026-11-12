# Decisions only the founder can make

Status: a checklist for Justin, prepared on 2026-11-12. It is preparation for counsel, not legal advice.
Where the repository or the research suggests a default, it is marked "suggestion, not legal advice"; the
lawyer confirms the final wording. Facts cite the repository as `path:line`, with paths from the top of the
repository. Line numbers in the Privacy and Terms drafts are those of 2026-11-12; if they have moved, search
for the quoted text. Outside research is labelled with its source and date and has not been checked by a
lawyer.

**The DoMe repository on GitHub is public** (`docs/LAUNCH_SETUP.md:66-69`; `docs/SECURITY.md:312`): anyone can
read this folder. Fill in a downloaded copy of this file on your own computer (on github.com, open the file
and use the "Download raw file" button). Do not add your answers, your address or the lawyer's replies to the
repository.

How to use it. In your own copy, tick a box when you decide, and write your answer after "Your answer:". Items
marked "ask counsel first" are easier to settle once the lawyer has answered email 2
(`privacy-terms-counsel-email.md`); the question number is given. You may send your filled-in copy to the
lawyer with email 2; read it through first. Section 7 lists every [FOUNDER] placeholder in the drafts and the
decision that fills it; section 8 lists every [COUNSEL] note and the email question that covers it.

## 1. The business

- [ ] **1.1 Legal name and type of business: sole proprietor or LLC.** Ask counsel first (question 9).
  - Why it matters: this name goes into the Terms, the Privacy notice, the Windows licence terms and the
    copyright line, and Stripe needs business details before it takes real payments.
  - What the repository shows: no business is named anywhere; every draft says "[FOUNDER: legal name of the
    business]" (section 7). Stripe's live mode needs business details, a bank account and identity
    (`docs/LAUNCH_SETUP.md:629`), and its payment page needs a public business name
    (`docs/LAUNCH_SETUP.md:628`). Business details on invoices are an open billing decision
    (`docs/BILLING.md:412`). Windows code signing uses your individual identity, so Windows shows your legal
    name as the publisher (`docs/LAUNCH_SETUP.md:766-771,788-791`), while Setup names the publisher "DoMe"
    (`pc-agent/packaging/installer/DoMe.iss:78`).
  - Outside research (New Jersey Division of Revenue fee page, via web search on 2026-11-12, not verified by
    counsel): forming a New Jersey LLC costs $100, and its annual report $75.
  - Suggestion: none in the repository. Decide before Stripe live mode and before the first public Windows
    release, because both carry the name.
  - Your answer:

- [ ] **1.2 Publisher name on Windows (code signing).** Decide together with 1.1.
  - What the repository shows: signing as a company later is a new identity, and Windows' trust in DoMe
    (SmartScreen reputation) starts again from zero; the setup guide says to choose now
    (`docs/LAUNCH_SETUP.md:788-791`). Signing costs $9.99 a month (`docs/LAUNCH_SETUP.md:795`).
  - Suggestion: settle it before the first public release, for the reason the guide gives.
  - Your answer:

- [ ] **1.3 Business address for the notices.**
  - Why it matters: the Privacy notice and the Terms end with a postal address
    (`mobile-app/src/pages/public/PrivacyPage.tsx:17,218`; `mobile-app/src/pages/public/TermsPage.tsx:162`).
  - Outside research (FTC CAN-SPAM guide, via web search on 2026-11-12, not verified by counsel): marketing
    email must show a valid postal address, which may be a registered PO box or private mailbox.
  - Suggestion, not legal advice: a PO box or mailbox service rather than your home address, if the lawyer
    agrees it also works for the Terms and the Privacy notice.
  - Your answer:

- [ ] **1.4 Contact email for privacy requests and support.**
  - Why it matters: the drafts send privacy requests and account deletions to "[FOUNDER: contact email for
    privacy requests]" and the Terms to "[FOUNDER: contact email]". A direct support contact
    (`VITE_DOME_SUPPORT_URL`) is still an open production item (`docs/LAUNCH_SETUP.md:715`). Without it,
    someone who cannot sign in cannot reach DoMe, because the support form needs sign-in
    (`mobile-app/src/pages/public/SupportPage.tsx:183-189`). The FFmpeg source offer in the Windows notices
    points to that Support page (`pc-agent/packaging/THIRD_PARTY_NOTICES.txt:51-55`).
  - What exists: mail.domeremote.com only sends Auth0's emails, from a no-reply sender, through Resend
    (`docs/LAUNCH_SETUP.md:717-719`). The repository records no mailbox that receives email.
  - Suggestion, not legal advice: one address on domeremote.com that you read, used for privacy requests and
    support at first, and set as the support contact. Not a personal address.
  - Your answer:

- [ ] **1.5 Copyright line.**
  - Where: "Copyright (c) [FOUNDER: year] [FOUNDER: legal name of the business]."
    (`pc-agent/packaging/THIRD_PARTY_NOTICES.txt:22`) and "© [FOUNDER: year] [FOUNDER: legal name of the
    business]." (`mobile-app/src/pages/public/TermsPage.tsx:56`).
  - Suggestion, not legal advice: the year DoMe is first published, and the name chosen in 1.1.
  - Who owns DoMe's code, and whether the public repository needs a licence or notice, is question 13 of
    email 2: the source code is in a public repository with no licence file, and it was written with an AI
    coding tool. Outside research (US Copyright Office report of 2025-01-29, via web search on 2026-11-12, not
    verified by counsel): copyright needs human authorship.
  - Your answer:

## 2. Who can use DoMe, and under which law

- [ ] **2.1 Minimum age.** Ask counsel first (question 5).
  - Where: `mobile-app/src/pages/public/TermsPage.tsx:25`; `mobile-app/src/pages/public/PrivacyPage.tsx:196`.
    Nothing else in the repository states an age.
  - Outside research (Federal Register, via web search on 2026-11-12, not verified by counsel): the amended
    federal rule on children under 13 (COPPA) had to be met by 2026-04-22.
  - Suggestion, not legal advice: 18, the drafts' own suggested default, "because paid plans are contracts
    and DoMe can shut down a PC" (`PrivacyPage.tsx:196`).
  - Your answer:

- [ ] **2.2 Launch regions: the United States only, or anywhere.** Ask counsel first (question 12).
  - Where: "[FOUNDER: launch regions, for example the United States only]"
    (`mobile-app/src/pages/public/TermsPage.tsx:26`). Checkout restricts no country today (the [COUNSEL] note on
    the same line; `PrivacyPage.tsx:225`).
  - Outside research (web search on 2026-11-12, not verified by counsel): EU privacy law can reach a business
    that targets people in the EU; US sanctions rules apply to a service reachable from anywhere.
  - Suggestion, not legal advice: the United States only at launch, the draft's own example.
  - Your answer:

- [ ] **2.3 Governing law.** Ask counsel first (question 8).
  - Where: "[FOUNDER: governing law (New Jersey?)]" (`TermsPage.tsx:149`;
    `pc-agent/packaging/installer/licence-terms.txt:23`). Venue, arbitration and a class-action waiver are
    left to the lawyer (`TermsPage.tsx:153`).
  - Suggestion, not legal advice: New Jersey, where you are (`docs/legal/h264-counsel-email.md:24`), as the
    drafts guess. Outside research (law-firm summaries, via web search on 2026-11-12, not verified by
    counsel): New Jersey's consumer-contract law (TCCWNA) affects how the disclaimer must be worded.
  - Your answer:

## 3. Money

- [ ] **3.1 Refund policy** for monthly plans, yearly plans and AI top-ups. Ask counsel first (question 2).
  - What is built today: cancelling stops the next renewal, the part-used period is not refunded, refunds are
    made by hand in Stripe, and a refund does not change the plan (`TermsPage.tsx:102`;
    `docs/BILLING.md:292-301,332-339`). Unused top-ups on account deletion are undecided (`TermsPage.tsx:103`).
  - Outside research (Stripe's pages, via web search on 2026-11-12, not verified by counsel): Stripe charges $15
    for each card dispute, and too many disputes bring card-network fines.
  - Suggestion, not legal advice: keep what is built, and add the draft's own example, "a refund on request
    within a set number of days after a first payment or a yearly renewal" (`TermsPage.tsx:103`); you choose
    the number of days.
  - Your answer:

- [ ] **3.2 Free trial.**
  - What is built: no trial (`cloud-api/KNOWN_ISSUES.md:107-109`; `docs/BILLING.md:412`); the Terms draft says
    "There is no free trial" (`TermsPage.tsx:102`).
  - Suggestion, not legal advice: no trial at launch. Free already needs no card
    (`docs/PRODUCT_AND_PLANS.md:18`), and outside research (law-firm summaries, via web search on 2026-11-12, not
    verified by counsel) says California's renewal law also covers trials that turn into paid plans.
  - Your answer:

- [ ] **3.3 Yearly plans at launch, or monthly only.** Ask counsel first (question 1).
  - What is built: Pro at $99.99 a year and Pro Plus at $249.99 a year (`shared/protocol/plans.json:152-158`).
    Payments need all five Stripe prices, the yearly ones included (`cloud-api/dome_api/settings.py:324-325`;
    `docs/LAUNCH_SETUP.md:617`), so leaving yearly plans out needs an engineer's change. DoMe's server sends no
    email of its own (`docs/legal/subprocessors.md`, row 6), and how Stripe's own customer emails are set is not
    recorded (see 4.6).
  - Outside research (law-firm summaries, via web search on 2026-11-12, not verified by counsel): several
    states require a reminder before a yearly plan renews (Maryland: 15 to 45 days before, from 2026-06-01),
    and a New Jersey statute on contracts of a year or more could not be confirmed either way.
  - Suggestion, not legal advice: offer yearly plans once renewal reminders are in place (from Stripe or from
    DoMe) and the lawyer has approved their wording; if that will not be ready at launch, start with monthly
    plans only.
  - Your answer:

- [ ] **3.4 Do AI top-ups ever expire?** "[FOUNDER: confirm that top-ups never expire.]" (`TermsPage.tsx:72`).
  - What is built: they never expire and are kept across plan changes (`shared/protocol/plans.json:2`;
    `docs/BILLING.md:408`).
  - Suggestion, not legal advice: keep "never expires" as built; the lawyer confirms any rules on prepaid
    credits (question 2). Unclaimed-property rules were not researched.
  - Your answer:

- [ ] **3.5 Notice before a price change.** "[FOUNDER: notice period; suggested default for counsel: 30
  days]" (`TermsPage.tsx:108`).
  - Suggestion, not legal advice: 30 days, the draft's own default.
  - Your answer:

- [ ] **3.6 If DoMe closes: how much notice, and whether unused paid time is refunded.**
  "[FOUNDER: notice period]" and "[FOUNDER: refund of unused paid time if DoMe closes.]" (`TermsPage.tsx:140`).
  - Suggestion: none in the repository.
  - Your answer:

- [ ] **3.7 Sales tax, and who is the seller.** Already decided on 2026-10-20: prices include tax, Stripe Tax
  is off, and DoMe is the seller (`docs/PROGRESS.md:787-797`; `cloud-api/DECISIONS.md:522-531`).
  - Still open: where DoMe must register for and collect sales tax (`TermsPage.tsx:79`), a question for an
    accountant. Selling through Stripe's Managed Payments instead (Stripe becomes the seller and handles sales
    tax, for an extra fee) is your business decision (`cloud-api/DECISIONS.md:528-531`).
  - Outside research (New Jersey Treasury bulletin TB-72 and a tax-publisher summary, via web search on
    2026-11-12, not verified by counsel or an accountant): New Jersey generally does not tax software used
    online, while about 30 states tax it.
  - Suggestion, not legal advice: keep the 2026-10-20 decision, and ask an accountant before the paid launch.
  - Your answer:

- [ ] **3.8 What Stripe's customer portal allows.** Proposed: upgrades and monthly-to-yearly take effect at
  once with unused time credited; downgrades and yearly-to-monthly at the end of the period; cancelling at
  the end of the period with no partial refund (`docs/BILLING.md:292-301,407`). The setup guide already asks
  for cancelling at period end and no pausing (`docs/LAUNCH_SETUP.md:387-389`).
  - Suggestion, not legal advice: adopt the proposal; the lawyer checks the wording (questions 1 and 2).
  - Your answer:

- [ ] **3.9 Card disputes (chargebacks): pause paid access while a payment is disputed, or not.** Ask counsel
  first (question 2).
  - What is built: a dispute is recorded and flagged for you, and nothing else happens: access is not paused
    and nothing is cancelled (`docs/BILLING.md:49,264`; `cloud-api/KNOWN_ISSUES.md:107-109`). The billing
    design proposes keeping it that way and leaves refund and dispute wording to you and a lawyer
    (`docs/BILLING.md:332-339`). The Terms say nothing about disputes. Checkout records no acceptance of the
    Terms (`mobile-app/src/pages/public/TermsPage.tsx:15`), which is the kind of record a dispute answer
    would draw on (question 1).
  - Suggestion: none in the repository beyond the proposal above.
  - Your answer:

## 4. Support, privacy requests and deletion

- [ ] **4.1 Support: who answers, through which channel, and the published response time.**
  - What is built: no response time is published until you set one
    (`cloud-api/dome_api/settings.py:123`); until then the app says "A response time has not been published
    for this deployment yet." (`mobile-app/src/pages/public/SupportPage.tsx:194`). There is no screen for
    answering tickets yet (`docs/SUPPORT.md:103-104`).
  - Suggestion, not legal advice: the repository's own rule, "pick a promise that holds on the worst normal
    week" (`docs/SUPPORT.md:103-105`), meaning one you can keep on your own; leave it unset until you are
    sure.
  - Your answer:

- [ ] **4.2 Self-serve account deletion before launch, and how fast a deletion request is completed.** Ask
  counsel first (question 4). "[FOUNDER: whether to build self-serve deletion before launch, and the time
  within which a request is completed.]" (`PrivacyPage.tsx:168`).
  - What is built: no delete route; deletion is a support request (`cloud-api/KNOWN_ISSUES.md:110-111`). The
    billing part exists: it cancels the subscription at once (`docs/BILLING.md:375-385`). The design plans a
    30-day soft delete and then a hard delete, not yet enforced (`docs/DATA_RETENTION.md:46`). The product
    spec asks that deletion revoke access at once and stop billing (`docs/spec/MASTER_PROMPT.md:490`).
  - Suggestion: none in the repository beyond the design above.
  - Your answer:

- [ ] **4.3 Does deleting a DoMe account also delete the sign-in at Auth0?** "[FOUNDER: whether deletion also
  removes the sign-in at Auth0.]" (`PrivacyPage.tsx:169`).
  - Suggestion: none in the repository.
  - Your answer:

- [ ] **4.4 How long each kind of record is kept.** "[FOUNDER: confirm each period.]" (`PrivacyPage.tsx:149`).
  - What is built: the planned periods are listed at `PrivacyPage.tsx:151-162`, from `docs/DATA_RETENTION.md`;
    nothing deletes old records automatically yet (`docs/DATA_RETENTION.md:5-8`). Deleting an account today
    also deletes DoMe's copy of its billing records, while the plan keeps them 7 years (the [COUNSEL] note at
    `PrivacyPage.tsx:169`). After an account is deleted, its PC linking codes and Stripe payment notices stay,
    without the link to the account (`PrivacyPage.tsx:169`; `docs/legal/data-map.md` section 7).
  - Suggestion, not legal advice: take the planned periods to the lawyer as the starting point; an engineer
    builds the automatic clean-up once they are settled.
  - Your answer:

- [ ] **4.5 Who may sign up on the test site (staging).**
  - What the repository shows: staging is public (`deploy/launch/environments.toml:24-28`) and holds real
    sign-ups, at least your own account and a test sign-up (`docs/PROGRESS.md:7-10`;
    `docs/LAUNCH_SETUP.md:748-749`). The Privacy draft asks the lawyer whether the test site needs its own
    notice (`PrivacyPage.tsx:142`).
  - Choice: keep staging open to anyone, or keep it for you and your testers (ask an engineer how).
  - Suggestion: none in the repository.
  - Your answer:

- [ ] **4.6 How DoMe sends customers notices.** "[FOUNDER: DoMe has no way yet to send customers notices by
  email or in the app; decide how notices will be sent before launch.]" (`PrivacyPage.tsx:213`;
  `TermsPage.tsx:108,157`). Ask counsel first (question 1).
  - Why it matters: the drafts promise notice "by email or in the app" before a price change, a change to the
    Terms and some changes to the Privacy notice, and renewal reminders may be required (question 1).
  - What is built: DoMe's server sends no email (a search of `cloud-api/dome_api` finds no sending code); only
    Auth0 sends sign-in emails, through Resend (`docs/LAUNCH_SETUP.md:717-719`). The app has no notice or
    message feature. Stripe can send its own customer emails, but how they are set for DoMe is not recorded.
  - Choice: Stripe's customer emails, email sent by DoMe (an engineer's work and a sending service), a notice
    shown in the app, or a mix.
  - Suggestion: none in the repository.
  - Your answer:

## 5. Things to confirm

The first items are [FOUNDER] look-ups with vendors, not choices. You or an engineer can do them;
`subprocessors.md` section 3 lists each vendor's public privacy and DPA pages (outside research). The last two
are facts other files in this packet ask you to confirm.

- [ ] Fly.io: what its network proxy logs about customer traffic, and for how long (`PrivacyPage.tsx:65`).
- [ ] Fly.io: how long it keeps DoMe's server logs (`PrivacyPage.tsx:161`).
- [ ] Fly.io Managed Postgres: the backup period and encryption at rest (`PrivacyPage.tsx:162`;
  `docs/DATA_RETENTION.md:93-95`).
- [ ] Anthropic: its API data terms, before the AI assistant is switched on (`PrivacyPage.tsx:91`;
  `docs/design/assistant.md:2092-2093`).
- [ ] Where Stripe, Resend, Cloudflare and Anthropic process data (`PrivacyPage.tsx:145`).
- [ ] Data processing agreements: accept or sign each vendor's, as the lawyer advises (question 11). None is
  recorded today (`docs/legal/subprocessors.md:18-19`).
- [ ] Is domeremote.com (production) running, with real accounts? The brief for this packet says production
  is not live yet, but one guide speaks of domeremote.com "today" (`docs/legal/data-map.md`, "Environments";
  `docs/LAUNCH_SETUP.md:313-315`).
- [ ] In the staging Auth0 tenant, is the Google connection on? If yes, Google takes part in sign-ins on the
  test site and must be named in the Privacy notice, which does not mention it today
  (`docs/legal/subprocessors.md` row 5; `docs/LAUNCH_SETUP.md:281-283`).

## 6. Other launch choices the repository leaves to you

- [ ] **6.1 Google sign-in in production: off, or on with your own Google keys.** Still open
  (`docs/LAUNCH_SETUP.md:720-721`). The guide says to turn Auth0's Google connection off for production,
  because Auth0's shared Google keys are not meant for it (`docs/LAUNCH_SETUP.md:281-283`). If it is on, Google
  joins the vendor list.
  - Suggestion: off at launch, as the guide says, until an engineer has set up your own keys.
  - Your answer:

- [ ] **6.2 Listing the browser extension in the Chrome Web Store**, in your own developer account
  (`docs/WINDOWS_INSTALL.md:737`; `docs/HANDOFF.md:461`); $5 once, per the vendor page
  (`docs/COST_MODEL.md:62`). Without a listing, release builds leave the YouTube add-on out
  (`docs/WINDOWS_INSTALL.md:737`). Outside research (Chrome developer pages, via web search on 2026-11-12,
  not verified by counsel): a listing needs a privacy policy link and disclosure inside the product. The
  extension is named "DoMe for YouTube" (`browser-extension/public/manifest.json:3`); whether YouTube's
  terms or the store's policies affect its name or listing is question 15 of email 2.
  - Your answer:

- [ ] **6.3 Buy an Inno Setup commercial licence before the paid launch.** Its makers ask commercial users to
  buy one (`docs/WINDOWS_INSTALL.md:736`; `docs/HANDOFF.md:463`). An action rather than a choice.
  - Your answer:

- [ ] **6.4 Website wording the lawyer may question.** The FAQ says Screen view lets you keep a floating mini
  view, hear the PC's sound and use a TV-style remote (`mobile-app/src/content/faq.tsx:121`); none of the
  three is built. The Screen view design asks you to approve new mini-view wording and recommends it
  (`docs/design/screen-view.md:1538-1541`). Paid plans show "unlimited" Screen view next to relay limits
  (`TermsPage.tsx:116`), and AI requests are shown as minutes (`mobile-app/src/content/plans.ts:107-113`).
  Everyday commands are called "unlimited, always" (`mobile-app/src/content/plans.ts:159,205`), while each
  phone may send at most 120 commands a minute, and slider moves at most 360 a minute
  (`shared/protocol/plans.json:28-33`; `docs/PRODUCT_AND_PLANS.md:47`). Email 2 asks about all of this
  (question 16).
  - Already changed in the Terms draft on 2026-11-12: the Terms used to say "We publish abuse limits", but no
    customer page shows them. They now say "Technical limits protect the service", with a [COUNSEL] note on
    whether the limits must be published (`TermsPage.tsx:134,140`). Your choice: publish the numbers on the
    Pricing or FAQ page (an engineer's change), or keep the Terms' "technical limits" wording.
  - Suggestion, not legal advice: approve the design's recommended mini-view wording, keep "technical limits"
    in the Terms, and let the lawyer judge "unlimited", the minutes and whether the limits must be published.
  - Your answer:

- [ ] **6.5 Approve the AI assistant's consent text** after the lawyer has reviewed it; it is marked for
  founder and lawyer review (`mobile-app/src/components/AssistantConsentSheet.tsx:2-3`). Its words are copied in
  `docs/legal/ai-consent-text.md`, which goes with email 2 (question 6). It does not yet mention two things the
  Privacy draft lists as sent to Anthropic (the time of day, and whether a request was spoken, typed or
  tapped); an engineer adds them when the text is revised (`PrivacyPage.tsx:230`).
  - Your answer:

- [ ] **6.6 Effective date of the Privacy notice and the Terms.** "[FOUNDER: date]" (`PrivacyPage.tsx:13`;
  `TermsPage.tsx:188`).
  - Suggestion: the day the reviewed text goes live, once the lawyer has approved it.
  - Your answer:

- [ ] **6.7 Accessibility: who answers accessibility complaints, and whether to publish a statement.** Ask
  counsel first (question 17).
  - What the repository shows: the app aims for WCAG AA contrast and 44 px touch targets
    (`mobile-app/README.md:61`; `mobile-app/DECISIONS.md:481`) and handles VoiceOver for the assistant
    (`docs/design/assistant.md:2139`). No accessibility statement is published (search of `mobile-app/src`).
  - Choice: who reads and answers accessibility complaints (at first, probably the support contact in 1.4),
    and whether to publish a statement once the lawyer has answered.
  - Suggestion: none in the repository.
  - Your answer:

## 7. Every [FOUNDER] placeholder

On 2026-11-12 the drafts hold 41 [FOUNDER] placeholders in four files, with 24 different texts. Each is quoted
below with where it is and the decision above that fills it. "Privacy" is
`mobile-app/src/pages/public/PrivacyPage.tsx`, "Terms" is `mobile-app/src/pages/public/TermsPage.tsx`,
"Licence" is `pc-agent/packaging/installer/licence-terms.txt` and "Notices" is
`pc-agent/packaging/THIRD_PARTY_NOTICES.txt`.

| Placeholder, quoted | Where | Decision |
| --- | --- | --- |
| [FOUNDER: legal name of the business] | Privacy 17, 218; Terms 13, 56, 162; Licence 23; Notices 22 | 1.1 |
| [FOUNDER: postal address] | Privacy 17, 218; Terms 162 | 1.3 |
| [FOUNDER: contact email for privacy requests] | Privacy 17, 168, 209, 218 | 1.4 |
| [FOUNDER: contact email] | Terms 139, 162 | 1.4 |
| [FOUNDER: year] | Terms 56; Notices 22 | 1.5 |
| [FOUNDER: minimum age; suggested default for counsel: 18] | Terms 25 | 2.1 |
| [FOUNDER: minimum age; suggested default for counsel: 18, because paid plans are contracts and DoMe can shut down a PC] | Privacy 196 | 2.1 |
| [FOUNDER: launch regions, for example the United States only] | Terms 26 | 2.2 |
| [FOUNDER: governing law (New Jersey?)] | Terms 149; Licence 23 | 2.3 |
| [FOUNDER: refund policy for monthly and yearly plans and for AI top-ups (for example, a refund on request within a set number of days after a first payment or a yearly renewal), and whether unused top-ups are refunded when an account is deleted.] | Terms 103 | 3.1 |
| [FOUNDER: confirm that top-ups never expire.] | Terms 72 | 3.4 |
| [FOUNDER: notice period; suggested default for counsel: 30 days] | Terms 108 | 3.5 |
| [FOUNDER: notice period] | Terms 140 | 3.6 |
| [FOUNDER: refund of unused paid time if DoMe closes.] | Terms 140 | 3.6 |
| [FOUNDER: whether to build self-serve deletion before launch, and the time within which a request is completed.] | Privacy 168 | 4.2 |
| [FOUNDER: whether deletion also removes the sign-in at Auth0.] | Privacy 169 | 4.3 |
| [FOUNDER: confirm each period.] | Privacy 149 | 4.4 |
| [FOUNDER: confirm with Fly.io what its network proxy logs about this traffic and for how long.] | Privacy 65 | 5 |
| [FOUNDER: confirm Fly.io’s log retention.] | Privacy 161 | 5 |
| [FOUNDER: confirm the backup period and encryption at rest with Fly.io.] | Privacy 162 | 5 |
| [FOUNDER: confirm Anthropic’s API data terms before the butler is switched on; a web search on 2026-11-12 summarised them as deleting API inputs and outputs within 30 days, with exceptions, and other sources conflict. Not verified by counsel.] | Privacy 91 | 5 |
| [FOUNDER: confirm where Stripe, Resend, Cloudflare and Anthropic process data.] | Privacy 145 | 5 |
| [FOUNDER: DoMe has no way yet to send customers notices by email or in the app; decide how notices will be sent before launch.] | Privacy 213; Terms 108, 157 | 4.6 |
| [FOUNDER: date] | Privacy 13; Terms 188 | 6.6 |

The research for this packet was done at commit 297f991, before the drafts were rewritten on 2026-11-12. It
found seven placeholders; this is where each one went:

| At commit 297f991, quoted | Now |
| --- | --- |
| Privacy 46: [FOUNDER: confirm Anthropic’s data terms and list Anthropic as a sub-processor.] | Anthropic is listed (Privacy 141); its data terms are Privacy 91 (section 5) |
| Privacy 55: [FOUNDER: list the connection relay provider (Cloudflare) as a sub-processor before Screen view is switched on in production.] | Cloudflare is listed (Privacy 102, 140); final once the lawyer approves the notice |
| Privacy 68: [FOUNDER: confirm retention periods and legal basis.] | Periods: Privacy 149 (4.4). Legal basis: a [COUNSEL] note (Privacy 25) |
| Terms 16: [FOUNDER: refund policy, taxes, trial terms.] | Refunds: Terms 103 (3.1). Tax: a [COUNSEL] note (Terms 79; 3.7). Trial: "There is no free trial" (Terms 102; 3.2) |
| Terms 18: [FOUNDER: review against applicable consumer law.] | A [COUNSEL] note (Terms 145) |
| Licence 23: [FOUNDER: the business's legal name, liability and governing law, matching the Terms of service.] | Licence 23-24: name and governing law (1.1, 2.3); liability is a [COUNSEL] note |
| Notices 22: [FOUNDER: the business's legal name and year for the copyright line.] | Notices 22: year and name (1.5) |

If the drafts change again, an engineer can list the placeholders afresh with
`grep -rn "\[FOUNDER" mobile-app/src/pages/public pc-agent/packaging`.

## 8. Every [COUNSEL] note

On 2026-11-12 the drafts hold 36 [COUNSEL] notes: 16 in the Privacy notice, 19 in the Terms and 1 in the
Windows licence terms (the same file names as in section 7; the two header lines, Privacy 13 and Terms 188,
only explain the mark and are not counted). Each is listed below with the email 2 question that covers it.
"Paragraph" means the paragraph of email 2 after question 17 that asks the lawyer to include the remaining
notes in the review and the estimate. Every note is also part of the review of the drafts that email 2 asks
for.

| Note, quoted (shortened where long) | Where | Covered by |
| --- | --- | --- |
| [COUNSEL: whether a statement of the legal basis for each use is needed for any launch region.] | Privacy 25 | Question 3 |
| [COUNSEL: confirm that no vendor arrangement counts as a “sale” or “sharing” under state law.] | Privacy 115 | Question 3 |
| [COUNSEL: whether to describe them.] (cookies set by the sign-in provider and Stripe) | Privacy 130 | Paragraph |
| [COUNSEL: confirm this wording.] (Do Not Track) | Privacy 130 | Paragraph |
| [COUNSEL: which of them act for us and which act on their own account (for example Stripe for payments), and which data processing agreements to accept.] | Privacy 134 | Question 11 |
| [COUNSEL: whether the test site needs its own notice.] | Privacy 142 | Paragraph; `data-map.md` section 9, question 4 |
| [COUNSEL: whether a statement on transfers outside the United States is needed.] | Privacy 145 | Paragraph; question 12 (launch regions) |
| [COUNSEL: confirm the periods and the reasons for keeping billing records.] | Privacy 149 | Questions 3 and 4 |
| [COUNSEL: today, deleting an account also deletes DoMe’s own copy of its subscription and top-up records, while the plan above keeps billing records 7 years; confirm which to follow.] | Privacy 169 | Question 4 |
| [COUNSEL: breach-notice duties, including New Jersey’s, and what an incident plan should contain.] | Privacy 191 | Question 14 |
| [COUNSEL: confirm the minimum age and the wording on children under 13 and on teenagers.] | Privacy 196 | Question 5 |
| [COUNSEL: confirm which state laws apply (for example New Jersey’s and California’s), from what size of business, and adjust this section.] | Privacy 200 | Question 3 |
| [COUNSEL: confirm] (that DoMe does no sale, targeted advertising or profiling) | Privacy 205 | Question 3 |
| [COUNSEL: the period the applicable laws require] (reply time for privacy requests) | Privacy 209 | Paragraph |
| [COUNSEL: how to verify an authorised agent.] | Privacy 209 | Paragraph |
| [COUNSEL: which changes need your consent and how much notice to give.] | Privacy 213 | Paragraph; question 1 (how notices are sent) |
| [COUNSEL: how people should accept these terms at sign-up and before paying (for example a box to tick) and what record of acceptance to keep; ...] | Terms 15 | Question 1 |
| [COUNSEL: US sanctions and export rules for a service and a download that can be reached from anywhere; ...] | Terms 26 | Question 12 |
| [COUNSEL: computer-misuse, wiretap and consent wording, and what DoMe should do when it is told about misuse.] | Terms 47 | Question 7 |
| [COUNSEL: whether to add the usual licence limits (personal, non-transferable, no resale or redistribution) without limiting the rights above.] | Terms 56 | Paragraph; question 13 (licence and "closed-source") |
| [COUNSEL: whether YouTube’s or the Chrome Web Store’s terms need anything here.] | Terms 58 | Question 15 |
| [COUNSEL: how to describe features that are advertised but not yet built.] | Terms 71 | Question 16 |
| [COUNSEL: rules for prepaid credits.] | Terms 72 | Question 2 |
| [COUNSEL, or an accountant: where DoMe must register for and collect sales tax.] | Terms 79 | Not in email 2: a question for an accountant (3.7) |
| [COUNSEL: the disclosures and consent needed before purchase under federal and state automatic-renewal laws, reminders before yearly renewals, and a confirmation after purchase. ...] | Terms 85 | Question 1 |
| [COUNSEL: confirm the wording against state automatic-renewal and consumer laws.] | Terms 103 | Question 2 |
| [COUNSEL: notice rules for price changes, especially for yearly plans.] | Terms 108 | Paragraph; question 1 |
| [COUNSEL: whether “unlimited” may be used next to these limits.] | Terms 116 | Question 16 |
| [COUNSEL: whether a stronger AI disclaimer is needed, given that simple actions run without a tap, and whether state chatbot-disclosure rules apply.] | Terms 129 | Question 6 |
| [COUNSEL: whether these limits must be published, given that the Pricing page calls everyday commands “unlimited”.] | Terms 134 | Paragraph; question 16 |
| [COUNSEL: notice before suspension, and refunds when we close a paid account.] | Terms 140 | Paragraph |
| [COUNSEL: review against applicable consumer law, including New Jersey’s Truth-in-Consumer Contract, Warranty and Notice Act ...; for Free users this cap is zero.] | Terms 145 | Question 8 |
| [COUNSEL: confirm, and whether customers keep the consumer protections of their own state.] (governing law) | Terms 149 | Question 8 |
| [COUNSEL: how disputes are resolved after that: courts and venue or arbitration, any class-action waiver, a small-claims option, and how long to allow for the informal step.] | Terms 153 | Question 8 |
| [COUNSEL: how much notice to give, and whether continuing to use DoMe can count as accepting a change.] | Terms 157 | Paragraph; question 1 (how notices are sent) |
| [COUNSEL: liability wording, matching the Terms of service.] | Licence 24 | Question 8 (the licence terms are attached to email 2); paragraph |

If the drafts change again, an engineer can list the notes afresh with
`grep -rn "\[COUNSEL" mobile-app/src/pages/public pc-agent/packaging`.
