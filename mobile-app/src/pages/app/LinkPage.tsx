/**
 * `/link?user_code=…` — approve a PC's linking request (ADR-0001 D4). The signed-in customer sees
 * what is asking, names the PC and explicitly enables remote control. Denying is always available.
 */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import type { rest } from "@dome/protocol";

import { api, ApiError } from "../../lib/api.ts";
import { secondsUntil } from "../../lib/format.ts";
import { errorMessage } from "../../lib/labels.ts";
import { Button, Card, Field, inputClass, Notice, Spinner } from "../../components/ui.tsx";

const USER_CODE = /^[0-9A-HJKMNP-TV-Z]{4}-?[0-9A-HJKMNP-TV-Z]{4}$/i;

function normaliseUserCode(raw: string): string | null {
  const t = raw.trim().toUpperCase().replace(/[\s-]+/g, "");
  if (!USER_CODE.test(t)) return null;
  return `${t.slice(0, 4)}-${t.slice(4, 8)}`;
}

export function LinkPage() {
  const [params] = useSearchParams();
  const initial = params.get("user_code") ?? "";
  const [codeInput, setCodeInput] = useState(initial);
  const code = normaliseUserCode(codeInput);
  const [lookup, setLookup] = useState<{ code: string; data: rest.AgentLinkPreviewResponse | null; error: ApiError | null } | null>(null);
  const preview = lookup && lookup.code === code ? lookup.data : null;
  const lookupError = lookup && lookup.code === code ? lookup.error : null;
  const loading = !!code && lookup?.code !== code;
  const [error, setError] = useState<ApiError | null>(null);
  const [name, setName] = useState("");
  const [enable, setEnable] = useState(true);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<rest.AgentLinkApproveResponse | "denied" | null>(null);

  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    api
      .linkPreview(code)
      .then((p) => {
        if (cancelled) return;
        setLookup({ code, data: p, error: null });
        setName((n) => n || p.pc_name_hint || "");
      })
      .catch((e: unknown) => {
        if (!cancelled) setLookup({ code, data: null, error: e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Could not look up that code.", true) });
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  const approve = async () => {
    if (!code || !preview) return;
    setBusy(true);
    setError(null);
    try {
      setDone(await api.linkApprove(code, { pc_name: name.trim() || preview.pc_name_hint || "My PC", remote_enabled: true }));
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Could not approve.", true));
    } finally {
      setBusy(false);
    }
  };
  const deny = async () => {
    if (!code) return;
    setBusy(true);
    try {
      await api.linkDeny(code);
      setDone("denied");
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Could not deny.", true));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-dvh bg-bg text-text">
      <div className="max-w-lg mx-auto px-4 pt-[calc(var(--safe-top)+1.5rem)] pb-[calc(2rem+var(--safe-bottom))] space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">Link a PC to your account</h1>
        {done === "denied" ? (
          <Notice tone="info" title="Request denied">
            <p>The PC was not linked. You can close this page.</p>
          </Notice>
        ) : done ? (
          <Card>
            <h2 className="text-lg font-semibold">PC linked</h2>
            <p className="text-sm text-text-muted mt-1">{done.enabled ? "Remote control is enabled. The PC will connect to DoMe in a moment." : "The PC is linked but not enabled: your plan’s limit of enabled PCs is reached. Choose which PC stays enabled under Devices."}</p>
            <p className="text-sm text-text-muted mt-2">Next: on the PC choose “Pair a phone” and scan the code with this phone under Devices.</p>
            <Link to="/app/devices/pair" className="tap mt-4 inline-flex items-center justify-center rounded-control bg-accent text-on-accent font-semibold px-5 w-full">
              Pair this phone
            </Link>
          </Card>
        ) : (
          <>
            <Card>
              <Field id="user-code" label="Code shown on the PC" hint="Two groups of four characters, for example ABCD-EFGH.">
                <input id="user-code" className={`${inputClass} font-mono tracking-widest uppercase`} value={codeInput} onChange={(e) => setCodeInput(e.currentTarget.value)} autoCapitalize="characters" autoComplete="one-time-code" spellCheck={false} />
              </Field>
            </Card>
            {loading ? <Spinner label="Looking up the code" /> : null}
            {error ?? lookupError ? (
              <Notice tone="danger">
                <p>{errorMessage(error ?? lookupError)}</p>
              </Notice>
            ) : null}
            {preview ? (
              <Card>
                <h2 className="font-semibold">A Windows PC is asking to join your account</h2>
                <dl className="mt-2 text-sm grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                  <dt className="text-text-muted">DoMe agent</dt>
                  <dd>version {preview.agent_version}</dd>
                  <dt className="text-text-muted">Platform</dt>
                  <dd>{preview.platform === "windows" ? "Windows" : "Development build"}</dd>
                  <dt className="text-text-muted">Code expires</dt>
                  <dd>in {secondsUntil(preview.expires_at)} s</dd>
                </dl>
                <div className="mt-4 space-y-3">
                  <Field id="pc-name" label="Name this PC">
                    <input id="pc-name" className={inputClass} value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={64} placeholder="e.g. Living room PC" />
                  </Field>
                  <label className="flex items-start gap-3 text-sm">
                    <input type="checkbox" className="mt-1 h-5 w-5" checked={enable} onChange={(e) => setEnable(e.currentTarget.checked)} />
                    <span>
                      Enable remote control for this PC. <span className="text-text-muted">Required to link; you can turn it off any time on the PC or under Devices.</span>
                    </span>
                  </label>
                </div>
                <Notice tone="info">
                  <p>Only approve if you started this on your own PC just now. Linking does not pair any phone yet — that happens separately on the PC with a verification code.</p>
                </Notice>
                <div className="mt-4 grid grid-cols-2 gap-3">
                  <Button size="lg" onClick={() => void deny()} disabled={busy}>
                    Deny
                  </Button>
                  <Button size="lg" variant="primary" onClick={() => void approve()} disabled={busy || !enable || name.trim() === ""} busy={busy}>
                    Link this PC
                  </Button>
                </div>
              </Card>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}
