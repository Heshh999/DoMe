/**
 * Typed commands. Deterministic parsing only (spec §13): the preview lists exactly what will be sent and to which
 * PC, step by step; ambiguous or unknown text asks for clarification instead of guessing, and its chips only fill
 * the text box. A plan of up to three steps runs in order: each step is its own signed command, sent only after the
 * previous one succeeded, and the run stops at the first step that does not, saying which one and why.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { refreshApprovedApps, useApprovedApps, useLabels, useMyGrant, useSelectedPc } from "../../app/hooks.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { FailureLinks } from "../../components/FailureLinks.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Icon } from "../../components/icons.tsx";
import { Button, Card, Chip, inputClass, Notice, PageHeader, SectionLabel, Steps } from "../../components/ui.tsx";
import { intentContext, previewStep, type LiveView, type PlanRun } from "../../lib/commandPlan.ts";
import { appCommandText, exampleAppPhrase, MAX_COMMAND_CHARS, parseCommand, type Choice, type PlanStep } from "../../lib/intents.ts";
import { ordinalWord } from "../../lib/intentWords.ts";
import { describeAction, errorMessage, recoverySteps, type LabelContext } from "../../lib/labels.ts";
import { APP_CHECK_DELAY_MS, APPS_FRESH_MS, createStopper, runPlanOnPc, STATUS_LABEL } from "../../lib/planRun.ts";
import { useDevicesStore } from "../../store/devices.ts";
import { useLiveStore } from "../../store/live.ts";

function ChoiceChips({ choices, onPick, label }: { choices: Choice[]; onPick: (text: string) => void; label: string }) {
  if (choices.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-2" aria-label={label}>
      {choices.map((c) => (
        <li key={`${c.label}|${c.text}`}>
          <Chip onClick={() => onPick(c.text)}>{c.label}</Chip>
        </li>
      ))}
    </ul>
  );
}

function TouchpadLink() {
  return (
    <Link to="/app/touchpad" className="tap dome-primary dome-press mt-2 inline-flex min-h-[44px] items-center rounded-control px-4 text-sm font-semibold">
      Open Touchpad
    </Link>
  );
}

function StepLine({ step, labels }: { step: PlanStep; labels: LabelContext }) {
  return <strong>{describeAction(step.action, step.params, step.target, labels)}</strong>;
}

export function CommandPage() {
  const { pc, pcId, pcName, live, fresh, canControl } = useSelectedPc();
  const labels = useLabels(pcId);
  // Loads the approved apps when the page opens (and when the app comes back to the foreground), so "open Notepad"
  // works without visiting Apps first.
  const { apps, at: appsAt, listing } = useApprovedApps(pcId);
  const grant = useMyGrant(pcId);
  const loadGrants = useDevicesStore((s) => s.loadGrants);
  const selectedTab = useLiveStore((s) => (pcId ? s.selectedTab[pcId] : undefined));
  const selectedSession = useLiveStore((s) => (pcId ? s.selectedSession[pcId] : undefined));
  const lastSearch = useLiveStore((s) => (pcId ? s.lastSearchByPc[pcId] : undefined));
  const stateSeq = useLiveStore((s) => (pcId ? s.pcs[pcId]?.stateSeq : undefined));
  const commands = useLiveStore((s) => s.commands);
  const [text, setText] = useState("");
  const [run, setRun] = useState<{ steps: PlanStep[]; run: PlanRun } | null>(null);
  const [checkedApps, setCheckedApps] = useState<ReadonlySet<string>>(() => new Set());
  const [checkingApp, setCheckingApp] = useState<string | null>(null);
  /** Stop for the running plan; it also wakes a step that is waiting for its answer. */
  const stopper = useRef(createStopper()).current;
  /** Set from the tap until the run ends, so a double tap never starts the plan twice (state updates come later). */
  const runningRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const state = live.state;

  const view: LiveView = useMemo(() => ({ state, stateSeq, selectedTab, selectedSession, lastSearch: lastSearch ?? null }), [state, stateSeq, selectedTab, selectedSession, lastSearch]);
  const ctx = useMemo(() => intentContext({ ...view, fresh, apps }), [view, fresh, apps]);
  const parsed = useMemo(() => parseCommand(text, ctx), [text, ctx]);
  const capabilities = grant ? grant.capabilities : null;
  const previews = parsed.kind === "plan" ? parsed.steps.map((s, i) => previewStep(s, i, view, capabilities)) : [];
  const blocked = previews.find((p) => p.problem)?.problem ?? null;
  const running = !!run && !run.run.done;
  const ready = parsed.kind === "plan" && !blocked && canControl && !running && text.trim() !== "";

  useEffect(() => {
    if (pcId && grant === undefined) void loadGrants(pcId);
  }, [pcId, grant, loadGrants]);

  // An app name that matches nothing: ask the PC for its app list once (it may have been added on the PC a moment
  // ago, DoMe tray → Add an app…) before saying it is not approved. Waits until the typing pauses.
  const listingBusy = !!listing && !listing.terminal && !listing.noAnswer;
  const missingApp = parsed.kind === "clarify" && !parsed.appsUnknown ? (parsed.missingApp ?? null) : null;
  const mustCheck = missingApp !== null && canControl && !checkedApps.has(missingApp.toLowerCase());
  useEffect(() => {
    if (!mustCheck || !missingApp || !pcId || listingBusy) return;
    const name = missingApp.toLowerCase();
    const timer = setTimeout(() => {
      const done = () => {
        setCheckedApps((s) => new Set(s).add(name));
        setCheckingApp(null);
      };
      if (appsAt !== null && Date.now() - appsAt < APPS_FRESH_MS) return done();
      setCheckingApp(name);
      void refreshApprovedApps(pcId).finally(done);
    }, APP_CHECK_DELAY_MS);
    return () => clearTimeout(timer);
  }, [mustCheck, missingApp, pcId, listingBusy, appsAt]);
  const checkingThisApp = missingApp !== null && (mustCheck || listingBusy || checkingApp === missingApp.toLowerCase());

  const pick = (value: string) => {
    setText(value);
    inputRef.current?.focus();
  };

  const submit = async () => {
    if (!ready || parsed.kind !== "plan" || !pcId || runningRef.current) return;
    const planSteps = parsed.steps;
    stopper.reset();
    runningRef.current = true;
    await runPlanOnPc(pcId, planSteps, {
      source: "text",
      stopper,
      onFirstSend: () => setText(""), // sent: the text (a search, perhaps) is not kept on screen
      onUpdate: (r) => setRun({ steps: planSteps, run: r }),
    }).finally(() => {
      runningRef.current = false;
    });
  };

  const runRecords = run ? run.run.steps.map((s) => (s.commandId ? (commands.find((c) => c.commandId === s.commandId) ?? null) : null)) : [];
  const failedAt = run?.run.failedAt ?? null;
  const failure = failedAt !== null ? (run?.run.steps[failedAt]?.failure ?? null) : null;
  const failedRecord = failedAt !== null ? runRecords[failedAt] : null;
  const lastSucceeded = run ? run.steps.filter((_, i) => run.run.steps[i]?.status === "succeeded").pop() : undefined;
  const showResults = !!lastSearch && lastSucceeded?.action === "youtube.search" && lastSearch.results.length > 0;
  const unknownAt = run ? run.run.steps.findIndex((s) => s.status === "unknown") : -1;
  // The PC pressed nothing (no F11 full screen in that app, or not allowed for it there): Maximize is offered as a
  // chip that fills the box, so it is a second command the customer sends, never a silent fallback.
  const failedStep = failedAt !== null ? run?.steps[failedAt] : undefined;
  const insteadAppId = failure?.code === "FULLSCREEN_UNSUPPORTED" && failedStep?.action === "app.set_fullscreen" ? failedStep.target?.app_id : undefined;
  const insteadApp = typeof insteadAppId === "string" ? apps?.find((a) => a.app_id === insteadAppId) : undefined;
  const insteadChoices: Choice[] = insteadApp && apps ? [{ label: "Maximize instead", text: appCommandText("maximize", insteadApp, apps) }] : [];
  // One of the customer's own approved apps, in words that parse back to it ("Full screen ZStream"), so every example works.
  const appName = exampleAppPhrase(apps);
  const examples: Array<{ title: string; items: string[] }> = [
    { title: "Volume", items: ["Make the volume 100%", "Turn it down a bit", "Mute the PC"] },
    { title: "YouTube", items: ["Pause YouTube", "Skip ahead 30 seconds", "Search YouTube for lofi beats", "Play the second video", "Play lofi beats on YouTube"] },
    { title: "Apps", items: [`Open ${appName}`, `Bring ${appName} to the front`, `Full screen ${appName}`, `Exit full screen ${appName}`, `Open ${appName} and maximize it`] },
    { title: "PC", items: ["Lock my computer", "Put my computer to sleep"] },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Type a command" subtitle="Plain words, no AI. DoMe shows exactly what it will send before it goes." />
      <PcSwitcher pc={pc} live={live} />
      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          className="space-y-3"
        >
          <label htmlFor="command-text" className="block font-semibold">
            What should {pcName ?? "the PC"} do?
          </label>
          <input id="command-text" ref={inputRef} className={inputClass} value={text} onChange={(e) => setText(e.currentTarget.value)} placeholder="e.g. make the volume 100%" autoComplete="off" autoCorrect="on" enterKeyHint="send" maxLength={MAX_COMMAND_CHARS} aria-describedby="command-preview" />
          <p className="text-xs text-text-faint">Tip: tap the microphone on your keyboard to dictate. Join up to three steps with “and” or “then”. Typed commands work without any AI service.</p>
          {/* quiet helper text while empty, so it never reads as a second field; a bordered preview once there is something to send */}
          <div id="command-preview" aria-live="polite" className={text.trim() === "" ? "flex min-h-[2.5rem] items-center gap-2 px-0.5 text-sm" : "min-h-[2.5rem] rounded-control border border-dashed border-border-strong bg-transparent px-3.5 py-3 text-sm"}>
            {text.trim() === "" ? (
              <span className="inline-flex items-center gap-2 text-text-muted">
                <Icon name="info" size={16} className="shrink-0 text-text-faint" />
                Try one of the examples below.
              </span>
            ) : parsed.kind === "plan" ? (
              <div className="space-y-1">
                {parsed.steps.length === 1 ? (
                  <p>
                    Will send: <StepLine step={parsed.steps[0]!} labels={labels} /> on <strong>{pcName ?? "your PC"}</strong>
                    {parsed.steps[0]!.confirmation ? " — you will confirm first." : "."}
                  </p>
                ) : (
                  <p>
                    Will run {parsed.steps.length} steps on <strong>{pcName ?? "your PC"}</strong>, one after another:
                  </p>
                )}
                <ol className={parsed.steps.length === 1 ? "space-y-1" : "list-decimal pl-5 space-y-1"} aria-label="Steps">
                  {parsed.steps.map((s, i) => (
                    <li key={i}>
                      {parsed.steps.length > 1 ? <StepLine step={s} labels={labels} /> : null}
                      {[s.detail, previews[i]?.where].filter(Boolean).length > 0 ? <span className="block text-xs text-text-muted">{[s.detail, previews[i]?.where].filter(Boolean).join(" · ")}</span> : null}
                      {s.warning ? <span className="block text-xs text-warning">{s.warning}</span> : null}
                      {previews[i]?.problem ? <span className="block text-warning">{previews[i]!.problem}</span> : null}
                    </li>
                  ))}
                </ol>
                <ChoiceChips choices={parsed.choices} onPick={pick} label="Other ways to say it" />
              </div>
            ) : parsed.kind === "clarify" ? (
              <div>
                {parsed.appsUnknown && listingBusy ? (
                  <span className="text-text-muted">Loading the approved apps from {pcName ?? "the PC"}…</span>
                ) : checkingThisApp ? (
                  <span className="text-text-muted">Checking the approved apps on {pcName ?? "the PC"}…</span>
                ) : (
                  <span className="text-warning">{parsed.message}</span>
                )}
                <ChoiceChips choices={parsed.choices} onPick={pick} label="Choices" />
                {parsed.link === "touchpad" ? <TouchpadLink /> : null}
              </div>
            ) : parsed.kind === "handoff" ? (
              <div>
                <span className="text-warning">{parsed.message}</span>
                <ChoiceChips choices={parsed.choices} onPick={pick} label="Choices" />
                <div>
                  <TouchpadLink />
                </div>
              </div>
            ) : (
              <span className="text-text-muted">{parsed.message ?? "That is not a command DoMe understands. Try the examples below, or use the buttons in the remote."}</span>
            )}
          </div>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" size="lg" full disabled={!ready} busy={running}>
              {parsed.kind === "plan" && parsed.steps.length > 1 ? `Run ${parsed.steps.length} steps` : "Send"}
            </Button>
            {run && !run.run.done && run.steps.length > 1 ? (
              <Button
                type="button"
                size="lg"
                onClick={() => stopper.stop()}
              >
                Stop
              </Button>
            ) : null}
          </div>
          {!canControl && text.trim() !== "" ? <p className="text-xs text-text-muted">Commands are available once {pcName ?? "the PC"} is online and its state is current.</p> : null}
        </form>
      </Card>
      {run ? (
        <Card>
          <h2 className="font-semibold mb-2">{run.steps.length === 1 ? "Outcome" : "Steps"}</h2>
          <ol className="space-y-3" aria-label="Progress">
            {run.steps.map((s, i) => {
              const record = runRecords[i];
              const stepRun = run.run.steps[i]!;
              const status = stepRun.status;
              const appId = typeof s.target?.app_id === "string" ? s.target.app_id : null;
              return (
                <li key={i} data-step-status={status}>
                  {record && status !== "waiting" ? (
                    <CommandOutcome record={record} pcName={pcName} labels={labels} compact={run.steps.length > 1 && i !== failedAt} />
                  ) : (
                    <p className="text-sm">
                      {run.steps.length > 1 ? `${i + 1}. ` : ""}
                      {describeAction(s.action, s.params, s.target, labels)} — <span className="text-text-muted">{STATUS_LABEL[status]}</span>
                    </p>
                  )}
                  {stepRun.waitingForWindow ? <p className="text-sm text-text-muted">Waiting for {(appId && labels.appNames?.[appId]) ?? "the app"} to open its window…</p> : null}
                </li>
              );
            })}
          </ol>
          {/* A single step the PC answered is fully explained by its outcome above; everything else is said here. */}
          {failure && failedAt !== null && !(run.steps.length === 1 && failedRecord?.terminal) ? (
            <div className="mt-3">
              <Notice tone={failure.mayHaveRun ? "warning" : "danger"} title={run.steps.length > 1 ? `Stopped at step ${failedAt + 1} of ${run.steps.length}` : failure.mayHaveRun ? "Result unknown" : "Not sent"}>
                <p>{failure.code === null || failure.mayHaveRun ? failure.message : errorMessage({ code: failure.code, message: failure.message }, { action: run.steps[failedAt]?.action, origin: failedRecord?.terminal?.origin })}</p>
                {/* recovery steps for a PC-reported failure are already in that step's outcome */}
                {failure.code && !failedRecord?.terminal && !failure.mayHaveRun ? <Steps steps={recoverySteps(failure.code, { action: run.steps[failedAt]?.action })} /> : null}
                {failedAt < run.steps.length - 1 ? <p>The steps after it were not sent.</p> : null}
                {failure.code && !failedRecord?.terminal ? <FailureLinks code={failure.code} /> : null}
              </Notice>
            </div>
          ) : null}
          {insteadChoices.length > 0 ? <ChoiceChips choices={insteadChoices} onPick={pick} label="Instead" /> : null}
          {run.run.stopped ? <p className="mt-3 text-sm text-text-muted">{unknownAt >= 0 ? `Stopped. Step ${unknownAt + 1} was already on its way and may still finish on the PC; the steps after it were not sent.` : "Stopped. The remaining steps were not sent."}</p> : null}
          {showResults ? (
            <div className="mt-3">
              <p className="text-sm text-text-muted">Tap a result to fill in the command that plays it:</p>
              <ChoiceChips choices={lastSearch.results.slice(0, 5).map((r) => ({ label: `${r.index}. ${r.title || "Untitled video"}`, text: `play the ${ordinalWord(r.index)} video` }))} onPick={pick} label="Search results" />
            </div>
          ) : null}
        </Card>
      ) : null}
      <section className="pt-1">
        <SectionLabel>Examples</SectionLabel>
        <Card>
          <div className="space-y-4">
            {examples.map((group) => (
              <div key={group.title}>
                <h3 className="mb-2 text-xs font-semibold text-text-muted">{group.title}</h3>
                <ul className="flex flex-wrap gap-2">
                  {group.items.map((ex) => (
                    <li key={ex}>
                      <Chip onClick={() => pick(ex)}>{ex}</Chip>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      </section>
    </div>
  );
}
