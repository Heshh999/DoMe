/** Public release history and known issues, rendered from src/content/release-notes.json (spec §11A). */
import notes from "../../content/release-notes.json";
import { Icon } from "../../components/icons.tsx";
import { PublicIcon } from "./publicIcons.tsx";

interface Release {
  version: string;
  date: string;
  state: string;
  changes: string[];
  verified: string[];
  not_verified: string[];
}
interface KnownIssue {
  title: string;
  detail: string;
  workaround: string;
}

const data = notes as { status: { channel: string; headline: string; verification: string }; releases: Release[]; known_issues: KnownIssue[] };

/** "pre-release" → "Pre-release" (sentence case, the way the page's text writes it). */
const sentenceCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function ReleaseNotesPage() {
  return (
    <div>
      <header className="dome-page-intro animate-rise">
        <p className="dome-eyebrow">Release notes</p>
        <h1 className="mt-3 text-h1 font-bold">Release history and known issues</h1>
        <p className="mt-4 max-w-2xl text-lg text-text-muted">{data.status.headline}</p>
        <div className="mt-6 flex flex-col items-start gap-2 rounded-card border border-border bg-bg-elevated px-4 py-3.5 text-sm leading-relaxed text-text-muted sm:flex-row sm:gap-3">
          <span className="dome-chip dome-chip-quiet mt-0.5 shrink-0">{sentenceCase(data.status.channel)}</span>
          <p>{data.status.verification}</p>
        </div>
      </header>

      <section aria-labelledby="known-issues">
        <h2 id="known-issues" className="text-title font-semibold">
          Known issues
        </h2>
        <ul className="mt-4 grid gap-3">
          {data.known_issues.map((k) => (
            <li key={k.title} className="dome-card p-5">
              <h3 className="font-semibold">{k.title}</h3>
              <p className="mt-1.5 text-[15px] leading-relaxed text-text-muted">{k.detail}</p>
              <p className="mt-3 flex gap-2 text-[15px] leading-relaxed">
                <span className="shrink-0 font-semibold text-text">Workaround</span>
                <span className="text-text-muted">{k.workaround}</span>
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="releases" className="mt-14">
        <h2 id="releases" className="text-title font-semibold">
          Releases
        </h2>
        <ol className="dome-timeline mt-5 space-y-4">
          {data.releases.map((r) => (
            <li key={r.version} className="relative">
              <span aria-hidden="true" className="dome-timeline-dot" />
              <article className="dome-card p-5 sm:p-6">
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <h3 className="text-[17px] font-semibold tracking-[-0.01em]">{r.version}</h3>
                  <p className="inline-flex flex-wrap items-center gap-x-2 text-sm text-text-faint">
                    <span className="whitespace-nowrap">{r.date}</span>
                    <span aria-hidden="true" className="hidden sm:inline">
                      ·
                    </span>
                    <span className="whitespace-nowrap">{r.state}</span>
                  </p>
                </div>
                <ul className="mt-4 space-y-2 text-[15px] leading-relaxed text-text-muted">
                  {r.changes.map((c) => (
                    <li key={c} className="flex gap-3">
                      <span aria-hidden="true" className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                      <span>{c}</span>
                    </li>
                  ))}
                </ul>
                <details className="dome-disclosure mt-3 border-t border-border">
                  <summary>
                    <span className="text-[15px] text-text-muted">Verification status</span>
                    <span aria-hidden="true" className="dome-disclosure-chevron">
                      <Icon name="chevron" size={16} strokeWidth={2} />
                    </span>
                  </summary>
                  <div className="grid gap-5 pb-4 text-[15px] leading-relaxed sm:grid-cols-2">
                    <div>
                      <p className="flex items-center gap-2 font-semibold text-success">
                        <Icon name="check" size={16} strokeWidth={2.25} />
                        Verified
                      </p>
                      <ul className="mt-2 space-y-2 text-text-muted">
                        {r.verified.map((v) => (
                          <li key={v}>{v}</li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <p className="flex items-center gap-2 font-semibold text-text">
                        <PublicIcon name="minus" size={16} strokeWidth={2.25} />
                        Not verified
                      </p>
                      <ul className="mt-2 space-y-2 text-text-muted">
                        {r.not_verified.map((v) => (
                          <li key={v}>{v}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </details>
              </article>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
