# DoMe legal packet: start here

Status: prepared on 2026-11-12 for Justin, DoMe's founder, to send to a US lawyer. This is preparation for
counsel, not legal advice. Nothing in this folder is a legal conclusion; every open point is for counsel to
confirm. Facts about DoMe cite the repository as `path:line` (paths from the top of the repository). Outside
research is labelled with its source and date and has not been checked by a lawyer.

**This folder is public.** The DoMe repository on GitHub is public (`docs/LAUNCH_SETUP.md:69-72`;
`docs/SECURITY.md:312`): anyone can read this folder. Fill in a downloaded copy of `founder-decisions.md` on
your own computer. Do not add your answers, your address, the lawyer's name or the lawyer's replies to the
repository. Whether keeping the lawyer's advice out of a public place matters for its confidentiality is for
counsel to confirm; email 2 asks them where to keep it.

## What this folder is

This folder holds what a lawyer needs to review DoMe before paid plans go on sale (the website and server have
been live at https://domeremote.com since 2026-10-15, with free accounts only,
`docs/PROGRESS.md:101-102,112-115,1045-1049`; the Windows program, DoMe for Windows 0.1.0, has been published for
download on GitHub since 2026-11-13 as a free beta, by your decision before counsel answers [GitHub Actions
Release run 43513385142, 2026-11-13] (`docs/LAUNCH_SETUP.md:854-875`; `founder-decisions.md` 1.2)), so that all
you have to do is choose a lawyer and send it. It has two emails ready to send, each with its questions; two fact
sheets saying what data DoMe handles and which outside companies receive it, with each fact traced to the code;
the AI assistant's consent screen, word for word; and a list of the business decisions only you can make. The
Privacy notice and Terms of service drafts are pages of the website, and the Windows program's licence terms and
notices ship with the installer; the table below says where each one is. Everything was checked against the
repository on 2026-11-12: commit 297f991, plus the drafts rewritten that day (the Privacy notice, the Terms, and
the Windows licence terms and notices). The Windows release of 2026-11-13 was added to this folder the same day
[GitHub Actions Release run 43513385142, 2026-11-13].

## What is here, and where each piece stands

| Document | What it is | Status on 2026-11-12 |
| --- | --- | --- |
| `h264-counsel-email.md` | Email 1: H.264 video patents and the FFmpeg (LGPL) build used by Screen view | **Ready to send.** Facts checked on 2026-11-09; production status corrected on 2026-11-13 (`docs/PROGRESS.md:101-102`); the Windows release of 2026-11-13 added the same day [GitHub Actions Release run 43513385142, 2026-11-13], with the correction that the FFmpeg build itself has been public as a GitHub pre-release since 2026-10-22 (`docs/WINDOWS_INSTALL.md:545-547`; [GitHub release av-lgpl-17.1.0-ffmpeg-n8.1.3-r3, 2026-10-22]). Not recorded as sent: the latest progress entry still lists "Send the counsel email" as next (`docs/PROGRESS.md:269-270`) |
| `privacy-terms-counsel-email.md` | Email 2: review of the Privacy notice, the Terms and the Windows licence terms, and seventeen launch questions | **Ready to send** once you replace [name] in your own email program. The Windows release of 2026-11-13 added the same day [GitHub Actions Release run 43513385142, 2026-11-13]; question 14 now notes that the drafts say 0.1.0 does not check for updates yet (`mobile-app/src/pages/public/TermsPage.tsx:57`) |
| `ai-consent-text.md` | The AI assistant's consent screen, word for word, with its three allowance sentences | **Ready to attach** to email 2 (question 6). Copied from `mobile-app/src/components/AssistantConsentSheet.tsx`, which is draft text "the founder and a lawyer review" (lines 2-3) |
| `founder-decisions.md` | The decisions only you can make, with suggested defaults; every [FOUNDER] gap and every [COUNSEL] note in the drafts | **To fill in**, in a downloaded copy. Not needed before you send the emails, but the lawyer can then review your choices instead of blanks |
| `data-map.md` | Every kind of information DoMe handles: what, why, where, how long, who can see it | **Ready to attach.** Updated on 2026-11-12 to match the rewritten Privacy draft: its citations point to the new draft, and the four mismatches of the old draft are recorded as fixed (section 8). Corrected on 2026-11-13: 0.1.0 installs the browser bridge program but registers it with no browser (`pc-agent/packaging/installer/DoMe.iss:164,683-686`), and the drafts now say 0.1.0 does not check for updates (`mobile-app/src/pages/public/PrivacyPage.tsx:80`) |
| `subprocessors.md` | One row per outside company: Fly.io, Neon, Auth0, Resend, Stripe, Cloudflare, Anthropic, GitHub and others | **Ready to attach.** Updated on 2026-11-12 to match the rewritten Privacy draft, which names the companies (`mobile-app/src/pages/public/PrivacyPage.tsx:136-144`) |
| Privacy notice | `mobile-app/src/pages/public/PrivacyPage.tsx`, shown at `/privacy` (`mobile-app/src/app/router.tsx:44`) | **Draft for review.** Rewritten on 2026-11-12 to match the code; carries a DRAFT banner and [FOUNDER] and [COUNSEL] marks. Final only after the lawyer's review |
| Terms of service | `mobile-app/src/pages/public/TermsPage.tsx`, shown at `/terms` (`mobile-app/src/app/router.tsx:45`) | **Draft for review.** Same as the Privacy notice |
| Windows licence terms | `pc-agent/packaging/installer/licence-terms.txt`, shown by Setup | **Draft** "for review by the DoMe founder and counsel" (line 3); [FOUNDER] name and governing law, [COUNSEL] liability wording (lines 23-24). Since 2026-11-13 the published Setup of DoMe for Windows 0.1.0 shows this draft to everyone who installs it [GitHub Actions Release run 43513385142, 2026-11-13] (`docs/LAUNCH_SETUP.md:867-869`). **Required attachment to email 2** (question 8); optional attachment to email 1 |
| Third-party notices | `pc-agent/packaging/THIRD_PARTY_NOTICES.txt`, installed with DoMe | **Draft**, "not yet reviewed by counsel" (line 13); [FOUNDER] copyright line (line 22). Optional attachment to email 1 |

