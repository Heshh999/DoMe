/**
 * Every failure gets the same two exits (spec §11A): the connection-health screen and the support
 * form with the error's category preselected. Never a payment step.
 */
import { Link } from "react-router";

import { supportCategoryFor } from "../lib/labels.ts";

export function supportLink(code: string | null | undefined): string {
  const params = new URLSearchParams();
  params.set("category", supportCategoryFor(code));
  if (code && /^[A-Z_]+$/.test(code)) params.set("code", code);
  return `/support?${params.toString()}`;
}

export function FailureLinks({ code, className = "" }: { code: string | null | undefined; className?: string }) {
  return (
    <p className={`text-sm flex flex-wrap gap-x-4 gap-y-1 ${className}`} data-testid="failure-links">
      <Link to="/app/health" className="text-accent font-semibold underline">
        Check connection health
      </Link>
      <Link to={supportLink(code)} className="text-accent font-semibold underline">
        Get help
      </Link>
    </p>
  );
}
