/**
 * Confirmation transaction UI (ADR-0001 D7). The primary line is rendered from the challenge's
 * action/params/target with the app's own labels; the PC name comes from the device inventory
 * matched by pc_id; `display.detail` is shown as plain secondary text and clearly marked as coming
 * from the PC. Approve is only offered when the challenge is bound to the command this phone sent.
 *
 * The sheet cannot be swiped away by accident, but it never traps the app: once the challenge has
 * expired, or while the relay connection is down (Approve/Decline could not reach the PC anyway), a
 * Close button dismisses it locally without sending anything — the PC discards an unanswered
 * challenge when it expires.
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
  /** Relay socket currently open. When false the countdown is not a live promise and Close is offered. */
  connected?: boolean;
  onRespond: (decision: "approve" | "decline") => Promise<void>;
  /** Local dismissal (no frame is sent). Required for the escape hatch; when absent, Close is not shown. */
  onDismiss?: () => void;
  now?: () => Date;
}

/** Fixed copy for every Sleep / Restart / Shutdown confirmation (spec §10). */
export const POWER_CONFIRMATION_COPY = "This can interrupt or end remote access to the PC. DoMe cannot wake or power it on again remotely in this version, so you will need to be at the PC to restore access.";

export function isPowerAction(action: string): boolean {
  return action === "power.sleep" || action === "power.restart" || action === "power.shutdown";
}

export function ConfirmationModal({ record, pcName, labels, connected = true, onRespond, onDismiss, now = () => new Date() }: ConfirmationModalProps) {
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
  const offline = !connected || pending.connectionLost;
  const canClose = onDismiss !== undefined && (expired || offline);
  const power = isPowerAction(pending.parsed.challenge.action);

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
      {power ? (
        <p className="mt-3 text-sm text-text" data-testid="confirm-power-copy">
          {POWER_CONFIRMATION_COPY}
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
      ) : offline ? (
        <div className="mt-3">
          <Notice tone="warning" title="Connection to DoMe lost">
            <p>Your answer cannot reach the PC right now. The PC discards this request when it expires{remaining > 0 ? ` (in about ${remaining}s)` : ""}; nothing happens unless you approve.</p>
          </Notice>
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
        <Button size="lg" variant="secondary" onClick={() => void respond("decline")} busy={busy === "decline"} disabled={busy !== null || expired}>
          Decline
        </Button>
        <Button size="lg" variant={bound ? "danger" : "secondary"} onClick={() => void respond("approve")} busy={busy === "approve"} disabled={!bound || expired || offline || busy !== null}>
          Approve
        </Button>
      </div>
      {canClose ? (
        <Button size="lg" variant="ghost" full className="mt-3" onClick={onDismiss} disabled={busy !== null}>
          Close
        </Button>
      ) : null}
    </Sheet>
  );
}
