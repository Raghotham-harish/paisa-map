import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { fmtDate } from "./components/SubscriptionPanel";
import { useAuth } from "./lib/auth";
import { WorkspaceProvider, useSyncProjectFromUrl } from "./lib/workspace";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import { CompanySwitcher } from "./components/CompanySwitcher";
import SignIn from "./pages/SignIn";
import Dashboard from "./pages/Dashboard";
import MapWorkspace from "./pages/MapWorkspace";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import ProjectWizard from "./pages/ProjectWizard";
import SavedLocations from "./pages/SavedLocations";
import Activity from "./pages/Activity";
import Billing from "./pages/Billing";
import Reports from "./pages/Reports";
import CustomerData from "./pages/CustomerData";
import Connections from "./pages/Connections";
import Forecast from "./pages/Forecast";
import ApiKeys from "./pages/ApiKeys";
import CompanySettings from "./pages/CompanySettings";
import InviteAccept from "./pages/InviteAccept";
import SharedProject from "./pages/SharedProject";

function WorkspaceUrlSync() {
  useSyncProjectFromUrl();
  return null;
}

// Grouped per the dashboard audit's N6/A7: 12 flat items didn't scale and
// gave no sense of what's what. "Workspace" = explore/build, "Data" = feed
// your own data in, "Account" = you and the relationship with PaisaMap.
// Credits folded into Billing (N5: "two nav items that are both money").
// `desktop`: a one-off setup task (uploading a spreadsheet, linking Google,
// developer keys) — left out of the phone menu; the page still works if opened.
const NAV_SECTIONS: { label: string; items: { to: string; label: string; end?: boolean; icon: string; desktop?: boolean }[] }[] = [
  {
    label: "Workspace",
    items: [
      { to: "/", label: "Dashboard", end: true, icon: "ti-layout-dashboard" },
      { to: "/map", label: "Map workspace", icon: "ti-map-2" },
      { to: "/projects", label: "Projects", icon: "ti-briefcase" },
      { to: "/locations", label: "Saved Locations", icon: "ti-map-pin" },
      { to: "/forecast", label: "Forecast", icon: "ti-trending-up" },
      { to: "/reports", label: "Reports", icon: "ti-file-text" },
    ],
  },
  {
    label: "Data",
    items: [
      { to: "/customer-data", label: "Store Data", icon: "ti-upload", desktop: true },
      { to: "/connections", label: "Connections", icon: "ti-plug", desktop: true },
    ],
  },
  {
    label: "Account",
    items: [
      { to: "/company", label: "Company", icon: "ti-building" },
      { to: "/activity", label: "Activity", icon: "ti-activity" },
      { to: "/billing", label: "Billing", icon: "ti-receipt" },
      { to: "/api-keys", label: "API Keys", icon: "ti-key", desktop: true },
    ],
  },
];

const DESKTOP_PAGES = NAV_SECTIONS.flatMap((s) => s.items).filter((i) => i.desktop).map((i) => i.to);

// P6: pages every signed-in account keeps when the dashboard paywall is on (the map,
// saved locations and the account pages). Everything else needs a trial or a plan.
const FREE_PAGES = ["/map", "/locations", "/company", "/activity", "/billing", "/api-keys"];
const isFreePage = (to: string) => FREE_PAGES.includes(to);

/** Stands in for a paid page when the selected company has no trial or plan. The
 *  server refuses the page's data anyway (402); this explains it instead of showing
 *  a page full of errors. */
function Paid({ children }: { children: JSX.Element }) {
  const { user } = useAuth();
  const d = user?.dashboard;
  if (!d || !d.paywall || d.allowed) return children;
  return (
    <div className="card paywall-card" data-testid="paywall">
      <p style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 700 }}>
        <i className="ti ti-lock" aria-hidden="true" /> Projects, forecasts and reports are part of a paid plan
      </p>
      <p style={{ margin: "0 0 14px", fontSize: 14 }}>
        Choose a plan to plan expansions, forecast revenue, compare sites and generate reports — a company
        that hasn't had one yet can start with a free 7-day trial, no card needed. Anything this company
        already saved is kept and comes back as soon as it has a plan.
      </p>
      <p style={{ margin: "0 0 16px", fontSize: 13, color: "var(--ink-soft)" }}>
        The map and your saved locations stay free.
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <Link className="btn" to="/billing">See plans and the free trial</Link>
        <Link className="btn secondary" to="/map">Open the map</Link>
      </div>
    </div>
  );
}

