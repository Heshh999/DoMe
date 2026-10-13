#!/usr/bin/env node
/**
 * Exports the demo film (src/components/film) as video files with Playwright + ffmpeg:
 *
 *   node scripts/export-film.mjs --out <dir> [--base <url>] [--fps 30] [--only wide|vertical] [--record] [--quick]
 *
 * Output (in --out, default: <tmp>/dome-film-export; exports are never committed): dome-demo-1080p.webm + .mp4
 * (1920×1080), dome-demo-vertical.webm + .mp4 (1080×1920), poster.png (scene 1's still, 1920×1080).
 *
 * How: the page /demo?export=1 renders the film chrome-less and installs `window.__domeFilm` (seek / play / pause).
 * Every frame of the film is a pure function of the clock, so the default mode steps the clock frame by frame
 * (seek → wait a paint → screenshot) and pipes PNGs into ffmpeg: the result is deterministic from t = 0, at a
 * constant frame rate, with every frame crisp. `--record` instead uses Playwright's `recordVideo` screencast of
 * the film playing in real time and trims the lead-in where the recording itself shows the film starting (see
 * detectLead); its frame timing and picture quality are the screencast's, so the default mode is the one to ship.
 *
 * Without --base the script serves the production build (`dist/`, run `pnpm build` first) with `vite preview`.
 * Tools: Playwright-core (from this package's node_modules or /opt/node-tools), the headless shell
 * (DOME_CHROMIUM or /opt/pw-browsers/…/headless_shell) and ffmpeg (FFMPEG or /usr/bin/ffmpeg).
 */
import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync, mkdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, "..");

function loadPlaywright() {
  for (const from of [import.meta.url, "/opt/node-tools/node_modules/"]) {
    try {
      return createRequire(from)("playwright-core");
    } catch {
      /* try the next place */
    }
  }
  throw new Error("playwright-core not found: install it in mobile-app or /opt/node-tools");
}

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : fallback;
};
const has = (name) => args.includes(name);

// exports are never committed: without --out they go to the system's temp folder
const OUT = resolve(flag("--out", join(tmpdir(), "dome-film-export")));
const FPS = Number(flag("--fps", has("--quick") ? "10" : "30"));
const ONLY = flag("--only", null);
const RECORD = has("--record");
const CHROMIUM = process.env.DOME_CHROMIUM ?? "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell";
const FFMPEG = process.env.FFMPEG ?? "/usr/bin/ffmpeg";
const FFPROBE = process.env.FFPROBE ?? FFMPEG.replace(/ffmpeg$/, "ffprobe");

const LAYOUTS = {
  wide: { name: "dome-demo-1080p", width: 1920, height: 1080, query: "wide" },
  vertical: { name: "dome-demo-vertical", width: 1080, height: 1920, query: "vertical" },
};

mkdirSync(OUT, { recursive: true });

/** `vite preview` of dist/ on a free port, unless --base was given. */
async function server() {
  const base = flag("--base", null);
  if (base) return { base, stop() {} };
  if (!existsSync(join(appDir, "dist", "index.html"))) throw new Error("dist/ is missing: run `pnpm build` first, or pass --base <url>");
  const port = 4179;
  const child = spawn("pnpm", ["exec", "vite", "preview", "--port", String(port), "--strictPort"], { cwd: appDir, stdio: ["ignore", "pipe", "pipe"] });
  const url = `http://localhost:${port}`;
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(url + "/demo");
      if (r.ok) return { base: url, stop: () => child.kill() };
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  child.kill();
  throw new Error("vite preview did not start");
}

function run(cmd, cmdArgs, { input } = {}) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(cmd, cmdArgs, { stdio: [input ? "pipe" : "ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("error", reject);
    child.on("close", (code) => (code === 0 ? resolveRun(out) : reject(new Error(`${cmd} exited ${code}\n${err.slice(-2000)}`))));
    if (input) child.stdin.end(input);
  });
}

