import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import { exportPublicJwk, kidFromJwk } from "@dome/protocol";

import { clearAccountState, deleteInstallationKey, getControllerIdentity, getExistingKeyPair, getOrCreateKeyPair, getStoredControllerId, getState, resetDbHandleForTests, setState, setStoredControllerId, STATE_SELECTED_PC } from "../src/lib/controllerKey.ts";

beforeEach(() => {
  // a fresh IndexedDB per test (fake-indexeddb/auto installed the global in test/setup.ts)
  globalThis.indexedDB = new IDBFactory();
  resetDbHandleForTests();
});

describe("controller key storage", () => {
  it("creates a non-extractable ECDSA P-256 key pair once and returns the same one afterwards", async () => {
    expect(await getExistingKeyPair()).toBeNull();
    const first = await getOrCreateKeyPair();
    expect(first.privateKey.extractable).toBe(false);
    expect((first.privateKey.algorithm as EcKeyAlgorithm).namedCurve).toBe("P-256");
    expect(first.privateKey.usages).toContain("sign");
    const jwk1 = await exportPublicJwk(first.publicKey);
    resetDbHandleForTests(); // simulate a new page load against the same database
    const second = await getOrCreateKeyPair();
    expect(await exportPublicJwk(second.publicKey)).toEqual(jwk1);
    await expect(crypto.subtle.exportKey("jwk", second.privateKey)).rejects.toThrow();
  });

  it("derives a stable kid (RFC 7638 thumbprint) from the stored key", async () => {
    const id = await getControllerIdentity();
    expect(id.kid).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(id.kid).toBe(await kidFromJwk(id.jwk));
    expect(Object.keys(id.jwk).sort()).toEqual(["crv", "kty", "x", "y"]);
    expect((await getControllerIdentity()).kid).toBe(id.kid);
  });

  it("sign-out clears account state but keeps the key; forgetting deletes the key", async () => {
    const id = await getControllerIdentity();
    await setStoredControllerId("22222222-2222-4222-8222-222222222222");
    await setState(STATE_SELECTED_PC, "33333333-3333-4333-8333-333333333333");
    await clearAccountState();
    expect(await getStoredControllerId()).toBeNull();
    expect(await getState(STATE_SELECTED_PC)).toBeNull();
    expect((await getControllerIdentity()).kid).toBe(id.kid);
    await deleteInstallationKey();
    expect(await getExistingKeyPair()).toBeNull();
    expect((await getControllerIdentity()).kid).not.toBe(id.kid);
  });

  it("discards a corrupt record and reports it", async () => {
    const { openDB } = await import("idb");
    const db = await openDB("dome", 1, {
      upgrade(d) {
        d.createObjectStore("keys");
        d.createObjectStore("state");
      },
    });
    await db.put("keys", { not: "a key" }, "controller");
    db.close();
    await expect(getExistingKeyPair()).rejects.toMatchObject({ name: "KeyStorageError", reason: "corrupt" });
    // getOrCreateKeyPair recovers by generating a fresh key (the customer must pair again)
    const pair = await getOrCreateKeyPair();
    expect(pair.privateKey.type).toBe("private");
  });

  it("reports unavailable storage instead of falling back to anything weaker", async () => {
    const saved = globalThis.indexedDB;
    // @ts-expect-error simulate a context without IndexedDB
    delete globalThis.indexedDB;
    resetDbHandleForTests();
    await expect(getOrCreateKeyPair()).rejects.toMatchObject({ name: "KeyStorageError", reason: "unavailable" });
    globalThis.indexedDB = saved;
  });
});
