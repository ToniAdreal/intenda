import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectWs, type Briefing as BriefingData, type Me, type OnboardingStatus } from './lib/api';
import { AppCtx, type ComposerOpts } from './lib/appctx';
import { ToastProvider, navigate, useRoute } from './lib/ui';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import Composer from './components/Composer';
import { ForgotPasswordPage, LoginPage, RegisterPage } from './components/Auth';
import { PrivacyPage, TermsPage } from './components/Legal';
import LandingPage from './components/Landing';
import Onboarding from './components/Onboarding';
import Briefing from './components/Briefing';
import Delegate from './components/Delegate';
import TaskProgress from './components/TaskProgress';
import Inbox from './components/Inbox';
import Review from './components/Review';
import Activity from './components/Activity';
import Automations from './components/Automations';
import Archive from './components/Archive';
import Settings from './components/Settings';

type Boot = 'loading' | 'anon' | 'onboarding' | 'ready';
type Theme = 'day' | 'night';

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('intenda-theme');
    if (saved === 'day' || saved === 'night') return saved;
  } catch { /* ignore */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'night' : 'day';
}

export default function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

function Shell() {
  const route = useRoute();
  const [boot, setBoot] = useState<Boot>('loading');
  const [me, setMe] = useState<Me | null>(null);
  const [theme, setThemeState] = useState<Theme>(initialTheme);
  const [badges, setBadges] = useState({ review: 0, tasks: 0, decisions: 0, inbox_unread: 0 });
  const [environment, setEnvironment] = useState<'demo' | 'live'>('demo');
  const [agentAvailable, setAgentAvailable] = useState(false);
  const [composer, setComposer] = useState<ComposerOpts | null>(null);
  const badgeTimer = useRef<number | undefined>(undefined);

  const applyTheme = useCallback((t: Theme) => {
    document.documentElement.dataset.theme = t;
    try {
      localStorage.setItem('intenda-theme', t);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    applyTheme(theme);
  }, [theme, applyTheme]);

  const setTheme = useCallback(
    (t: Theme) => {
      setThemeState(t);
      applyTheme(t);
    },
    [applyTheme],
  );

  const refreshBadges = useCallback(() => {
    api<BriefingData>('/api/v1/briefing')
      .then((b) => {
        setBadges({
          review: b.badges.review,
          tasks: b.badges.tasks_active,
          decisions: b.badges.decisions,
          inbox_unread: b.badges.unread,
        });
        setEnvironment(b.environment === 'live' ? 'live' : 'demo');
      })
      .catch(() => {});
    api<{ available: boolean }>('/api/v1/agent/status')
      .then((s) => setAgentAvailable(s.available))
      .catch(() => {});
  }, []);

  const bootstrap = useCallback(async () => {
    try {
      const meData = await api<{ user: Me }>('/api/v1/auth/me');
      setMe(meData.user);
      // Server appearance is authoritative once signed in (spec 1.8).
      api<{ settings: { appearance: { mode: string } } }>('/api/v1/settings')
        .then((s) => {
          const mode = s.settings.appearance.mode;
          if (mode === 'day' || mode === 'night') setTheme(mode);
        })
        .catch(() => {});
      const ob = await api<OnboardingStatus>('/api/v1/onboarding/status');
      if (ob.state === 'READY') {
        setBoot('ready');
        refreshBadges();
      } else {
        setBoot('onboarding');
      }
    } catch {
      setMe(null);
      setBoot('anon');
    }
  }, [refreshBadges, setTheme]);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  // Live updates: any task/review/sync event refreshes badges.
  useEffect(() => {
    if (boot !== 'ready') return;
    const close = connectWs((event) => {
      if (typeof event?.type === 'string' && event.type !== 'server.heartbeat') {
        window.clearTimeout(badgeTimer.current);
        badgeTimer.current = window.setTimeout(refreshBadges, 400);
      }
    });
    return close;
  }, [boot, refreshBadges]);

  const logout = useCallback(async () => {
    try {
      await api('/api/v1/auth/logout', { method: 'POST', body: {} });
    } catch { /* session already gone */ }
    setMe(null);
    setBoot('anon');
    navigate('/login');
  }, []);

  const openComposer = useCallback((opts?: ComposerOpts) => setComposer(opts ?? { mode: 'new' }), []);

  // Public legal pages: reachable without signing in (OAuth verification, footer links).
  if (route === '/privacy') return <PrivacyPage />;
  if (route === '/terms') return <TermsPage />;

  if (boot === 'loading') {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <div className="skeleton" style={{ height: 180 }} />
        </div>
      </div>
    );
  }

  if (boot === 'anon') {
    if (route === '/register') return <RegisterPage onAuthed={bootstrap} />;
    if (route === '/forgot-password') return <ForgotPasswordPage />;
    // Public landing page at / so signed-out visitors (and OAuth reviewers)
    // see what the product is before being asked to sign in.
    if (route === '/')
      return (
        <LandingPage
          onSignIn={() => navigate('/login')}
          onRegister={() => navigate('/register')}
        />
      );
    return <LoginPage onAuthed={bootstrap} />;
  }

  if (boot === 'onboarding' || !me) {
    return <Onboarding onReady={bootstrap} />;
  }

  let page: React.ReactNode;
  if (route === '/briefing' || route === '/') page = <Briefing />;
  else if (route === '/delegate') page = <Delegate />;
  else if (route === '/tasks') page = <TaskProgress />;
  else if (route.startsWith('/tasks/')) page = <TaskProgress taskId={route.slice('/tasks/'.length)} />;
  else if (route === '/inbox') page = <Inbox />;
  else if (route === '/review') page = <Review />;
  else if (route === '/activity') page = <Activity />;
  else if (route === '/automations') page = <Automations />;
  else if (route === '/archive') page = <Archive />;
  else if (route === '/settings') page = <Settings />;
  else page = <Briefing />;

  return (
    <AppCtx.Provider
      value={{
        me,
        theme,
        setTheme,
        badges,
        refreshBadges,
        environment,
        agentAvailable,
        openComposer,
        logout,
      }}
    >
      <div className="app">
        <Sidebar />
        <div className="main">
          <Topbar />
          {page}
        </div>
      </div>
      {composer && <Composer opts={composer} onClose={() => setComposer(null)} />}
    </AppCtx.Provider>
  );
}
