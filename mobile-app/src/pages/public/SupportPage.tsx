/**
 * Support (spec §11A). Self-help topics for everyone; a request form for signed-in customers:
 * category preselected from the help link (`?category=…&code=…`), message, optional redacted
 * diagnostics the customer reviews before including. 201 → the reference is shown. A definite
 * rejection says "Not sent"; a failure after which the server may still have stored the request
 * (network drop, 5xx, unreadable 2xx) says receipt could not be confirmed and points to "Your
 * requests" before any retry. Both offer a copyable redacted summary; neither claims receipt.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import type { rest } from "@dome/protocol";

import { api, ApiError, loginUrl } from "../../lib/api.ts";
import { APP_VERSION, buildDiagnostics, diagnosticsText, type DiagnosticsReport } from "../../lib/diagnostics.ts";
import { absoluteTime } from "../../lib/format.ts";
import { errorMessage } from "../../lib/labels.ts";
import { CATEGORIES, submissionFailure, type SubmissionFailure } from "../../lib/support.ts";
import { Icon, type IconName } from "../../components/icons.tsx";
import { Button, Field, inputClass, Notice } from "../../components/ui.tsx";
import { useDevicesStore } from "../../store/devices.ts";
import { useInputStore } from "../../store/input.ts";
import { useLiveStore } from "../../store/live.ts";
import { useSessionStore } from "../../store/session.ts";
import { PublicIcon, type PublicIconName } from "./publicIcons.tsx";

const SUPPORT_URL: string | undefined = import.meta.env.VITE_DOME_SUPPORT_URL;

const TOPICS: Array<{ title: string; icon: IconName | { public: PublicIconName }; steps: string[] }> = [
  { title: "The PC shows Offline", icon: "pulse", steps: ["Check the PC is awake and connected to the internet.", "Click the DoMe tray icon on the PC; it should say Connected. Use Reconnect if not.", "If the tray icon is missing, start DoMe from the Start menu.", "Connection health in the app shows which part is failing."] },
  { title: "YouTube controls say the extension is not connected", icon: "youtube", steps: ["Open Chrome or Edge on the PC and check the DoMe extension is installed and enabled.", "Reload the YouTube tab so the extension attaches to it.", "Restart the browser if the extension icon still shows it is disconnected."] },
  { title: "The touchpad or keyboard says it is not permitted", icon: "touchpad", steps: ["Touchpad and keyboard are separate permissions the PC owner grants on the PC (tray → Paired phones).", "Existing pairings do not get them automatically.", "They reach every app of the unlocked Windows session, so grant them only to phones you trust."] },
  { title: "Typing paused or went to the wrong place", icon: "keyboard", steps: ["DoMe types into whatever the PC has focused; click the field with the touchpad first.", "Live typing pauses when an edit cannot be mirrored safely (middle edits, emoji deletion). Use Compose and Send for the rest.", "If the window in front changed, the PC stops the remaining keys on purpose."] },
  { title: "The pairing code is rejected", icon: "key", steps: ["Codes last 5 minutes and work once. Ask the PC for a new one (tray → Pair a phone).", "Make sure the phone is signed in to the same account the PC is linked to.", "Compare the six-digit verification code on both screens before approving on the PC."] },
  { title: "This phone was revoked", icon: "phone", steps: ["Revocation is permanent for that installation. Pair again from the PC.", "If you did not revoke it yourself, review Devices and remove anything you do not recognise."] },
  { title: "An action shows “Outcome unknown”", icon: { public: "help" }, steps: ["The PC lost its connection while running it. It may or may not have happened.", "Check the PC’s current state (it refreshes when the PC reconnects) before sending the same action again."] },
  { title: "Microphone or dictation does not work", icon: "mic", steps: ["DoMe uses your keyboard’s dictation; check that dictation is enabled in iOS Settings → General → Keyboard.", "Typing works exactly the same way."] },
];

function isCategory(v: string | null): v is rest.SupportTicketRequest["category"] {
  return CATEGORIES.some((c) => c.value === v);
}

type Submit = { kind: "idle" } | { kind: "sending" } | { kind: "received"; ticket: rest.SupportTicket } | { kind: "failed"; error: ApiError; outcome: SubmissionFailure; summary: string };

export function SupportPage() {
  const [params] = useSearchParams();
  const status = useSessionStore((s) => s.status);
  const load = useSessionStore((s) => s.load);
  const presetCategory = params.get("category");
  const presetCode = params.get("code");
  const [category, setCategory] = useState<rest.SupportTicketRequest["category"]>(isCategory(presetCategory) ? presetCategory : "other");
  const [message, setMessage] = useState("");
  const [includeDiag, setIncludeDiag] = useState(false);
  const [report, setReport] = useState<DiagnosticsReport | null>(null);
  const [submit, setSubmit] = useState<Submit>({ kind: "idle" });
  const [tickets, setTickets] = useState<rest.SupportTicket[] | null>(null);
  const [ticketsError, setTicketsError] = useState<string | null>(null);
  const [ticketsVersion, setTicketsVersion] = useState(0);
  const [copied, setCopied] = useState(false);
  const code = presetCode && /^[A-Z_]+$/.test(presetCode) && presetCode.length <= 64 ? presetCode : null;
  const signedIn = status === "signed_in";

  useEffect(() => {
    if (status === "unknown") void load();
  }, [status, load]);
  useEffect(() => {
    if (!signedIn) return;
    api
      .supportTickets()
      .then((r) => {
        setTickets(r.tickets);
        setTicketsError(null);
      })
      .catch((e: unknown) => setTicketsError(errorMessage(e instanceof ApiError ? e : null)));
  }, [signedIn, submit.kind, ticketsVersion]);

  const diagText = useMemo(() => (report ? diagnosticsText(report) : ""), [report]);

  const prepareDiagnostics = () => {
    const live = useLiveStore.getState();
    const devices = useDevicesStore.getState();
    const session = useSessionStore.getState().session;
    const input = useInputStore.getState().session;
    setReport(
      buildDiagnostics({
        relayStatus: live.relayStatus,
        controllerBound: live.controllerId !== null,
        keyAvailable: true,
        selectedPcId: devices.selectedPcId,
        pcs: devices.pcs.map((p) => {
          const l = live.pcs[p.id];
          return { id: p.id, connection: l?.connection ?? p.connection, enabled: p.enabled, lastSeen: l?.lastSeen ?? p.last_seen, stale: l?.stale ?? true, remoteEnabled: l?.state?.remote_enabled ?? null, extensionConnected: l?.state?.extension_connected ?? null, sessionLocked: l?.state?.session_locked ?? null };
        }),
        commands: live.commands,
        sessionPresent: session !== null,
        plan: session?.plan ?? null,
        input,
      }),
    );
    setIncludeDiag(true);
  };

  const send = async () => {
    const body: rest.SupportTicketRequest = { category, message: message.trim().slice(0, 2000), app_version: APP_VERSION };
    if (code) body.error_code = code;
    if (includeDiag && diagText) body.diagnostics = diagText;
    setSubmit({ kind: "sending" });
    try {
      const ticket = await api.supportTicketCreate(body);
      setSubmit({ kind: "received", ticket });
      setMessage("");
      setIncludeDiag(false);
      setReport(null);
    } catch (e) {
      const outcome = submissionFailure(e);
      const error = e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Something went wrong on this phone.", false);
      const summary = [`DoMe support request (${outcome === "not_sent" ? "not sent" : "delivery not confirmed"} — ${error.code})`, `Category: ${category}`, code ? `Error code: ${code}` : null, `App: ${APP_VERSION}`, `Message: ${body.message}`, includeDiag && diagText ? `\nRedacted diagnostics:\n${diagText}` : null].filter((l): l is string => l !== null).join("\n");
      setSubmit({ kind: "failed", error, outcome, summary });
    }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div>
      <header className="dome-page-intro animate-rise">
        <p className="dome-eyebrow">Support</p>
        <h1 className="mt-3 text-h1 font-bold">Help, with specifics.</h1>
        <p className="mt-4 max-w-2xl text-lg text-text-muted">Most problems have a specific cause that DoMe reports directly in the app. The steps below cover the common ones; the form sends a request to us when they do not help.</p>
      </header>

      <section aria-labelledby="common-problems">
        <h2 id="common-problems" className="text-title font-semibold">
          Common problems
        </h2>
        <div className="dome-card dome-hairlines mt-4 px-5 sm:px-6">
          {TOPICS.map((t) => (
            <details key={t.title} className="dome-disclosure">
              <summary>
                <span className="flex min-w-0 items-center gap-3.5">
                  <span className="dome-well dome-well-sm">{typeof t.icon === "string" ? <Icon name={t.icon} size={18} /> : <PublicIcon name={t.icon.public} size={18} />}</span>
                  <span className="text-[15px] sm:text-base">{t.title}</span>
                </span>
                <span aria-hidden="true" className="dome-disclosure-chevron">
                  <Icon name="chevron" size={16} strokeWidth={2} />
                </span>
              </summary>
              <ol className="space-y-2.5 pb-5 pl-[3.125rem] text-[15px] leading-relaxed text-text-muted">
                {t.steps.map((s, i) => (
                  <li key={s} className="flex gap-3">
                    <span aria-hidden="true" className="w-4 shrink-0 font-semibold tabular-nums text-text-faint">
                      {i + 1}
                    </span>
                    <span>{s}</span>
                  </li>
                ))}
              </ol>
            </details>
          ))}
        </div>
        <p className="mt-4 text-[15px] text-text-muted">
          See also the{" "}
          <Link to="/release-notes" className="font-semibold text-accent underline decoration-accent/35 underline-offset-[3px] hover:decoration-current">
            release history and known issues
          </Link>
          .
        </p>
      </section>

      <section className="dome-card mt-12 space-y-4 p-6 sm:p-7" aria-labelledby="support-form-title">
        <h2 id="support-form-title" className="text-title font-semibold">
          Send a support request
        </h2>
        {!signedIn ? (
          <div className="space-y-3 text-[15px] text-text-muted">
            <p>Sign in so the request is tied to your account; we never need your password or any pairing code.</p>
            {status === "loading" || status === "unknown" ? <p>Checking your sign-in…</p> : null}
            <a href={loginUrl(`/support${params.toString() ? `?${params.toString()}` : ""}`)} className="tap dome-primary dome-press inline-flex items-center rounded-full px-5 text-[15px] font-semibold">
              Sign in to continue
            </a>
          </div>
        ) : submit.kind === "received" ? (
          <Notice tone="success" title={`Received — reference ${submit.ticket.reference}`}>
            <p>Keep this reference. Status: {submit.ticket.status.replace("_", " ")}.</p>
            {submit.ticket.response_expectation ? <p>{submit.ticket.response_expectation}</p> : <p>A response time has not been published for this deployment yet.</p>}
            <Button size="md" variant="ghost" onClick={() => setSubmit({ kind: "idle" })}>
              Send another request
            </Button>
          </Notice>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (message.trim()) void send();
            }}
          >
            <Field id="support-category" label="What is it about?">
              <select id="support-category" className={inputClass} value={category} onChange={(e) => setCategory(e.currentTarget.value as rest.SupportTicketRequest["category"])}>
                {CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </Field>
            {code ? <p className="text-xs text-text-muted">Error you were looking at: <span className="font-mono">{code}</span> (included with the request).</p> : null}
            <Field id="support-message" label="What happened?" hint="What you did, what you expected, what you saw. Up to 2000 characters. Do not include passwords or pairing codes.">
              <textarea id="support-message" className={`${inputClass} min-h-[120px]`} value={message} maxLength={2000} onChange={(e) => setMessage(e.currentTarget.value)} required />
            </Field>
            <div className="space-y-2">
              {!report ? (
                <Button size="md" type="button" onClick={prepareDiagnostics}>
                  Attach redacted diagnostics…
                </Button>
              ) : (
                <>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" className="h-5 w-5" checked={includeDiag} onChange={(e) => setIncludeDiag(e.currentTarget.checked)} />
                    Include the diagnostics below ({diagText.length.toLocaleString()} characters)
                  </label>
                  <details className="text-xs">
                    <summary className="cursor-pointer text-text-muted">Review exactly what would be sent</summary>
                    <pre className="mt-2 max-h-64 overflow-auto rounded-control bg-bg-sunken border border-border p-2 whitespace-pre-wrap break-all" data-testid="diagnostics-preview">
                      {diagText}
                    </pre>
                  </details>
                  <p className="text-xs text-text-faint">Versions, connection states, error codes and timing only. No pairing material, credentials, typed text, media titles or URLs. Nothing is sent until you press Send.</p>
                </>
              )}
            </div>
            {submit.kind === "failed" && submit.outcome === "not_sent" ? (
              <Notice tone="danger" title="Not sent">
                <p>{errorMessage(submit.error)} Support has not received this request.</p>
                <p>Copy the redacted summary below and send it another way{SUPPORT_URL ? "" : " once a support contact is configured"}.</p>
                <textarea readOnly className={`${inputClass} font-mono text-xs min-h-[120px]`} value={submit.summary} aria-label="Redacted summary of the unsent request" data-testid="unsent-summary" />
                <div className="flex gap-2">
                  <Button size="md" type="button" onClick={() => void copy(submit.summary)}>
                    {copied ? "Copied" : "Copy summary"}
                  </Button>
                  <Button size="md" type="button" variant="ghost" onClick={() => setSubmit({ kind: "idle" })}>
                    Try again
                  </Button>
                </div>
              </Notice>
            ) : null}
            {submit.kind === "failed" && submit.outcome === "unconfirmed" ? (
              <Notice tone="warning" title="Not confirmed">
                <p>{errorMessage(submit.error)} DoMe could not confirm whether support received this request: it may have arrived before the problem.</p>
                <p>Refresh “Your requests” below first. If the request is listed there, it was received; send it again only if it is not, so it does not arrive twice.</p>
                <textarea readOnly className={`${inputClass} font-mono text-xs min-h-[120px]`} value={submit.summary} aria-label="Redacted summary of the unconfirmed request" data-testid="unsent-summary" />
                <div className="flex flex-wrap gap-2">
                  <Button size="md" type="button" variant="primary" onClick={() => setTicketsVersion((n) => n + 1)}>
                    Refresh your requests
                  </Button>
                  <Button size="md" type="button" onClick={() => void copy(submit.summary)}>
                    {copied ? "Copied" : "Copy summary"}
                  </Button>
                  <Button size="md" type="button" variant="ghost" onClick={() => setSubmit({ kind: "idle" })}>
                    Back to the form
                  </Button>
                </div>
              </Notice>
            ) : null}
            <Button type="submit" variant="primary" busy={submit.kind === "sending"} disabled={!message.trim() || submit.kind === "sending"}>
              Send request
            </Button>
          </form>
        )}
        {SUPPORT_URL ? (
          <p className="text-sm text-text-muted">
            You can also{" "}
            <a href={SUPPORT_URL} className="text-accent font-semibold" rel="noopener noreferrer" target="_blank">
              contact support directly
            </a>
            .
          </p>
        ) : null}
      </section>

      {signedIn ? (
        <section className="mt-12 space-y-3" aria-labelledby="your-requests">
          <h2 id="your-requests" className="text-title font-semibold">
            Your requests
          </h2>
          {ticketsError ? <p className="text-sm text-text-muted">Could not load your requests: {ticketsError}</p> : null}
          {tickets && tickets.length === 0 ? <p className="text-sm text-text-muted">No requests yet.</p> : null}
          {tickets && tickets.length > 0 ? (
            <ul className="space-y-2">
              {tickets.map((t) => (
                <li key={t.ticket_id} className="dome-card p-4 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono font-semibold">{t.reference}</span>
                    <span className="text-xs text-text-muted">{t.status.replace("_", " ")}</span>
                  </div>
                  <p className="text-xs text-text-muted mt-1">
                    {CATEGORIES.find((c) => c.value === t.category)?.label ?? t.category} · {absoluteTime(t.created_at)}
                  </p>
                  {t.answer ? <p className="mt-2 whitespace-pre-wrap">{t.answer}</p> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
