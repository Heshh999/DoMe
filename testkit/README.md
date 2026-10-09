# DoMe test kit: try DoMe on your own PC and iPhone

This folder runs the whole DoMe service on your Windows PC (in Docker) and gives it a temporary
HTTPS address, so your iPhone can open the DoMe app from anywhere and control this PC. It also runs
the DoMe PC program from the source code and builds the browser extension for YouTube control.

It is a **test setup, not the product as customers will get it**. Read "What this is not" before
leaving it running.

Three double-click files do everything:

| File | What it does |
| --- | --- |
| `1 Start test server.cmd` | Starts DoMe in Docker, opens two temporary HTTPS addresses, prints the sign-in passphrase and shows a QR code for your iPhone. |
| `2 Start DoMe on this PC.cmd` | Prepares and starts the DoMe PC program, builds the browser extension, links this PC to your test account. Keep its window open while testing. |
| `3 Stop test server.cmd` | Stops the test server; asks whether to keep or delete the test data. |

## What you need

- A Windows 10 or 11 PC with internet access.
- **Docker Desktop**, installed and running (its window says "Engine running").
  Download: https://www.docker.com/products/docker-desktop/
- **Chrome or Edge** on the PC (for YouTube control).
- An **iPhone with Safari** (any phone or browser works; the app is designed for iPhone).
- About 10 GB of free disk space. The first start takes 5-15 minutes while Docker downloads and
  builds; later starts take about a minute.
- **uv**, a free tool that installs Python for the DoMe PC program. Step 2 offers to install it for
  your Windows user if it is missing (no administrator rights needed).
- This repository on the PC (cloned with git, or downloaded as a ZIP and extracted).

## Step 1: start the test server

1. Start Docker Desktop and wait for "Engine running".
2. Double-click **`1 Start test server.cmd`**.
3. When it finishes, the window shows:
   - the **app address** (`https://<random words>.trycloudflare.com`),
   - the **sign-in passphrase** (three groups of four characters, new on every start),
   - and a QR code opens in a picture viewer.

You can close this window afterwards; DoMe keeps running in Docker.

## Step 2: open DoMe on the iPhone and sign in

1. Point the iPhone camera at the QR code (or type the app address into Safari).
2. Tap **Sign in**. The DoMe **development sign-in page** opens (dark page with a yellow warning).
3. Type the **passphrase** from the step 1 window, then tap **alice@example.test** (or type any
   email and tap Continue). The email is only a test name; there is no password.
4. You are back in the DoMe app. It says the phone is not paired yet; that comes in step 4.

Home Screen: you can add DoMe to the Home Screen (Share, then "Add to Home Screen"). If you do,
open it from the Home Screen icon and **sign in and pair from inside it**: the Home Screen app may
not share Safari's storage, so a phone paired in Safari can count as a different phone.

## Step 3: start DoMe on this PC

1. Double-click **`2 Start DoMe on this PC.cmd`**.
2. **First time only, the browser extension:** the script builds it (2-4 minutes) and opens the
   Extensions page of Chrome or Edge. Switch on **Developer mode**, click **Load unpacked** and
   choose the folder the window shows (the path is already copied; paste it with Ctrl+V). Reload any
   YouTube tabs that were already open, then press Enter in the window.
3. **First time only, linking:** a browser window opens. Sign in with the passphrase and **the same
   email you used on the iPhone**, check the PC name, leave **Enable remote control for this PC**
   ticked and click **Link this PC**.
4. The DoMe icon appears near the clock (it may be hidden under the `^` arrow).

Keep this window open while you test. Closing it, or choosing **Quit DoMe** in the tray menu, stops
DoMe on the PC.

## Step 4: pair the iPhone

1. On the PC: right-click the DoMe tray icon, choose **Pair a phone...**. A window shows a QR code
   and a 20-character code, valid for 5 minutes.
2. On the iPhone, in the DoMe app: **More -> Devices -> Pair with a PC**. Choose **Scan QR code**
   (allow the camera) or **Type the code**. Scanning inside the app keeps the pairing in the app you
   are using; the iPhone camera app would open the code in Safari instead.
3. Name the phone, choose what it may do, tap **Request pairing**.
4. Both screens show a **6-digit number**. If they match, approve on the PC and choose the
   permissions this phone gets.
5. The iPhone says **Paired**.

## Step 5: things to try

Everything below **really happens on this PC**.

