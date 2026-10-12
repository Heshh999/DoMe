/**
 * Pairing (ADR-0001 D5). The 20-symbol code is a secret the DoMe service never sees: it only leaves
 * this page as its SHA-256 handle and as the HMAC key for the 6-digit verification code. The code
 * lives in component state only — never in the URL (the deep-link fragment is scrubbed on arrival),
 * storage or logs.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { ProtocolError, type Capability, type rest } from "@dome/protocol";

import { getRuntime } from "../../app/runtime.ts";
import { Icon } from "../../components/icons.tsx";
import { Button, Card, CheckRow, Field, IconWell, inputClass, Notice, PageHeader, Pill, Segmented, Spinner, Steps } from "../../components/ui.tsx";
import { api, ApiError } from "../../lib/api.ts";
import { getControllerIdentity, KeyStorageError, setStoredControllerId } from "../../lib/controllerKey.ts";
import { secondsUntil } from "../../lib/format.ts";
import { capabilityLabel, INPUT_SCOPE_EXPLANATION, errorMessage, recoverySteps } from "../../lib/labels.ts";
import { errorSummary, log } from "../../lib/log.ts";
import { ALL_CAPABILITIES, defaultControllerName, pairingCodeHandle, pairingVerificationCode, parsePairingInput, takeCodeFromLocation, takeScanAgainHint } from "../../lib/pairing.ts";
import { CameraError, detectQrSupport, startQrScanner, type QrScanner } from "../../lib/qr.ts";
import { useDevicesStore } from "../../store/devices.ts";

type Step = { kind: "enter"; mode: "scan" | "type" } | { kind: "details"; code: string } | { kind: "claiming"; code: string } | { kind: "waiting"; status: rest.PairingStatusResponse; verification: string } | { kind: "done"; status: rest.PairingStatusResponse } | { kind: "failed"; code: string; message: string };

const POLL_MS = 2000;

/** The four corner marks of the scanning frame (drawn, no glyph). */
function ScanFrame() {
  const corner = "absolute h-7 w-7 border-accent";
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-[13%]">
      <span className={`${corner} left-0 top-0 rounded-tl-xl border-l-[3px] border-t-[3px]`} />
      <span className={`${corner} right-0 top-0 rounded-tr-xl border-r-[3px] border-t-[3px]`} />
      <span className={`${corner} bottom-0 left-0 rounded-bl-xl border-b-[3px] border-l-[3px]`} />
      <span className={`${corner} bottom-0 right-0 rounded-br-xl border-b-[3px] border-r-[3px]`} />
    </div>
  );
}