export default function App() {
  const { user, loading, signOut } = useAuth();
  const location = useLocation();
  const bleed = location.pathname === "/map";
  // The forecast + compare dashboards need more than the default 880px column.
  const wide = location.pathname === "/forecast";
  // Phones: the sidenav is a slide-in drawer behind the menu button (see
  // styles.css "Phones"). Closes on every navigation and on Escape.
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [location.pathname]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  // E2 — a project's public share link works for anyone with the URL, no
  // session at all, so this is checked before the loading/auth gates below
  // rather than as an authenticated <Route> (same reasoning as InviteAccept,
  // one step further since this page never needs even a signed-out identity).
  if (location.pathname.startsWith("/projects/shared/")) return <SharedProject />;

  if (loading) return <div className="loading">Loading…</div>;
  if (!user) {
    // An invite link (D1) must work before sign-in too — rendered here,
    // outside WorkspaceProvider, so InviteAccept never depends on
    // useWorkspace(). Once sign-in completes, `user` becomes truthy and the
    // normal authenticated <Route path="/invite/:token"> below takes over.
    if (location.pathname.startsWith("/invite/")) return <InviteAccept />;
    return <SignIn />;
  }

  const initial = (user.name || user.email || "?").trim()[0]?.toUpperCase() || "?";
  const paywalled = !!user.dashboard?.paywall && !user.dashboard.allowed;

  return (
    <WorkspaceProvider>
      <WorkspaceUrlSync />
      <div className={navOpen ? "shell nav-open" : "shell"}>
        <header className="mobile-bar">
          <button className="mobile-menu-btn" aria-label="Open menu" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>
            <i className="ti ti-menu-2" aria-hidden="true" />
          </button>
          <a className="brand" href="/">
            <img src="/assets/logo-horizontal-v2.svg" alt="PaisaMaps" height="22" />
          </a>
          <div className="avatar avatar-fallback mobile-bar-avatar" aria-hidden="true">{initial}</div>
        </header>
        <div className="nav-backdrop" onClick={() => setNavOpen(false)} aria-hidden="true" />
        <aside className="sidenav" aria-label="Main menu">
          <button className="mobile-menu-btn nav-close" aria-label="Close menu" onClick={() => setNavOpen(false)}>
            <i className="ti ti-x" aria-hidden="true" />
          </button>
          <a className="brand" href="/">
            <img src="/assets/logo-horizontal-v2.svg" alt="PaisaMaps" height="24" />
          </a>
          <nav>
            {NAV_SECTIONS.map((section) => (
              <div className="sidenav-section" key={section.label}>
                <div className="sidenav-section-label">{section.label}</div>
                {section.items.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.end}
                           className={({ isActive }) => [isActive ? "active" : "", item.desktop ? "desktop-only-nav" : ""].join(" ").trim()}>
                    <i className={`ti ${item.icon}`} aria-hidden="true" />
                    <span>{item.label}</span>
                    {paywalled && !isFreePage(item.to) && (
                      <i className="ti ti-lock nav-lock" aria-label="Needs a plan" data-testid="nav-lock" />
                    )}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>

          <div className="sidenav-user">
            {user.picture_url ? (
              <img className="avatar" src={user.picture_url} alt="" />
            ) : (
              <div className="avatar avatar-fallback">{initial}</div>
            )}
            <div className="user-info">
              <div className="user-name">{user.name || user.email}</div>
              <span className={`pill plan-${user.plan}`}>{user.tier_label ?? user.plan}</span>
            </div>
          </div>
          <a className="back" href="/">
            <i className="ti ti-arrow-left" aria-hidden="true" /> Back to map
          </a>
          <a
            className="back"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              signOut();
            }}
          >
            <i className="ti ti-logout" aria-hidden="true" /> Sign out
          </a>
        </aside>
        <div className="workspace-col">
          {!bleed && (
            <header className="topbar">
              <CompanySwitcher />
              <ProjectSwitcher />
            </header>
          )}
          <main className={bleed ? "content content-bleed" : wide ? "content content-wide" : "content"}>
            {DESKTOP_PAGES.includes(location.pathname) && (
              <div className="desktop-hint" role="note">
                <i className="ti ti-device-desktop" aria-hidden="true" /> This page is easier on a computer.
              </div>
            )}
            {/* A failed renewal (P13). The server already refuses paid actions once locked;
                this is only the explanation. Billing stays usable so it can be fixed. */}
            {user.account?.past_due && !user.account.locked && location.pathname !== "/billing" && (
              <div className="past-due-banner" role="alert" data-testid="past-due-banner">
                <i className="ti ti-alert-triangle" aria-hidden="true" />
                <span>Your last payment failed. Update your payment method by {fmtDate(user.account.lock_at)} to keep using paid features.</span>
                <Link to="/billing">Fix in Billing</Link>
              </div>
            )}
            {user.account?.locked && location.pathname !== "/billing" && (
              <div className="lock-overlay" data-testid="lock-overlay">
                <div className="card lock-card" role="alertdialog" aria-labelledby="lock-title">
                  <p id="lock-title" style={{ margin: "0 0 8px", fontSize: 18, fontWeight: 700 }}>Your account is paused</p>
                  <p style={{ margin: "0 0 16px", fontSize: 13.5 }}>
                    This month's plan renewal is still unpaid. Your projects, saved locations and reports are safe —
                    everything comes back the moment a payment goes through.
                  </p>
                  <Link className="btn" to="/billing">Update payment in Billing</Link>
                </div>
              </div>
            )}
            <Routes>
              <Route path="/" element={<Paid><Dashboard /></Paid>} />
              <Route path="/map" element={<MapWorkspace />} />
              <Route path="/projects" element={<Paid><Projects /></Paid>} />
              <Route path="/projects/new" element={<Paid><ProjectWizard /></Paid>} />
              <Route path="/projects/:id" element={<Paid><ProjectDetail /></Paid>} />
              <Route path="/locations" element={<SavedLocations />} />
              <Route path="/customer-data" element={<Paid><CustomerData /></Paid>} />
              <Route path="/forecast" element={<Paid><Forecast /></Paid>} />
              <Route path="/connections" element={<Paid><Connections /></Paid>} />
              <Route path="/reports" element={<Paid><Reports /></Paid>} />
              <Route path="/company" element={<CompanySettings />} />
              <Route path="/activity" element={<Activity />} />
              <Route path="/credits" element={<Navigate to="/billing" replace />} />
              <Route path="/billing" element={<Billing />} />
              <Route path="/api-keys" element={<ApiKeys />} />
              <Route path="/invite/:token" element={<InviteAccept />} />
            </Routes>
          </main>
        </div>
      </div>
    </WorkspaceProvider>
  );
}
