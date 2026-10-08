/**
 * Confirmation transaction UI (ADR-0001 D7). The primary line is rendered from the challenge's
 * action/params/target with the app's own labels; the PC name comes from the device inventory
 * matched by pc_id; `display.detail` is shown as plain secondary text and clearly marked as coming
 * from the PC. Approve is only offered when the challenge is bound to the command this phone sent.
 */
import { useEffect, useState } from "react";

import type { CommandRecord } from "../lib/commands.ts";
import { describeChallenge } from "../lib/confirmations.ts";
import { secondsUntil } from "../lib/format.ts";
import type { LabelContext } from "../lib/labels.ts";
import { Button, Notice } from "./ui.tsx";
import { Sheet } from "./Sheet.tsx";

export interface ConfirmationModalProps {
  record: CommandRecord;
  pcName: string | null;
  labels?: LabelContext;
  onRespond: (decision: "approve" | "decline") => Promise<void>;
  now?: () => Date;
}

export function ConfirmationModal({ record, pcName, labels, onRespond, now = () => new Date() }: ConfirmationModalProps) {
  const pending = record.confirmation;
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [remaining, setRemaining] = useState(() => (pending ? secondsUntil(pending.parsed.challenge.expires_at, now()) : 0));

  useEffect(() => {
    if (!pending) return;
    const tick = () => setRemaining(secondsUntil(pending.parsed.challenge.expires_at, now()));
    tick();
    const h = setInterval(tick, 1000);
    return () => clearInterval(h);
  }, [pending, now]);

  if (!pending) return null;
  const desc = describeChallenge(pending.parsed.challenge, pcName, labels);
  const expired = remaining <= 0;
  const bound = pending.bindingProblem === null;

  const respond = async (decision: "approve" | "decline") => {
    setBusy(decision);
    setError(null);
    try {
      await onRespond(decision);
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^[A-Z_]+: /, "") : "Could not send your answer.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Sheet open dismissible={false} labelledBy="confirm-title">
      <p className="text-xs font-semibold uppercase tracking-wider text-warning mb-1">Confirm on this phone</p>
      <h2 id="confirm-title" className="text-xl font-bold leading-snug" data-testid="confirm-primary">
        {desc.primary}
      </h2>
      <p className="mt-1 text-text-muted">
        on <span className="font-semibold text-text">{desc.pcName}</span>
      </p>
      {desc.detail ? (
        <p className="mt-3 rounded-control bg-bg-sunken border border-border px-3 py-2 text-sm text-text-muted break-words" data-testid="confirm-detail">
          <span className="block text-[11px] uppercase tracking-wider text-text-faint mb-0.5">Reported by the PC</span>
          {desc.detail}
        </p>
      ) : null}
      {!bound ? (
        <div className="mt-3">
          <Notice tone="danger" title="This confirmation does not match what you sent">
            <p>{pending.bindingProblem}</p>
            <p>For safety only Decline is available.</p>
          </Notice>
        </div>
      ) : null}
      {expired ? (
        <div className="mt-3">
          <Notice tone="warning">The confirmation timed out. Send the action again if you still want it.</Notice>
        </div>
      ) : (
        <p className="mt-3 text-sm text-text-muted" aria-live="polite">
          Expires in <span className="font-mono tabular-nums">{remaining}s</span>. Nothing happens until you approve.
        </p>
      )}
      {error ? (
        <div className="mt-3">
          <Notice tone="danger">{error}</Notice>
        </div>
      ) : null}
      <div className="mt-5 grid grid-cols-2 gap-3">
        <Button size="lg" variant="secondary" onClick={() => void respond("decline")} busy={busy === "decline"} disabled={busy !== null}>
          Decline
        </Button>
        <Button size="lg" variant={bound ? "danger" : "secondary"} onClick={() => void respond("approve")} busy={busy === "approve"} disabled={!bound || expired || busy !== null}>
          Approve
        </Button>
      </div>
    </Sheet>
  );
}
