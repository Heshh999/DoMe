// Generates the PWA icons (PNG, no external image libraries) and the SVG favicon into public/icons.
// A simple mark: a rounded dark tile with a bright "dome" arc and a dot. Run: pnpm gen:icons
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const outDir = resolve(dirname(fileURLToPath(import.meta.url)), "../public/icons");
mkdirSync(outDir, { recursive: true });

const BG = [0x0b, 0x0f, 0x17];
const ACCENT = [0x5e, 0xe1, 0xc0];
const ACCENT_DIM = [0x2c, 0x7a, 0x6a];

function crc32(buf) {
  let c;
  const table = [];
  for (let n = 0; n < 256; n++) {
    c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  let crc = 0xffffffff;
  for (const b of buf) crc = table[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

function png(size, pixel) {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      const [r, g, b, a] = pixel(x, y);
      const o = y * (size * 4 + 1) + 1 + x * 4;
      raw[o] = r;
      raw[o + 1] = g;
      raw[o + 2] = b;
      raw[o + 3] = a;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** Icon geometry in unit coordinates; `maskable` fills the whole square (safe zone 80%). */
function makePixel(size, { maskable, transparentCorners }) {
  const s = size;
  const radius = maskable ? 0 : s * 0.22;
  const scale = maskable ? 0.8 : 1;
  const cx = s / 2;
  const cy = s / 2;
  return (x, y) => {
    const px = x + 0.5;
    const py = y + 0.5;
    // rounded-square mask
    let inside = true;
    if (!maskable) {
      const dx = Math.max(Math.abs(px - cx) - (s / 2 - radius), 0);
      const dy = Math.max(Math.abs(py - cy) - (s / 2 - radius), 0);
      inside = dx * dx + dy * dy <= radius * radius;
    }
    if (!inside) return transparentCorners ? [0, 0, 0, 0] : [...BG, 255];
    // dome arc: ring segment above centre
    const ux = (px - cx) / (s * scale);
    const uy = (py - cy) / (s * scale);
    const r = Math.hypot(ux, uy);
    if (uy < 0.05 && r > 0.27 && r < 0.36) return [...ACCENT, 255];
    if (uy < 0.05 && r > 0.36 && r < 0.385) return [...ACCENT_DIM, 255];
    // base line
    if (Math.abs(uy - 0.1) < 0.035 && Math.abs(ux) < 0.38) return [...ACCENT, 255];
    // dot (the "phone")
    if (Math.hypot(ux, uy - 0.27) < 0.07) return [...ACCENT, 255];
    return [...BG, 255];
  };
}

for (const [name, size, opts] of [
  ["icon-192.png", 192, { maskable: false, transparentCorners: true }],
  ["icon-512.png", 512, { maskable: false, transparentCorners: true }],
  ["icon-512-maskable.png", 512, { maskable: true, transparentCorners: false }],
  ["apple-touch-icon.png", 180, { maskable: true, transparentCorners: false }],
]) {
  writeFileSync(resolve(outDir, name), png(size, makePixel(size, opts)));
}

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#0b0f17"/>
  <path d="M14 35 A18 18 0 0 1 50 35" fill="none" stroke="#5ee1c0" stroke-width="5" stroke-linecap="round"/>
  <rect x="12" y="36" width="40" height="4.5" rx="2" fill="#5ee1c0"/>
  <circle cx="32" cy="49" r="4.5" fill="#5ee1c0"/>
</svg>
`;
writeFileSync(resolve(outDir, "favicon.svg"), svg);
console.log(`wrote icons to ${outDir}`);
