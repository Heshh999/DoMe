/**
 * Live typing (spec §10A B): turns what the phone's keyboard did to a real <textarea> into literal
 * `text` / `key` input events — exactly once per committed edit, and only when the mapping onto the
 * PC's focused field is certain.
 *
 * The model is deliberately narrow. `known` is what this phone has sent to the PC in the current live
 * run and nothing more; it is NOT assumed to mirror the PC field (the customer may have typed on the
 * PC, the field may have been empty or not). An edit is mapped only when it happens at the END of
 * `known` (the PC caret is where we left it) and every character it removes is a plain BMP code unit
 * that one Backspace on Windows removes with certainty. Everything else (an edit in the middle, a
 * selection replacement that reaches back into the middle, removing an emoji, a surrogate pair or a
 * combining mark, an IME that rewrote earlier text) pauses live entry: nothing is sent, and the sheet
 * switches to Compose and Send with an explanation. The phone never "repairs" an uncertain edit by
 * selecting all or deleting text it cannot see.
 *
 * IME compositions: candidate text that appears in the textarea while `composing` is never forwarded;
 * the diff is taken once, at compositionend. Because every commit is a diff against `known`, a late
 * `input` event after compositionend (iOS ordering varies) finds nothing new and sends nothing.
 */
import type { InputEvent as ProtoInputEvent } from "@dome/protocol";

export const TEXT_EVENT_MAX = 256;

export type TypingEvent = ProtoInputEvent;

export type TypingCommit = { ok: true; events: TypingEvent[] } | { ok: false; reason: "middle_edit" | "uncertain_deletion" | "composing" };

/** Code units a single Windows Backspace removes with certainty: BMP, not a surrogate, not a combining mark, not a ZWJ/variation selector. */
function simpleUnit(ch: string): boolean {
  const c = ch.charCodeAt(0);
  if (c >= 0xd800 && c <= 0xdfff) return false; // surrogate halves
  if (c >= 0x0300 && c <= 0x036f) return false; // combining diacriticals
  if (c >= 0x1ab0 && c <= 0x1aff) return false;
  if (c >= 0x1dc0 && c <= 0x1dff) return false;
  if (c >= 0x20d0 && c <= 0x20ff) return false;
  if (c >= 0xfe00 && c <= 0xfe0f) return false; // variation selectors
  if (c === 0x200d || c === 0x200c) return false; // ZWJ / ZWNJ
  if (c >= 0x0e30 && c <= 0x0e4e) return false; // Thai vowels/tone marks (cluster-dependent)
  if (c >= 0x0900 && c <= 0x0dff) return false; // Indic scripts: conjunct-dependent deletion
  if (c >= 0x1100 && c <= 0x11ff) return false; // Hangul jamo
  if (c >= 0x3130 && c <= 0x318f) return false;
  return true;
}

export function certainDeletion(removed: string): boolean {
  for (const ch of removed.split("")) if (!simpleUnit(ch)) return false;
  return true;
}

/** Split literal text into ≤ 256-char `text` events without cutting a surrogate pair or a grapheme cluster. */
export function splitText(text: string): TypingEvent[] {
  const events: TypingEvent[] = [];
  const units = graphemes(text);
  let chunk = "";
  for (const g of units) {
    if (chunk.length + g.length > TEXT_EVENT_MAX) {
      if (chunk) events.push({ type: "text", text: chunk });
      chunk = "";
    }
    chunk += g;
  }
  if (chunk) events.push({ type: "text", text: chunk });
  return events;
}

function graphemes(text: string): string[] {
  const Seg = (globalThis as { Intl?: { Segmenter?: new (locale?: string, o?: { granularity: string }) => { segment(s: string): Iterable<{ segment: string }> } } }).Intl?.Segmenter;
  if (Seg) {
    try {
      return [...new Seg(undefined, { granularity: "grapheme" }).segment(text)].map((s) => s.segment);
    } catch {
      /* fall through */
    }
  }
  return [...text]; // code points: at least never splits a surrogate pair
}

/** Common prefix/suffix diff between what was sent (`known`) and the textarea's current value. */
export function diffEdit(known: string, value: string): { prefix: number; removed: string; inserted: string; atEnd: boolean } {
  let p = 0;
  const max = Math.min(known.length, value.length);
  while (p < max && known.charCodeAt(p) === value.charCodeAt(p)) p++;
  let s = 0;
  while (s < max - p && known.charCodeAt(known.length - 1 - s) === value.charCodeAt(value.length - 1 - s)) s++;
  const removed = known.slice(p, known.length - s);
  const inserted = value.slice(p, value.length - s);
  return { prefix: p, removed, inserted, atEnd: s === 0 };
}

export class LiveTyping {
  /** Text this phone has committed to the PC in this live run (a prefix of the textarea, never the PC field). */
  private known = "";
  private composing = false;

  get isComposing(): boolean {
    return this.composing;
  }

  get sentText(): string {
    return this.known;
  }

  /** New target window or a fresh session: nothing sent yet for this run. */
  reset(value = ""): void {
    this.known = value;
    this.composing = false;
  }

  compositionStart(): void {
    this.composing = true;
  }

  /** compositionend: the final text is in the textarea now; one diff commits it exactly once. */
  compositionEnd(value: string): TypingCommit {
    this.composing = false;
    return this.commit(value);
  }

  /**
   * An `input` event changed the textarea to `value`. During a composition nothing is forwarded.
   * Returns the events to send (possibly none when the value already matches what was sent) or the
   * reason live entry must pause. On success `known` advances; on failure it is left unchanged so the
   * caller can decide (the sheet switches to Compose and Send and resets).
   */
  commit(value: string): TypingCommit {
    if (this.composing) return { ok: false, reason: "composing" };
    const d = diffEdit(this.known, value);
    if (d.removed === "" && d.inserted === "") return { ok: true, events: [] };
    if (!d.atEnd) return { ok: false, reason: "middle_edit" };
    if (d.removed !== "" && !certainDeletion(d.removed)) return { ok: false, reason: "uncertain_deletion" };
    const events: TypingEvent[] = [];
    for (let i = 0; i < d.removed.length; i++) events.push({ type: "key", key: "backspace" });
    if (d.inserted !== "") events.push(...splitText(d.inserted));
    this.known = value;
    return { ok: true, events };
  }

  /**
   * Backspace pressed while the textarea has nothing left to delete (`beforeinput` with
   * deleteContentBackward and an empty textarea): the only certain meaning is "one Backspace on the PC".
   */
  bareBackspace(value: string): TypingEvent | null {
    if (this.composing || value !== "" || this.known !== "") return null;
    return { type: "key", key: "backspace" };
  }
}

export const PAUSE_EXPLANATION: Record<Exclude<TypingCommit, { ok: true }>["reason"], string> = {
  middle_edit: "You changed text that was already sent, not just the end. DoMe cannot mirror that edit on the PC without guessing where the PC’s cursor is, so live typing paused. Correct it on the PC, or type the rest with Compose and Send.",
  uncertain_deletion: "That deletion removed a character (an emoji, accent or script cluster) that one Backspace on Windows may not remove the same way. Nothing was sent. Use the PC to fix it, or Compose and Send for the rest.",
  composing: "Your keyboard is still composing text. It is sent once the composition finishes.",
};
