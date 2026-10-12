/**
 * The Assistant's conversation (docs/design/assistant.md §4.2): the owner's words on the right, the butler's lines on
 * the left (each labelled for screen readers), and plan cards that show every step before and while it runs.
 * Everything is rendered as React text — model replies and choices included — never as HTML or markdown. AI bubbles
 * say whether the turn was counted.
 */
import { useState } from "react";
import { Link } from "react-router";

import type { JsonValue } from "@dome/protocol";

import { useLabels } from "../app/hooks.ts";
import type { CommandRecord } from "../lib/commands.ts";
import { describeAction } from "../lib/labels.ts";
import { STATUS_LABEL } from "../lib/planRun.ts";
import { powerVerb } from "../lib/power.ts";
import type { ChatChip, ChatItem } from "../store/assistant.ts";
import { useLiveStore } from "../store/live.ts";
import { BrandMark } from "./brand.tsx";
import { CommandOutcome } from "./CommandOutcome.tsx";
import { Icon } from "./icons.tsx";
import { Button } from "./ui.tsx";

export interface ConversationHandlers {
  onChip(chip: ChatChip): void;
  onRun(itemId: number): void;
  onDismiss(itemId: number): void;
  onStop(): void;
  onHear(itemId: number): void;
  /** The large Cancel shut-down / restart / sleep under a power step. */
  onCancelPower(itemId: number): void;
}

function AiTag({ ai }: { ai: { charged: boolean } | null }) {
  if (!ai) return null;
  return <p className="mt-1 text-[11px] text-text-faint">{ai.charged ? "Asked Claude · counts as 1 request" : "Asked Claude · not counted"}</p>;
}

function Chips({ chips, onChip, label }: { chips: ChatChip[]; onChip: (c: ChatChip) => void; label: string }) {
  if (chips.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-2" aria-label={label}>
      {chips.map((c) => (
        <li key={`${c.kind}|${c.label}|${c.text}`}>
          <button type="button" onClick={() => onChip(c)} className="tap dome-press rounded-full border border-border bg-surface px-3.5 py-2 text-sm font-medium text-text hover:bg-surface-hover">
            {c.label}
          </button>
        </li>
      ))}
    </ul>
  );
}

function TouchpadButton() {
  return (
    <Link to="/app/touchpad" className="tap mt-2 inline-flex items-center rounded-control dome-primary font-semibold px-4 text-sm">
      Open Touchpad
    </Link>
  );
}

const POWER_CANCEL_LABEL: Record<string, string> = { "power.shutdown": "Cancel shut-down", "power.restart": "Cancel restart", "power.sleep": "Cancel sleep" };

/**
 * After a power step succeeded: a large Cancel until it fires. The tap goes through the Assistant (power.cancel, signed
 * by this phone, its outcome shown and said). The button goes once the cancel succeeded, once a PC state that named
 * this countdown no longer does (cancelled elsewhere), or once the countdown's time has passed.
 */
function PowerCancel({ item, record, labels, onCancel }: { item: Extract<ChatItem, { kind: "plan" }>; record: CommandRecord; labels: ReturnType<typeof useLabels>; onCancel(): void }) {
  const pending = useLiveStore((s) => s.pcs[item.pcId]?.state?.pending_power_action ?? null);
  const now = useLiveStore((s) => s.now);
  const cancelRecord = useLiveStore((s) => (item.cancel?.commandId ? (s.commands.find((c) => c.commandId === item.cancel!.commandId) ?? null) : null));
  const named = pending?.command_id === record.commandId;
  // whether a PC state has named this countdown yet (stored from an earlier render: its absence later means it ended)
  const [seenNamed, setSeenNamed] = useState(named);
  if (named && !seenNamed) setSeenNamed(true);
  const result = record.terminal?.result as { fires_at?: string; countdown_seconds?: number } | null | undefined;
  if (!result?.countdown_seconds) return null;
  const cancel = item.cancel;
  const firesAt = named ? pending.fires_at : result.fires_at;
  const running = named || (!seenNamed && !!firesAt && Date.parse(firesAt) > now);
  const outcome = cancel?.status === "done" && cancelRecord ? <CommandOutcome record={cancelRecord} pcName={item.pcName} labels={labels} compact /> : null;
  if (cancel?.status === "done") return outcome;
  return (
    <>
      {running || cancel?.status === "sending" ? (
        <Button variant="danger" size="xl" full className="mt-3" busy={cancel?.status === "sending"} onClick={onCancel}>
          {cancel?.status === "sending" ? "Cancelling…" : (POWER_CANCEL_LABEL[record.action] ?? `Cancel ${powerVerb(record.action)}`)}
        </Button>
      ) : null}
      {cancel?.status === "failed" && cancel.error ? (
        <p className="mt-2 text-sm text-warning" role="alert">
          {cancel.error}
        </p>
      ) : null}
    </>
  );
}

