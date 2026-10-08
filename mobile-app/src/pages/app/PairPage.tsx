/**
 * Pairing (ADR-0001 D5). The 20-symbol code is a secret the DoMe service never sees: it only leaves
 * this page as its SHA-256 handle and as the HMAC key for the 6-digit verification code. The code
 * lives in component state only — never in the URL (the deep-link fragment is scrubbed on arrival),
 * storage or logs.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { ProtocolError, type Capability, type rest } from "@dome/protocol";

import { getRuntime } from "../../app/runtime.ts";
import { Button, Card, Field, inputClass, Notice, Pill, Spinner, Steps } from "../../components/ui.tsx";
import { api, ApiError } from "../../lib/api.ts";
import { getControllerIdentity, KeyStorageError, setStoredControllerId } from "../../lib/controllerKey.ts";
import { secondsUntil } from "../../lib/format.ts";
import { capabilityLabel, errorMessage, recoverySteps } from "../../lib/labels.ts";
import { errorSummary, log } from "../../lib/log.ts";
import { ALL_CAPABILITIES, defaultControllerName, pairingCodeHandle, pairingVerificationCode, parsePairingInput, takeCodeFromLocation } from "../../lib/pairing.ts";
import { CameraError, detectQrSupport, startQrScanner, type QrScanner } from "../../lib/qr.ts";
import { useDevicesStore } from "../../store/devices.ts";

type Step = { kind: "enter"; mode: "scan" | "type" } | { kind: "details"; code: string } | { kind: "claiming"; code: string } | { kind: "waiting"; status: rest.PairingStatusResponse; verification: string } | { kind: "done"; status: rest.PairingStatusResponse } | { kind: "failed"; code: string; message: string };

const POLL_MS = 2000;

export function PairPage() {
  const navigate = useNavigate();
  const refreshDevices = useDevicesStore((s) => s.refresh);
  const [step, setStep] = useState<Step>(() => {
    const code = takeCodeFromLocation(window.location, window.history, "/app/devices/pair");
    return code ? { kind: "details", code } : { kind: "enter", mode: detectQrSupport() === "unsupported" ? "type" : "scan" };
  });
  const [manual, setManual] = useState("");
  const [manualError, setManualError] = useState<string | null>(null);
  const [name, setName] = useState(() => defaultControllerName(navigator.userAgent));
  const [caps, setCaps] = useState<Set<Capability>>(() => new Set(ALL_CAPABILITIES));
  const [cameraError, setCameraError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const scannerRef = useRef<QrScanner | null>(null);

  const acceptCode = useCallback((text: string) => {
    try {
      const code = parsePairingInput(text);
      setManualError(null);
      setStep({ kind: "details", code });
    } catch {
      setManualError("That is not a DoMe pairing code. It has 20 letters and digits in four groups, like ABCDE-FGHJK-MNPQR-STVWX.");
    }
  }, []);

  // Camera scanner lifecycle
  useEffect(() => {
    if (step.kind !== "enter" || step.mode !== "scan") return;
    const video = videoRef.current;
    if (!video) return;
    let cancelled = false;
    setCameraError(null);
    startQrScanner(
      video,
      (text) => {
        if (cancelled) return;
        try {
          const code = parsePairingInput(text);
          scannerRef.current?.stop();
          setStep({ kind: "details", code });
        } catch {
          /* not a DoMe code; keep scanning */
        }
      },
      () => undefined,
    )
      .then((s) => {
        if (cancelled) s.stop();
        else scannerRef.current = s;
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setCameraError(e instanceof CameraError ? e.message : "The camera could not be started. Type the code instead.");
        setStep({ kind: "enter", mode: "type" });
      });
    return () => {
      cancelled = true;
      scannerRef.current?.stop();
      scannerRef.current = null;
    };
  }, [step]);

  const claim = async (code: string) => {
    setStep({ kind: "claiming", code });
    try {
      const identity = await getControllerIdentity();
      const status = await api.pairingClaim({ code_hash: await pairingCodeHandle(code), public_jwk: identity.jwk, display_name: name.trim() || "This phone", requested_capabilities: [...caps] as unknown as rest.PairingClaimRequest["requested_capabilities"] });
      const verification = await pairingVerificationCode(code, status.pairing_id, status.pc_id, identity.kid);
      // The code has done its job; it is not kept anywhere from here on.
      setStep({ kind: "waiting", status, verification });
      log.info("pairing.claimed", { pairing_id: status.pairing_id, pc_id: status.pc_id });
    } catch (e) {
      log.warn("pairing.claim_failed", errorSummary(e));
      if (e instanceof KeyStorageError) setStep({ kind: "failed", code: "KEY_STORAGE", message: e.message });
      else if (e instanceof ApiError || e instanceof ProtocolError) setStep({ kind: "failed", code: e.code, message: errorMessage(e) });
      else setStep({ kind: "failed", code: "INTERNAL", message: "Pairing could not be started." });
    }
  };

  // Poll while waiting for approval on the PC
  useEffect(() => {
    if (step.kind !== "waiting") return;
    const { pairing_id } = step.status;
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const status = await api.pairingStatus(pairing_id, ctrl.signal);
        if (ctrl.signal.aborted) return;
        if (status.state === "claimed") {
          setStep((s) => (s.kind === "waiting" ? { ...s, status } : s));
          timer = setTimeout(() => void poll(), POLL_MS);
          return;
        }
        if (status.state === "approved") {
          if (status.controller_id) await setStoredControllerId(status.controller_id).catch(() => undefined);
          setStep({ kind: "done", status });
          getRuntime().relay.reconnect();
          void refreshDevices();
          return;
        }
        setStep({ kind: "failed", code: status.state === "declined" ? "PAIRING_DECLINED" : "PAIRING_CODE_INVALID", message: status.state === "declined" ? errorMessage({ code: "PAIRING_DECLINED" }) : "The pairing request expired before it was approved on the PC." });
      } catch (e) {
        if (ctrl.signal.aborted) return;
        if (e instanceof ApiError && e.status === 404) {
          setStep({ kind: "failed", code: "PAIRING_CODE_INVALID", message: errorMessage(e) });
          return;
        }
        timer = setTimeout(() => void poll(), POLL_MS * 2); // transient: keep polling
      }
    };
    timer = setTimeout(() => void poll(), POLL_MS);
    return () => {
      ctrl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [step, refreshDevices]);

  const reset = () => setStep({ kind: "enter", mode: detectQrSupport() === "unsupported" ? "type" : "scan" });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Link to="/app/devices" className="tap inline-flex items-center text-accent font-semibold text-sm px-1" aria-label="Back to devices">
          ‹ Devices
        </Link>
      </div>
      <h1 className="text-2xl font-bold tracking-tight">Pair this phone with a PC</h1>

      {step.kind === "enter" ? (
        <>
          <Notice tone="info">
            <p>
              On the PC, open the DoMe tray menu and choose <strong>Pair a phone</strong>. It shows a QR code and a 20-character code that work for 5 minutes.
            </p>
          </Notice>
          <div role="tablist" aria-label="How to enter the code" className="grid grid-cols-2 gap-1 rounded-control bg-bg-sunken p-1">
            <button role="tab" type="button" aria-selected={step.mode === "scan"} disabled={detectQrSupport() === "unsupported"} onClick={() => setStep({ kind: "enter", mode: "scan" })} className={`rounded-[0.7rem] py-2.5 text-sm font-semibold disabled:opacity-40 ${step.mode === "scan" ? "bg-bg-elevated shadow" : "text-text-muted"}`}>
              Scan QR code
            </button>
            <button role="tab" type="button" aria-selected={step.mode === "type"} onClick={() => setStep({ kind: "enter", mode: "type" })} className={`rounded-[0.7rem] py-2.5 text-sm font-semibold ${step.mode === "type" ? "bg-bg-elevated shadow" : "text-text-muted"}`}>
              Type the code
            </button>
          </div>
          {step.mode === "scan" ? (
            <Card className="p-2">
              <div className="relative aspect-square overflow-hidden rounded-[0.8rem] bg-black">
                <video ref={videoRef} className="h-full w-full object-cover" playsInline muted aria-label="Camera preview for scanning the pairing QR code" />
                <div aria-hidden="true" className="pointer-events-none absolute inset-[12%] rounded-xl border-2 border-accent/80" />
              </div>
              <p className="text-xs text-text-muted mt-2 px-1">Point the camera at the QR code on the PC’s screen. The camera is only used on this page.</p>
            </Card>
          ) : (
            <Card>
              {cameraError ? (
                <div className="mb-3">
                  <Notice tone="warning">{cameraError}</Notice>
                </div>
              ) : null}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  acceptCode(manual);
                }}
                className="space-y-3"
              >
                <Field id="pair-code" label="Pairing code from the PC" hint="Four groups of five. Dashes and letter case do not matter; O and 0, I/L and 1 are treated the same.">
                  <input id="pair-code" className={`${inputClass} font-mono tracking-[0.15em] uppercase`} value={manual} onChange={(e) => setManual(e.currentTarget.value)} placeholder="ABCDE-FGHJK-MNPQR-STVWX" autoCapitalize="characters" autoComplete="off" autoCorrect="off" spellCheck={false} inputMode="text" maxLength={32} aria-invalid={manualError ? true : undefined} aria-describedby={manualError ? "pair-code-error" : "pair-code-hint"} />
                </Field>
                {manualError ? (
                  <p id="pair-code-error" className="text-sm text-danger" role="alert">
                    {manualError}
                  </p>
                ) : null}
                <Button type="submit" variant="primary" size="lg" full disabled={manual.trim().length < 20}>
                  Continue
                </Button>
              </form>
            </Card>
          )}
        </>
      ) : null}

      {step.kind === "details" ? (
        <Card>
          <h2 className="font-semibold">Almost there</h2>
          <p className="text-sm text-text-muted mt-1">Code accepted. Name this phone and choose what it may do on the PC. You approve the pairing on the PC itself.</p>
          <div className="mt-4 space-y-4">
            <Field id="phone-name" label="Name for this phone" hint="Shown on the PC and in your device list.">
              <input id="phone-name" className={inputClass} value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={64} />
            </Field>
            <fieldset>
              <legend className="text-sm font-medium mb-2">Permissions to request</legend>
              <ul className="space-y-2">
                {ALL_CAPABILITIES.map((cap) => (
                  <li key={cap}>
                    <label className="flex items-center gap-3 text-sm">
                      <input
                        type="checkbox"
                        className="h-5 w-5"
                        checked={caps.has(cap)}
                        disabled={cap === "status"}
                        onChange={(e) =>
                          setCaps((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(cap);
                            else next.delete(cap);
                            next.add("status");
                            return next;
                          })
                        }
                      />
                      {capabilityLabel(cap)}
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <Button size="lg" onClick={reset}>
              Back
            </Button>
            <Button size="lg" variant="primary" disabled={name.trim() === ""} onClick={() => void claim(step.code)}>
              Request pairing
            </Button>
          </div>
        </Card>
      ) : null}

      {step.kind === "claiming" ? (
        <Card className="text-center py-8">
          <Spinner label="Sending the pairing request" />
          <p className="text-sm text-text-muted mt-3">Sending the request to DoMe…</p>
        </Card>
      ) : null}

      {step.kind === "waiting" ? (
        <Card className="text-center">
          <p className="text-xs uppercase tracking-wider text-text-faint">Verification code</p>
          <p className="font-mono text-5xl font-bold tracking-[0.2em] my-3" aria-label={`Verification code ${step.verification.split("").join(" ")}`} data-testid="verification-code">
            {step.verification}
          </p>
          <p className="text-sm">
            <strong>{step.status.pc_name}</strong> shows a code too. Approve on the PC only if it matches this one.
          </p>
          <div className="mt-3 flex items-center justify-center gap-2">
            <Pill tone={step.status.pc_online === false ? "warning" : "info"} pulse>
              {step.status.pc_online === false ? "PC is offline — it will ask when it reconnects" : "Waiting for approval on the PC"}
            </Pill>
          </div>
          <p className="text-xs text-text-faint mt-3">Expires in {secondsUntil(step.status.expires_at)} s. Nothing is paired until you approve on the PC.</p>
          <Button className="mt-4" variant="ghost" onClick={reset}>
            Cancel
          </Button>
        </Card>
      ) : null}

      {step.kind === "done" ? (
        <Card>
          <Pill tone="success">Paired</Pill>
          <h2 className="text-lg font-semibold mt-2">This phone can now control {step.status.pc_name}</h2>
          {step.status.granted_capabilities ? <p className="text-sm text-text-muted mt-1">Allowed: {step.status.granted_capabilities.map(capabilityLabel).join(", ")}.</p> : null}
          <p className="text-sm text-text-muted mt-2">Try Play/Pause from the remote. If YouTube control is what you want, make sure the DoMe browser extension is installed in Chrome or Edge on the PC.</p>
          <Button className="mt-4" variant="primary" size="lg" full onClick={() => void navigate("/app")}>
            Go to Home
          </Button>
        </Card>
      ) : null}

      {step.kind === "failed" ? (
        <Card>
          <Notice tone="danger" title="Pairing did not complete">
            <p>{step.message}</p>
            <Steps steps={recoverySteps(step.code)} />
          </Notice>
          <Button className="mt-4" full onClick={reset}>
            Start again
          </Button>
        </Card>
      ) : null}
    </div>
  );
}
