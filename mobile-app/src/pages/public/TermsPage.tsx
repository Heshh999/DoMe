/** Terms of service — DRAFT for founder and counsel review. Styled with .dome-prose; the text changes only in a legal round. */
import type { ReactNode } from "react";
import { Link } from "react-router";

import { DraftBanner } from "./DraftBanner.tsx";

/** [heading, body]: one section each, in reading order. */
const SECTIONS: ReadonlyArray<readonly [string, ReactNode]> = [
  [
    "About these terms",
    <>
      <p>
        These terms are an agreement between you and [FOUNDER: legal name of the business] (“DoMe”, “we”, “us”), which offers DoMe Remote at domeremote.com. They cover your DoMe account, the DoMe web app, the DoMe service, the DoMe program for Windows and the DoMe browser extension. Our <Link to="/privacy">Privacy notice</Link> explains what data DoMe handles and why.
      </p>
      <p>By creating an account or using DoMe, you agree to these terms. [COUNSEL: how people should accept these terms at sign-up and before paying (for example a box to tick) and what record of acceptance to keep; today DoMe’s checkout does not ask for acceptance.]</p>
    </>,
  ],
  [
    "The service",
    <p>DoMe provides software and a relay service that let you control a Windows PC you own or are authorized to control, from your own phone. You may only link PCs and pair phones that you are entitled to use.</p>,
  ],
  [
    "Who may use DoMe",
    <ul>
      <li>You must be at least [FOUNDER: minimum age; suggested default for counsel: 18] and able to make a binding contract.</li>
      <li>DoMe is offered to people in [FOUNDER: launch regions, for example the United States only]. [COUNSEL: US sanctions and export rules for a service and a download that can be reached from anywhere; checkout restricts no country today.]</li>
      <li>You may not use DoMe where the law forbids it, or after we have closed your account for breaking these terms.</li>
    </ul>,
  ],
  [
    "Accounts",
    <>
      <p>One account belongs to one person. Keep your sign-in secure. You are responsible for the phones you approve on your PCs; revoke any you no longer trust.</p>
      <p>You sign in through our sign-in provider with an email address you have confirmed. Keep that address current, because we may send important notices to it. Tell us at once if you think someone else has used your account.</p>
    </>,
  ],
  [
    "Acceptable use",
    <>
      <p>Do not use DoMe to access a computer without authorization, to interfere with others, or to circumvent restrictions of third-party services such as advertising or paywalls. DoMe performs only the actions listed in the app; it does not provide remote shell access.</p>
      <ul>
        <li>Only control a PC that you own or that its owner has allowed you to control, and pair only phones that are yours.</li>
        <li>Do not use DoMe, including Screen view, to watch or monitor other people, or to see their messages, calls or activity, without their knowledge and consent.</li>
        <li>Do not use DoMe to break the law or another service’s terms, to overload or probe the DoMe service, or to get around DoMe’s limits or security.</li>
        <li>Do not resell DoMe or share one account between several people.</li>
      </ul>
      <p>[COUNSEL: computer-misuse, wiretap and consent wording, and what DoMe should do when it is told about misuse.]</p>
    </>,
  ],
  [
    "The Windows program and browser extension",
    <ul>
      <li>You may install the DoMe program for Windows on Windows PCs that you own or are allowed to control, and use it with your own DoMe account. Setup shows licence terms for the program that say the same.</li>
      <li>DoMe includes software made by others, each under its own licence. THIRD_PARTY_NOTICES.txt and the “licenses” folder in DoMe’s folder on your PC list them with their licence texts. These terms do not limit the rights those licences give you.</li>
      <li>Some of those parts are licensed under the GNU Lesser General Public License: the pystray library and, in versions that include Screen view, the FFmpeg libraries. You may replace those parts with your own versions, and you may modify DoMe for your own use and reverse engineer it to debug such modifications. Nothing in these terms forbids that.</li>
      <li>DoMe’s own code is closed-source software. © [FOUNDER: year] [FOUNDER: legal name of the business]. [COUNSEL: whether to add the usual licence limits (personal, non-transferable, no resale or redistribution) without limiting the rights above.]</li>
      <li>About once a day the program checks for a new version. It downloads one only when you ask, and you start the installer yourself.</li>
      <li>The DoMe browser extension for YouTube follows the same rules. It is not yet published in the Chrome Web Store. [COUNSEL: whether YouTube’s or the Chrome Web Store’s terms need anything here.]</li>
      <li>The program and the extension are provided “as is”, as described under Disclaimer and liability.</li>
    </ul>,
  ],
  [
    "Free, Pro and Pro Plus",
    <>
      <p>
        The Free plan is provided without charge. Pro and Pro Plus are subscriptions with the limits shown on the <Link to="/pricing">Pricing page</Link> at the time of purchase. Prices shown before paid launch are planned, not binding.
      </p>
      <ul>
        <li>Free needs no card and never turns into a paid plan by itself.</li>
        <li>Pro and Pro Plus are not on sale yet. Pro Plus goes on sale only once the AI butler is switched on, because its extra AI requests are its only difference from Pro.</li>
        <li>Features marked “Coming” on the Pricing page are not part of a plan until they are released. [COUNSEL: how to describe features that are advertised but not yet built.]</li>
        <li>On Pro and Pro Plus you can buy AI top-ups: one-off packs of AI requests. You choose to buy each one; nothing is added or charged automatically. A top-up balance does not expire and stays with your account if you move to Free, but it can be spent only on Pro or Pro Plus. [FOUNDER: confirm that top-ups never expire.] [COUNSEL: rules for prepaid credits.]</li>
      </ul>
    </>,
  ],
  [
    "Billing and automatic renewal",
    <ul>
      <li>Prices are in US dollars and include any tax: the price shown is the amount charged. [COUNSEL, or an accountant: where DoMe must register for and collect sales tax.]</li>
      <li>You pay through Stripe, on Stripe’s own pages. Card details never reach DoMe.</li>
      <li>
        <strong>Your subscription renews automatically</strong> at the end of each monthly or yearly period, and Stripe charges your card for the next period, until you cancel.
      </li>
      <li>If a payment fails, you keep your paid plan for 7 days so you can update your card; after that your account returns to Free.</li>
      <li>[COUNSEL: the disclosures and consent needed before purchase under federal and state automatic-renewal laws, reminders before yearly renewals, and a confirmation after purchase. Today each subscribe button and each paid plan on the Pricing page say that the plan renews automatically at the price shown, explain how to cancel and link to these terms and the Privacy notice; checkout records no acceptance.]</li>
    </ul>,
  ],
  [
    "Cancelling",
    <>
      <p>You can cancel at any time; access continues until the end of the paid period and your settings are kept.</p>
      <ul>
        <li>To cancel, open Billing in the app and choose Manage subscription. It opens Stripe’s customer portal, where you can cancel, change plan or update your card. You do not need to contact us.</li>
        <li>Cancelling takes effect at the end of the current monthly or yearly period. Until then you keep your paid plan, and you are not charged again.</li>
        <li>Your account then returns to Free and nothing is deleted. For 14 days you can choose which PCs and phones stay enabled within Free’s limits; if you do not choose, the ones used most recently stay enabled. You can subscribe again at any time.</li>
      </ul>
    </>,
  ],
  [
    "Refunds",
    <>
      <p>As DoMe is set up today: cancelling stops the next renewal and you keep the paid plan until the end of the period you paid for; the unused part of that period is not refunded. Payments are not refunded automatically, and any refund is made by hand through Stripe. A refund does not by itself change your plan. There is no free trial.</p>
      <p>[FOUNDER: refund policy for monthly and yearly plans and for AI top-ups (for example, a refund on request within a set number of days after a first payment or a yearly renewal), and whether unused top-ups are refunded when an account is deleted.] [COUNSEL: confirm the wording against state automatic-renewal and consumer laws.]</p>
    </>,
  ],
  [
    "Price changes",
    <p>We may change our prices. If the price of your subscription changes, we will tell you by email or in the app at least [FOUNDER: notice period; suggested default for counsel: 30 days] before the new price applies at a renewal, and you can cancel before then. A new price never applies to a period you have already paid for. [FOUNDER: DoMe has no way yet to send customers notices by email or in the app; decide how notices will be sent before launch.] [COUNSEL: notice rules for price changes, especially for yearly plans.]</p>,
  ],
  [
    "Screen view (beta)",
    <ul>
      <li>Screen view shows a live picture of your PC on your phone. It is a beta: it may change, have errors, be limited or be withdrawn. It is switched on only on our test site so far.</li>
      <li>It works only after the PC’s owner allows it in a DoMe window on that PC, and while it runs the PC shows a “Screen view on” bar on every screen.</li>
      <li>Do not use Screen view to watch someone who is using the PC without their knowledge, or to view other people’s messages or calls without their consent.</li>
      <li>Free includes 10 minutes of Screen view per 5-hour window. Paid plans have no minute allowance, but fair-use limits protect the service (for example a monthly limit on relay connections per account). [COUNSEL: whether this fair-use wording and example are enough.]</li>
    </ul>,
  ],
  [
    "The AI butler",
    <ul>
      <li>The AI butler is optional. It is off until you turn it on and agree that requests DoMe cannot work out by itself are sent to our AI provider, as the Privacy notice explains. It is not switched on for DoMe accounts yet.</li>
      <li>
        <strong>The AI can make mistakes.</strong> It can mishear or misunderstand you, pick the wrong action or give a wrong answer. Check what it shows before you rely on it.
      </li>
      <li>It only proposes up to three steps from DoMe’s fixed list of actions, and your phone checks and shows them. It cannot see your screen, type text, open websites or files, buy anything or change DoMe’s permissions.</li>
      <li>Simple actions such as pause or volume run right away; opening apps, full screen and locking wait for your tap on Run; power actions and closing apps ask you to confirm. You can turn on “Ask before every action” in Settings. You confirm each disruptive action and remain responsible for what you ask your PC to do.</li>
      <li>AI requests count against your plan’s allowance. Everyday commands DoMe understands by itself never count.</li>
      <li>[COUNSEL: whether a stronger AI disclaimer is needed, given that simple actions run without a tap, and whether state chatbot-disclosure rules apply.]</li>
    </ul>,
  ],
  [
    "Availability and limits",
    <p>DoMe depends on your internet connection, your PC and third-party platforms (Windows, browsers, YouTube). Features may be limited or unavailable when those change. Technical limits protect the service, for example on how many commands a phone can send each minute; we may suspend accounts that keep exceeding them or that violate these terms. [COUNSEL: whether these limits must be published; the Pricing page shows one of them, 120 commands a minute.]</p>,
  ],
  [
    "Suspension and ending your account",
    <>
      <p>You may stop using DoMe and delete your account at any time, in the app under Settings, Delete account. Deleting it cancels any subscription at once, with no automatic refund, and the account is erased 30 days later, as the Privacy notice explains. You can also ask through the support form or at [FOUNDER: contact email].</p>
      <p>We may suspend or close an account that breaks these terms, keeps exceeding those technical limits or puts other people or the service at risk. [COUNSEL: notice before suspension, and refunds when we close a paid account.] If we stop offering DoMe, we will tell you at least [FOUNDER: notice period] ahead. [FOUNDER: refund of unused paid time if DoMe closes.]</p>
    </>,
  ],
  [
    "Disclaimer and liability",
    <p>DoMe is provided “as is”. Remote actions such as shutting down a PC can interrupt your work; you confirm each disruptive action and remain responsible for what you ask your PC to do. To the extent permitted by law, our liability is limited to the amount you paid in the twelve months before a claim. [COUNSEL: review against applicable consumer law, including New Jersey’s Truth-in-Consumer Contract, Warranty and Notice Act and its treatment of “to the extent permitted by law”; for Free users this cap is zero.]</p>,
  ],
  [
    "Governing law",
    <p>These terms are governed by the laws of [FOUNDER: governing law (New Jersey?)]. [COUNSEL: confirm, and whether customers keep the consumer protections of their own state.]</p>,
  ],
  [
    "Disputes",
    <p>If you have a problem with DoMe, please contact us first so we can try to fix it. [COUNSEL: how disputes are resolved after that: courts and venue or arbitration, any class-action waiver, a small-claims option, and how long to allow for the informal step.]</p>,
  ],
  [
    "Changes to these terms",
    <p>We may update these terms with notice in the app or by email. [FOUNDER: DoMe has no way yet to send customers notices by email or in the app; decide how notices will be sent before launch.] The new version is posted on this page with a new effective date. If you do not agree to a change, you can cancel and stop using DoMe before it takes effect. [COUNSEL: how much notice to give, and whether continuing to use DoMe can count as accepting a change.]</p>,
  ],
  [
    "Contact",
    <p>
      [FOUNDER: legal name of the business], [FOUNDER: postal address], [FOUNDER: contact email]. Signed-in users can also use the <Link to="/support">support form</Link>.
    </p>,
  ],
  [
    "Open items for the founder and counsel",
    <ul>
      <li>Business identity: legal name, entity type, postal address, contact email and the copyright year. The Windows installer’s licence terms and third-party notices carry the same placeholders.</li>
      <li>Governing law, venue and dispute resolution; minimum age; launch regions.</li>
      <li>Refund, trial and price-change policy; whether top-ups ever expire.</li>
      <li>Disclosures before purchase: a step that records acceptance at sign-up and at checkout (the renewal statement and the link to these terms are now shown).</li>
      <li>Notices: a way to send customers notices (by email or in the app) is not built; it is needed for price-change, terms-change and renewal notices.</li>
      <li>Sales tax: prices include tax, Stripe Tax is off and DoMe is the seller.</li>
      <li>Advertising claims: paid Screen view described as “without the Free plan’s time limit (fair-use limits apply)” next to relay limits; everyday commands described as free and “never counted against AI limits” next to a fair-use limit of 120 a minute; features shown as “Coming” or described before they are built; AI requests described as minutes.</li>
      <li>Windows packaging: an Inno Setup commercial licence before paid launch; code signing uses the founder’s individual identity while Setup names the publisher “DoMe”; the H.264 question for Screen view (docs/legal/h264-counsel-email.md).</li>
      <li>Relationship to the privacy notice and to the licence terms shown in Setup.</li>
    </ul>,
  ],
];

export function TermsPage() {
  return (
    <article className="dome-prose">
      <header className="dome-page-intro animate-rise">
        <p className="dome-eyebrow">Legal</p>
        <h1 className="mt-3 text-h1 font-bold">Terms of service</h1>
        <DraftBanner />
        <p className="mt-4">Effective date: [FOUNDER: date]. Items marked [COUNSEL] need a lawyer’s judgement; nothing in this draft is a legal conclusion.</p>
      </header>
      {SECTIONS.map(([h, body]) => (
        <section key={h}>
          <h2>{h}</h2>
          {body}
        </section>
      ))}
    </article>
  );
}
