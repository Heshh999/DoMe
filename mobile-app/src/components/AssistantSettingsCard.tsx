/**
 * Settings → Assistant (docs/design/assistant.md §4.11): whether the AI assistant is available on this account and
 * why not, consent (given and withdrawn on the server, for every phone of the account), the allowance (on Free, the
 * one-time taste, with no renewal), and this phone's preferences — the Ask tab, using AI here, the butler's name and
 * form of address, asking before every action, reading replies aloud, the voice and its rate. Nothing typed or said
 * is ever stored.
 */
import { useEffect, useId, useState } from "react";

import { getSpeaker, loadAssistantStatus } from "../app/voice.ts";
import { api } from "../lib/api.ts";
import { unavailableText } from "../lib/assistant.ts";
import { allowanceSummary, allowanceWarns } from "../lib/billing.ts";
import { BUTLER_NAME, DEFAULT_BUTLER_NAME, FORM_OF_ADDRESS } from "../lib/assistantContext.ts";
import { errorMessage } from "../lib/labels.ts";
import { isDeniedVoice, MAX_RATE, MIN_RATE, type VoiceLike } from "../lib/speech.ts";
import { useAssistantStore } from "../store/assistant.ts";
import { AssistantConsentSheet, CONSENT_VERSION } from "./AssistantConsentSheet.tsx";
import { Icon } from "./icons.tsx";
import { Button, Card, IconWell, inputClass, Notice, Toggle } from "./ui.tsx";

export const TEST_VOICE_LINE = "Good evening. How may I help?";


function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <div className="min-w-0">
        <p className="font-semibold leading-snug">{label}</p>
        {hint ? <p className="mt-0.5 text-xs text-text-muted">{hint}</p> : null}
      </div>
      {children}
    </div>
  );
}

const ADDRESS_PRESETS = ["", "sir", "madam"];