**Which version of the drafts the website shows.** The test site, https://staging.domeremote.com, has shown the
rewritten drafts since its deploy of 2026-11-13 (commit 63de996, `docs/PROGRESS.md:164-165`). The live site,
https://domeremote.com, has been up since 2026-10-15 (`docs/PROGRESS.md:101-102,1045-1049`). Until 2026-11-13 it ran
commit 35a203f of 2026-10-20 [GitHub Actions Deploy run 39464803237, 2026-10-20], which showed the older drafts.
On 2026-11-13 it was updated to commit bf83141 [GitHub Actions Deploy run 43508425555, 2026-11-13]
(`docs/PROGRESS.md:116-125`), so its /privacy and /terms now serve the rewritten drafts too, still under the DRAFT
banner; that follows from the commit deployed, not from a look at the pages [GitHub Actions Deploy run
43508425555, 2026-11-13] (`docs/PROGRESS.md:126-129`). After the update you signed in there, and Settings ends with
Delete account [founder-reported 2026-11-13]. So open each page once before you send a link: the new Privacy
notice starts with the heading "Who we are", and the new Terms with "About these terms". If you see "What DoMe is"
or "The service" first, an old draft is showing: send the staging link or a PDF instead, tell an engineer, and do
not print the page while it shows the old draft. The staging site is public and tells search engines not to list
it (`deploy/launch/environments.toml:24-28`; `cloud-api/dome_api/security/headers.py:40-42`), and both pages show
the DRAFT banner (`mobile-app/src/pages/public/DraftBanner.tsx:10`).

## Who to send it to, and in what order

Choose one of two ways:

- **One lawyer for everything.** A US technology lawyer who works with small software businesses and covers
  privacy, consumer subscriptions and IP licensing. Simpler: one engagement letter, one briefing, and answers
  that fit together (for example, the business name decided in email 2 also goes into the Windows files
  that email 1 is about).
- **Two lawyers.** A technology or IP lawyer for email 1 (patents and open-source licences), and a privacy
  and consumer-protection lawyer for email 2. One licensed in New Jersey, where you are, helps for email 2.
  Email 2 also asks about the source code's licence and ownership (question 13), which an IP lawyer may
  answer better; you can forward that question to the IP lawyer.

