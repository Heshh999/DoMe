/**
 * The coarse state an assistant turn carries (docs/design/assistant.md §4.9; rest.schema.json `assistant_context`):
 * enums and numbers only, plus the persona's two pattern-limited words. Built from typed fields of the live view and
 * the parser's context; there is no path from a title, an app name, tab or window text, a search query or a result
 * into it, and it is validated against the contract before it leaves the phone.
 */
import { schemas, type rest } from "@dome/protocol";

import { resultsTabProblem, type LiveView } from "./commandPlan.ts";
import type { IntentContext } from "./intents.ts";
import { resolveYoutubeTarget } from "./targets.ts";

/** rest.schema.json `assistant_persona` patterns: letters, space, . ' - only. */
export const BUTLER_NAME = /^[A-Za-z][A-Za-z .'-]{0,19}$/;
export const FORM_OF_ADDRESS = /^[A-Za-z][A-Za-z .'-]{0,23}$/;
export const DEFAULT_BUTLER_NAME = "DoMe";

const CAPABILITIES: ReadonlySet<string> = new Set<rest.AssistantCapability>(["status", "media", "volume", "apps", "lock", "power"]);

export interface PersonaPrefs {
  butlerName: string;
  /** "" = none: no sir, madam or name. */
  address: string;
}

export interface ContextView extends LiveView {
  /** The selected PC can be controlled now (online, enabled, remote on, state current). */
  controllable: boolean;
  /** The PC's state is current (volumes are only sent then). */
  fresh: boolean;
}

/** 5–11 morning, 12–16 afternoon, 17–21 evening, otherwise night (local time; never the time itself). */
export function partOfDay(now: Date): NonNullable<rest.AssistantContext["part_of_day"]> {
  const h = now.getHours();
  if (h >= 5 && h <= 11) return "morning";
  if (h >= 12 && h <= 16) return "afternoon";
  if (h >= 17 && h <= 21) return "evening";
  return "night";
}

export function persona(prefs: PersonaPrefs): rest.AssistantPersona {
  const name = BUTLER_NAME.test(prefs.butlerName) ? prefs.butlerName : DEFAULT_BUTLER_NAME;
  return FORM_OF_ADDRESS.test(prefs.address) ? { name, address: prefs.address } : { name };
}

function youtubePlayback(view: ContextView): rest.AssistantContext["playback"]["youtube"] {
  const yt = resolveYoutubeTarget(view.state, view.selectedTab);
  if (yt.ok) return yt.tab.context === "other" ? "list" : yt.tab.paused === false ? "playing" : "paused";
  return yt.reason === "no_player" ? "list" : "none";
}

const level = (v: number | null | undefined): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 && v <= 100 ? v : null);

/**
 * `assistant_context` for one turn. `capabilities` is this phone's grant on the PC (null while unknown: omitted);
 * pointer and keyboard are never included. Throws only if the result would not satisfy the contract (a bug).
 */
export function buildAssistantContext(view: ContextView, ctx: IntentContext, capabilities: readonly string[] | null, prefs: PersonaPrefs, now: Date): rest.AssistantContext {
  const media = ctx.playback?.media;
  const out: rest.AssistantContext = {
    persona: persona(prefs),
    pc_connection: view.controllable ? "online" : "offline",
    playback: {
      youtube: view.state ? youtubePlayback(view) : "none",
      media: media?.available ? (media.playing ? "playing" : "paused") : "none",
    },
  };
  const pcVolume = view.fresh ? level(ctx.pcVolume) : null;
  if (pcVolume !== null) out.pc_volume = pcVolume;
  const ytVolume = view.fresh ? level(ctx.youtubeVolume) : null;
  if (ytVolume !== null) out.youtube_volume = ytVolume;
  if (view.lastSearch && resultsTabProblem(view.state, view.lastSearch, view.stateSeq) === null) out.search_results = Math.min(10, view.lastSearch.results.length);
  if (capabilities) out.capabilities = [...new Set(capabilities.filter((c) => CAPABILITIES.has(c)))].slice(0, 6) as NonNullable<rest.AssistantContext["capabilities"]>;
  out.part_of_day = partOfDay(now);
  schemas.validateRest("assistant_context", out);
  return out;
}