- **Remote**: Windows volume and mute; play/pause/next for whatever is playing (Spotify, a video).
- **YouTube** (Remote, YouTube tab): open a video in Chrome or Edge with the extension loaded.
- **Touchpad** and **Type**: move the mouse, click, scroll, type text into the active window.
- **Apps**: opens apps you approved on the PC. To approve one, open a Command Prompt in the
  repository folder and run, for example:
  `pc-agent\.venv\Scripts\dome-agent.exe approve-app notepad C:\Windows\System32\notepad.exe --name Notepad`
  (the tray menu's **Approved apps...** lists them).
- **Lock, sleep, restart, shut down** (if you granted power control): always asks for confirmation,
  and it does lock or shut down the PC.
- **More -> Devices**: rename or revoke phones, see the PC's status.

Please write down what worked, what did not, and anything confusing. For a problem with the app,
**More -> Settings & help** has a diagnostics report you can download and attach.

## Stopping

- Double-click **`3 Stop test server.cmd`**. It asks whether to delete all test data; answer **N** to
  keep your test account, linked PC and paired phones for next time.
- On the PC, right-click the DoMe tray icon and choose **Quit DoMe** (or close the step 2 window).

## Every start is a new address

The temporary addresses (Cloudflare "quick tunnels", no account needed) change every time the test
server starts, and stop working when you stop it, restart Docker or restart the PC. After a new
start:

- Open the **new** address on the iPhone (scan the new QR code) and sign in again. A Home Screen icon
  added earlier still points to the old address: remove it and add the new one.
- **Pair the iPhone again.** The phone keeps its pairing key per address, so at a new address it is
  a new phone to DoMe. The free plan allows **2 phones**: when pairing says the device limit is
  reached, tap **Manage devices** and revoke the phones from earlier runs, then pair again.
- Run `2 Start DoMe on this PC.cmd` again. The PC stays linked; the script gives the PC program the
  new address.

To avoid all this, use a stable address (next section).

## Stable address (optional)

If you have your own HTTPS forwarding (for example a Cloudflare named tunnel on your own domain),
point two hostnames at this PC:

| Hostname | Forwards to |
| --- | --- |
| app, e.g. `https://dome-test.example.com` | `http://127.0.0.1:18080` |
| sign-in, e.g. `https://dome-signin.example.com` | `http://127.0.0.1:18081` |

Then create `testkit\stable-addresses.txt` with:

```
APP_URL=https://dome-test.example.com
SIGNIN_URL=https://dome-signin.example.com
```

`1 Start test server.cmd` then uses these instead of quick tunnels. The app address must be just
`https://host` (no path). With a stable address you stay signed in across restarts and the phone
stays paired. Delete the file to go back to quick tunnels.

## Start over

1. Run `3 Stop test server.cmd` and answer **y** to delete all test data.
2. Quit DoMe from the tray, then delete the folder `%LOCALAPPDATA%\DoMe` (the PC program's link,
   pairings, settings and logs).
3. On the iPhone, in the DoMe app: **More -> Settings & help -> Forget this installation** (before
   stopping), or clear the website data for the trycloudflare.com address in Safari settings.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| "Docker is not installed" / "not running" | Install or start Docker Desktop, wait for "Engine running", run the script again. |
| "Cloudflare did not create the temporary address" | Cloudflare limits how often quick tunnels can be created. Wait a minute or two and run step 1 again. |
| "No temporary address ... after 90 seconds" | The PC cannot reach Cloudflare. A VPN, company network or firewall can block it; try another network. |
| The iPhone cannot open the address | New addresses can take a minute to work. Try again; try mobile data instead of Wi-Fi. Make sure the address is from the **latest** start. |
| Sign-in says "Wrong passphrase" | Use the passphrase from the **latest** step 1 window; it changes on every start. It is also in `testkit\.state\current.json`. |
| Sign-in says "Too many wrong passphrases" | Wait 10 minutes, or run step 1 again (new passphrase). |
| "DoMe is already running on this PC" | Right-click the tray icon, **Quit DoMe**, run step 2 again. |
| The link browser window did not open | Copy the address printed in the step 2 window into a browser on the PC. |
| YouTube controls stay greyed out | Check that the extension is loaded and switched on, then reload the YouTube tab. To rebuild it, open a Command Prompt in this folder and run `"2 Start DoMe on this PC.cmd" -RebuildExtension`, then click the reload arrow on the DoMe card on the Extensions page. |
| Pairing says the device limit is reached | Tap **Manage devices**, revoke phones you no longer use, pair again (see "Every start is a new address"). |
| Anything else | Server logs: open a Command Prompt in the repository folder and run `docker compose -f testkit\docker-compose.yml logs api`. PC program log: `%LOCALAPPDATA%\DoMe\logs\agent.log`. Send these with your notes. |

## What this is not

- **Not the real sign-in.** The sign-in page is DoMe's development identity provider: no passwords,
  any email works. The only thing keeping strangers out is the passphrase (new on every start,
  10 wrong tries lock sign-in for 10 minutes). Use test names, not real accounts.
- **Public addresses.** Anyone who learns the app address can open the DoMe website and the sign-in
  page. Controlling the PC still needs a signed-in account **and** a phone you approved on the PC,
  but stop the test server when you are not testing.
- **Development mode.** The server runs with `DOME_ENV=development`, a local database in Docker on
  this PC, and no billing. Data stays on this PC (Docker volumes `dome-test_pgdata` and
  `dome-test_api-run`).
- **Real actions.** The PC program is the real one: commands from the phone really change volume,
  move the mouse, type, lock and shut down this PC.

## What the kit writes on this PC

| Where | What |
| --- | --- |
| `testkit\.state\` | Session secret, current addresses and passphrase (`current.json`), the QR picture, the extension build and its key (`extension-key.pem`, keeps the extension ID fixed). Not committed to git. |
| Docker | Containers and volumes named `dome-test...`. |
| `%LOCALAPPDATA%\DoMe\` | The PC program's link, pairings, settings and logs. |
| Windows registry (your user only) | `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent` and the Edge equivalent, so the extension can talk to the PC program. Removed by `dome-agent uninstall-native-host`. |
| `pc-agent\.venv\` | The PC program's Python environment (created by uv). |