export function PairPage() {
  const navigate = useNavigate();
  const refreshDevices = useDevicesStore((s) => s.refresh);
  const selectPc = useDevicesStore((s) => s.select);
  const [scanAgain] = useState(() => takeScanAgainHint(window.location, window.history, "/app/devices/pair"));
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
          // the PC this phone just paired with is the one the user means to control next, even when
          // another PC was selected before (a second PC, or one linked earlier and still listed)
          void selectPc(status.pc_id).then(() => refreshDevices());
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
  }, [step, refreshDevices, selectPc]);

  const reset = () => setStep({ kind: "enter", mode: detectQrSupport() === "unsupported" ? "type" : "scan" });

  return (
    <div className="space-y-4">
      <PageHeader title="Pair this phone with a PC" back={{ to: "/app/devices", label: "Devices", ariaLabel: "Back to devices" }} />

      {step.kind === "enter" ? (
        <>
          {scanAgain ? (
            <Notice tone="warning" title="Scan the code again">
              <p>You are signed in now. For your security the code from the QR link was not kept while you signed in — point the camera at the PC’s QR code again, or type the code. If it has expired, choose Pair a phone on the PC once more.</p>
            </Notice>
          ) : null}
          <Notice tone="info">
            <p>
              On the PC, open the DoMe tray menu and choose <strong>Pair a phone</strong>. It shows a QR code and a 20-character code that work for 5 minutes.
            </p>
          </Notice>
          <Segmented<"scan" | "type">
            label="How to enter the code"
            value={step.mode}
            onChange={(mode) => setStep({ kind: "enter", mode })}
            options={[
              { value: "scan", label: "Scan QR code", icon: "qr", disabled: detectQrSupport() === "unsupported" },
              { value: "type", label: "Type the code", icon: "keyboard" },
            ]}
          />
          {step.mode === "scan" ? (
            <Card className="p-2.5">
              <div className="relative aspect-square overflow-hidden rounded-[1rem] bg-navy">
                <video ref={videoRef} className="h-full w-full object-cover" playsInline muted aria-label="Camera preview for scanning the pairing QR code" />
                <ScanFrame />
              </div>
              <p className="flex items-start gap-2 px-1.5 pb-1 pt-3 text-xs text-text-muted">
                <Icon name="camera" size={16} className="mt-px shrink-0 text-text-faint" />
                Point the camera at the QR code on the PC’s screen. The camera is only used on this page.
              </p>
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
          <div className="flex items-center gap-3.5">
            <IconWell icon="check" tone="success" size={44} radius={14} />
            <div className="min-w-0">
              <h2 className="text-[17px] font-semibold leading-snug">Almost there</h2>
              <p className="text-xs text-text-muted">Code accepted.</p>
            </div>
          </div>
          <p className="mt-3 text-sm text-text-muted">Name this phone and choose what it may do on the PC. You approve the pairing on the PC itself.</p>
          <div className="mt-4 space-y-4">
            <Field id="phone-name" label="Name for this phone" hint="Shown on the PC and in your device list.">
              <input id="phone-name" className={inputClass} value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={64} />
            </Field>
            <fieldset>
              <legend className="mb-2 text-sm font-semibold">Permissions to request</legend>
              <ul className="space-y-2">
                {ALL_CAPABILITIES.map((cap) => (
                  <li key={cap}>
                    <CheckRow
                      label={capabilityLabel(cap)}
                      checked={caps.has(cap)}
                      disabled={cap === "status"}
                      onChange={(checked) =>
                        setCaps((prev) => {
                          const next = new Set(prev);
                          if (checked) next.add(cap);
                          else next.delete(cap);
                          next.add("status");
                          return next;
                        })
                      }
                    />
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-text-muted">Touchpad and keyboard are requested by default; the PC owner decides on the PC what this phone actually gets. {INPUT_SCOPE_EXPLANATION}</p>
            </fieldset>
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3">
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
        <Card className="py-9 text-center">
          <Spinner label="Sending the pairing request" />
          <p className="mt-3 text-sm text-text-muted">Sending the request to DoMe…</p>
        </Card>
      ) : null}

      {step.kind === "waiting" ? (
        <Card className="text-center">
          <p className="dome-eyebrow">Verification code</p>
          <p className="my-4 font-mono text-[2.75rem] font-bold leading-none tracking-[0.22em] tabular-nums" aria-label={`Verification code ${step.verification.split("").join(" ")}`} data-testid="verification-code">
            {step.verification}
          </p>
          <p className="text-sm">
            <strong>{step.status.pc_name}</strong> shows a code too. Approve on the PC only if it matches this one.
          </p>
          <div className="mt-4 flex items-center justify-center gap-2">
            <Pill tone={step.status.pc_online === false ? "warning" : "info"} pulse>
              {step.status.pc_online === false ? "PC is offline — it will ask when it reconnects" : "Waiting for approval on the PC"}
            </Pill>
          </div>
          <p className="mt-3 text-xs text-text-faint">Expires in {secondsUntil(step.status.expires_at)} s. Nothing is paired until you approve on the PC.</p>
          <Button className="mt-4" variant="quiet" onClick={reset}>
            Cancel
          </Button>
        </Card>
      ) : null}

      {step.kind === "done" ? (
        <Card>
          <div className="flex items-center gap-3.5">
            <IconWell icon="checkCircle" tone="success" size={44} radius={14} />
            <Pill tone="success">Paired</Pill>
          </div>
          <h2 className="mt-3 text-lg font-semibold">This phone can now control {step.status.pc_name}</h2>
          {step.status.granted_capabilities ? <p className="mt-1 text-sm text-text-muted">Allowed: {step.status.granted_capabilities.map(capabilityLabel).join(", ")}.</p> : null}
          <p className="mt-2 text-sm text-text-muted">Try Play/Pause from the remote. If YouTube control is what you want, make sure the DoMe browser extension is installed in Chrome or Edge on the PC.</p>
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
          {step.code === "DEVICE_LIMIT_REACHED" ? (
            <div className="mt-4 grid grid-cols-2 gap-3">
              <Button size="lg" variant="primary" onClick={() => void navigate("/app/devices")}>
                Manage devices
              </Button>
              <Button size="lg" onClick={reset}>
                Start again
              </Button>
            </div>
          ) : (
            <Button className="mt-4" full onClick={reset}>
              Start again
            </Button>
          )}
        </Card>
      ) : null}
    </div>
  );
}
