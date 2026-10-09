# DoMe on your iPhone — sign in, pair, install

Status: **pre-release engineering build. The DoMe web app has not been opened on an iPhone yet.**
The app (`mobile-app/`) is built iPhone-first and its libraries and screens are unit-tested in a
browser-like test environment (205 tests at the last recorded run, with a fake socket and a fake
IndexedDB), and the pairing and command paths it uses are integration-tested on Linux with a
Python stand-in for the phone. Camera scanning, key persistence in Safari, Add to Home Screen,
and resume after the phone was locked are **not iPhone-tested**. This document describes the flow
as the code implements it and marks every iOS-specific behaviour that still needs a real device.

Related: `docs/WINDOWS_INSTALL.md` (the PC side), `docs/TROUBLESHOOTING.md`, `mobile-app/README.md`.

## 1. What you need

- An iPhone running a current iOS with Safari. The app uses WebCrypto (ECDSA P-256, non-extractable
  keys), IndexedDB, WebSockets, `getUserMedia` for the camera and the service-worker API. A minimum
  iOS version has **not** been established; the ADR chose P-256 because it is available non-extractable
  in current iOS Safari (`docs/adr/0001-foundational-decisions.md` D5), and a first device pass must
  confirm it.
- A DoMe account (created by signing in through the account's identity provider; DoMe never sees
  a password).
- A Windows PC with the DoMe agent linked to the same account and running (`docs/WINDOWS_INSTALL.md`).
- Internet on both devices. DoMe always goes through the DoMe service; there is **no local Wi-Fi
  mode**, even when phone and PC share a network (ADR-0001 D10).

Android phones and desktop browsers can use the same web app; "iPhone" below means any supported
browser unless an iOS-specific note says otherwise.

## 2. Sign in

1. Open the DoMe web address in Safari (the public site: product pages, pricing, FAQ, support,
   privacy and terms drafts live there too).
2. Tap **Sign in**. Safari goes to `/v1/auth/login`, which redirects to the identity provider
   (Authorization Code + PKCE). Sign in there.
3. You return to `/app` (the dashboard). The session is a server-side session with an `HttpOnly`
   cookie; nothing secret is stored in the page or in `localStorage`.

Signing out (More → Settings → **Sign out**) closes the live connection, ends the server session and
clears account state on the phone but **keeps this installation's signing key**, so signing back in
does not require pairing again. **Forget this installation** (same page) deletes the key; every PC
stops accepting this phone until it is paired again.

## 3. Pair the phone with a PC

Pairing is separate from signing in: knowing the account password never enrols a phone. The PC must
approve each phone, and the proof that the two screens talk about the same key is a verification
code that the DoMe service cannot compute (ADR-0001 D5; `version.json → rules.pairing_secret`).

### 3.1 Get a code from the PC

On the PC: tray icon → **Pair a phone…**. The PC shows a QR code and a 20-symbol code in four groups
(`XXXXX-XXXXX-XXXXX-XXXXX`). The code is valid for **5 minutes** and works **once**.

### 3.2 Scan or type it on the phone

Devices → **Pair a PC** (or scan the QR with the iPhone camera app: the QR contains
`https://<dome>/pair#code=…` and opens the app's pairing page; the code stays in the URL fragment and
is never sent to the server as part of the address).

- **Scan QR code**: the page asks for camera access, which is used only on this page. iOS Safari has
  no `BarcodeDetector`, so the app falls back to reading camera frames with jsQR (**not
  iPhone-tested**; the fallback is unit-tested). If the camera is denied or fails, the page switches
  to typing.
- **Type the code**: enter the four groups; case does not matter and `I`/`L` → `1`, `O` → `0` are
  corrected automatically.

