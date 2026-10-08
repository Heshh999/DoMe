/**
 * Camera QR scanning: `BarcodeDetector` where the browser offers it (Chrome/Edge, recent Safari),
 * otherwise `getUserMedia` frames decoded by jsQR on a canvas. Decoded text is handed to the caller
 * exactly once per distinct value and is never logged.
 */
import jsQR from "jsqr";

export type QrSupport = "barcode-detector" | "jsqr" | "unsupported";

interface BarcodeDetectorLike {
  detect(source: ImageBitmapSource): Promise<Array<{ rawValue: string }>>;
}
interface BarcodeDetectorCtor {
  new (options?: { formats?: string[] }): BarcodeDetectorLike;
  getSupportedFormats?: () => Promise<string[]>;
}

function barcodeDetector(): BarcodeDetectorCtor | null {
  const ctor = (globalThis as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector;
  return ctor ?? null;
}

export function detectQrSupport(): QrSupport {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return "unsupported";
  return barcodeDetector() ? "barcode-detector" : "jsqr";
}

export interface QrScanner {
  stop(): void;
  readonly method: QrSupport;
}

export class CameraError extends Error {
  readonly reason: "denied" | "unavailable" | "insecure" | "unsupported";
  constructor(reason: CameraError["reason"], message: string) {
    super(message);
    this.name = "CameraError";
    this.reason = reason;
  }
}

export async function startQrScanner(video: HTMLVideoElement, onCode: (text: string) => void, onError: (e: Error) => void): Promise<QrScanner> {
  const support = detectQrSupport();
  if (support === "unsupported") throw new CameraError("unsupported", "This browser cannot open the camera. Type the code instead.");
  if (typeof isSecureContext !== "undefined" && !isSecureContext) throw new CameraError("insecure", "The camera needs a secure (https) page. Type the code instead.");

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
  } catch (e) {
    const name = e instanceof Error ? e.name : "";
    if (name === "NotAllowedError" || name === "SecurityError") throw new CameraError("denied", "Camera access was not allowed. Allow it in Settings, or type the code instead.");
    throw new CameraError("unavailable", "No camera is available. Type the code instead.");
  }
  video.srcObject = stream;
  video.setAttribute("playsinline", "true");
  video.muted = true;
  await video.play().catch(() => undefined);

  let stopped = false;
  let lastValue: string | null = null;
  let detector: BarcodeDetectorLike | null = null;
  const Ctor = barcodeDetector();
  if (Ctor) {
    try {
      const formats = (await Ctor.getSupportedFormats?.()) ?? ["qr_code"];
      if (formats.includes("qr_code")) detector = new Ctor({ formats: ["qr_code"] });
    } catch {
      detector = null;
    }
  }
  const method: QrSupport = detector ? "barcode-detector" : "jsqr";
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  const deliver = (value: string) => {
    if (value && value !== lastValue) {
      lastValue = value;
      onCode(value);
    }
  };

  const tick = async () => {
    if (stopped) return;
    try {
      if (video.readyState >= 2 && video.videoWidth > 0) {
        if (detector) {
          const codes = await detector.detect(video);
          for (const c of codes) deliver(c.rawValue);
        } else if (ctx) {
          const w = Math.min(video.videoWidth, 640);
          const h = Math.round((video.videoHeight / video.videoWidth) * w);
          canvas.width = w;
          canvas.height = h;
          ctx.drawImage(video, 0, 0, w, h);
          const img = ctx.getImageData(0, 0, w, h);
          const result = jsQR(img.data, w, h, { inversionAttempts: "dontInvert" });
          if (result?.data) deliver(result.data);
        }
      }
    } catch (e) {
      onError(e instanceof Error ? e : new Error("scan failed"));
    }
    if (!stopped) setTimeout(() => void tick(), 180);
  };
  void tick();

  return {
    method,
    stop() {
      stopped = true;
      for (const track of stream.getTracks()) track.stop();
      video.srcObject = null;
    },
  };
}
