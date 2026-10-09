import { describe, expect, it } from "vitest";

import { certainDeletion, diffEdit, LiveTyping, PAUSE_EXPLANATION, splitText } from "../src/lib/typing.ts";

describe("LiveTyping", () => {
  it("forwards appended text once per input event and never resends what was already sent", () => {
    const t = new LiveTyping();
    expect(t.commit("h")).toEqual({ ok: true, events: [{ type: "text", text: "h" }] });
    expect(t.commit("he")).toEqual({ ok: true, events: [{ type: "text", text: "e" }] });
    expect(t.commit("hello world")).toEqual({ ok: true, events: [{ type: "text", text: "llo world" }] });
    expect(t.commit("hello world")).toEqual({ ok: true, events: [] }); // duplicate input event: nothing
    expect(t.sentText).toBe("hello world");
  });

  it("an IME composition commits exactly once at compositionend, whatever the event order", () => {
    const t = new LiveTyping();
    t.commit("a ");
    t.compositionStart();
    expect(t.commit("a に")).toEqual({ ok: false, reason: "composing" }); // candidate, not forwarded
    expect(t.commit("a にほ")).toEqual({ ok: false, reason: "composing" });
    expect(t.compositionEnd("a 日本")).toEqual({ ok: true, events: [{ type: "text", text: "日本" }] });
    // iOS sometimes fires a trailing input event after compositionend with the same value
    expect(t.commit("a 日本")).toEqual({ ok: true, events: [] });
    // and sometimes the input event comes first: compositionend then finds nothing new
    t.compositionStart();
    t.commit("a 日本語");
    expect(t.compositionEnd("a 日本語")).toEqual({ ok: true, events: [{ type: "text", text: "語" }] });
    expect(t.commit("a 日本語")).toEqual({ ok: true, events: [] });
  });

  it("autocorrect replacing the last word maps to backspaces + text when every removed character is simple", () => {
    const t = new LiveTyping();
    t.commit("I teh");
    // common prefix "I t": the certain mapping removes "eh" (two backspaces) and types "he "
    expect(t.commit("I the ")).toEqual({ ok: true, events: [{ type: "key", key: "backspace" }, { type: "key", key: "backspace" }, { type: "text", text: "he " }] });
  });

  it("an edit in the middle of already-sent text pauses live entry (nothing is sent, nothing is deleted on the PC)", () => {
    const t = new LiveTyping();
    t.commit("hello world");
    const r = t.commit("hello big world");
    expect(r).toEqual({ ok: false, reason: "middle_edit" });
    expect(t.sentText).toBe("hello world"); // unchanged: the caller resets and switches to Compose and Send
    expect(PAUSE_EXPLANATION.middle_edit).toMatch(/paused/i);
    expect(PAUSE_EXPLANATION.middle_edit).not.toMatch(/select all/i);
  });

  it("deleting an emoji, a surrogate pair or a combining mark is uncertain and pauses instead of guessing backspaces", () => {
    const t = new LiveTyping();
    t.commit("ok 👍");
    expect(t.commit("ok ")).toEqual({ ok: false, reason: "uncertain_deletion" });
    const u = new LiveTyping();
    u.commit("café");
    expect(u.commit("cafe")).toEqual({ ok: false, reason: "uncertain_deletion" });
    expect(certainDeletion("abc 123 .,!")).toBe(true);
    expect(certainDeletion("é")).toBe(true); // precomposed U+00E9 is one BMP unit
    expect(certainDeletion("👍")).toBe(false);
    expect(certainDeletion("한")).toBe(true); // precomposed syllable
    expect(certainDeletion("한")).toBe(false); // jamo sequence
  });

  it("a bare Backspace in an empty textarea is one backspace on the PC, but only when nothing of ours could be deleted instead", () => {
    const t = new LiveTyping();
    expect(t.bareBackspace("")).toEqual({ type: "key", key: "backspace" });
    t.commit("x");
    expect(t.bareBackspace("x")).toBeNull();
    t.compositionStart();
    expect(t.bareBackspace("")).toBeNull();
  });

  it("splits long text into ≤ 256-character events without cutting an emoji or a grapheme", () => {
    const base = "a".repeat(255) + "👍" + "b".repeat(10);
    const events = splitText(base);
    expect(events.length).toBe(2);
    const texts = events.map((e) => (e.type === "text" ? e.text : ""));
    expect(texts[0]).toBe("a".repeat(255));
    expect(texts[1]).toBe("👍" + "b".repeat(10));
    for (const t of texts) expect(t.length).toBeLessThanOrEqual(256);
  });

  it("diffEdit finds the changed region", () => {
    expect(diffEdit("abc", "abcd")).toEqual({ prefix: 3, removed: "", inserted: "d", atEnd: true });
    expect(diffEdit("abcd", "abc")).toEqual({ prefix: 3, removed: "d", inserted: "", atEnd: true });
    expect(diffEdit("abcd", "aXcd")).toEqual({ prefix: 1, removed: "b", inserted: "X", atEnd: false });
  });
});
