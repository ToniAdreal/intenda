import { useEffect, useMemo, useState } from 'react';
import {
  Archive,
  CornerUpLeft,
  Forward,
  Inbox as InboxIcon,
  MailOpen,
  Reply,
  ReplyAll,
  Send,
  ShieldAlert,
  Sparkles,
  Star,
  Trash2,
} from 'lucide-react';
import {
  api,
  type AgentIdentity,
  type Draft,
  type MailMessage,
  type ThreadItem,
} from '../lib/api';
import { useApp } from '../lib/appctx';
import {
  EnvLabel,
  ErrorCard,
  SkeletonRows,
  errorText,
  fmtDateTime,
  fmtTime,
  initials,
  navigate,
  useToast,
} from '../lib/ui';

const FOLDERS = [
  { id: 'inbox', label: 'Inbox', icon: InboxIcon },
  { id: 'sent', label: 'Sent', icon: Send },
  { id: 'starred', label: 'Starred', icon: Star },
] as const;

type Folder = 'inbox' | 'sent' | 'drafts' | 'starred' | 'archive' | 'trash';

function useQueryParam(name: string): string | null {
  const [v, setV] = useState(() => new URLSearchParams(window.location.search).get(name));
  useEffect(() => {
    const onPop = () => setV(new URLSearchParams(window.location.search).get(name));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [name]);
  return v;
}

export default function Inbox() {
  const toast = useToast();
  const { openComposer, refreshBadges } = useApp();
  const messageParam = useQueryParam('message');
  // Agent inbox view: ?account=<agent account id> filters every list to
  // the Agent's own mailbox (identity mode).
  const accountParam = useQueryParam('account');
  const [agentIdentity, setAgentIdentity] = useState<AgentIdentity | null>(null);
  const [folder, setFolder] = useState<Folder>('inbox');
  const [messages, setMessages] = useState<MailMessage[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [threads, setThreads] = useState<ThreadItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MailMessage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = () => setReloadKey((k) => k + 1);

  useEffect(() => {
    if (!accountParam) {
      setAgentIdentity(null);
      return;
    }
    let alive = true;
    api<{ agent_identity: AgentIdentity | null }>('/api/v1/settings')
      .then((s) => alive && setAgentIdentity(s.agent_identity))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [accountParam]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const acct = accountParam ? `&account_id=${accountParam}` : '';
    const jobs: Promise<unknown>[] = [
      api<{ items: ThreadItem[] }>(`/api/v1/threads?limit=100${acct}`).then((d) => alive && setThreads(d.items)),
      api<{ items: Draft[] }>('/api/v1/drafts').then((d) => alive && setDrafts(d.items)),
    ];
    if (folder !== 'drafts') {
      jobs.push(
        api<{ items: MailMessage[] }>(`/api/v1/emails?folder=${folder}&limit=100${acct}`).then(
          (d) => alive && setMessages(d.items),
        ),
      );
    }
    Promise.all(jobs)
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [folder, reloadKey, accountParam]);

  const visibleDrafts = accountParam
    ? drafts.filter((d) => d.mail_account_id === accountParam)
    : drafts;

  // Deep link from search / briefing: open a specific message.
  useEffect(() => {
    if (messageParam) {
      setFolder('inbox');
      setSelectedId(messageParam);
    }
  }, [messageParam]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let alive = true;
    api<{ message: MailMessage }>(`/api/v1/emails/${selectedId}`)
      .then((d) => {
        if (!alive) return;
        setDetail(d.message);
        if (!d.message.is_read) {
          api(`/api/v1/emails/${selectedId}/read-state`, {
            method: 'POST',
            body: { is_read: true },
          })
            .then(() => {
              refreshBadges();
              setMessages((prev) => prev.map((x) => (x.id === selectedId ? { ...x, is_read: true } : x)));
            })
            .catch(() => {});
        }
      })
      .catch((e) => alive && setError(e));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, reloadKey]);

  const counts = useMemo(
    () => ({
      inbox: threads.filter((t) => !t.is_archived).reduce((n, t) => n + t.unread_count, 0),
      drafts: visibleDrafts.length,
      archive: threads.filter((t) => t.is_archived).length,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [threads, visibleDrafts],
  );

  const act = async (fn: () => Promise<unknown>, okText: string, clearSelection = false) => {
    try {
      await fn();
      toast({ text: okText });
      refreshBadges();
      if (clearSelection) {
        setSelectedId(null);
        setDetail(null);
      }
      reload();
    } catch (e) {
      toast({ text: errorText(e), error: true });
    }
  };

  const handoff = async () => {
    if (!detail) return;
    try {
      const r = await api<{ conversation: any; context_preview: any }>(
        `/api/v1/emails/${detail.id}/hand-off`,
        { method: 'POST', body: {} },
      );
      toast({
        text: `Agent conversation started for “${r.context_preview.subject}” (${r.context_preview.message_count} message(s) of context). Continue in Delegate Work.`,
      });
      navigate('/delegate');
    } catch (e) {
      toast({ text: errorText(e), error: true });
    }
  };

  const senderLabel = (msg: MailMessage) =>
    msg.is_sent
      ? `To: ${msg.to_recipients.map((r) => r.name ?? r.email).join(', ')}`
      : (msg.from_name ?? msg.from_address);

  return (
    <div className="view" style={{ maxWidth: 1380 }}>
      <div className="view-head with-pill">
        <div>
          <span className="kicker">{accountParam ? 'AGENT INBOX' : 'INBOX'}</span>
          <h1>{accountParam ? "Your Agent's mailbox" : 'Your mailbox'}</h1>
          <p className="view-sub">
            {accountParam ? (
              <>
                Mail addressed to{' '}
                {agentIdentity ? (
                  <>
                    {agentIdentity.display_name} &lt;{agentIdentity.address}&gt;
                  </>
                ) : (
                  'your Agent'
                )}
                . The Agent is the first reader here; anything it sends goes out under its own
                name, with its disclosure attached — never as you.
              </>
            ) : (
              'Read, reply, archive — or hand a thread to the Agent with its full context attached.'
            )}
          </p>
        </div>
        <EnvLabel env={detail?.environment ?? messages[0]?.environment ?? 'demo'} />
      </div>

      {error != null && <ErrorCard error={error} onRetry={reload} />}

      <div className="inbox-layout">
        <aside className="panel folder-pane">
          <div className="pane-title">Folders</div>
          {FOLDERS.map((f) => (
            <button key={f.id} className={`folder-item${folder === f.id ? ' active' : ''}`} onClick={() => { setFolder(f.id); setSelectedId(null); }}>
              <f.icon size={16} />
              <span className="folder-label">{f.label}</span>
              {f.id === 'inbox' && counts.inbox > 0 && <span className="folder-count">{counts.inbox}</span>}
            </button>
          ))}
          <button className={`folder-item${folder === 'drafts' ? ' active' : ''}`} onClick={() => { setFolder('drafts'); setSelectedId(null); }}>
            <MailOpen size={16} />
            <span className="folder-label">Drafts</span>
            {counts.drafts > 0 && <span className="folder-count">{counts.drafts}</span>}
          </button>
          <button className={`folder-item${folder === 'archive' ? ' active' : ''}`} onClick={() => { setFolder('archive'); setSelectedId(null); }}>
            <Archive size={16} />
            <span className="folder-label">Archive</span>
            {counts.archive > 0 && <span className="folder-count">{counts.archive}</span>}
          </button>
          <button className={`folder-item${folder === 'trash' ? ' active' : ''}`} onClick={() => { setFolder('trash'); setSelectedId(null); }}>
            <Trash2 size={16} />
            <span className="folder-label">Trash</span>
          </button>
          <div className="pane-divider" />
          <div className="pane-title">About this mailbox</div>
          {accountParam && (
            <span className="tag-row" style={{ cursor: 'default' }}>
              <span className="tag-dot" style={{ background: 'var(--accent, #2563eb)' }} />
              Agent mailbox — the Agent reads here first
            </span>
          )}
          <span className="tag-row" style={{ cursor: 'default' }}>
            <span className="tag-dot" style={{ background: 'var(--khaki)' }} />
            Demo data — sends are simulated
          </span>
        </aside>

        <section className="panel list-pane">
          <div className="pane-title" style={{ padding: '0 0 10px' }}>
            {folder === 'drafts' ? 'Drafts' : folder[0].toUpperCase() + folder.slice(1)}
          </div>
          {loading && <SkeletonRows rows={5} />}
          {!loading && folder === 'drafts' && (
            <div className="mail-list">
              {visibleDrafts.length === 0 && <div className="empty-mini">No drafts. Agent drafts waiting for approval live in the Review Queue.</div>}
              {visibleDrafts.map((d) => (
                <button
                  key={d.id}
                  className="mail-row"
                  onClick={() => openComposer({ draftId: d.id, mode: 'new', onDone: reload })}
                >
                  <span className="mail-avatar">Dr</span>
                  <span className="mail-main">
                    <span className="mail-row-top">
                      <span className="mail-from">{d.to_recipients.map((r) => r.name ?? r.email).join(', ') || '(no recipient)'}</span>
                      <span className="mail-time">{fmtTime(d.updated_at)}</span>
                    </span>
                    <span className="mail-subject">{d.subject || '(no subject)'}</span>
                    <span className="mail-snippet">{d.body_text.slice(0, 90)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
          {!loading && folder !== 'drafts' && (
            <div className="mail-list">
              {messages.length === 0 && <div className="empty-mini">Nothing in this folder.</div>}
              {messages.map((msg) => (
                <button key={msg.id} className={`mail-row${selectedId === msg.id ? ' active' : ''}`} onClick={() => setSelectedId(msg.id)}>
                  <span className="mail-avatar">{initials(msg.from_name ?? msg.from_address)}</span>
                  <span className="mail-main">
                    <span className="mail-row-top">
                      <span className="mail-from" style={msg.is_read ? { fontWeight: 500 } : undefined}>
                        {senderLabel(msg)}
                      </span>
                      <span className="mail-time">{fmtTime(msg.created_at)}</span>
                    </span>
                    <span className="mail-subject">{msg.subject}</span>
                    <span className="mail-snippet">{msg.snippet}</span>
                  </span>
                  {msg.injection_suspect && (
                    <span
                      className="injection-flag"
                      title={`Possible prompt injection: ${(msg.injection_flags ?? []).map((f) => f.reason).join('; ')}`}
                    >
                      <ShieldAlert size={14} />
                    </span>
                  )}
                  {!msg.is_read && <span className="unread-dot" />}
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="panel read-pane">
          {!detail && (
            <div className="empty-card" style={{ border: 'none', background: 'transparent' }}>
              <img className="empty-motif only-night" src="assets/empty-inbox-dark.png" alt="" />
              <img className="empty-motif only-day" src="assets/empty-inbox-light.png" alt="" />
              Select a message to read it here. Replies, forwards, archiving and Agent hand-off all happen in place.
            </div>
          )}
          {detail && (
            <>
              <h2 className="read-subject">{detail.subject}</h2>
              <div className="read-pills">
                <EnvLabel env={detail.environment} />
                {detail.identity_id && (
                  <span className="pill pill-accent">
                    {detail.is_sent ? 'Sent by your Agent' : 'Addressed to your Agent'}
                  </span>
                )}
                {detail.has_attachment && <span className="pill pill-neutral">Has attachments</span>}
                {detail.is_starred && <span className="pill pill-khaki">Starred</span>}
                {detail.injection_suspect && (
                  <span
                    className="pill pill-danger"
                    title={(detail.injection_flags ?? []).map((f) => f.reason).join('; ')}
                  >
                    Possible prompt injection
                  </span>
                )}
              </div>
              {detail.injection_suspect && (
                <div className="injection-warning">
                  This message was flagged as a possible prompt-injection attempt
                  {(detail.injection_flags ?? []).length > 0 && (
                    <>: {(detail.injection_flags ?? []).map((f) => f.reason).join('; ')}</>
                  )}
                  . It is excluded from automatic Agent processing. You can still read it and act
                  manually; anything the Agent drafts for you still goes through the Review Queue.
                </div>
              )}
              <div className="read-meta">
                <span className="mail-avatar">{initials(detail.from_name ?? detail.from_address)}</span>
                <span className="read-meta-main">
                  <span className="read-from">{detail.from_name ?? detail.from_address}</span>
                  <span className="read-sub">
                    {detail.from_address} · to {detail.to_recipients.map((r) => r.name ?? r.email).join(', ')} · {fmtDateTime(detail.created_at)}
                  </span>
                </span>
                <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                  <button
                    className="icon-btn"
                    aria-label={detail.is_starred ? 'Unstar' : 'Star'}
                    onClick={() =>
                      act(
                        () => api(`/api/v1/emails/${detail.id}/star`, { method: 'POST', body: { is_starred: !detail.is_starred } }),
                        detail.is_starred ? 'Star removed.' : 'Starred.',
                      )
                    }
                  >
                    <Star size={16} fill={detail.is_starred ? 'currentColor' : 'none'} />
                  </button>
                  <button
                    className="icon-btn"
                    aria-label="Archive thread"
                    onClick={() => act(() => api(`/api/v1/threads/${detail.thread_id}/archive`, { method: 'POST', body: {} }), 'Thread archived.', true)}
                  >
                    <Archive size={16} />
                  </button>
                  <button
                    className="icon-btn"
                    aria-label="Move to trash"
                    onClick={() => act(() => api(`/api/v1/emails/${detail.id}`, { method: 'DELETE' }), 'Moved to trash.', true)}
                  >
                    <Trash2 size={16} />
                  </button>
                </span>
              </div>
              <div className="read-body">
                {(detail.body_text ?? '').split('\n\n').map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
              {detail.attachments && detail.attachments.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div className="pane-title" style={{ padding: '0 0 8px' }}>Attachments</div>
                  <div className="chip-row">
                    {detail.attachments.map((a) => (
                      <span key={a.id} className="ctx-chip">
                        {a.filename} · {Math.max(1, Math.round(a.size_bytes / 1024))} KB · {a.scan_status}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div className="btn-row read-actions">
                <button className="btn btn-primary" onClick={() => openComposer({ mode: 'reply', messageId: detail.id, onDone: reload })}>
                  <Reply size={14} /> Reply
                </button>
                <button className="btn btn-dark" onClick={() => openComposer({ mode: 'reply_all', messageId: detail.id, onDone: reload })}>
                  <ReplyAll size={14} /> Reply all
                </button>
                <button className="btn btn-dark" onClick={() => openComposer({ mode: 'forward', messageId: detail.id, onDone: reload })}>
                  <Forward size={14} /> Forward
                </button>
                <button className="btn btn-ghost" onClick={() => act(() => api(`/api/v1/emails/${detail.id}/read-state`, { method: 'POST', body: { is_read: false } }), 'Marked as unread.')}>
                  <CornerUpLeft size={14} /> Mark unread
                </button>
              </div>
              <div className="agent-cta">
                <span className="agent-cta-icon">
                  <Sparkles size={17} />
                </span>
                <span className="agent-cta-main">
                  <span className="agent-cta-title">Let the Agent handle this thread</span>
                  <span className="agent-cta-desc">Starts a conversation with this thread attached as context. Nothing is sent without your approval path.</span>
                </span>
                <button className="btn btn-primary" onClick={handoff}>
                  Hand off to Agent
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
