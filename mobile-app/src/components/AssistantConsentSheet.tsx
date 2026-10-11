/**
 * Consent to the AI assistant (docs/design/assistant.md §4.11). DRAFT text: the founder and a lawyer review it before
 * any public release. Its version is the contract's `assistant_consent_version`; changing this text changes that
 * value in rest.schema.json, so cloud-api asks every account again.
 */
import type { rest } from "@dome/protocol";

import { renewalDate } from "../lib/assistant.ts";
import { Sheet } from "./Sheet.tsx";
import { Button, Notice } from "./ui.tsx";

/** rest.schema.json `assistant_consent_version` (the text below). */
export const CONSENT_VERSION: rest.AssistantConsentVersion = "2026-10-11";

export function AssistantConsentSheet({ open, allowance, busy, error, onAccept, onClose }: { open: boolean; allowance: rest.AssistantAllowance | null; busy: boolean; error: string | null; onAccept(): void; onClose(): void }) {
  return (
    <Sheet open={open} title="Turn on the AI assistant?" onClose={onClose} labelledBy="assistant-consent-title">
      <div className="max-h-[60dvh] overflow-y-auto space-y-2 text-sm text-text-muted">
        <p>The assistant uses Claude, an AI model made by Anthropic, to understand requests DoMe can't work out by itself and to answer short questions about using your PC.</p>
        <p>
          <strong className="text-text">Sent to Anthropic, through DoMe's server, only for requests DoMe can't work out by itself:</strong> those words (including any YouTube search words in them), your earlier requests to the assistant and its replies from the past 10 minutes, a short note of what DoMe did by itself in that time (without naming apps, players, videos or searches), the name and form of address you chose, and a few facts about your PC: whether it is online, whether YouTube or another player is playing, the volume levels, how many search results are showing and which kinds of actions this phone may perform.
        </p>
        <p>
          <strong className="text-text">Never sent:</strong> audio, your screen, the list of apps on your PC or the names it shows for them, player names, video or window titles, the words of commands DoMe handled by itself, anything typed with the Touchpad keyboard, your clipboard or files.
        </p>
        <p>Your iPhone turns your voice into text first; Apple may process that on its servers.</p>
        <p>DoMe does not store what you say or what the assistant answers. It keeps counts (requests and tokens) to apply your monthly allowance. Anthropic's API data terms apply to the text it receives.</p>
        <p>Everything the assistant suggests is shown on this phone and sent by this phone. Simple actions such as pausing or changing the volume run straight away; opening or switching apps, full screen and locking the PC wait for your tap on Run; shutting down, restarting, sleeping and closing apps ask you to confirm. Buttons and typed commands keep working without the assistant.</p>
        <p>
          <strong className="text-text">Allowance:</strong> {allowance ? `${allowance.limit} requests a month, renewing ${renewalDate(allowance.period_end)}.` : "shown in Settings once it is known."} Requests DoMe understands by itself are free and don't count.
        </p>
      </div>
      {error ? (
        <div className="mt-3">
          <Notice tone="danger">{error}</Notice>
        </div>
      ) : null}
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Button onClick={onClose}>Not now</Button>
        <Button variant="primary" busy={busy} onClick={onAccept}>
          Turn on
        </Button>
      </div>
    </Sheet>
  );
}
