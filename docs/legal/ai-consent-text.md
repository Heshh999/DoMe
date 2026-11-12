# Appendix to email 2: the AI assistant's consent screen, word for word

Status: copied on 2026-11-12 for counsel, from `mobile-app/src/components/AssistantConsentSheet.tsx:24-48`
(consent version "2026-10-11", line 13). It is preparation for counsel, not legal advice. The code marks it as
DRAFT text that "the founder and a lawyer review" before any public release (lines 2-3). This is the screen
question 6 of `privacy-terms-counsel-email.md` asks about. Attach this file to email 2.

The feature is called "the AI butler" in the app and in the drafts, and "the assistant" in the body of this
screen and in the emails: they are the same feature. It is switched on in no deployment yet
(`cloud-api/dome_api/settings.py:150`; `docs/LAUNCH_SETUP.md:702`), so the lawyer cannot see this screen in the
app. Changing this text changes its version, and the server then asks every account to agree again
(`AssistantConsentSheet.tsx:3-4`).

## The screen

**Title:** Turn on the AI butler?

The assistant uses Claude, an AI model made by Anthropic, to understand requests DoMe can't work out by itself
and to answer short questions about using your PC.

**Sent to Anthropic, through DoMe's server, only for requests DoMe can't work out by itself:** those words
(including any YouTube search words in them), your earlier requests to the assistant and its replies from the
past 10 minutes, a short note of what DoMe did by itself in that time (without naming apps, players, videos or
searches), the name and form of address you chose, and a few facts about your PC: whether it is online,
whether YouTube or another player is playing, the volume levels, how many search results are showing and which
kinds of actions this phone may perform.

**Never sent:** audio, your screen, the list of apps on your PC or the names it shows for them, player names,
video or window titles, the words of commands DoMe handled by itself, anything typed with the Touchpad
keyboard, your clipboard or files.

Your iPhone turns your voice into text first; Apple may process that on its servers.

DoMe does not store what you say or what the assistant answers. It keeps counts (requests and tokens) to apply
your allowance. Anthropic's API data terms apply to the text it receives.

Everything the assistant suggests is shown on this phone and sent by this phone. Simple actions such as pausing
or changing the volume run straight away; opening or switching apps, full screen and locking the PC wait for
your tap on Run; shutting down, restarting, sleeping and closing apps ask you to confirm. Buttons and typed
commands keep working without the assistant.

**Allowance:** [one of the three sentences below] Requests DoMe understands by itself are free and don't count.

**Buttons:** Not now · Turn on

## The three versions of the allowance sentence

The sentence depends on what the server reports for the account (`AssistantConsentSheet.tsx:16-20`):

1. On a paid plan: "[N] requests per 5-hour window, up to [M] every 7 days; the app shows when each refreshes."
   The planned values are N = 30 and M = 120 on Pro, and N = 90 and M = 300 on Pro Plus
   (`shared/protocol/plans.json:61-68,109-116`; the 5 hours and 7 days are `mobile-app/src/content/plans.ts:94-95`).
2. On Free: "a one-time taste of [N] requests; it does not renew." The planned value is N = 10
   (`shared/protocol/plans.json:12`).
3. When the allowance is not known yet: "shown in Settings once it is known."

## Known differences, for counsel

- The Privacy notice draft also lists the time of day (morning, afternoon, evening or night) and whether a request
  was spoken, typed or tapped as sent to Anthropic (`mobile-app/src/pages/public/PrivacyPage.tsx:89`), as the
  server does (`cloud-api/dome_api/assistant/service.py:136-141`). This screen does not mention them yet; the
  Privacy draft's open items note that it needs the same addition (`PrivacyPage.tsx:230`).
- An older copy of this text in the design document says "monthly allowance"
  (`docs/design/assistant.md:1606`); the allowance now uses 5-hour and weekly windows, as above.