If you were not signed in when you opened the QR link, the app sends you to sign in **without** keeping
the code (by design: the code is a secret and is not stored while Safari leaves for the identity
provider). After sign-in it shows "Scan the code again" — rescan or retype; if the 5 minutes ran out,
choose *Pair a phone* on the PC again (`mobile-app/KNOWN_ISSUES.md` #4).

### 3.3 Name this phone and choose permissions

The page says "Code accepted. Name this phone and choose what it may do on the PC." Pick a name and
the capabilities to request: *See status*, *Control YouTube and media*, *Change PC volume*, *Open and
manage approved apps*, *Lock Windows*, *Sleep, restart, shut down (always confirmed)*. The PC can grant
a subset. Only a SHA-256 handle of the code, your public key and these choices leave the phone.

### 3.4 Compare the verification code and approve on the PC

The phone now shows a large **6-digit verification code** and "Waiting for approval on the PC"
(or "PC is offline — it will ask when it reconnects": a claim against an offline PC is kept until the
code expires and delivered when the PC connects). The PC shows the phone's name, the requested
permissions and its own 6-digit code.

**Approve on the PC only if both codes match.** Both devices derive the code from the pairing code
and the exact key the service forwarded, so a service that substituted another key could not make the
two screens agree. A mismatch means something is wrong: decline on the PC.

The phone polls every 2 s. On approval it shows "Paired", reconnects its live connection so the new
identity is bound, and the PC appears under Devices with "This phone may …". On decline it shows
`PAIRING_DECLINED`; on expiry `PAIRING_CODE_INVALID` ("The pairing request expired before it was
approved on the PC.").

Free accounts can pair **2** phones per account; a third claim is refused with `DEVICE_LIMIT_REACHED`
(design assumption from `shared/protocol/plans.json`).

## 4. Add to Home Screen (iOS Safari)

More → Settings → **Install on your iPhone** shows the steps; they are the standard Safari ones:

1. Open the DoMe app in Safari.
2. Tap the **Share** button.
3. Scroll and tap **Add to Home Screen**, then **Add**.

The installed app opens full-screen (standalone display, dark theme colour, DoMe icon; manifest from
`vite-plugin-pwa`). The service worker precaches the built app shell only — never `/v1` API responses,
`/ws`, `/link`, `/pair` or `/.well-known` — so an update to the app is picked up automatically and
nothing private is cached.

**The home-screen app is a separate installation.** iOS gives it its own storage, so it has its own
signing key: pair it with your PC once from its Devices page, and revoke the Safari-tab installation
from Devices if you no longer use it. The Settings page says exactly this. Evidence: the separate
storage is a platform fact to confirm on a device (**not iPhone-tested**).

## 5. If browser storage is cleared

This phone's identity is a non-extractable key pair stored in IndexedDB (`mobile-app/src/lib/
controllerKey.ts`). Safari → Settings → Clear History and Website Data, deleting the home-screen app,
Private Browsing, or any eviction of site data removes it. What happens then:

- The app explains that this installation must pair again. There is deliberately **no recovery
  shortcut**: no cloud backup of the key, no re-enrolment from the account, no support override
  (spec §7, ADR-0001 D5). The old controller entry stays visible under Devices (from any signed-in
  device) so you can revoke it.
- A corrupt key is removed and reported the same way (`KEY_STORAGE` on the pairing page).
- Your account, PCs, and the other phones are unaffected.

iOS note to verify on a device: Safari can delete script-writable storage for sites that have not
been used for a while, and the rules differ for web apps added to the home screen. Until measured
(`mobile-app/README.md` → "Not verifiable here": reopen after 24 h and after a device restart), assume
that an installation you have not opened for weeks may need to be paired again.

## 6. Everyday use on an iPhone

- **Status is truthful.** The PC pill shows *Online*, *Reconnecting* or *Offline since …*. After the
  phone was locked or the app was in the background, iOS often reports the WebSocket as open although
  it is dead; the app re-subscribes, pings, and reconnects if nothing arrives within 5 s, and shows
  *Online · refreshing* with consequential controls disabled until fresh state arrives (also after 75 s
  without any state frame). Expected behaviour on a real device: *refreshing* → *Online* within about
  6 s of unlocking (`mobile-app/KNOWN_ISSUES.md` #5, **not iPhone-tested**).
- **Every result is the PC's result.** Sent → PC received → Running on the PC → Done / Failed /
  Expired / Cancelled / Outcome unknown. "No answer yet" is shown without claiming failure.
- **Confirmations** for sleep, restart, shutdown and closing an app appear as a modal that names the PC
  and the exact action, with a 60 s countdown. Approve signs the confirmation on this phone; Decline or
  letting it expire runs nothing.
- **Text commands** (Command tab) are parsed on the phone with a fixed rule table ("Pause YouTube",
  "Skip this video", "Set my PC volume to 35 percent", "Open Discord", "Lock my computer", "Put my
  computer to sleep" → confirmation). Use the iPhone keyboard's dictation key to speak them; DoMe has no
  microphone feature of its own.
- **Two volume controls** on the Remote tab: the YouTube player volume and the Windows system volume
  are separate on purpose. Sliders send at most one command per 250 ms plus the final value.
- **Fullscreen** cannot be started from the phone (browsers require a click on the PC itself); the app
  explains `ACTIVATION_REQUIRED` and offers Theater mode.

## 7. Limitations of iOS web apps (what DoMe does not promise)

| Limitation | Effect in DoMe |
| --- | --- |
| No background execution: iOS suspends the page and its WebSocket when the screen locks or you switch apps | Nothing runs in the background; the app refreshes state when you return. No command is queued for later. |
| No web push notifications are used | DoMe does not notify you about anything; check the app. |
| No `BarcodeDetector` in Safari | QR scanning uses the jsQR fallback with `getUserMedia`, which needs camera permission each installation. Typing the code always works. |
| Site storage can be evicted; home-screen apps and Safari tabs have separate storage | Each installation pairs separately; a cleared installation pairs again (§5). |
| No native app, no App Store listing | DoMe is a web app; there is no iOS native app in this release and native app-store subscription rules do not apply yet (spec §14). |
| Offline is offline | A full-screen "You're offline" state replaces the app; it never looks connected. |
| Voice input | Only the keyboard's dictation; push-to-talk and cloud transcription are a later, separately gated release (spec §13). |
| Light/dark | Dark by default, light follows the system setting. Reduced motion is respected. |

## 8. Revoking a phone

Devices lists every paired installation with its last-seen time. **Revoke** (any device, including the
one you hold) closes its live connections immediately, the PC refuses further commands from it, and it
can only be re-paired on the PC. Revocation also works on the PC side (`DoMe.exe revoke <id>` or the
pairing window) and keeps working after a plan downgrade. Lost or sold phone: revoke it from another
signed-in device, or sign out of all sessions from More → Settings (session list and revoke).

## 9. First-device checklist (to be done on a real iPhone)

From `mobile-app/README.md` → "Not verifiable here"; record the results in `docs/ACCEPTANCE.md` with
the tag **iPhone-tested**:

1. Install to the home screen, pair, kill the app, reopen after 24 h and after a restart: Devices still
   shows "This phone" without re-pairing.
2. Devices → Pair → Scan: the camera prompt appears once, the PC's QR is read within about 2 s; denying
   the camera falls back to typing.
3. Pause YouTube from the Remote, background the app for 2 minutes, resume: *Online · refreshing* →
   *Online*; no command replays.
4. With the PC agent and extension running: Next changes the video in a background tab; the result
   shows the transition.
5. Sleep… → the modal shows the registry wording and the PC's countdown detail; Approve → "Windows
   accepted the request".
6. Lighthouse/PWA audit: installability, contrast ≥ 4.5:1, 44 px touch targets, safe-area insets.
