/**
 * Bottom sheet / modal dialog (native `<dialog>` for focus trapping and Escape handling). Used for
 * the confirmation transaction and pickers; `dismissible={false}` keeps a confirmation from being
 * closed by accident (Escape/backdrop) — the content decides which buttons lead out (Approve/Decline,
 * or Close once the challenge expired or the connection is down).
 */
import { useEffect, useRef, type ReactNode } from "react";

export function Sheet({ open, title, children, onClose, dismissible = true, labelledBy }: { open: boolean; title?: string; children: ReactNode; onClose?: () => void; dismissible?: boolean; labelledBy?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      try {
        el.showModal();
      } catch {
        el.setAttribute("open", "");
      }
    } else if (!open && el.open) el.close();
  }, [open]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onCancel = (e: Event) => {
      if (!dismissible) e.preventDefault();
      else onClose?.();
    };
    el.addEventListener("cancel", onCancel);
    return () => el.removeEventListener("cancel", onCancel);
  }, [dismissible, onClose]);
  if (!open) return null;
  const titleId = labelledBy ?? "sheet-title";
  return (
    // phones: the box sits at the bottom of the screen (margin-top auto inside the UA's top/bottom 0 inset); larger screens centre it
    <dialog ref={ref} aria-labelledby={titleId} aria-modal="true" className="fixed inset-x-0 bottom-0 mx-auto mb-0 mt-auto w-full max-w-lg bg-transparent p-0 backdrop:bg-black/60 open:flex sm:inset-0 sm:my-auto">
      <div className="animate-rise w-full rounded-t-[1.75rem] border border-border bg-bg-raised px-5 pt-3 pb-[calc(1.25rem+var(--safe-bottom))] text-text shadow-[var(--dome-shadow-raised)] sm:rounded-card sm:pt-5">
        <div aria-hidden="true" className="dome-grabber mx-auto mb-4 sm:hidden" />
        {title ? (
          <h2 id={titleId} className="mb-3 text-[1.25rem] font-bold tracking-[-0.02em]">
            {title}
          </h2>
        ) : null}
        {children}
      </div>
    </dialog>
  );
}
