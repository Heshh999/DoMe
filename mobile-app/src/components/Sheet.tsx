/**
 * Bottom sheet / modal dialog (native `<dialog>` for focus trapping and Escape handling). Used for
 * the confirmation transaction and pickers; `dismissible={false}` keeps a confirmation from being
 * closed by accident — Decline is the only way out.
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
    <dialog ref={ref} aria-labelledby={titleId} aria-modal="true" className="m-0 w-full max-w-lg mx-auto sm:my-auto fixed inset-x-0 bottom-0 sm:inset-0 bg-transparent p-0 backdrop:bg-black/60 open:flex">
      <div className="w-full rounded-t-[1.4rem] sm:rounded-card bg-bg-elevated border border-border text-text shadow-2xl p-5 pb-[calc(1.25rem+var(--safe-bottom))]">
        {title ? (
          <h2 id={titleId} className="text-lg font-bold mb-3">
            {title}
          </h2>
        ) : null}
        {children}
      </div>
    </dialog>
  );
}
