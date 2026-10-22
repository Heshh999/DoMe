/**
 * The screen wake lock while a Screen view runs (docs/design/screen-view.md §2.2 step 3): requested when wanted,
 * released when not, requested again after the browser dropped it while hidden, harmless where the API is missing or
 * refuses.
 */
import { describe, expect, it } from "vitest";

import { WakeLockHolder, wakeLockApi } from "../src/lib/wakeLock.ts";

function fakeApi(refuse = false) {
  const sentinels: Array<{ released: boolean; release(): Promise<void> }> = [];
  return {
    sentinels,
    api: {
      request: async (type: "screen") => {
        expect(type).toBe("screen");
        if (refuse) throw new DOMException("not allowed", "NotAllowedError");
        const s = {
          released: false,
          release: async () => {
            s.released = true;
          },
        };
        sentinels.push(s);
        return s;
      },
    },
  };
}

describe("WakeLockHolder", () => {
  it("requests one lock while wanted, releases it when not, and re-requests after the browser dropped it", async () => {
    const f = fakeApi();
    const holder = new WakeLockHolder(f.api);
    await holder.want(true);
    await holder.want(true);
    expect(f.sentinels).toHaveLength(1);
    expect(holder.held).toBe(true);
    f.sentinels[0]!.released = true; // the page was hidden: the browser released it
    expect(holder.held).toBe(false);
    await holder.revisit();
    expect(f.sentinels).toHaveLength(2);
    await holder.want(false);
    expect(f.sentinels[1]!.released).toBe(true);
    await holder.revisit();
    expect(f.sentinels).toHaveLength(2); // not wanted: nothing requested on return
  });
  it("no API, or a refusal, is simply no lock", async () => {
    expect(wakeLockApi({})).toBeNull();
    const none = new WakeLockHolder(null);
    await none.want(true);
    expect(none.held).toBe(false);
    const refusing = new WakeLockHolder(fakeApi(true).api);
    await refusing.want(true);
    expect(refusing.held).toBe(false);
  });
  it("a lock granted after it stopped being wanted is released at once", async () => {
    const f = fakeApi();
    const holder = new WakeLockHolder(f.api);
    const pending = holder.want(true);
    void holder.want(false);
    await pending;
    expect(f.sentinels[0]!.released).toBe(true);
    expect(holder.held).toBe(false);
  });
});