function PlanCard({ item, handlers }: { item: Extract<ChatItem, { kind: "plan" }>; handlers: ConversationHandlers }) {
  const labels = useLabels(item.pcId);
  const commands = useLiveStore((s) => s.commands);
  const records = (item.run?.steps ?? []).map((s) => (s.commandId ? (commands.find((c) => c.commandId === s.commandId) ?? null) : null));
  return (
    <div className="dome-card p-4" data-plan-status={item.status}>
      <p className="flex items-center gap-1.5 text-caption font-semibold uppercase tracking-[0.1em] text-text-faint">
        <Icon name="pc" size={14} />
        On {item.pcName ?? "your PC"}:
      </p>
      <ol className={`mt-2 space-y-2 ${item.steps.length > 1 ? "list-decimal pl-5 marker:text-text-faint" : ""}`} aria-label="Steps">
        {item.steps.map((step, i) => {
          const run = item.run?.steps[i];
          const record = records[i] ?? null;
          const preview = item.previews[i];
          return (
            <li key={i} data-step-status={run?.status ?? "waiting"}>
              {record && run && run.status !== "waiting" ? (
                <CommandOutcome record={record} pcName={item.pcName} labels={labels} showPc={false} compact={item.steps.length > 1 && item.run?.failedAt !== i} />
              ) : (
                <p className="text-sm">
                  <strong>{describeAction(step.action, step.params as Record<string, JsonValue>, step.target, labels)}</strong>
                  {run ? <span className="text-text-muted"> — {STATUS_LABEL[run.status]}</span> : null}
                </p>
              )}
              {[step.detail, preview?.where].filter(Boolean).length > 0 ? <span className="block text-xs text-text-muted">{[step.detail, preview?.where].filter(Boolean).join(" · ")}</span> : null}
              {step.warning ? <span className="block text-xs text-warning">{step.warning}</span> : null}
              {preview?.problem && item.status === "blocked" ? <span className="block text-sm text-warning">{preview.problem}</span> : null}
              {run?.failure && (run.failure.code === null || run.failure.mayHaveRun) ? <span className="block text-sm text-warning">{run.failure.message}</span> : null}
              {record && run?.status === "succeeded" && /^power\.(?:sleep|restart|shutdown)$/.test(step.action) ? <PowerCancel item={item} record={record} labels={labels} onCancel={() => handlers.onCancelPower(item.id)} /> : null}
            </li>
          );
        })}
      </ol>
      {item.status === "waiting_run" ? (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <Button variant="primary" size="lg" onClick={() => handlers.onRun(item.id)}>
            Run
          </Button>
          <Button size="lg" onClick={() => handlers.onDismiss(item.id)}>
            Cancel
          </Button>
        </div>
      ) : null}
      {item.status === "running" ? (
        <Button size="lg" full className="mt-3" onClick={handlers.onStop}>
          Stop
        </Button>
      ) : null}
      {item.status === "dismissed" ? <p className="mt-2 text-xs text-text-muted">Not run.</p> : null}
      {item.run?.stopped ? <p className="mt-2 text-xs text-text-muted">Stopped. The remaining steps were not sent.</p> : null}
      <Chips chips={item.chips} onChip={handlers.onChip} label="Other ways to say it" />
      {item.results.length > 0 ? (
        <div className="mt-2">
          <p className="text-xs text-text-muted">Tap a result to play it:</p>
          <Chips chips={item.results} onChip={handlers.onChip} label="Search results" />
        </div>
      ) : null}
      <AiTag ai={item.ai} />
    </div>
  );
}

/** The butler's small round avatar: the DoMe mark on a Navy-to-Blue disc. */
export function ButlerAvatar({ size = 30 }: { size?: number }) {
  return (
    <span aria-hidden="true" style={{ width: size, height: size }} className="flex shrink-0 items-center justify-center rounded-full bg-[linear-gradient(145deg,#2a62ff,#001441)] text-white shadow-[0_6px_16px_-6px_rgb(1_66_251/0.7)]">
      <BrandMark className="w-[66%]" />
    </span>
  );
}

export function Conversation({ items, handlers, butlerName }: { items: ChatItem[]; handlers: ConversationHandlers; butlerName: string }) {
  return (
    <ol className="space-y-3" aria-label="Conversation">
      {items.map((item) => {
        if (item.kind === "owner") {
          return (
            <li key={item.id} className="animate-rise flex justify-end">
              <p className="dome-primary max-w-[82%] rounded-[1.25rem] rounded-br-md px-4 py-2.5 text-[15px] whitespace-pre-wrap break-words">
                <span className="sr-only">You said: </span>
                {item.text}
              </p>
            </li>
          );
        }
        if (item.kind === "butler") {
          return (
            <li key={item.id} className="animate-rise flex items-end justify-start gap-2">
              <ButlerAvatar />
              <div className={`max-w-[82%] rounded-[1.25rem] rounded-bl-md border px-4 py-2.5 text-[15px] ${item.tone === "warning" ? "border-warning/40 bg-warning/8" : "border-border bg-bg-elevated shadow-[var(--dome-shadow-card)]"}`}>
                <p className="whitespace-pre-wrap break-words">
                  <span className="sr-only">{butlerName}: </span>
                  {item.text}
                </p>
                {item.tapToHear ? (
                  <button type="button" onClick={() => handlers.onHear(item.id)} className="tap mt-1 text-sm font-semibold text-accent underline">
                    Tap to hear
                  </button>
                ) : null}
                <Chips chips={item.chips} onChip={handlers.onChip} label="Choices" />
                {item.touchpad ? <TouchpadButton /> : null}
                <AiTag ai={item.ai} />
              </div>
            </li>
          );
        }
        return (
          <li key={item.id} className="animate-rise ml-[38px] max-w-[88%]">
            <PlanCard item={item} handlers={handlers} />
          </li>
        );
      })}
    </ol>
  );
}