Finding a lawyer. Outside research (a web-search summary of the New Jersey State Bar Association's public
pages, https://njsba.com/public/, read on 2026-11-12; the pages were not opened in full and this is not
verified by counsel): the state bar association does not refer people to lawyers itself; it points to the
lawyer referral services of the county bar associations, whose staff ask about your need by phone. Ask for a lawyer
who handles technology, privacy and consumer contracts for small online businesses.

Order:

1. Read `founder-decisions.md` and fill in what you have already decided, in your own downloaded copy. Leave
   the rest for the lawyer.
2. Send **email 2** (`privacy-terms-counsel-email.md`) first. Its answers (the business, its legal name,
   governing law) fill gaps in every other document, and the final Privacy notice and Terms are needed
   before the paid launch.
3. Send **email 1** (`h264-counsel-email.md`) the same week, to the same lawyer or to the IP lawyer. Its
   answer is needed only before Screen view is switched on for customers, but it is a short consult, so
   there is no reason to wait. Email 1 does not mention it, but the FFmpeg build scripts its question 5 asks
   about are already public in the repository (`pc-agent/packaging/THIRD_PARTY_NOTICES.txt:45-46`); email 2,
   question 13 says so. With two lawyers, tell the IP lawyer this when you send email 1.
4. With one lawyer, you can send both in one message, or tell them in email 2 that email 1 follows.

## What to attach

Send PDFs. A lawyer may not be able to read the Markdown (`.md`) files as they are: their tables show as long
lines of `|` characters outside GitHub. Ask an engineer to save each file as a PDF from GitHub's formatted
view, or do it yourself: on github.com, open the file, then use your browser's Print and Save as PDF. To get
a file itself (for example `founder-decisions.md` to fill in), open it on github.com and use the "Download raw
file" button.

| Email | Attach | How to get it |
| --- | --- | --- |
| 2 | Privacy notice draft | The link https://staging.domeremote.com/privacy (check the version first, above), or a PDF of that page: your browser's Print, then Save as PDF |
| 2 | Terms of service draft | The link https://staging.domeremote.com/terms, or a PDF, the same way |
| 2 | `data-map.md` and `subprocessors.md` | From this folder, as PDFs (above) |
| 2 | `ai-consent-text.md` (the AI consent screen) | From this folder, as a PDF |
| 2 | `licence-terms.txt` (the Windows licence terms) | `pc-agent/packaging/installer/licence-terms.txt`; it is plain text, so it can be attached as it is |
| 2, optional | `founder-decisions.md`, filled in | Your own downloaded copy, not the one in the repository. Read it through first |
| 1, optional | `THIRD_PARTY_NOTICES.txt` and `licence-terms.txt` | As the note at the top of email 1 says |

**Never attach:** passwords, keys, tokens or signing secrets; settings or `.env` files; screenshots of the
Fly.io, Stripe, GitHub, Auth0, Cloudflare, Neon, Resend or Microsoft dashboards; account or customer ids;
customer data (sign-ups, support requests, database exports, logs); or the source code (the lawyer can be
told it is public, as email 2 does). If the lawyer asks for another repository document, ask an engineer to
check it first: some older engineering notes are out of date. `data-map.md` section 8 lists the known ones;
another is the old price line in `docs/PRODUCT_AND_PLANS.md:222`.

## Launch steps that wait on counsel

