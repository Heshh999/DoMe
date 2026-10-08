/** Terms of service — DRAFT for founder review. */
import { DraftBanner } from "./DraftBanner.tsx";

export function TermsPage() {
  return (
    <article className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Terms of service</h1>
        <DraftBanner />
      </header>
      {[
        ["The service", "DoMe provides software and a relay service that let you control a Windows PC you own or are authorised to control, from your own phone. You may only link PCs and pair phones that you are entitled to use."],
        ["Accounts", "One account belongs to one person. Keep your sign-in secure. You are responsible for the phones you approve on your PCs; revoke any you no longer trust."],
        ["Acceptable use", "Do not use DoMe to access a computer without authorisation, to interfere with others, or to circumvent restrictions of third-party services such as advertising or paywalls. DoMe performs only the actions listed in the app; it does not provide remote shell access."],
        ["Free and Pro", "The Free plan is provided without charge. Pro is a subscription with the limits shown on the Pricing page at the time of purchase. Prices shown before paid launch are planned, not binding. You can cancel at any time; access continues until the end of the paid period and your settings are kept. [FOUNDER: refund policy, taxes, trial terms.]"],
        ["Availability and limits", "DoMe depends on your internet connection, your PC and third-party platforms (Windows, browsers, YouTube). Features may be limited or unavailable when those change. We publish abuse limits for manual controls and may suspend accounts that exceed them or violate these terms."],
        ["Disclaimer and liability", "DoMe is provided “as is”. Remote actions such as shutting down a PC can interrupt your work; you confirm each disruptive action and remain responsible for what you ask your PC to do. To the extent permitted by law, our liability is limited to the amount you paid in the twelve months before a claim. [FOUNDER: review against applicable consumer law.]"],
        ["Changes and termination", "We may update these terms with notice in the app or by email. You may stop using DoMe and delete your account at any time."],
        ["Open items for founder review", "Governing law and venue; business identity; refund and dispute policy; consumer-protection wording for launch regions; relationship to the privacy notice."],
      ].map(([h, p]) => (
        <section key={h} className="space-y-1 text-sm text-text-muted">
          <h2 className="text-lg font-semibold text-text">{h}</h2>
          <p>{p}</p>
        </section>
      ))}
    </article>
  );
}
