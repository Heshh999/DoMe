import { PublicIcon } from "./publicIcons.tsx";

export function DraftBanner() {
  return (
    <p role="note" className="dome-draft mt-5">
      <span aria-hidden="true" className="dome-draft-well">
        <PublicIcon name="info" size={18} />
      </span>
      <span className="pt-1">
        <strong>DRAFT</strong> — prepared for founder review. Not yet legally reviewed and not a statement of compliance. Items marked [FOUNDER] need business input.
      </span>
    </p>
  );
}
