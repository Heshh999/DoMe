/**
 * Controller identity for this installation: a non-extractable ECDSA P-256 key pair kept as
 * CryptoKey objects in IndexedDB (`dome` → `keys` → `controller`). The private key never leaves
 * WebCrypto; the public JWK and `kid` are derived on demand. Account-scoped state (controller_id,
 * selected PC) lives in the `state` store so sign-out can clear it while keeping the key.
 *
 * If storage is unavailable or the key is gone (cleared site data, private browsing), the UI
 * explains that this installation must pair again. There is deliberately no recovery shortcut.
 *
 * Since protocol 1.4 the same database also holds the PC identity keys pinned at pairing (`pins`,
 * rules.pc_key_pinning; written only by `lib/pcKeyPins.ts` after the MAC verified). A pin belongs to
 * the pairing, so it lives next to the key: sign-out keeps both, "Forget this installation" deletes both.
 */
import { openDB, type DBSchema, type IDBPDatabase } from "idb";

import { exportPublicJwk, generateControllerKeyPair, kidFromJwk, type EcPublicJwk } from "@dome/protocol";

import { log } from "./log.ts";

/** One PC identity key this phone trusts for Screen view answers (rules.pc_key_pinning (3)). */
export interface PcKeyPin {
  pc_id: string;
  pc_kid: string;
  pc_public_jwk: EcPublicJwk;
  pinned_at: string;
}

interface DomeDb extends DBSchema {
  keys: { key: string; value: CryptoKeyPair };
  state: { key: string; value: string };
  pins: { key: string; value: PcKeyPin };
}

export class KeyStorageError extends Error {
  readonly reason: "unavailable" | "corrupt";
  constructor(reason: "unavailable" | "corrupt", message: string) {
    super(message);
    this.name = "KeyStorageError";
    this.reason = reason;
  }
}

export interface ControllerIdentity {
  keyPair: CryptoKeyPair;
  jwk: EcPublicJwk;
  kid: string;
}

const DB_NAME = "dome";
/** 1: keys + state. 2 (protocol 1.4): + pins. The upgrade only ever adds stores; nothing is migrated or dropped. */
const DB_VERSION = 2;
const KEY_RECORD = "controller";
export const STATE_CONTROLLER_ID = "controller_id";
export const STATE_SELECTED_PC = "selected_pc_id";

let dbPromise: Promise<IDBPDatabase<DomeDb>> | null = null;

function db(): Promise<IDBPDatabase<DomeDb>> {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new KeyStorageError("unavailable", "IndexedDB is not available in this browser context"));
  }
  if (!dbPromise) {
    dbPromise = openDB<DomeDb>(DB_NAME, DB_VERSION, {
      upgrade(database) {
        if (!database.objectStoreNames.contains("keys")) database.createObjectStore("keys");
        if (!database.objectStoreNames.contains("state")) database.createObjectStore("state");
        if (!database.objectStoreNames.contains("pins")) database.createObjectStore("pins");
      },
      blocking() {
        // another tab upgraded the schema; drop our handle so the next call reopens
        dbPromise = null;
      },
    }).catch((e: unknown) => {
      dbPromise = null;
      throw new KeyStorageError("unavailable", `IndexedDB could not be opened: ${e instanceof Error ? e.name : "error"}`);
    });
  }
  return dbPromise;
}

function isUsableKeyPair(value: unknown): value is CryptoKeyPair {
  if (!value || typeof value !== "object") return false;
  const { privateKey, publicKey } = value as Partial<CryptoKeyPair>;
  if (!privateKey || !publicKey) return false;
  const alg = privateKey.algorithm as EcKeyAlgorithm | undefined;
  return (
    privateKey.type === "private" &&
    privateKey.extractable === false &&
    alg?.name === "ECDSA" &&
    alg.namedCurve === "P-256" &&
    privateKey.usages.includes("sign") &&
    publicKey.type === "public"
  );
}

export async function getExistingKeyPair(): Promise<CryptoKeyPair | null> {
  const database = await db();
  const stored = await database.get("keys", KEY_RECORD);
  if (stored === undefined) return null;
  if (!isUsableKeyPair(stored)) {
    log.warn("controller_key.corrupt_record_discarded");
    await database.delete("keys", KEY_RECORD);
    throw new KeyStorageError("corrupt", "The stored controller key was unusable and has been removed; pair again.");
  }
  return stored;
}

export async function getOrCreateKeyPair(): Promise<CryptoKeyPair> {
  const existing = await getExistingKeyPair().catch((e: unknown) => {
    if (e instanceof KeyStorageError && e.reason === "corrupt") return null;
    throw e;
  });
  if (existing) return existing;
  const pair = await generateControllerKeyPair();
  const database = await db();
  // Another tab may have raced us; the first writer wins so both tabs share one identity.
  const tx = database.transaction("keys", "readwrite");
  const current = await tx.store.get(KEY_RECORD);
  if (current !== undefined && isUsableKeyPair(current)) {
    await tx.done;
    return current;
  }
  await tx.store.put(pair, KEY_RECORD);
  await tx.done;
  log.info("controller_key.generated");
  return pair;
}

export async function getControllerIdentity(): Promise<ControllerIdentity> {
  const keyPair = await getOrCreateKeyPair();
  const jwk = await exportPublicJwk(keyPair.publicKey);
  const kid = await kidFromJwk(jwk);
  return { keyPair, jwk, kid };
}

export async function getState(key: string): Promise<string | null> {
  const database = await db();
  return (await database.get("state", key)) ?? null;
}

export async function setState(key: string, value: string | null): Promise<void> {
  const database = await db();
  if (value === null) await database.delete("state", key);
  else await database.put("state", value, key);
}

export function getStoredControllerId(): Promise<string | null> {
  return getState(STATE_CONTROLLER_ID);
}

export function setStoredControllerId(id: string | null): Promise<void> {
  return setState(STATE_CONTROLLER_ID, id);
}

/** Sign-out / account switch: forget everything account-specific but keep the installation key. */
export async function clearAccountState(): Promise<void> {
  const database = await db();
  await database.clear("state");
}

/** Full local reset (Settings → "Forget this installation"): the key and every PC pin are deleted, pairing is required again. */
export async function deleteInstallationKey(): Promise<void> {
  const database = await db();
  await database.clear("state");
  await database.clear("pins");
  await database.delete("keys", KEY_RECORD);
  log.info("controller_key.deleted_by_user");
}

// ---- PC key pins (protocol 1.4) ------------------------------------------------------------------------
// Only lib/pcKeyPins.ts writes here, and only after verifyPcKeyMac passed with the pairing code.

export async function readPin(pcId: string): Promise<PcKeyPin | null> {
  const database = await db();
  return (await database.get("pins", pcId)) ?? null;
}

export async function writePin(pin: PcKeyPin): Promise<void> {
  const database = await db();
  await database.put("pins", pin, pin.pc_id);
}

export async function deletePin(pcId: string): Promise<void> {
  const database = await db();
  await database.delete("pins", pcId);
}

export async function listPins(): Promise<PcKeyPin[]> {
  const database = await db();
  return database.getAll("pins");
}

/** Test/diagnostic hook: forget the cached connection so a fresh `openDB` happens. */
export function resetDbHandleForTests(): void {
  dbPromise = null;
}
