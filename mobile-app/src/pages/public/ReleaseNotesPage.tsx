/** Public release history and known issues, rendered from src/content/release-notes.json (spec §11A). */
import notes from "../../content/release-notes.json";

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

export function ReleaseNotesPage() {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Release history and known issues</h1>
        <p className="text-text-muted mt-2">{data.status.headline}</p>
        <p className="text-sm text-text-muted mt-2">{data.status.verification}</p>
      </header>

      <section className="space-y-3">
        <h2 className="text-xl font-bold">Known issues</h2>
        <ul className="space-y-2">
          {data.known_issues.map((k) => (
            <li key={k.title} className="rounded-card border border-border p-4">
              <p className="font-semibold">{k.title}</p>
              <p className="text-sm text-text-muted mt-1">{k.detail}</p>
              <p className="text-sm mt-1">
                <span className="font-medium">Workaround: </span>
                <span className="text-text-muted">{k.workaround}</span>
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="text-xl font-bold">Releases</h2>
        {data.releases.map((r) => (
          <article key={r.version} className="rounded-card border border-border p-4 space-y-2">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="font-semibold">{r.version}</h3>
              <span className="text-xs text-text-faint">
                {r.date} · {r.state}
              </span>
            </div>
            <ul className="list-disc pl-5 text-sm text-text-muted space-y-1">
              {r.changes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            <details className="text-sm">
              <summary className="cursor-pointer text-text-muted">Verification status</summary>
              <p className="mt-2 font-medium">Verified</p>
              <ul className="list-disc pl-5 text-text-muted">
                {r.verified.map((v) => (
                  <li key={v}>{v}</li>
                ))}
              </ul>
              <p className="mt-2 font-medium">Not verified</p>
              <ul className="list-disc pl-5 text-text-muted">
                {r.not_verified.map((v) => (
                  <li key={v}>{v}</li>
                ))}
              </ul>
            </details>
          </article>
        ))}
      </section>
    </div>
  );
}
