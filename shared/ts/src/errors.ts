export class ProtocolError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly detail: Record<string, unknown>;

  constructor(code: string, message: string, retryable = false, detail: Record<string, unknown> = {}) {
    super(`${code}: ${message}`);
    this.name = "ProtocolError";
    this.code = code;
    this.retryable = retryable;
    this.detail = detail;
  }

  toFrameError(): { code: string; message: string; retryable: boolean; detail?: Record<string, unknown> } {
    const out: { code: string; message: string; retryable: boolean; detail?: Record<string, unknown> } = {
      code: this.code,
      message: this.message.replace(/^[A-Z_]+: /, ""),
      retryable: this.retryable,
    };
    if (Object.keys(this.detail).length > 0) out.detail = this.detail;
    return out;
  }
}
