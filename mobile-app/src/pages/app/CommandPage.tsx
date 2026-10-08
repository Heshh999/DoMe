/**
 * Typed commands. Deterministic parsing only (spec §13): the preview says exactly what will be
 * sent and to which PC; ambiguous or unknown text asks for clarification instead of guessing.
 */
import { useMemo, useState } from "react";

import { useLabels, useSelectedPc, useSend } from "../../app/hooks.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Button, Card, inputClass, Notice, Steps } from "../../components/ui.tsx";
import { parseIntent } from "../../lib/intents.ts";
import { errorMessage, recoverySteps } from "../../lib/labels.ts";
import { resolveMediaTarget, resolveYoutubeTarget } from "../../lib/targets.ts";
import { useLiveStore } from "../../store/live.ts";

const EXAMPLES = ["Pause YouTube", "Skip this video", "Go back ten seconds", "Set my PC volume to 35 percent", "Open Discord", "Lock my computer", "Put my computer to sleep"];

export function CommandPage() {
  const { pc, pcId, pcName, live, fresh, canControl } = useSelectedPc();
  const { send, record, sendError, clearError, sending } = useSend(pcId);
  const labels = useLabels(pcId);
  const apps = useLiveStore((s) => (pcId ? s.appsByPc[pcId]?.apps : undefined));
  const selectedTab = useLiveStore((s) => (pcId ? s.selectedTab[pcId] : undefined));
  const selectedSession = useLiveStore((s) => (pcId ? s.selectedSession[pcId] : undefined));
  const [text, setText] = useState("");
  const state = live.state;
  const yt = resolveYoutubeTarget(state, selectedTab);
  const media = resolveMediaTarget(state, selectedSession);

  const intent = useMemo(
    () =>
      parseIntent(text, {
        apps: apps ?? null,
        pcVolume: fresh && typeof state?.volume?.value === "number" ? state.volume.value : null,
        youtubeVolume: yt.ok && typeof yt.tab.volume === "number" ? yt.tab.volume : null,
      }),
    [text, apps, fresh, state, yt],
  );

  let targetProblem: string | null = null;
  let target = intent.kind === "action" ? intent.target : null;
  if (intent.kind === "action" && intent.needsTarget === "youtube") {
    if (yt.ok) target = yt.target;
    else targetProblem = yt.reason === "ambiguous" ? "More than one YouTube tab is playing. Choose one in the remote first." : yt.reason === "no_extension" ? "The DoMe browser extension is not connected on the PC." : yt.reason === "none_attached" ? "Reload the YouTube tab on the PC so DoMe can control it." : "No YouTube tab is open on the PC.";
  }
  if (intent.kind === "action" && intent.needsTarget === "media") {
    if (media.ok) target = media.target;
    else targetProblem = media.reason === "ambiguous" ? "More than one media player is active. Choose one in the remote first." : "No Windows media player is active.";
  }
  const ready = intent.kind === "action" && !targetProblem && canControl && text.trim() !== "";

  const submit = async () => {
    if (intent.kind !== "action" || !ready) return;
    const rec = await send(intent.action, intent.params, target, "text");
    if (rec) setText("");
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Type a command</h1>
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
          <input id="command-text" className={inputClass} value={text} onChange={(e) => setText(e.currentTarget.value)} placeholder="e.g. pause YouTube" autoComplete="off" autoCorrect="on" enterKeyHint="send" maxLength={120} aria-describedby="command-preview" />
          <p className="text-xs text-text-faint">Tip: tap the microphone on your keyboard to dictate. Typed commands work without any AI service.</p>
          <div id="command-preview" aria-live="polite" className="min-h-[2.5rem] text-sm">
            {text.trim() === "" ? (
              <span className="text-text-muted">Try one of the examples below.</span>
            ) : intent.kind === "action" ? (
              targetProblem ? (
                <span className="text-warning">{targetProblem}</span>
              ) : (
                <span>
                  Will send: <strong>{intent.summary}</strong> on <strong>{pcName ?? "your PC"}</strong>
                  {intent.confirmation ? " — you will confirm first." : "."}
                </span>
              )
            ) : intent.kind === "clarify" ? (
              <span className="text-warning">{intent.question}</span>
            ) : (
              <span className="text-text-muted">That is not a command DoMe understands. Try the examples below, or use the buttons in the remote.</span>
            )}
          </div>
          <Button type="submit" variant="primary" size="lg" full disabled={!ready} busy={sending}>
            Send
          </Button>
          {!canControl && text.trim() !== "" ? <p className="text-xs text-text-muted">Commands are available once {pcName ?? "the PC"} is online and its state is current.</p> : null}
        </form>
      </Card>
      {sendError ? (
        <Notice tone="danger" title="Not sent">
          <p>{errorMessage(sendError)}</p>
          <Steps steps={recoverySteps(sendError.code)} />
          <Button size="md" variant="ghost" onClick={clearError}>
            Dismiss
          </Button>
        </Notice>
      ) : null}
      {record ? (
        <Card>
          <h2 className="font-semibold mb-2">Outcome</h2>
          <CommandOutcome record={record} pcName={pcName} labels={labels} />
        </Card>
      ) : null}
      <Card>
        <h2 className="font-semibold mb-2">Examples</h2>
        <ul className="flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <li key={ex}>
              <button type="button" onClick={() => setText(ex)} className="tap rounded-full border border-border bg-surface px-3 py-2 text-sm hover:bg-surface-hover">
                {ex}
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
