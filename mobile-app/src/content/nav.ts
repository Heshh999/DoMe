/** The public website's navigation: the header links (the phone menu shows the same) and the footer groups. */
export const NAV: ReadonlyArray<{ to: string; label: string }> = [
  { to: "/demo", label: "Demo" },
  { to: "/pricing", label: "Pricing" },
  { to: "/faq", label: "FAQ" },
  { to: "/download", label: "Download" },
  { to: "/support", label: "Support" },
];

export const FOOTER: ReadonlyArray<{ title: string; links: ReadonlyArray<{ to: string; label: string }> }> = [
  {
    title: "Product",
    links: [
      { to: "/demo", label: "Demo" },
      { to: "/pricing", label: "Pricing" },
      { to: "/download", label: "Download" },
      { to: "/app", label: "Open the app" },
    ],
  },
  {
    title: "Help",
    links: [
      { to: "/faq", label: "FAQ" },
      { to: "/support", label: "Support" },
      { to: "/release-notes", label: "Release notes" },
    ],
  },
  {
    title: "Legal",
    links: [
      { to: "/privacy", label: "Privacy" },
      { to: "/terms", label: "Terms" },
    ],
  },
];