async function probe(file) {
  try {
    const out = await run(FFPROBE, ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate:format=duration", "-of", "json", file]);
    const j = JSON.parse(out);
    return { duration: Number(j.format?.duration ?? 0), width: j.streams?.[0]?.width, height: j.streams?.[0]?.height };
  } catch {
    return { duration: NaN };
  }
}

const mb = (file) => `${(statSync(file).size / 1024 / 1024).toFixed(1)} MB`;

async function openFilm(browser, layout, base, extra = {}) {
  const context = await browser.newContext({ viewport: { width: layout.width, height: layout.height }, deviceScaleFactor: 1, colorScheme: "dark", reducedMotion: "no-preference", ...extra });
  const page = await context.newPage();
  page.on("pageerror", (e) => console.error("page error:", e.message));
  await page.goto(`${base}/demo?export=1&layout=${layout.query}`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => typeof window.__domeFilm === "object" && document.fonts.status === "loaded", null, { timeout: 30_000 });
  await page.waitForTimeout(400);
  const total = await page.evaluate(() => window.__domeFilm.total);
  return { context, page, total };
}

/** Frame-stepped export: every frame screenshot at an exact film time, piped into ffmpeg. */
async function exportFrames(browser, layout, base) {
  const { context, page, total } = await openFilm(browser, layout, base);
  const frames = Math.ceil((total / 1000) * FPS) + 1;
  const mp4 = join(OUT, `${layout.name}.mp4`);
  const ff = spawn(FFMPEG, ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-i", "pipe:0", "-c:v", "libx264", "-preset", "fast", "-crf", "15", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4], { stdio: ["pipe", "inherit", "inherit"] });
  const started = Date.now();
  for (let f = 0; f < frames; f++) {
    const t = Math.min(Math.round((f * 1000) / FPS), total);
    await page.evaluate(
      (ms) =>
        new Promise((done) => {
          window.__domeFilm.seek(ms);
          requestAnimationFrame(() => requestAnimationFrame(() => done()));
        }),
      t,
    );
    const png = await page.screenshot({ type: "png", animations: "allow", caret: "hide" });
    if (!ff.stdin.write(png)) await once(ff.stdin, "drain");
    if (f % (FPS * 5) === 0) process.stdout.write(`  ${layout.name}: ${t / 1000}s / ${total / 1000}s (${((Date.now() - started) / 1000).toFixed(0)}s elapsed)\n`);
  }
  ff.stdin.end();
  await once(ff, "close");
  await context.close();
  const webm = join(OUT, `${layout.name}.webm`);
  process.stdout.write(`  ${layout.name}: encoding webm…\n`);
  await run(FFMPEG, ["-y", "-loglevel", "error", "-i", mp4, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "31", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p", "-an", webm]);
  return [mp4, webm];
}

/**
 * Where the film starts inside a screencast. Before play() the page holds the film's static t = 0 frame (the dusk
 * room, lamp off), and the first thing that moves is the lamp at film 250 ms, so the end of that frozen stretch
 * marks play() + 0.25 s. ffmpeg's freezedetect finds it on the recording itself; `limit` (seconds) keeps the search
 * inside the lead-in, before the film's own still moments. Returns null when nothing frozen was found.
 */
async function detectLead(file, limit) {
  const log = await new Promise((resolveRun, reject) => {
    const child = spawn(FFMPEG, ["-hide_banner", "-i", file, "-t", String(Math.ceil(limit + 1)), "-vf", "freezedetect=n=0.002:d=0.25", "-an", "-f", "null", "-"], { stdio: ["ignore", "ignore", "pipe"] });
    let err = "";
    child.stderr.on("data", (d) => (err += d));
    child.on("error", reject);
    child.on("close", () => resolveRun(err));
  });
  const ends = [...log.matchAll(/freeze_end: ([\d.]+)/g)].map((m) => Number(m[1])).filter((v) => v <= limit);
  if (ends.length === 0) return null;
  return Math.max(0, ends[ends.length - 1] - 0.25);
}

/** Screencast export: Playwright recordVideo of the film playing in real time, trimmed to where the film starts. */
async function exportRecording(browser, layout, base) {
  const dir = join(OUT, ".record");
  mkdirSync(dir, { recursive: true });
  const tCreate = Date.now();
  const { context, page, total } = await openFilm(browser, layout, base, { recordVideo: { dir, size: { width: layout.width, height: layout.height } } });
  const video = page.video();
  const tPlay = Date.now();
  await page.evaluate(() => window.__domeFilm.play());
  await page.waitForTimeout(total + 600);
  await page.evaluate(() => window.__domeFilm.pause());
  await context.close();
  const raw = await video.path();
  // the lead-in ends within (page creation → play) plus a little; fall back to the wall-clock estimate
  const detected = await detectLead(raw, (tPlay - tCreate) / 1000 + 1.5);
  const info = await probe(raw);
  const lead = detected ?? Math.max(0, info.duration - (Date.now() - tPlay) / 1000);
  process.stdout.write(`  ${layout.name}: screencast ${info.duration.toFixed(2)} s, film starts at ${lead.toFixed(2)} s (${detected === null ? "estimated" : "detected"})\n`);
  // A screencast's clock stretches while the browser renders heavy frames, so the film's 66 s can span more of the
  // recording: keep everything after the lead-in rather than cutting at a fixed length (the end card just holds on).
  const mp4 = join(OUT, `${layout.name}.mp4`);
  const webm = join(OUT, `${layout.name}.webm`);
  await run(FFMPEG, ["-y", "-loglevel", "error", "-ss", lead.toFixed(3), "-i", raw, "-r", String(FPS), "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4]);
  await run(FFMPEG, ["-y", "-loglevel", "error", "-i", mp4, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "31", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p", "-an", webm]);
  rmSync(dir, { recursive: true, force: true });
  return [mp4, webm];
}

async function poster(browser, base) {
  const layout = LAYOUTS.wide;
  const { context, page } = await openFilm(browser, layout, base);
  const still = await page.evaluate(() => window.__domeFilm.scenes[0].still);
  await page.evaluate(
    (ms) =>
      new Promise((done) => {
        window.__domeFilm.seek(ms);
        requestAnimationFrame(() => requestAnimationFrame(() => done()));
      }),
    still,
  );
  const file = join(OUT, "poster.png");
  writeFileSync(file, await page.screenshot({ type: "png" }));
  await context.close();
  return file;
}

const { chromium } = loadPlaywright();
const srv = await server();
const browser = await chromium.launch({ executablePath: CHROMIUM });
const made = [];
try {
  for (const [key, layout] of Object.entries(LAYOUTS)) {
    if (ONLY && ONLY !== key) continue;
    process.stdout.write(`${layout.name} (${layout.width}×${layout.height}, ${FPS} fps, ${RECORD ? "recordVideo" : "frame-stepped"})\n`);
    made.push(...(await (RECORD ? exportRecording : exportFrames)(browser, layout, srv.base)));
  }
  made.push(await poster(browser, srv.base));
} finally {
  await browser.close();
  srv.stop();
}
process.stdout.write("\nDelivered:\n");
for (const file of made) {
  const info = file.endsWith(".png") ? null : await probe(file);
  process.stdout.write(`  ${file}  ${mb(file)}${info ? `  ${info.width}×${info.height}  ${info.duration.toFixed(2)} s` : ""}\n`);
}
