/** An operation failure with a stable error code from shared/protocol/errors.json. */
export class OpError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "OpError";
    this.code = code;
  }
}

const CODE_RE = /^[A-Z_]+$/;

/** Shape of `bridge_response.error`; codes outside the contract's pattern collapse to INTERNAL. */
export function toErrorObject(err: unknown): { code: string; message: string } {
  if (err instanceof OpError) return { code: CODE_RE.test(err.code) ? err.code : "INTERNAL", message: clipMessage(err.message) };
  if (err instanceof Error) return { code: "INTERNAL", message: clipMessage(`${err.name}: ${err.message}`) };
  return { code: "INTERNAL", message: "unexpected failure" };
}

export function clipMessage(text: string): string {
  // eslint-disable-next-line no-control-regex -- stripping control characters is the point
  const clean = text.replace(/[\u0000-\u001f\u007f]/g, " ").trim();
  return clean.length > 512 ? clean.slice(0, 509) + "..." : clean || "error";
}
