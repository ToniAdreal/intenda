import { useEffect, useRef, useState } from 'react';
import {
  Archive,
  Check,
  History,
  Inbox,
  LayoutDashboard,
  ListTodo,
  Moon,
  Settings,
  Sparkles,
  Sun,
  Workflow,
} from 'lucide-react';
import { useApp } from '../lib/appctx';
import { navigate, useRoute } from '../lib/ui';
import { api } from '../lib/api';

const NAV: { path: string; label: string; icon: typeof Inbox; badge?: 'review' | 'tasks' | 'inbox_unread' }[] = [
  { path: '/briefing', label: 'Today Briefing', icon: LayoutDashboard },
  { path: '/delegate', label: 'Delegate Work', icon: Sparkles },
  { path: '/tasks', label: 'Task Progress', icon: ListTodo, badge: 'tasks' },
  { path: '/inbox', label: 'Inbox', icon: Inbox, badge: 'inbox_unread' },
  { path: '/review', label: 'Review Queue', icon: Check, badge: 'review' },
  { path: '/activity', label: 'Activity', icon: History },
  { path: '/automations', label: 'Automations', icon: Workflow },
  { path: '/archive', label: 'Archive', icon: Archive },
  { path: '/settings', label: 'Settings', icon: Settings },
];

function AppearanceControl() {
  const { theme, setTheme } = useApp();
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node) && !btnRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    const first = menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"]');
    first?.focus();
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open ]);

  const choose = (mode: 'day' | 'night') => {
    setTheme(mode);
    setOpen(false);
    btnRef.current?.focus();
    // Persist server-side via PATCH /settings; the local change already applied.
    api('/api/v1/settings', { method: 'PATCH', body: { appearance: { mode } } }).catch(() => {
      /* offline: local preference remains and re-syncs on next change */
    });
  };

  const onMenuKey = (e: React.KeyboardEvent) => {
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [],
    );
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'Escape') {
      setOpen(false);
      btnRef.current?.focus();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      items[(idx + 1) % items.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      items[(idx - 1 + items.length) % items.length]?.focus();
    }
  };

  return (
    <div className="appearance-wrap">
      <button
        ref={btnRef}
        className="appearance-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Appearance, current mode ${theme === 'day' ? 'Day' : 'Night'}`}
        onClick={() => setOpen((v) => !v)}
      >
        {theme === 'day' ? <Sun size={17} /> : <Moon size={17} />}
        <span>Appearance</span>
        <span className="mode-name">{theme === 'day' ? 'Day' : 'Night'}</span>
      </button>
      {open && (
        <div className="appearance-menu" role="menu" ref={menuRef} onKeyDown={onMenuKey}>
          {(
            [
              { mode: 'day' as const, label: 'Day', icon: Sun },
              { mode: 'night' as const, label: 'Night', icon: Moon },
            ]
          ).map(({ mode, label, icon: Icon }) => (
            <button
              key={mode}
              role="menuitemradio"
              aria-checked={theme === mode}
              className="appearance-option"
              onClick={() => choose(mode)}
            >
              <Icon size={16} />
              <span>{label}</span>
              {theme === mode && <Check size={15} className="check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Sidebar() {
  const route = useRoute();
  const { badges, agentAvailable, environment } = useApp();
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-row">
          <img className="brand-logo only-night" src="assets/logo-lockup-dark.png" alt="Intenda" />
          <img className="brand-logo only-day" src="assets/logo-lockup-light.png" alt="Intenda" />
        </div>
        <div className="brand-sub">FROM INTENT TO DONE</div>
      </div>
      <button className="new-delegate-btn" onClick={() => navigate('/delegate')}>
        <Sparkles size={16} /> New delegation
      </button>
      <nav className="nav" aria-label="Primary">
        {NAV.map((item) => {
          const active = route === item.path || route.startsWith(item.path + '/');
          const badge = item.badge ? badges[item.badge] : 0;
          return (
            <button
              key={item.path}
              className={`nav-item${active ? ' active' : ''}`}
              aria-current={active ? 'page' : undefined}
              onClick={() => navigate(item.path)}
            >
              <item.icon size={18} />
              <span className="nav-label">{item.label}</span>
              {badge > 0 && <span className="nav-badge">{badge}</span>}
            </button>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <div className="agent-status">
          <span
            className="status-dot"
            style={
              agentAvailable
                ? undefined
                : { background: 'var(--warn)', boxShadow: '0 0 0 3px var(--warn-bg)' }
            }
          />
          <span>
            Agent {agentAvailable ? 'connected' : 'in fallback mode'} ·{' '}
            {environment === 'demo' ? 'Demo mailbox' : 'Live mailbox'}
          </span>
        </div>
        <AppearanceControl />
      </div>
    </aside>
  );
}
