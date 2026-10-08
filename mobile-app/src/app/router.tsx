import { createBrowserRouter } from "react-router";

import { AppsPage } from "../pages/app/AppsPage.tsx";
import { BillingPage } from "../pages/app/BillingPage.tsx";
import { CommandPage } from "../pages/app/CommandPage.tsx";
import { DashboardPage } from "../pages/app/DashboardPage.tsx";
import { DevicesPage } from "../pages/app/DevicesPage.tsx";
import { LinkPage } from "../pages/app/LinkPage.tsx";
import { MorePage } from "../pages/app/MorePage.tsx";
import { PairPage } from "../pages/app/PairPage.tsx";
import { PairRedirect } from "../pages/app/PairRedirect.tsx";
import { RemotePage } from "../pages/app/RemotePage.tsx";
import { RoutinesPage } from "../pages/app/RoutinesPage.tsx";
import { SettingsPage } from "../pages/app/SettingsPage.tsx";
import { DownloadPage } from "../pages/public/DownloadPage.tsx";
import { FaqPage } from "../pages/public/FaqPage.tsx";
import { LandingPage } from "../pages/public/LandingPage.tsx";
import { NotFoundPage } from "../pages/public/NotFoundPage.tsx";
import { PricingPage } from "../pages/public/PricingPage.tsx";
import { PrivacyPage } from "../pages/public/PrivacyPage.tsx";
import { SupportPage } from "../pages/public/SupportPage.tsx";
import { TermsPage } from "../pages/public/TermsPage.tsx";
import { AppShell, RequireSession } from "./AppShell.tsx";
import { PublicLayout } from "./PublicLayout.tsx";

export const router = createBrowserRouter([
  {
    element: <PublicLayout />,
    children: [
      { path: "/", element: <LandingPage /> },
      { path: "/pricing", element: <PricingPage /> },
      { path: "/faq", element: <FaqPage /> },
      { path: "/download", element: <DownloadPage /> },
      { path: "/support", element: <SupportPage /> },
      { path: "/privacy", element: <PrivacyPage /> },
      { path: "/terms", element: <TermsPage /> },
    ],
  },
  {
    path: "/app",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "remote", element: <RemotePage /> },
      { path: "command", element: <CommandPage /> },
      { path: "apps", element: <AppsPage /> },
      { path: "routines", element: <RoutinesPage /> },
      { path: "devices", element: <DevicesPage /> },
      { path: "devices/pair", element: <PairPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "billing", element: <BillingPage /> },
      { path: "more", element: <MorePage /> },
    ],
  },
  { path: "/pair", element: <PairRedirect /> },
  {
    path: "/link",
    element: (
      <RequireSession>
        <LinkPage />
      </RequireSession>
    ),
  },
  { path: "*", element: <NotFoundPage /> },
]);
