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
import { Wordmark } from "../../components/brand.tsx";
import { Button, Card, CheckRow, Field, IconWell, inputClass, KeyValue, Notice, Spinner } from "../../components/ui.tsx";

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
    <main className="relative isolate min-h-dvh text-text">
      <div className="dome-backdrop" aria-hidden="true" />
      <div className="mx-auto max-w-lg space-y-4 px-5 pt-[calc(var(--safe-top)+1.25rem)] pb-[calc(2rem+var(--safe-bottom))]">
        <Wordmark compact title="DoMe" className="h-[22px] w-auto" />
        <header className="pt-2 pb-1">
          <p className="dome-eyebrow mb-1.5">Link a PC</p>
          <h1 className="text-[2rem] leading-[1.1] font-bold tracking-[-0.03em]">Link a PC to your account</h1>
        </header>
        {done === "denied" ? (
          <Notice tone="info" title="Request denied">
            <p>The PC was not linked. You can close this page.</p>
          </Notice>
        ) : done ? (
          <Card>
            <div className="flex items-center gap-3.5">
              <IconWell icon="checkCircle" tone="success" size={44} radius={14} />
              <h2 className="text-lg font-semibold">PC linked</h2>
            </div>
            <p className="mt-3 text-sm text-text-muted">{done.enabled ? "Remote control is enabled. The PC will connect to DoMe in a moment." : "The PC is linked but not enabled: your plan’s limit of enabled PCs is reached. Choose which PC stays enabled under Devices."}</p>
            <p className="mt-2 text-sm text-text-muted">Next: on the PC choose “Pair a phone” and scan the code with this phone under Devices.</p>
            <Link to="/app/devices/pair" className="tap dome-primary dome-press mt-4 inline-flex min-h-[52px] w-full items-center justify-center rounded-control px-5 font-semibold">
              Pair this phone
            </Link>
          </Card>
        ) : (
          <>
            <Card>
              <Field id="user-code" label="Code shown on the PC" hint="Two groups of four characters, for example ABCD-EFGH.">
                <input id="user-code" className={`${inputClass} font-mono uppercase tracking-widest`} value={codeInput} onChange={(e) => setCodeInput(e.currentTarget.value)} autoCapitalize="characters" autoComplete="one-time-code" spellCheck={false} />
              </Field>
            </Card>
            {loading ? (
              <p className="flex items-center gap-2 px-1 text-sm text-text-muted">
                <Spinner label="Looking up the code" small /> Looking up the code…
              </p>
            ) : null}
            {error ?? lookupError ? (
              <Notice tone="danger">
                <p>{errorMessage(error ?? lookupError)}</p>
              </Notice>
            ) : null}
            {preview ? (
              <Card>
                <div className="flex items-center gap-3.5">
                  <IconWell icon="pc" size={44} radius={14} />
                  <h2 className="font-semibold leading-snug">A Windows PC is asking to join your account</h2>
                </div>
                <KeyValue
                  className="mt-3"
                  rows={[
                    { label: "DoMe agent", value: `version ${preview.agent_version}` },
                    { label: "Platform", value: preview.platform === "windows" ? "Windows" : "Development build" },
                    { label: "Code expires", value: `in ${secondsUntil(preview.expires_at)} s` },
                  ]}
                />
                <div className="mt-4 space-y-3">
                  <Field id="pc-name" label="Name this PC">
                    <input id="pc-name" className={inputClass} value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={64} placeholder="e.g. Living room PC" />
                  </Field>
                  <CheckRow label="Enable remote control for this PC." hint="Required to link; you can turn it off any time on the PC or under Devices." checked={enable} onChange={setEnable} />
                </div>
                <div className="mt-4">
                  <Notice tone="info">
                    <p>Only approve if you started this on your own PC just now. Linking does not pair any phone yet — that happens separately on the PC with a verification code.</p>
                  </Notice>
                </div>
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
