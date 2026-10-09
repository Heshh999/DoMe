/**
 * Truthful rendering of one command record: lifecycle state, the PC it targets, and — only once a
 * terminal result arrived — what actually happened, with recovery steps for known error codes.
 */
import type { CommandRecord } from "../lib/commands.ts";
import { describeAction, errorMessage, lifecycleLabel, recoverySteps, type LabelContext } from "../lib/labels.ts";
import { outcomeTone, resultSummary } from "../lib/outcome.ts";
import { FailureLinks } from "./FailureLinks.tsx";
import { Pill, Steps } from "./ui.tsx";

export function CommandOutcome({ record, pcName, labels, compact = false }: { record: CommandRecord; pcName: string | null; labels?: LabelContext; compact?: boolean }) {
  const tone = outcomeTone(record);
  const stateText = record.terminal ? lifecycleLabel(record.terminal.state, record.terminal.origin) : record.noAnswer ? "No answer from the PC yet" : lifecycleLabel(record.state);
  const summary = resultSummary(record);
  const err = record.terminal?.error ?? null;
  const steps = err ? recoverySteps(err.code) : [];
  return (
    <div className="space-y-2" data-testid="command-outcome">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold leading-snug">{describeAction(record.action, record.params, record.target, labels)}</p>
          <p className="text-xs text-text-muted">on {pcName ?? "your PC"}</p>
        </div>
        <Pill tone={tone} pulse={!record.terminal && !record.noAnswer}>
          {stateText}
        </Pill>
      </div>
      {!compact ? (
        <>
          {record.terminal?.state === "succeeded" && record.terminal.resultInvalid ? <p className="text-sm text-warning">The PC reported success, but its answer did not match what this app expects. Updating DoMe on both devices may fix this.</p> : null}
          {summary ? <p className="text-sm text-text-muted">{summary}</p> : null}
          {record.terminal?.warning ? <p className="text-sm text-warning">{record.terminal.warning}</p> : null}
          {err ? (
            <div className="text-sm">
              <p className="text-text">{errorMessage(err)}</p>
              {steps.length > 0 ? (
                <div className="mt-1 text-text-muted">
                  <Steps steps={steps} />
                </div>
              ) : null}
              <FailureLinks code={err.code} className="mt-1" />
            </div>
          ) : null}
          {record.noAnswer && !record.terminal ? <p className="text-sm text-text-muted">The PC has not answered yet. This does not mean it failed — check the PC’s state before sending it again.</p> : null}
        </>
      ) : null}
    </div>
  );
}
