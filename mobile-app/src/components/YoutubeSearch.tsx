/**
 * Search YouTube from the phone (protocol 1.2 `youtube.search` / `youtube.open_result`). The typed text goes
 * only into the signed command (rules.search_queries): it is never logged, the box clears once the command is
 * sent, and nothing here is kept beyond the in-memory results of the last search. Results are untrusted text
 * from the PC, rendered as plain text; tapping one opens it in the results tab on the PC, after pausing a video
 * playing in another tab (so two never play at once). Once a video was opened from the list, that tab plays it and
 * the list can no longer be used: search again.
 */
import { useState } from "react";

import { LIMITS, type JsonValue } from "@dome/protocol";

import { resultsTabProblem, reusableResultsTab } from "../lib/commandPlan.ts";
import type { CommandRecord } from "../lib/commands.ts";
import { mmss } from "../lib/format.ts";
import { browserInstanceIds, browserLabel, playingElsewhere, resolveYoutubeBrowserTarget, resolveYoutubeTarget, tabKey, targetForSearchResult, youtubeTargetFor } from "../lib/targets.ts";
import { useLiveStore, type LastSearch, type PcState } from "../store/live.ts";
import { Button, Card, inputClass, Toggle } from "./ui.tsx";

type Send = (action: string, params?: Record<string, JsonValue>, target?: Record<string, JsonValue> | null, source?: CommandRecord["source"]) => Promise<CommandRecord | null>;

/** The contract refuses C0/C1 control characters in a query (actions.json youtube.search params.query.pattern). */
function hasControlChars(text: string): boolean {
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0;
    if (c < 0x20 || (c >= 0x7f && c <= 0x9f)) return true;
  }
  return false;
}

