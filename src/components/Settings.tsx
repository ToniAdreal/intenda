import { useEffect, useState } from 'react';
import { Download, Moon, Plus, Sun, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { useApp } from '../lib/appctx';
import {
  EnvLabel,
  ErrorCard,
  Modal,
  SkeletonRows,
  errorText,
  fmtDateTime,
  navigate,
  useAsync,
  useToast,
} from '../lib/ui';
import { AUTH_MODES } from './Delegate';

interface SettingsData {
  profile: { name: string; email: string; created_at: string };
  appearance: { mode: string };
  agent: {
    response_style?: string;
    context_permission?: string;
    default_authorization_mode: string;
    model_provider: string;
    model_id: string;
    model_available: boolean;
    data_use: string;
  };
  notifications: Record<string, boolean>;
  privacy: Record<string, boolean>;
  mailboxes: any[];
  agent_identity: {
    id: string;
    mail_account_id: string | null;
    kind: string;
    address: string;
    display_name: string;
    disclosure_text: string;
    status: string;
    is_default: boolean;
  } | null;
  about: { app: string; version: string; environment: string };
}

interface SessionRow {
  id: string;
  created_at: string;
  last_seen_at: string;
  user_agent: string | null;
  current: boolean;
}

interface Contact {
  id: string;
  name: string;
  email: string;
  company: string | null;
  notes: string | null;
}

export default function Settings() {
  const toast = useToast();
  const app = useApp();
  const { data, error, loading, reload } = useAsync(() => api<SettingsData>('/api/v1/settings'), []);
  const { data: sessions, reload: reloadSessions } = useAsync(
    () => api<{ items: SessionRow[] }>('/api/v1/auth/sessions'),
    [],
  );
  const { data: contacts, reload: reloadContacts } = useAsync(
    () => api<{ items: Contact[] }>('/api/v1/contacts?limit=100'),
    [],
  );
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmDeleteDemo, setConfirmDeleteDemo] = useState(false);
  const [confirmDeleteAccount, setConfirmDeleteAccount] = useState(false);
  const [deletePassword, setDeletePassword] = useState('');
  const [deleteText, setDeleteText] = useState('');
  const [newContact, setNewContact] = useState({ name: '', email: '', company: '' });

  useEffect(() => {
    if (data) setName(data.profile.name);
  }, [data]);

  if (loading) {
    return (
      <div className="view">
        <SkeletonRows rows={6} />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="view">
        <ErrorCard error={error ?? new Error('Settings unavailable.')} onRetry={reload} />
      </div>
    );
  }
  const s = data;

  const patch = async (body: Record<string, unknown>, okText?: string) => {
    setBusy(true);
    try {
      await api('/api/v1/settings', { method: 'PATCH', body });
      if (okText) toast({ text: okText });
      reload();
    } catch (e) {
      toast({ text: errorText(e), error: true });
    } finally {
      setBusy(false);
    }
  };

  const chooseAppearance = (mode: 'day' | 'night') => {
    app.setTheme(mode);
    patch({ appearance: { mode } }, `Appearance set to ${mode === 'day' ? 'Day' : 'Night'}.`);
  };

  const exportData = async () => {
    setBusy(true);
    try {
      const r = await api<{ export_job: { download_url: string } }>('/api/v1/settings/data-export', {
        method: 'POST',
        body: { format: 'json' },
      });
      window.open(r.export_job.download_url, '_blank');
      toast({ text: 'Export ready — the download has started.' });
    } catch (e) {
      toast({ text: errorText(e), error: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="view">
      <div className="view-head with-pill">
        <div>
          <span className="kicker">SETTINGS</span>
          <h1>Your account, your rules</h1>
          <p className="view-sub">Profile, appearance, mailboxes, Agent behavior, privacy and data — everything takes effect immediately.</p>
        </div>
        <EnvLabel env={s.about.environment} />
      </div>

      <div className="settings-grid">
        <section className="panel settings-section">
          <h2>Profile</h2>
          <p className="desc">How you appear in Intenda and in the mail you send.</p>
          <div className="inline-form">
            <div className="field">
              <label htmlFor="st-name">Full name</label>
              <input id="st-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="st-email">Account email</label>
              <input id="st-email" value={s.profile.email} disabled />
            </div>
            <button className="btn btn-primary" disabled={busy || !name.trim() || name === s.profile.name} onClick={() => patch({ profile: { name } }, 'Profile saved.')}>
              Save
            </button>
          </div>
          <p className="panel-foot-note">Account created {fmtDateTime(s.profile.created_at)}.</p>
        </section>

        <section className="panel settings-section">
          <h2>Appearance</h2>
          <p className="desc">Day and Night use the same layout and the same single accent color. Your choice is saved to your account and this device.</p>
          <div className="radio-cards">
            {(
              [
                { mode: 'day' as const, icon: Sun, title: 'Day', desc: 'White workbench surfaces for bright rooms.' },
                { mode: 'night' as const, icon: Moon, title: 'Night', desc: 'Near-black workbench surfaces for low light.' },
              ]
            ).map(({ mode, icon: Icon, title, desc }) => (
              <button
                key={mode}
                className={`radio-card${app.theme === mode ? ' selected' : ''}`}
                onClick={() => chooseAppearance(mode)}
                aria-pressed={app.theme === mode}
              >
                <Icon size={18} />
                <span>
                  <span className="rc-title">{title}</span>
                  <span className="rc-desc" style={{ display: 'block' }}>{desc}</span>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="panel settings-section">
          <h2>Connected mailboxes</h2>
          <p className="desc">Demo and Live mailboxes are always labelled. Disconnecting revokes access and deletes synced data for that mailbox.</p>
          {s.mailboxes.length === 0 && <div className="empty-mini">No mailbox connected.</div>}
          {s.mailboxes.map((mb) => (
            <div key={mb.id} className="switch-row">
              <span className="sr-main">
                <span className="sr-title">
                  {mb.email_address} {mb.environment === 'demo' ? <span className="demo-label">Demo</span> : <span className="live-label">Live</span>}
                  {mb.role === 'agent' && <span className="demo-label">Agent mailbox</span>}
                </span>
                <span className="sr-desc" style={{ display: 'block' }}>
                  {mb.provider} · {mb.status}
                  {mb.last_sync_at ? ` · last synced ${fmtDateTime(mb.last_sync_at)}` : ''}
                </span>
              </span>
              <button
                className="btn btn-danger-ghost"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await api(`/api/v1/accounts/${mb.id}`, { method: 'DELETE' });
                    toast({ text: 'Mailbox disconnected and its synced data deleted.' });
                    reload();
                  } catch (e) {
                    toast({ text: errorText(e), error: true });
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Disconnect
              </button>
            </div>
          ))}
          <p className="panel-foot-note">Connecting another live mailbox requires the provider's OAuth screen; it is started from the onboarding flow.</p>
        </section>

        {s.agent_identity && (
          <section className="panel settings-section">
            <h2>Agent identity</h2>
            <p className="desc">
              Your Agent has its own mailbox and sends as itself — never as you. Mail addressed to
              the Agent lands in the Agent inbox in the sidebar, and every send from this identity
              carries the disclosure below.
            </p>
            <div className="draft-rows">
              <div className="draft-row">
                <span className="draft-row-label">Sends as</span>
                <span>{s.agent_identity.display_name} &lt;{s.agent_identity.address}&gt;</span>
              </div>
              <div className="draft-row">
                <span className="draft-row-label">Status</span>
                <span>{s.agent_identity.status}</span>
              </div>
              <div className="draft-row">
                <span className="draft-row-label">Disclosure</span>
                <span style={{ whiteSpace: 'pre-wrap' }}>{s.agent_identity.disclosure_text}</span>
              </div>
            </div>
            <p className="panel-foot-note">
              In this demo the identity is seeded and read-only. Disconnecting the Agent mailbox
              removes the identity with it.
            </p>
          </section>
        )}

        <section className="panel settings-section">
          <h2>Agent &amp; authorization</h2>
          <p className="desc">
            Model: {s.agent.model_provider} · <span className="mono">{s.agent.model_id}</span> ·{' '}
            {s.agent.model_available ? 'connected' : 'not connected — deterministic fallback answers, with no side effects'}. {s.agent.data_use}
          </p>
          <div className="field">
            <label>Default authorization level for new tasks</label>
            <div className="radio-cards">
              {AUTH_MODES.map((m) => (
                <button
                  key={m.id}
                  className={`radio-card${s.agent.default_authorization_mode === m.id ? ' selected' : ''}`}
                  onClick={() => patch({ agent: { default_authorization_mode: m.id } }, 'Default authorization level saved.')}
                  aria-pressed={s.agent.default_authorization_mode === m.id}
                >
                  <span>
                    <span className="rc-title">{m.name}</span>
                    <span className="rc-desc" style={{ display: 'block' }}>{m.desc}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
          <div className="field" style={{ marginTop: 14 }}>
            <label htmlFor="st-style">Response style</label>
            <select
              id="st-style"
              value={s.agent.response_style ?? 'balanced'}
              onChange={(e) => patch({ agent: { response_style: e.target.value } }, 'Response style saved.')}
            >
              <option value="concise">Concise</option>
              <option value="balanced">Balanced</option>
              <option value="detailed">Detailed</option>
            </select>
          </div>
        </section>

        <section className="panel settings-section">
          <h2>Notifications</h2>
          <p className="desc">In-app notifications for the events that need you.</p>
          {(
            [
              { key: 'decision_needed', title: 'Decision needed', desc: 'A task is waiting for your approval or choice.' },
              { key: 'review_ready', title: 'Draft ready for review', desc: 'A new item lands in the Review Queue.' },
              { key: 'task_completed', title: 'Task completed', desc: 'A delegated task delivers its result.' },
              { key: 'task_failed', title: 'Task needs attention', desc: 'A step failed or a task is blocked.' },
            ]
          ).map((n) => (
            <div key={n.key} className="switch-row">
              <span className="sr-main">
                <span className="sr-title">{n.title}</span>
                <span className="sr-desc" style={{ display: 'block' }}>{n.desc}</span>
              </span>
              <button
                className="switch"
                role="switch"
                aria-checked={s.notifications[n.key] !== false}
                aria-label={n.title}
                onClick={() => patch({ notifications: { [n.key]: s.notifications[n.key] === false } })}
              />
            </div>
          ))}
        </section>

        <section className="panel settings-section">
          <h2>Contacts</h2>
          <p className="desc">People the Agent can reference when drafting. Contacts come from your mailbox and anything you add here.</p>
          {(contacts?.items ?? []).map((c) => (
            <div key={c.id} className="switch-row">
              <span className="sr-main">
                <span className="sr-title">{c.name}</span>
                <span className="sr-desc" style={{ display: 'block' }}>
                  {c.email}
                  {c.company ? ` · ${c.company}` : ''}
                </span>
              </span>
              <button
                className="btn btn-ghost"
                aria-label={`Delete contact ${c.name}`}
                onClick={async () => {
                  try {
                    await api(`/api/v1/contacts/${c.id}`, { method: 'DELETE' });
                    reloadContacts();
                  } catch (e) {
                    toast({ text: errorText(e), error: true });
                  }
                }}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          <div className="inline-form" style={{ marginTop: 12 }}>
            <div className="field">
              <label htmlFor="ct-name">Name</label>
              <input id="ct-name" value={newContact.name} onChange={(e) => setNewContact((p) => ({ ...p, name: e.target.value }))} />
            </div>
            <div className="field">
              <label htmlFor="ct-email">Email</label>
              <input id="ct-email" value={newContact.email} onChange={(e) => setNewContact((p) => ({ ...p, email: e.target.value }))} />
            </div>
            <div className="field">
              <label htmlFor="ct-company">Company (optional)</label>
              <input id="ct-company" value={newContact.company} onChange={(e) => setNewContact((p) => ({ ...p, company: e.target.value }))} />
            </div>
            <button
              className="btn btn-dark"
              disabled={!newContact.name.trim() || !newContact.email.trim()}
              onClick={async () => {
                try {
                  await api('/api/v1/contacts', {
                    method: 'POST',
                    body: { name: newContact.name, email: newContact.email, company: newContact.company || null },
                  });
                  setNewContact({ name: '', email: '', company: '' });
                  reloadContacts();
                } catch (e) {
                  toast({ text: errorText(e), error: true });
                }
              }}
            >
              <Plus size={14} /> Add
            </button>
          </div>
        </section>

        <section className="panel settings-section">
          <h2>Sessions</h2>
          <p className="desc">Devices currently signed in to your account.</p>
          {(sessions?.items ?? []).map((sess) => (
            <div key={sess.id} className="switch-row">
              <span className="sr-main">
                <span className="sr-title">{sess.current ? 'This session' : 'Session'}</span>
                <span className="sr-desc" style={{ display: 'block' }}>
                  Signed in {fmtDateTime(sess.created_at)} · last active {fmtDateTime(sess.last_seen_at)}
                  {sess.user_agent ? ` · ${sess.user_agent.slice(0, 60)}` : ''}
                </span>
              </span>
              {!sess.current && (
                <button
                  className="btn btn-ghost"
                  onClick={async () => {
                    try {
                      await api(`/api/v1/auth/sessions/${sess.id}`, { method: 'DELETE' });
                      reloadSessions();
                    } catch (e) {
                      toast({ text: errorText(e), error: true });
                    }
                  }}
                >
                  Revoke
                </button>
              )}
            </div>
          ))}
          <div className="btn-row">
            <button
              className="btn btn-dark"
              onClick={async () => {
                try {
                  await api('/api/v1/auth/sessions/revoke-others', { method: 'POST', body: {} });
                  toast({ text: 'All other sessions revoked.' });
                  reloadSessions();
                } catch (e) {
                  toast({ text: errorText(e), error: true });
                }
              }}
            >
              Revoke all other sessions
            </button>
          </div>
        </section>

        <section className="panel settings-section">
          <h2>Privacy &amp; data</h2>
          <p className="desc">Export everything, delete the demo data, or close the account. Destructive actions always ask first.</p>
          <div className="btn-row" style={{ marginTop: 0 }}>
            <button className="btn btn-dark" onClick={exportData} disabled={busy}>
              <Download size={14} /> Export my data (JSON)
            </button>
            <button className="btn btn-danger-ghost" onClick={() => setConfirmDeleteDemo(true)} disabled={busy}>
              Delete demo data
            </button>
            <button className="btn btn-danger-ghost" onClick={() => setConfirmDeleteAccount(true)} disabled={busy}>
              Delete account
            </button>
          </div>
          <p className="panel-foot-note">
            {s.about.app} v{s.about.version} · Environment: {s.about.environment}. Audit records are kept for actions taken on your behalf.
          </p>
          <p className="panel-foot-note">
            Read our{' '}
            <button type="button" className="link-btn" onClick={() => navigate('/privacy')}>
              Privacy Policy
            </button>{' '}
            and{' '}
            <button type="button" className="link-btn" onClick={() => navigate('/terms')}>
              Terms of Service
            </button>
            .
          </p>
        </section>
      </div>

      {confirmDeleteDemo && (
        <Modal
          title="Delete demo data?"
          sub="This permanently deletes the demo mailbox, its messages, demo tasks, drafts and review items. Your account stays, and you can reconnect a mailbox afterwards."
          onClose={() => setConfirmDeleteDemo(false)}
        >
          <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn btn-ghost" onClick={() => setConfirmDeleteDemo(false)}>
              Keep data
            </button>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api('/api/v1/settings/delete-demo-data', { method: 'POST', body: { confirm: true } });
                  toast({ text: 'Demo data deleted.' });
                  setConfirmDeleteDemo(false);
                  reload();
                } catch (e) {
                  toast({ text: errorText(e), error: true });
                } finally {
                  setBusy(false);
                }
              }}
            >
              Delete demo data
            </button>
          </div>
        </Modal>
      )}

      {confirmDeleteAccount && (
        <Modal
          title="Delete your account?"
          sub="This closes the account and revokes every session. Enter your password and type DELETE to confirm."
          onClose={() => setConfirmDeleteAccount(false)}
        >
          <div className="field">
            <label htmlFor="da-password">Password</label>
            <input id="da-password" type="password" value={deletePassword} onChange={(e) => setDeletePassword(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="da-text">Type DELETE</label>
            <input id="da-text" value={deleteText} onChange={(e) => setDeleteText(e.target.value)} />
          </div>
          <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn btn-ghost" onClick={() => setConfirmDeleteAccount(false)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || deleteText !== 'DELETE' || !deletePassword}
              onClick={async () => {
                setBusy(true);
                try {
                  await api('/api/v1/settings/account', {
                    method: 'DELETE',
                    body: { password: deletePassword, confirmation_text: deleteText },
                  });
                  app.logout();
                } catch (e) {
                  toast({ text: errorText(e), error: true });
                  setBusy(false);
                }
              }}
            >
              Delete account
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