export function AssistantSettingsCard() {
  const status = useAssistantStore((s) => s.status);
  const statusError = useAssistantStore((s) => s.statusError);
  const allowance = useAssistantStore((s) => s.allowance);
  const prefs = useAssistantStore((s) => s.prefs);
  const setPrefs = useAssistantStore((s) => s.setPrefs);
  const [sheet, setSheet] = useState(false);
  const [busy, setBusy] = useState<"consent" | "withdraw" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState(prefs.butlerName);
  const [custom, setCustom] = useState(ADDRESS_PRESETS.includes(prefs.address) ? "" : prefs.address);
  const [addressMode, setAddressMode] = useState(ADDRESS_PRESETS.includes(prefs.address) ? prefs.address : "custom");
  // read again when the picker opens: Safari lists its voices a moment after the page loads
  const [voices, setVoices] = useState<VoiceLike[]>(() => getSpeaker().voices());
  const id = useId();

  useEffect(() => {
    void loadAssistantStatus();
  }, []);

  const consented = !!status?.consent && status.consent.version === status.consent_version;
  const outdated = !!status?.consent && !consented;
  const canConsent = !!status && status.unavailable_reason !== "not_configured" && status.unavailable_reason !== "not_in_plan";
  const why = status ? unavailableText(status) : null;

  const accept = async () => {
    setError(null);
    if (consented) {
      // the account already agreed (on another phone): this phone only needs its own switch
      setPrefs({ useAi: true, showAssistant: true });
      setSheet(false);
      return;
    }
    setBusy("consent");
    try {
      useAssistantStore.getState().setStatus(await api.assistantConsent({ version: CONSENT_VERSION, accept: true }));
      setPrefs({ useAi: true, showAssistant: true });
      setSheet(false);
    } catch (e) {
      setError(errorMessage(e && typeof e === "object" && "code" in e ? { code: String((e as { code: unknown }).code) } : null));
    } finally {
      setBusy(null);
    }
  };

  const withdraw = async () => {
    setError(null);
    setBusy("withdraw");
    try {
      await api.assistantWithdraw();
      setPrefs({ useAi: false });
      await loadAssistantStatus();
    } catch (e) {
      setError(errorMessage(e && typeof e === "object" && "code" in e ? { code: String((e as { code: unknown }).code) } : null));
    } finally {
      setBusy(null);
    }
  };

  const saveName = (value: string) => {
    const trimmed = value.trim();
    if (trimmed === "") {
      setName(DEFAULT_BUTLER_NAME);
      setPrefs({ butlerName: DEFAULT_BUTLER_NAME });
    } else if (BUTLER_NAME.test(trimmed)) setPrefs({ butlerName: trimmed });
  };
  const nameInvalid = name.trim() !== "" && !BUTLER_NAME.test(name.trim());
  const customInvalid = addressMode === "custom" && custom.trim() !== "" && !FORM_OF_ADDRESS.test(custom.trim());

  const english = voices.filter((v) => !isDeniedVoice(v));
  const novelty = voices.filter((v) => isDeniedVoice(v));

  return (
    <Card>
      <div className="flex items-start gap-3.5">
        <IconWell icon="ask" size={40} />
        <div className="min-w-0 flex-1 pt-0.5">
          <h2 className="font-semibold leading-snug">AI butler</h2>
          <p className="mt-1 text-sm text-text-muted">Talk to DoMe from the Ask page. Commands DoMe understands by itself always work, without any AI service.</p>
        </div>
      </div>

      <div className="mt-3 space-y-2" aria-live="polite">
        {statusError ? <p className="text-sm text-text-muted">The AI butler's status could not be read right now. Voice without AI still works.</p> : null}
        {why ? <Notice tone="info">{why}</Notice> : null}
        {status && consented ? (
          <p className="flex items-center gap-1.5 text-sm font-medium text-success">
            <Icon name="checkCircle" size={16} />
            AI butler is on for your account.
          </p>
        ) : null}
        {outdated ? <Notice tone="warning">The terms changed; read and turn on again.</Notice> : null}
        {allowance && allowance.kind !== "none" && status && status.unavailable_reason !== "not_configured" && status.unavailable_reason !== "not_in_plan" ? <p className={`text-sm ${allowanceWarns(allowance) ? "font-medium text-warning" : "text-text-muted"}`}>{allowanceSummary(allowance)}</p> : null}
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </div>

      {canConsent && !consented ? (
        <Button className="mt-3" variant="primary" onClick={() => setSheet(true)}>
          Turn on the AI butler…
        </Button>
      ) : null}

      <div className="mt-4 divide-y divide-border border-y border-border">
        <Row label="Show the Ask tab" hint="The tab bar shows Ask instead of Type. Both stay under More.">
          <Toggle label="Show the Ask tab" checked={prefs.showAssistant} onChange={(v) => setPrefs({ showAssistant: v })} />
        </Row>
        <Row label="Use AI on this phone" hint={consented ? "Requests DoMe can't work out go to the AI butler." : "Turn on the AI butler first."}>
          <Toggle
            label="Use AI on this phone"
            checked={prefs.useAi && consented}
            disabled={!consented}
            onChange={(v) => {
              if (v && !prefs.useAi) setSheet(true); // shown once on this phone as a reminder of what is sent
              else setPrefs({ useAi: v });
            }}
          />
        </Row>
        <Row label="Ask before every action" hint="Every plan waits for your tap on Run, even pausing a video.">
          <Toggle label="Ask before every action" checked={prefs.confirmEveryAction} onChange={(v) => setPrefs({ confirmEveryAction: v })} />
        </Row>
        <Row label="Read replies aloud" hint="Turn off if you use VoiceOver.">
          <Toggle label="Read replies aloud" checked={!prefs.muted} onChange={(v) => setPrefs({ muted: !v })} />
        </Row>
      </div>

      <div className="mt-4 space-y-4">
        <div className="space-y-1.5">
          <label htmlFor={`${id}-name`} className="block text-sm font-semibold">
            Butler's name
          </label>
          <input id={`${id}-name`} className={inputClass} value={name} maxLength={20} autoComplete="off" onChange={(e) => setName(e.currentTarget.value)} onBlur={(e) => saveName(e.currentTarget.value)} aria-invalid={nameInvalid} />
          <p className="text-xs text-text-muted">{nameInvalid ? "Letters, spaces, full stops, apostrophes and hyphens only, up to 20 characters." : "What the butler calls itself. Default: DoMe."}</p>
        </div>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-address`} className="block text-sm font-semibold">
            How it addresses you
          </label>
          <select
            id={`${id}-address`}
            className={inputClass}
            value={addressMode}
            onChange={(e) => {
              const v = e.currentTarget.value;
              setAddressMode(v);
              // "Something else…" uses its own text only: until valid text is typed there, no address is used
              if (v !== "custom") setPrefs({ address: v });
              else setPrefs({ address: FORM_OF_ADDRESS.test(custom.trim()) ? custom.trim() : "" });
            }}
          >
            <option value="">None</option>
            <option value="sir">sir</option>
            <option value="madam">madam</option>
            <option value="custom">Something else…</option>
          </select>
          {addressMode === "custom" ? (
            <>
              <input
                aria-label="Form of address"
                className={inputClass}
                value={custom}
                maxLength={24}
                autoComplete="off"
                onChange={(e) => setCustom(e.currentTarget.value)}
                onBlur={(e) => {
                  const v = e.currentTarget.value.trim();
                  if (v === "") {
                    // emptied: no form of address, as None
                    setAddressMode("");
                    setPrefs({ address: "" });
                  } else if (FORM_OF_ADDRESS.test(v)) setPrefs({ address: v });
                }}
                aria-invalid={customInvalid}
              />
              {customInvalid ? <p className="text-xs text-warning">Letters, spaces, full stops, apostrophes and hyphens only, up to 24 characters.</p> : null}
            </>
          ) : null}
          <p className="text-xs text-text-muted" data-testid="address-in-use">
            In use: {prefs.address ? `“${prefs.address}”` : "none"}
          </p>
        </div>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-voice`} className="block text-sm font-semibold">
            Voice
          </label>
          <select id={`${id}-voice`} className={inputClass} value={prefs.voiceURI ?? ""} onFocus={() => setVoices(getSpeaker().voices())} onChange={(e) => setPrefs({ voiceURI: e.currentTarget.value || null })}>
            <option value="">Automatic (a British voice)</option>
            {english.map((v) => (
              <option key={v.voiceURI} value={v.voiceURI}>
                {v.name} ({v.lang.replace(/_/g, "-")})
              </option>
            ))}
            {novelty.length > 0 ? (
              <optgroup label="Novelty voices">
                {novelty.map((v) => (
                  <option key={v.voiceURI} value={v.voiceURI}>
                    {v.name} ({v.lang.replace(/_/g, "-")})
                  </option>
                ))}
              </optgroup>
            ) : null}
          </select>
          <p className="text-xs text-text-muted">For a better voice, download Daniel (Enhanced): iPhone Settings → Accessibility → Spoken Content → Voices → English.</p>
        </div>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-rate`} className="flex justify-between text-sm font-semibold">
            <span>Speaking rate</span>
            <span className="font-medium tabular-nums text-text-muted">{prefs.rate.toFixed(2)}×</span>
          </label>
          <input id={`${id}-rate`} type="range" className="slider" style={{ "--fill": `${((prefs.rate - MIN_RATE) / (MAX_RATE - MIN_RATE)) * 100}%` } as React.CSSProperties} min={MIN_RATE} max={MAX_RATE} step={0.05} value={prefs.rate} onChange={(e) => setPrefs({ rate: Number(e.currentTarget.value) })} />
        </div>
        <Button onClick={() => getSpeaker().speak(TEST_VOICE_LINE, { gesture: true })}>
          <Icon name="volume" size={18} />
          Test voice
        </Button>
        <p className="text-xs text-text-faint">Hear nothing? Check the side switch isn't on Silent and the volume is up.</p>
      </div>

      {consented ? (
        // the sentence on its own line, then a full-width quiet button: never a squeezed three-line column
        <div className="mt-4 space-y-2 border-t border-border pt-3">
          <p className="text-xs text-text-muted">Turning it off applies to your account on every phone.</p>
          <Button variant="danger-ghost" size="md" full className="border border-danger/30" busy={busy === "withdraw"} onClick={() => void withdraw()}>
            Turn off the AI butler
          </Button>
        </div>
      ) : null}

      <AssistantConsentSheet open={sheet} allowance={allowance} busy={busy === "consent"} error={error} onAccept={() => void accept()} onClose={() => setSheet(false)} />
    </Card>
  );
}
