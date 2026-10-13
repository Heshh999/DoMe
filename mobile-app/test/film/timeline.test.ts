/**
 * The film's timeline is content as much as code: the scene list, captions and status chips carry the website's
 * honesty rules (rollout brief §4) and the plan facts (§5). These tests pin them.
 */
import { describe, expect, it } from "vitest";

import { handAt, pcAt, phoneAt, stageAt } from "../../src/components/film/script.ts";
import { beatAt, CONCEPT_NOTE, EASE, PLAN_FACTS, SCENES, sceneAt, sceneByNumber, seg, TOTAL_MS, typedWords, window as win } from "../../src/components/film/timeline.ts";

describe("the scene list", () => {
  it("has the six storyboard beats, numbered 1–6 and contiguous from 0 to the end of the film", () => {
    expect(SCENES.map((s) => s.n)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(SCENES.map((s) => s.id)).toEqual(["room", "ask", "screen", "point", "plans", "end"]);
    expect(SCENES[0]!.start).toBe(0);
    expect(SCENES[SCENES.length - 1]!.end).toBe(TOTAL_MS);
    for (let i = 1; i < SCENES.length; i++) expect(SCENES[i]!.start).toBe(SCENES[i - 1]!.end);
    for (const s of SCENES) {
      expect(s.caption.length).toBeGreaterThan(0);
      expect(s.status.label.length).toBeGreaterThan(0);
      expect(s.still).toBeGreaterThanOrEqual(s.start);
      expect(s.still).toBeLessThan(s.end);
    }
    // about a minute, as the brief asks (50–70 s)
    expect(TOTAL_MS).toBeGreaterThanOrEqual(50_000);
    expect(TOTAL_MS).toBeLessThanOrEqual(70_000);
  });

  it("keeps the storyboard's captions", () => {
    expect(sceneByNumber(1).caption).toBe("Your PC, in your hand.");
    expect(sceneByNumber(2).caption).toMatch(/^Just ask\./);
    // "everyday" is kept for the free, non-AI commands: the butler handles requests in your own words
    expect(sceneByNumber(2).caption).toMatch(/requests in your own words/);
    expect(sceneByNumber(2).caption).not.toMatch(/everyday/i);
    expect(sceneByNumber(3).caption).toBe("See your PC. Tap what you see.");
    expect(sceneByNumber(4).caption).toMatch(/^Point-and-talk\./);
    expect(sceneByNumber(4).caption).toMatch(/the AI never sees your screen/);
    expect(sceneByNumber(5).caption).toMatch(/^Free to start\./);
    expect(sceneByNumber(6).caption).toBe("Your PC, in your hand.");
  });

  it("finds the scene for any time, including the ends", () => {
    expect(sceneAt(-5).n).toBe(1);
    expect(sceneAt(0).n).toBe(1);
    expect(sceneAt(7_999).n).toBe(1);
    expect(sceneAt(8_000).n).toBe(2);
    expect(sceneAt(TOTAL_MS - 1).n).toBe(6);
    expect(sceneAt(TOTAL_MS).n).toBe(6);
    expect(sceneAt(TOTAL_MS + 1_000).n).toBe(6);
    expect(sceneByNumber(99).n).toBe(1);
  });
});

describe("honesty rules (brief §4)", () => {
  it("labels Screen view as Coming, point-and-talk as Planned, and the paid plans as arriving at paid launch", () => {
    const screen = sceneByNumber(3);
    expect(screen.status.tone).toBe("coming");
    expect(screen.status.label).toMatch(/Screen view · Coming/);
    expect(screen.status.label).toMatch(/Free: 10 min per 5 h/);
    expect(screen.status.label).toMatch(/Pro: unlimited/);

    const point = sceneByNumber(4);
    expect(point.status.tone).toBe("planned");
    expect(point.status.label).toMatch(/Planned/);

    const plans = sceneByNumber(5);
    expect(plans.status.label).toBe("Pro and Pro Plus arrive at paid launch");
    expect(sceneByNumber(6).status.label).toMatch(/Pro and Pro Plus at paid launch/);
  });

  it("presents the AI butler as available today, with the free taste, and remote control as free now", () => {
    expect(sceneByNumber(1).status.tone).toBe("now");
    expect(sceneByNumber(1).status.label).toMatch(/Available now, free/);
    expect(sceneByNumber(2).status.tone).toBe("now");
    expect(sceneByNumber(2).status.label).toBe("AI butler · Available now · Free: 10 to try · more with Pro and Pro Plus");
  });

  it("never claims recordings, screenshots or AI vision anywhere in the film's words", () => {
    const words = [...SCENES.flatMap((s) => [s.caption, s.status.label, ...(s.beats ?? []).map((b) => b.text)]), ...PLAN_FACTS.flatMap((p) => [p.status, ...p.lines.map((l) => l.text)]), CONCEPT_NOTE].join(" ");
    expect(words).not.toMatch(/record(ed|ing)/i);
    expect(words).not.toMatch(/AI (can )?sees?\b(?! your screen)/);
    expect(CONCEPT_NOTE).toBe("Concept preview. Screen view and point-and-talk are in development.");
  });
});

describe("plan facts (brief §5)", () => {
  const [free, pro, plus] = PLAN_FACTS as [(typeof PLAN_FACTS)[number], (typeof PLAN_FACTS)[number], (typeof PLAN_FACTS)[number]];
  const texts = (p: (typeof PLAN_FACTS)[number]) => p.lines.map((l) => l.text.replace(/\u00a0/g, " "));

  it("shows Free as available and Pro / Pro Plus as coming, with the owner's prices", () => {
    expect(free.name).toBe("Free");
    expect(free.price).toBe("$0");
    expect(free.per).toBe("");
    expect(free.status).toBe("Available now");
    expect(free.tone).toBe("now");
    expect(pro.name).toBe("Pro");
    expect(pro.price).toBe("$9.99");
    expect(pro.per).toBe("/ month");
    expect(pro.sub).toBe("or $99.99 / year");
    expect(pro.status).toBe("Coming at paid launch");
    expect(plus.name).toBe("Pro Plus");
    expect(plus.price).toBe("$24.99");
    expect(plus.sub).toBe("or $249.99 / year");
    expect(plus.status).toBe("Coming at paid launch");
  });

  it("carries the contents of each plan, with Screen view marked Coming on every card", () => {
    expect(texts(free)).toContain("1 PC · 2 phones");
    expect(texts(free)).toContain("Screen view: 10 min per 5 h");
    expect(texts(free)).toContain("AI: 10 requests to try, once");
    expect(texts(pro)).toContain("5 PCs · 5 phones");
    expect(texts(pro)).toContain("Screen view: unlimited");
    expect(texts(pro)).toContain("AI: 30 requests per 5 h, up to 120 a week");
    expect(texts(pro)).toContain("Top-up: 100 requests for $2.99");
    expect(texts(plus)).toContain("AI: 90 requests per 5 h, up to 300 a week");
    expect(texts(plus)).toContain("Top-up: 100 requests for $2.99");
    for (const p of PLAN_FACTS) {
      expect(texts(p)).toContain("Remote control: all of it");
      const screen = p.lines.find((l) => l.text.startsWith("Screen view"));
      expect(screen?.status).toBe("Coming");
      // the vertical card keeps exactly two lines
      expect(p.lines.filter((l) => l.key)).toHaveLength(2);
    }
  });
});

describe("beats", () => {
  it("say what happens in scenes 2–4 without sound, including the spoken words", () => {
    expect(beatAt(8_500)).toBeNull();
    expect(beatAt(10_000)?.text).toBe("Put on the next episode and make it full screen");
    expect(beatAt(10_000)?.speech).toBe(true);
    expect(beatAt(14_000)?.text).toMatch(/Very good\. Playing the next episode, full screen\./);
    expect(beatAt(27_000)?.text).toBe("Two fingers to scroll");
    expect(beatAt(43_000)?.text).toBe("click that");
    expect(beatAt(44_500)?.text).toMatch(/Clicking Subscribe/);
    expect(beatAt(56_000)).toBeNull();
    for (const s of SCENES) for (const b of s.beats ?? []) {
      expect(b.at).toBeGreaterThanOrEqual(s.start);
      expect(b.at).toBeLessThan(s.end);
    }
  });
});

describe("motion helpers", () => {
  it("seg is 0 before, 1 after, eased in between", () => {
    expect(seg(999, 1_000, 500)).toBe(0);
    expect(seg(1_500, 1_000, 500)).toBe(1);
    const mid = seg(1_250, 1_000, 500);
    expect(mid).toBeGreaterThan(0.5); // the brand easing is front-loaded
    expect(mid).toBeLessThan(1);
    expect(EASE(0)).toBe(0);
    expect(EASE(1)).toBe(1);
  });

  it("window is 1 inside, fades at both ends and is 0 outside", () => {
    expect(win(0, 100, 1_000, 100)).toBe(0);
    expect(win(500, 100, 1_000, 100)).toBe(1);
    expect(win(1_000, 100, 1_000, 100)).toBe(0);
    expect(win(950, 100, 1_000, 100)).toBeLessThan(1);
  });

  it("types words in over time", () => {
    expect(typedWords("put on the next", 0, 100, 4)).toBe("");
    expect(typedWords("put on the next", 100, 100, 4)).toBe("put");
    expect(typedWords("put on the next", 350, 100, 4)).toBe("put on");
    expect(typedWords("put on the next", 5_000, 100, 4)).toBe("put on the next");
  });
});

describe("the script", () => {
  it("is a pure function of time: the same t always gives the same frame", () => {
    for (const t of [0, 1_200, 5_900, 17_600, 36_500, 46_400, 56_000, 64_000]) {
      expect(pcAt(t)).toEqual(pcAt(t));
      expect(phoneAt(t)).toEqual(phoneAt(t));
      expect(handAt(t)).toEqual(handAt(t));
      expect(stageAt(t)).toEqual(stageAt(t));
    }
  });

  it("never starts blank: the room is on stage from the first frame, then the lamp, the PC and the phone arrive", () => {
    expect(stageAt(0).room).toBe(1);
    expect(stageAt(0).lamp).toBe(0);
    expect(pcAt(0).on).toBe(0);
    expect(phoneAt(0).entrance).toBe(0);
    expect(stageAt(1_200).lamp).toBe(1);
    expect(pcAt(2_100).on).toBe(1);
    expect(phoneAt(3_400).entrance).toBe(1);
  });

  it("follows the storyboard's beats", () => {
    // scene 2: the request is typed, the plan is shown first, then the PC obeys (next episode, full screen)
    expect(phoneAt(13_000).ownerText).toBe("Put on the next episode and make it full screen");
    expect(phoneAt(14_500).plan?.running).toBe(false);
    expect(pcAt(14_500).episode).toBe(3);
    expect(pcAt(18_000).episode).toBe(4);
    expect(pcAt(18_000).fullscreen).toBe(1);
    expect(phoneAt(18_000).plan?.steps.every((s) => s.done)).toBe(true);
    // scene 3: the mirror arrives, shrinks to the corner while Apps opens, the TV app gets a D-pad, PC audio can be muted
    expect(phoneAt(24_000).mirror).toBe(1);
    expect(phoneAt(31_000).page).toBe("apps");
    expect(phoneAt(31_000).mini).toBe(1);
    expect(pcAt(34_200).app).toBe("tv");
    expect(phoneAt(34_000).dpadPress).toBe("right");
    expect(phoneAt(38_000).muted).toBe(true);
    // scene 4: the finger rests on Subscribe, the user says "click that", the name lights up, the PC clicks it
    expect(phoneAt(44_000).spoken).toBe("click that");
    expect(phoneAt(44_000).nameTag).toBe(1);
    expect(pcAt(46_000).subscribed).toBe(true);
    // scene 5 and 6
    expect(stageAt(56_000).cards.every((c) => c === 1)).toBe(true);
    expect(stageAt(56_000).proGlow).toBe(1);
    expect(stageAt(64_000).lockup).toBe(1);
    expect(stageAt(64_000).buttons).toBe(1);
  });
});