export function YoutubeSearch({ pcId, state, canControl, send }: { pcId: string | null; state: PcState | null; canControl: boolean; send: Send }) {
  const selectedTab = useLiveStore((s) => (pcId ? s.selectedTab[pcId] : undefined));
  const lastSearch = useLiveStore((s) => (pcId ? s.lastSearchByPc[pcId] : undefined));
  const stateSeq = useLiveStore((s) => (pcId ? s.pcs[pcId]?.stateSeq : undefined));
  const [query, setQuery] = useState("");
  /** null = default: reuse a tab only when the customer picked it, or the last search's results tab. */
  const [inTab, setInTab] = useState<boolean | null>(null);
  const [browserId, setBrowserId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  // The tab a search may reuse: the one the customer picked, else the last results tab, else the one being controlled.
  const current = resolveYoutubeTarget(state, selectedTab);
  const resultsTab = reusableResultsTab(state, lastSearch);
  const candidate = current.ok && current.explicit ? current.tab : (resultsTab ?? (current.ok ? current.tab : null));
  const useTab = !!candidate && (inTab ?? ((current.ok && current.explicit) || !!resultsTab));
  const where = resolveYoutubeBrowserTarget(state, useTab && candidate ? tabKey(candidate) : null, browserId);
  const inResultsTab = where.ok && where.mode === "tab" && !!resultsTab && tabKey(where.tab) === tabKey(resultsTab);
  const browsers = browserInstanceIds(state);
  const text = query.trim();
  const valid = text.length > 0 && !hasControlChars(text);
  const ready = canControl && where.ok && valid && !sending;
  const listProblem = lastSearch ? resultsTabProblem(state, lastSearch, stateSeq) : null;
  // Opening a result plays it; a video playing in another tab is paused first so the two never play at once.
  const others = lastSearch ? playingElsewhere(state, lastSearch.tab) : [];

  const submit = async () => {
    if (!ready || !where.ok) return;
    setSending(true);
    const rec = await send("youtube.search", { query: text }, where.target, "button");
    setSending(false);
    if (rec) setQuery(""); // sent: the text is not kept on screen (a "Not sent" error keeps it for a retry)
  };

  const open = async (last: LastSearch, index: number, videoId: string) => {
    for (const other of others) await send("youtube.set_paused", { paused: true }, youtubeTargetFor(other), "button");
    await send("youtube.open_result", { index, video_id: videoId }, targetForSearchResult(last.tab), "button");
  };

  return (
    <Card>
      <form
        role="search"
        aria-label="Search YouTube on the PC"
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label htmlFor="youtube-search" className="block font-semibold">
          Search YouTube
        </label>
        <div className="flex gap-2">
          <input id="youtube-search" type="search" className={inputClass} value={query} onChange={(e) => setQuery(e.currentTarget.value)} placeholder="e.g. lofi beats" maxLength={LIMITS.youtube_search_query_max_chars} enterKeyHint="search" autoComplete="off" spellCheck={false} aria-describedby="youtube-search-where" />
          <Button type="submit" variant="primary" disabled={!ready} busy={sending}>
            Search
          </Button>
        </div>
        <p id="youtube-search-where" className="text-sm text-text-muted">
          {where.ok ? (inResultsTab ? "Searches again in the results tab" : where.mode === "tab" ? `Searches in this tab: ${where.tab.title ?? "YouTube"}` : `Opens a new tab in ${browserLabel(state, where.browserInstanceId)}`) : where.reason === "no_extension" ? "Searching needs the DoMe browser extension, which is not connected on the PC." : where.reason === "no_browser" ? "Open Chrome or Edge with the DoMe extension on the PC to search." : "Choose the browser to search in."}
        </p>
        {candidate ? (
          <div className="flex items-center justify-between gap-3 text-sm">
            <span>{candidate === resultsTab ? "Search in the results tab" : "Use the YouTube tab DoMe is controlling"}</span>
            <Toggle checked={useTab} onChange={(v) => setInTab(v)} label={candidate === resultsTab ? "Search in the results tab" : "Use the YouTube tab DoMe is controlling"} />
          </div>
        ) : null}
        {!useTab && browsers.length > 1 ? (
          <ul className="flex flex-wrap gap-2" aria-label="Browser">
            {browsers.map((id) => (
              <li key={id}>
                <button type="button" aria-pressed={where.ok && where.mode === "new_tab" && where.browserInstanceId === id} onClick={() => setBrowserId(id)} className={`rounded-full border px-3 py-2 text-sm ${where.ok && where.mode === "new_tab" && where.browserInstanceId === id ? "border-accent bg-accent/10" : "border-border bg-surface"}`}>
                  {browserLabel(state, id)}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="text-xs text-text-faint">Your search goes only to the browser on your PC. DoMe does not log or save it.</p>
      </form>
      {lastSearch ? (
        <div className="mt-4">
          <h3 className="font-semibold mb-2">Results</h3>
          {lastSearch.results.length === 0 ? (
            <p className="text-sm text-text-muted">No video results.</p>
          ) : (
            <ol className="space-y-1">
              {lastSearch.results.map((r) => (
                <li key={`${r.index}:${r.video_id}`}>
                  <button type="button" disabled={!canControl || listProblem !== null} onClick={() => void open(lastSearch, r.index, r.video_id)} className="w-full text-left rounded-control border border-border bg-surface px-3 py-2.5 hover:bg-surface-hover disabled:opacity-50">
                    <span className="block font-medium line-clamp-2">
                      {r.index}. {r.title || "Untitled video"}
                    </span>
                    {r.channel || r.is_live || r.duration_seconds !== undefined ? <span className="block text-xs text-text-faint truncate">{[r.channel, r.is_live ? "Live" : r.duration_seconds !== undefined ? mmss(r.duration_seconds) : null].filter(Boolean).join(" · ")}</span> : null}
                  </button>
                </li>
              ))}
            </ol>
          )}
          {listProblem ? (
            <p className="text-sm text-warning mt-2">{listProblem}</p>
          ) : (
            <p className="text-xs text-text-faint mt-2">
              Tap a result to play it in the results tab on the PC.
              {others.length === 1 ? ` ${others[0]!.title ? `“${others[0]!.title}” is` : "A video is"} playing in another tab; it is paused first, so two videos don't play at once.` : others.length > 1 ? " The videos playing in other tabs are paused first." : ""}
            </p>
          )}
        </div>
      ) : null}
    </Card>
  );
}
