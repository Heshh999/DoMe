/**
 * Every failure gets the same two exits (spec §11A): the connection-health screen and the support
 * form with the error's category preselected. Never a payment step.
 */
import { Link } from "react-router";

import { supportLink } from "../lib/support.ts";

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
