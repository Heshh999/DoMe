/**
 * Settings → Delete account (docs/BILLING.md §11, cloud-api DECISIONS #121). The sheet says plainly what happens, then
 * asks for the phrase typed out; only then is the red button enabled. DELETE /v1/account answers:
 *
 * - 204: the account is deleted and the session cookie cleared. `onDeleted` (the Settings page) makes the phone forget
 *   the account and leaves for the home page, which says "Your account was deleted.".
 * - 403 REAUTH_REQUIRED: this session was not created by a sign-in again whose password entry the provider confirmed
 *   in the last 10 minutes (a plain sign-in never counts: the provider may answer it without asking). "Sign in again"
 *   asks the provider for its login page (`prompt=login`) and comes back to Settings with this sheet open
 *   (`DELETE_ACCOUNT_RETURN`); the person types the phrase once more.
 * - anything else: nothing changes on this phone and the error is said in plain words. It never claims that nothing
 *   was deleted: an answer lost on the way (NETWORK) may follow a deletion that happened. Then the next request finds
 *   the session gone, and signing in lands on the home page's "This account was deleted" notice.
 *
 * The sheet names the account it deletes: a sign-in again may have been made with another one.
 */
import { useState } from "react";
import { Link } from "react-router";

import type { rest } from "@dome/protocol";

import { api, ApiError, loginUrl } from "../lib/api.ts";
import { errorMessage } from "../lib/labels.ts";
import { supportContact } from "../lib/signInProblem.ts";
import { Sheet } from "./Sheet.tsx";
import { Button, Field, inputClass, Notice } from "./ui.tsx";

/** rest.schema.json `account_delete_request`: what the person types, and what is sent. */
export const DELETE_PHRASE: rest.AccountDeleteRequest["confirm"] = "delete my account";
/** Settings opens this sheet while the address carries `?delete_account=1`. */
export const DELETE_ACCOUNT_PARAM = "delete_account";
/** Where "Sign in again" comes back to: Settings, with this sheet open. */
export const DELETE_ACCOUNT_RETURN = `/app/settings?${DELETE_ACCOUNT_PARAM}=1`;

/** The server compares the phrase exactly, so what was typed is trimmed and lower-cased first (iOS capitalises the first letter). */
function typedPhrase(typed: string): string {
  return typed.trim().toLowerCase();
}

type Phase = { kind: "idle" } | { kind: "deleting" } | { kind: "reauth" } | { kind: "failed"; message: string };

const SIGN_IN_LINK = "tap dome-primary dome-press inline-flex min-h-[44px] w-full items-center justify-center rounded-control px-4 text-[15px] font-semibold";

export function DeleteAccountSheet({ open, email, onClose, onDeleted }: { open: boolean; email: string | null; onClose(): void; onDeleted(): Promise<void> }) {
  const [typed, setTyped] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const busy = phase.kind === "deleting";
  const matches = typedPhrase(typed) === DELETE_PHRASE;
  const contact = supportContact();

  const close = () => {
    if (!busy) onClose();
  };

  const remove = async () => {
    if (!matches || busy) return;
    setPhase({ kind: "deleting" });
    try {
      await api.deleteAccount({ confirm: DELETE_PHRASE });
    } catch (e) {
      if (e instanceof ApiError && e.code === "REAUTH_REQUIRED") setPhase({ kind: "reauth" });
      else setPhase({ kind: "failed", message: errorMessage(e instanceof ApiError ? e : null) });
      return;
    }
    await onDeleted();
  };

  return (
    <Sheet open={open} title="Delete your account?" onClose={close} dismissible={!busy} labelledBy="delete-account-title">
      <div className="max-h-[40dvh] space-y-2 overflow-y-auto text-sm text-text-muted" data-testid="delete-account-facts">
        <p>
          {email ? (
            <>
              This deletes the DoMe account <strong className="font-semibold text-text">{email}</strong>. You can’t undo it.
            </>
          ) : (
            "This deletes your DoMe account. You can’t undo it."
          )}
        </p>
        <ul className="list-disc space-y-1.5 pl-5 marker:text-text-faint">
          <li>Your paid plan, if you have one, is cancelled at once. There is no automatic refund.</li>
          <li>Any unused AI top-up requests go with the account.</li>
          <li>Every phone and PC is disconnected. The DoMe program on your PC will ask to be linked again.</li>
          <li>Your settings are deleted and the AI butler is turned off.</li>
          <li>DoMe keeps a record of your payments, without your name or email. Our payment provider keeps its own records, including your name and email.</li>
          <li>After 30 days, your account is erased for good.</li>
          <li>
            Your sign-in (the email and password you use to sign in) is kept by our sign-in provider. If you want it removed too,{" "}
            {contact ? (
              <a href={contact} className="font-semibold text-accent" rel="noopener noreferrer" target="_blank">
                contact support
              </a>
            ) : (
              <Link to="/support" className="font-semibold text-accent">
                contact support
              </Link>
            )}{" "}
            before you delete your account.
          </li>
        </ul>
      </div>
      <div className="mt-4">
        <Field id="delete-account-phrase" label={`Type “${DELETE_PHRASE}” to confirm`}>
          <input id="delete-account-phrase" className={inputClass} value={typed} onChange={(e) => setTyped(e.currentTarget.value)} disabled={busy} autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} enterKeyHint="done" maxLength={64} />
        </Field>
      </div>
      {phase.kind === "reauth" ? (
        <div className="mt-3 space-y-3" data-testid="delete-account-reauth">
          <Notice tone="warning">
            <p>For your safety, sign in again to confirm.</p>
          </Notice>
          <a href={loginUrl(DELETE_ACCOUNT_RETURN, { prompt: "login" })} className={SIGN_IN_LINK}>
            Sign in again
          </a>
        </div>
      ) : null}
      {phase.kind === "failed" ? (
        <div className="mt-3">
          <Notice tone="danger">
            <p>{phase.message}</p>
          </Notice>
        </div>
      ) : null}
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Button onClick={close} disabled={busy}>
          Cancel
        </Button>
        <Button variant="danger" onClick={() => void remove()} busy={busy} disabled={!matches || phase.kind === "reauth"}>
          Delete account
        </Button>
      </div>
    </Sheet>
  );
}
