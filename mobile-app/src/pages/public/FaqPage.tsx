/**
 * Frequently asked questions (src/content/faq.tsx), grouped by topic: each group is a card of disclosure
 * rows, with a sticky jump list on wide screens. The page draws its own container (it is a "wide" page).
 */
import { Link } from "react-router";

import { Icon } from "../../components/icons.tsx";
import { FAQ_GROUPS, type FaqGroup } from "../../content/faq.tsx";
import { PublicIcon } from "./publicIcons.tsx";

function GroupIcon({ name }: { name: FaqGroup["icon"] }) {
  if (name === "shield" || name === "ask") return <Icon name={name} size={20} />;
  return <PublicIcon name={name} size={20} />;
}

const LINK = "font-semibold text-accent underline decoration-accent/35 underline-offset-[3px] hover:decoration-current";

export function FaqPage() {
  return (
    <div className="mx-auto max-w-6xl px-5 py-12 lg:px-8 lg:py-16">
      <header className="dome-page-intro animate-rise">
        <p className="dome-eyebrow">FAQ</p>
        <h1 className="mt-3 text-h1 font-bold">Questions, answered plainly.</h1>
        <p className="mt-4 max-w-2xl text-lg text-text-muted">What DoMe does, what it never does, and what is still on the way.</p>
      </header>

      <div className="grid gap-10 lg:grid-cols-[14rem_1fr] lg:gap-14">
        <nav aria-label="Topics" className="dome-jump hidden lg:block">
          <ul className="sticky top-24 space-y-0.5">
            {FAQ_GROUPS.map((g) => (
              <li key={g.id}>
                <a href={`#${g.id}`}>
                  <span className="dome-well dome-well-xs mr-3">
                    <GroupIcon name={g.icon} />
                  </span>
                  {g.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="space-y-12">
          {FAQ_GROUPS.map((g) => (
            <section key={g.id} id={g.id} aria-labelledby={`faq-${g.id}`}>
              <div className="flex items-center gap-3">
                <span className="dome-well">
                  <GroupIcon name={g.icon} />
                </span>
                <h2 id={`faq-${g.id}`} className="text-title font-semibold">
                  {g.title}
                </h2>
                {g.status ? <span className="dome-chip dome-chip-quiet">{g.status}</span> : null}
              </div>
              <div className="dome-card dome-hairlines mt-4 px-5 sm:px-6">
                {g.entries.map((e) => (
                  <details key={e.q} className="dome-disclosure">
                    <summary>
                      <span className="text-[15px] sm:text-base">{e.q}</span>
                      <span aria-hidden="true" className="dome-disclosure-chevron">
                        <Icon name="chevron" size={16} strokeWidth={2} />
                      </span>
                    </summary>
                    <div className="space-y-3 pb-5 text-[15px] leading-relaxed text-text-muted">{typeof e.a === "string" ? <p>{e.a}</p> : e.a}</div>
                  </details>
                ))}
              </div>
            </section>
          ))}
          <p className="text-[15px] text-text-muted">
            Something else? See{" "}
            <Link to="/support" className={LINK}>
              Support
            </Link>{" "}
            or the{" "}
            <Link to="/release-notes" className={LINK}>
              release notes and known issues
            </Link>
            .
          </p>
        </div>
      </div>
    </div>
  );
}
