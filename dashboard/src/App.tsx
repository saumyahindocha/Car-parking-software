import { useEffect, useState } from 'react';
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { api } from './api';
import { AuthProvider, useAuth } from './auth';
import { ago, time } from './format';
import { SiteProvider, useSite } from './site';
import { Dot, ToastProvider, useToast } from './ui';
import { LiveProvider, useLive } from './useLive';
import LoginPage from './pages/Login';
import LivePage from './pages/Live';
import ReviewPage from './pages/Review';
import CashPage from './pages/Cash';
import VehiclesPage from './pages/Vehicles';
import DefaultersPage from './pages/Defaulters';
import ReportsPage from './pages/Reports';
import ConfigPage from './pages/Config';
import AlertsPage from './pages/Alerts';
import AuditPage from './pages/Audit';
import PrivacyPage from './pages/Privacy';

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <BrowserRouter>
          <Gate />
        </BrowserRouter>
      </AuthProvider>
    </ToastProvider>
  );
}

function Gate() {
  const { user } = useAuth();
  if (!user) return <LoginPage />;
  return (
    <LiveProvider enabled={!!user}>
      <SiteProvider>
        <Shell />
      </SiteProvider>
    </LiveProvider>
  );
}

const ALERT_TITLES: Record<string, string> = {
  CAMERA_STALL: 'Camera stalled',
  INTERNET_DOWN: 'Internet down',
  EXIT_UNPAID: 'Unpaid exit',
  WRONG_WAY: 'Wrong-way vehicle',
  OFFLINE_CLAIM_STALE: 'Stale offline UPI claim',
};

function Shell() {
  const { user, isAdmin, logout } = useAuth();
  const { live } = useSite();
  const { notify } = useToast();
  const [openAlerts, setOpenAlerts] = useState<number | null>(null);
  const loc = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [loc.pathname]);

  const loadAlerts = () =>
    api
      .alerts({ open_only: true, hours: 24 })
      .then((a) => setOpenAlerts(a.length))
      .catch(() => undefined);
  useEffect(() => {
    void loadAlerts();
    const id = setInterval(loadAlerts, 30000);
    return () => clearInterval(id);
  }, []);

  const { connected } = useLive(['alert'], (m) => {
    const d = m.data as { kind?: string; message?: string; gate_id?: string };
    const kind = d.kind ?? 'ALERT';
    const crit = kind === 'CAMERA_STALL' || kind === 'INTERNET_DOWN';
    notify(
      <span>
        {d.message ?? kind}
        {d.gate_id ? <span className="muted"> · {d.gate_id}</span> : null}
      </span>,
      crit ? 'bad' : 'warn',
      { title: ALERT_TITLES[kind] ?? kind.replace(/_/g, ' '), sticky: crit },
    );
    setOpenAlerts((n) => (n ?? 0) + 1);
  });

  const rc = live?.review_counts;
  const reviewTotal = rc ? rc.unread + rc.review + rc.orphans + rc.offline_claims + rc.disputes : undefined;
  const net = live?.internet?.online;
  const gw = live?.gateway?.online;
  const offlineDevices = live?.devices.filter((d) => !d.online).length ?? 0;

  const nav: { to: string; label: string; badge?: number; admin?: boolean }[] = [
    { to: '/', label: 'Live' },
    { to: '/review', label: 'Review queue', badge: reviewTotal },
    { to: '/cash', label: 'Cash control' },
    { to: '/vehicles', label: 'Vehicle ledger' },
    { to: '/defaulters', label: 'Defaulters' },
    { to: '/reports', label: 'Reports' },
    { to: '/alerts', label: 'Alerts', badge: openAlerts ?? undefined },
    { to: '/config', label: 'Configuration' },
    { to: '/privacy', label: 'Privacy', admin: true },
    { to: '/audit', label: 'Audit log', admin: true },
  ];

  return (
    <div className={`shell ${navOpen ? 'nav-open' : ''}`}>
      <header className="topbar">
        <button className="icon-btn nav-toggle" aria-label="Menu" onClick={() => setNavOpen((v) => !v)}>
          ☰
        </button>
        <div className="brand">
          <span className="logo">P</span>
          <span>Parking Control</span>
        </div>
        <div className="topbar-status">
          <span className="pill" title={connected ? 'Live updates connected' : 'Live updates reconnecting…'}>
            <Dot ok={connected} /> Live
          </span>
          <span className="pill" title={live?.internet?.since ? `since ${time(live.internet.since)}` : 'no watchdog data yet'}>
            <Dot ok={net} /> Internet
          </span>
          <span className="pill" title={`Payment gateway: ${live?.gateway?.name ?? '?'}`}>
            <Dot ok={gw} /> UPI gateway
          </span>
          {offlineDevices > 0 && (
            <NavLink to="/" className="pill pill-bad">
              {offlineDevices} device{offlineDevices > 1 ? 's' : ''} offline
            </NavLink>
          )}
          {!!openAlerts && (
            <NavLink to="/alerts" className="pill pill-warn">
              {openAlerts} open alert{openAlerts > 1 ? 's' : ''}
            </NavLink>
          )}
          {live && <span className="muted small">updated {ago(live.now)}</span>}
        </div>
        <div className="topbar-user">
          <span>
            {user?.name} <span className="muted">· {user?.role}</span>
          </span>
          <button className="btn btn-sm" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <nav className="sidebar">
        {nav
          .filter((n) => !n.admin || isAdmin)
          .map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
              <span>{n.label}</span>
              {!!n.badge && <span className="nav-badge">{n.badge}</span>}
            </NavLink>
          ))}
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<LivePage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/cash" element={<CashPage />} />
          <Route path="/vehicles" element={<VehiclesPage />} />
          <Route path="/vehicles/:id" element={<VehiclesPage />} />
          <Route path="/defaulters" element={<DefaultersPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/alerts" element={<AlertsPage onChange={loadAlerts} />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/privacy" element={isAdmin ? <PrivacyPage /> : <Navigate to="/" />} />
          <Route path="/audit" element={isAdmin ? <AuditPage /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