| Launch step | What it waits on from the lawyer | Other conditions the repository names | Source |
| --- | --- | --- | --- |
| Screen view switched on for customers (the production switch `DOME_SCREEN_VIEW_ENABLED`) | The answer on H.264 and on the LGPL notice and source offer (email 1); if the answer is no, Screen view ships with VP8 video only. A privacy notice that names Cloudflare, the connection relay, as a sub-processor: the new draft does (`PrivacyPage.tsx:102,140`), and it becomes final with the lawyer's review | The device round on a real PC and iPhone; a signed Windows build that includes Screen view (DoMe for Windows 0.1.0, published 2026-11-13, includes it, switched off for accounts and not yet verified on a device, `docs/LAUNCH_SETUP.md:830-834,864-865`); code signing (done for 0.1.0) and the update check; branch protection with required review | `docs/LAUNCH_SETUP.md:830-834`; `docs/design/screen-view.md:1469-1473`; `docs/ACCEPTANCE.md:569-574`; `docs/HANDOFF.md:441-446` |
| Final Privacy notice and Terms (DRAFT banner removed, effective date set) | Review of both drafts (email 2) | Your [FOUNDER] details (`founder-decisions.md`); a support contact: `support@domeremote.com`, on staging and on the live site since 2026-11-13 (`docs/LAUNCH_SETUP.md:783-788`; `docs/PROGRESS.md:116-125`; [GitHub Actions Deploy run 43508425555, 2026-11-13]); a way to send customers notices, not built (`founder-decisions.md` 4.6). An engineer applies the changes in a "legal round" and updates the test that checks the pages' headings (`mobile-app/test/public/pages.test.tsx`) | `PrivacyPage.tsx:1,13`; `TermsPage.tsx:1,188`; `DraftBanner.tsx:10` |
| Paid plans on sale (Stripe live mode in production) | The renewal disclosures, acceptance step, notices and refund policy (email 2, questions 1 and 2); the business entity (question 9) | Stripe's live activation (business details, bank account, identity); an Inno Setup commercial licence; where to collect sales tax, a question for an accountant | `TermsPage.tsx:79,85,103,108`; `docs/LAUNCH_SETUP.md:697`; `docs/WINDOWS_INSTALL.md:747`; `docs/HANDOFF.md:479` |
| Paid ads | Your choice (2026-11-13): no paid advertising before the lawyer has answered; the advertising-claims question is email 2, question 16 | — | [founder decision, 2026-11-13]; `docs/PROGRESS.md:33-39`; `founder-decisions.md` 1.2 |
| AI assistant switched on, and with it Pro Plus on sale | Review of the consent text and the AI wording (email 2, question 6) | Anthropic's API data terms confirmed; tests on real iPhones and measured costs; the consent text brought in line with the Privacy draft (`PrivacyPage.tsx:230`) | `docs/design/assistant.md:2090-2093`; `docs/DATA_RETENTION.md:104-109`; `AssistantConsentSheet.tsx:2-3`; `docs/BILLING.md:227-230` |
| Self-serve account deletion (built: Settings, Delete account, then erased 30 days later by the hourly clean-up) | Whether the process meets the law, how fast a request sent to support must be completed, and how long billing records must be kept (email 2, question 4) | Your run on staging against Auth0 and Stripe's test mode (so far tested with automated tests only); deleting the Auth0 login by hand when someone asks (`founder-decisions.md` 4.3) | `cloud-api/KNOWN_ISSUES.md:110-111,210-224`; `PrivacyPage.tsx:168`; `DeleteAccountSheet.tsx:80-100` |
| The browser extension in the Chrome Web Store | Whether the privacy notice covers the extension well enough, and whether YouTube's terms or the store's policies affect its name "DoMe for YouTube" (email 2, question 15). Outside research (Chrome developer pages, via web search on 2026-11-12, not verified by counsel): a listing needs a privacy policy link and disclosure in the product | A listing in your developer account, then the extension's id built into the Windows release | `docs/WINDOWS_INSTALL.md:748`; `PrivacyPage.tsx:232`; `browser-extension/public/manifest.json:3` |

## Coming back to this later

- [ ] Read this page, then `founder-decisions.md`, and tick what you have decided, in your own copy.
- [ ] Ask an engineer to re-check this folder against the current code before you send it. Each file says
      the date and commit it was checked against, and the drafts may have changed since 2026-11-12.
- [ ] Check that the live and staging pages show the new drafts (see "Which version of the drafts the
      website shows"), or ask an engineer for PDFs of the new drafts.
- [ ] Choose one lawyer or two. Before agreeing, ask for the conflict check, the engagement letter and the fee.
- [ ] Send email 2, then email 1, with the attachments listed above. Nothing secret.
- [ ] Write down the date you sent each one; an engineer can record it in `docs/PROGRESS.md` (the date only:
      no names, no advice).
- [ ] When the answers arrive, ask the lawyer where to keep their written advice; it must not go into this
      repository, which is public.
- [ ] Ask an engineer to apply the resulting wording to the drafts and the Windows files in one legal round.
      The engineer changes only the wording the lawyer approved; your answers and the lawyer's advice stay in
      your own files.
- [ ] Laws change. The outside research here was read on 2026-11-12; for example, the FTC was rewriting its
      subscription-cancellation rule (law-firm summaries, via web search on 2026-11-12, not verified by
      counsel). Ask the lawyer to answer on the law as it stands when they review.
- [ ] Only after the lawyer has approved: remove the DRAFT banners, set the effective dates, and switch on
      what was waiting (the table above).
