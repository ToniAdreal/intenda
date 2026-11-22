import { useEffect, useState } from 'react';
import { AlertTriangle, Check, Clock, Hand, Pause, Play, RotateCcw, X } from 'lucide-react';
import {
  api,
  type Decision,
  type PendingAction,
  type ReviewItem,
  type TaskItem,
  type TaskStep,
} from '../lib/api';
import { useApp } from '../lib/appctx';
import {
  ErrorCard,
  Modal,
  SkeletonRows,
  errorText,
  fmtDateTime,
  navigate,
  timeAgo,
  useAsync,
  useToast,
} from '../lib/ui';

const STATUS_PILL: Record<string, string> = {
  running: 'pill-accent',
  queued: 'pill-neutral',
  waiting_user: 'pill-warn',
  paused: 'pill-neutral',
  takeover: 'pill-info',
  manual_control: 'pill-info',
  needs_attention: 'pill-danger',
  failed: 'pill-danger',
  completed: 'pill-ok',
  cancelled: 'pill-neutral',
  draft: 'pill-neutral',
  pending: 'pill-neutral',
  modified: 'pill-warn',
  approved: 'pill-ok',
  rejected: 'pill-danger',
  sent: 'pill-ok',
};

const MODE_LABEL: Record<string, string> = {
  confirm_every_step: 'Confirm Every Step',
  confirm_milestones: 'Confirm at Key Milestones',
  confirm_before_send: 'Fully Automatic but Confirm Before Sending',
};

export function StatusPill({ status }: { status: string }) {
  return <span className={`pill ${STATUS_PILL[status] ?? 'pill-neutral'}`}>{status.replace(/_/g, ' ')}</span>;
}

export default function TaskProgress({ taskId }: { taskId?: string }) {
  return taskId ? <TaskDetail taskId={taskId} /> : <TaskList />;
}

function TaskList() {
  const [filter, setFilter] = useState('all');
  const { data, error, loading, reload } = useAsync(
    () => api<{ items: TaskItem[] }>(`/api/v1/tasks?limit=100${filter !== 'all' ? `&status=${filter}` : ''}`),
    [filter],
  );
  const filters = ['all', 'running', 'waiting_user', 'needs_attention', 'paused', 'completed', 'failed'];
  return (
    <div className="view">
      <div className="view-head with-pill">
        <div>
          <span className="kicker">TASK PROGRESS</span>
          <h1>Delegated work, step by step</h1>
          <p className="view-sub">Every task shows its plan, its current step, and exactly where it is waiting on you.</p>
        </div>
      </div>
      <div className="panel filter-bar">
        {filters.map((f) => (
          <button key={f} className={`filter-pill${filter === f ? ' active' : ''}`} onClick={() => setFilter(f)}>
            {f === 'all' ? 'All' : f.replace(/_/g, ' ')}
          </button>
        ))}
      </div>
      {loading && <SkeletonRows />}
      {error != null && <ErrorCard error={error} onRetry={reload} />}
      {data && data.items.length === 0 && (
        <div className="empty-card">
          <img className="empty-motif only-night" src="assets/empty-tasks-dark.png" alt="" />
          <img className="empty-motif only-day" src="assets/empty-tasks-light.png" alt="" />
          No tasks in this state. Delegate new work and it will appear here.
          <div className="btn-row">
            <button className="btn btn-primary" onClick={() => navigate('/delegate')}>
              Delegate work
            </button>
          </div>
        </div>
      )}
      {data && data.items.length > 0 && (
        <section className="panel" style={{ padding: '6px 18px' }}>
          <div className="list-rows">
            {data.items.map((t) => (
              <button key={t.id} className="list-row" onClick={() => navigate(`/tasks/${t.id}`)}>
                <span className="list-row-main">
                  <span className="list-row-title">
                    {t.goal} <span className="mono" style={{ fontSize: 12, color: 'var(--t4)' }}>{t.display_id}</span>
                  </span>
                  <span className="list-row-meta" style={{ display: 'block' }}>
                    {MODE_LABEL[t.authorization_mode] ?? t.authorization_mode} · Updated {timeAgo(t.updated_at)}
                  </span>
                  <span className="progress" style={{ marginTop: 8, maxWidth: 420 }}>
                    <span className="progress-bar" style={{ width: `${t.progress_percent}%` }} />
                  </span>
                </span>
                <StatusPill status={t.status} />
                <span className="agent-pct">{t.progress_percent}%</span>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

interface TaskDetailData {
  task: TaskItem;
  steps: TaskStep[];
  latest_events: { id: string; event_type: string; payload: Record<string, any>; created_at: string }[];
  outputs: ReviewItem[];
}

function TaskDetail({ taskId }: { taskId: string }) {
  const toast = useToast();
  const { refreshBadges } = useApp();
  const { data, error, loading, reload } = useAsync(
    () => api<TaskDetailData>(`/api/v1/tasks/${taskId}`),
    [taskId],
  );
  const { data: decisionData, reload: reloadDecisions } = useAsync(
    () => api<{ items: Decision[] }>(`/api/v1/decisions?task_id=${taskId}`),
    [taskId],
  );
  const { data: confirmationData, reload: reloadConfirmations } = useAsync(
    () => api<{ items: PendingAction[] }>(`/api/v1/confirmations?task_id=${taskId}`),
    [taskId],
  );
  const [busy, setBusy] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [freeText, setFreeText] = useState<Record<string, string>>({});

  useEffect(() => {
    const t = window.setInterval(() => {
      if (data && ['running', 'waiting_user', 'queued'].includes(data.task.status)) {
        reload();
        reloadDecisions();
        reloadConfirmations();
      }
    }, 4000);
    return () => window.clearInterval(t);
  }, [data?.task.status, reload, reloadDecisions, reloadConfirmations]);

  if (loading) {
    return (
      <div className="view">
        <SkeletonRows rows={5} />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="view">
        <ErrorCard error={error ?? new Error('Task not found.')} onRetry={reload} />
        <div className="btn-row">
          <button className="btn btn-dark" onClick={() => navigate('/tasks')}>
            Back to all tasks
          </button>
        </div>
      </div>
    );
  }

  const { task, steps, latest_events, outputs } = data;
  const pending = (decisionData?.items ?? []).filter((d) => d.status === 'pending');
  const pendingConfirmations = (confirmationData?.items ?? []).filter((a) => a.status === 'pending');
  const pendingOutputs = outputs.filter((o) => o.status === 'pending' || o.status === 'modified');

  // The one-time token for a runner-prepared confirmation travels in the
  // task.confirmation.required event, readable only by this task's owner.
  const confirmationToken = (actionId: string): string | null => {
    const ev = latest_events.find(
      (e) => e.event_type === 'task.confirmation.required' && e.payload?.confirmation_id === actionId,
    );
    return (ev?.payload?.confirmation_token as string) ?? null;
  };

  const call = async (fn: () => Promise<unknown>, okText?: string) => {
    setBusy(true);
    try {
      await fn();
      if (okText) toast({ text: okText });
      refreshBadges();
      reload();
      reloadDecisions();
      reloadConfirmations();
    } catch (e) {
      toast({ text: errorText(e), error: true });
    } finally {
      setBusy(false);
    }
  };

  const resolve = (d: Decision, body: Record<string, unknown>, okText: string) =>
    call(() => api(`/api/v1/decisions/${d.id}/resolve`, { method: 'POST', body }), okText);

  return (
    <div className="view">
      <div className="view-head with-pill">
        <div>
          <span className="kicker">
            TASK <span className="mono">{task.display_id}</span>
          </span>
          <h1 style={{ fontSize: 22, lineHeight: 1.35 }}>{task.goal}</h1>
          <p className="view-sub">
            {MODE_LABEL[task.authorization_mode] ?? task.authorization_mode} · Created {fmtDateTime(task.created_at)}
          </p>
        </div>
        <StatusPill status={task.status} />
      </div>

      <section className="panel progress-panel">
        <div className="progress-top">
          <span className="progress-status">
            {task.status === 'completed'
              ? 'Delivered'
              : task.status === 'waiting_user'
                ? 'Waiting for your decision below'
                : task.status === 'takeover' || task.status === 'manual_control'
                  ? 'You have manual control — the Agent is paused'
                  : task.status.replace(/_/g, ' ')}
          </span>
          <span className="progress-pct">{task.progress_percent}%</span>
        </div>
        <span className="progress progress-lg">
          <span className="progress-bar" style={{ width: `${task.progress_percent}%` }} />
        </span>
      </section>

      <div className="task-grid">
        <section className="panel timeline-panel">
          <div className="section-head">
            <h2>Steps</h2>
            <span className="section-note">
              {steps.filter((s) => s.status === 'completed').length} of {steps.length} complete
            </span>
          </div>
          {steps.map((s) => (
            <div
              key={s.id}
              className={`tl-item${s.status === 'completed' ? ' done' : ''}${s.status === 'running' || s.status === 'waiting_user' ? ' current' : ''}${s.status === 'pending' ? ' todo' : ''}`}
            >
              <span className="tl-dot">
                {s.status === 'completed' ? (
                  <Check size={14} />
                ) : s.status === 'running' ? (
                  <span className="tl-pulse" />
                ) : s.status === 'failed' ? (
                  <AlertTriangle size={14} />
                ) : s.status === 'waiting_user' ? (
                  <Clock size={14} />
                ) : (
                  <span style={{ fontSize: 11, fontWeight: 700 }}>{s.sequence + 1}</span>
                )}
              </span>
              <span className="tl-body">
                <span className="tl-label">{s.title}</span>
                <span className="tl-note" style={{ display: 'block' }}>
                  {s.step_type.replace(/_/g, ' ')} · {s.status.replace(/_/g, ' ')}
                  {s.requires_approval ? ' · confirmation gate' : ''}
                  {s.risk_level && s.risk_level !== 'low' ? ` · ${s.risk_level} risk` : ''}
                </span>
                {s.output_refs?.summary && (
                  <span className="tl-note" style={{ display: 'block' }}>{String(s.output_refs.summary)}</span>
                )}
                {s.output_refs?.chosen_option_label && (
                  <span className="tl-note" style={{ display: 'block' }}>
                    You chose: {String(s.output_refs.chosen_option_label)}
                  </span>
                )}
                {s.status === 'failed' && (
                  <span className="tl-note" style={{ display: 'block', color: 'var(--danger)' }}>
                    Failed{s.error_code ? ` (${s.error_code})` : ''}.{' '}
                    <button
                      className="link-btn"
                      disabled={busy}
                      onClick={() =>
                        call(
                          () => api(`/api/v1/tasks/${task.id}/retry`, { method: 'POST', body: { step_id: s.id, confirm_retry: true } }),
                          'Step queued for retry.',
                        )
                      }
                    >
                      Retry step
                    </button>
                  </span>
                )}
              </span>
            </div>
          ))}

          <div className="section-head" style={{ marginTop: 22 }}>
            <h2>Activity</h2>
          </div>
          {latest_events.length === 0 && <div className="empty-mini">No events yet.</div>}
          {latest_events.slice(0, 12).map((e) => (
            <div key={e.id} className="feed-item">
              <span className="feed-text">
                {e.payload?.message ?? e.event_type.replace(/[._]/g, ' ')}
              </span>
              <span className="feed-time">{timeAgo(e.created_at)}</span>
            </div>
          ))}
        </section>

        <div className="task-side">
          {pendingConfirmations.map((a) => {
            const token = confirmationToken(a.id);
            return (
              <section key={a.id} className="panel gate-panel">
                <span className="pill pill-warn">
                  <Clock size={12} /> Confirmation needed
                </span>
                <h3 className="gate-heading">{a.summary}</h3>
                <p className="gate-desc">
                  This change has only been prepared — nothing has happened yet. It runs once, exactly as
                  described, when you confirm with its one-time token. The token expires{' '}
                  {fmtDateTime(a.expires_at)}; after that the step must be prepared again.
                </p>
                <div className="btn-row">
                  <button
                    className="btn btn-primary"
                    disabled={busy || !token}
                    onClick={() =>
                      token &&
                      call(
                        () => api(`/api/v1/confirmations/${a.id}/confirm`, { method: 'POST', body: { token } }),
                        'Confirmed and executed. The task continues.',
                      )
                    }
                  >
                    Confirm &amp; execute
                  </button>
                  <button
                    className="btn btn-ghost"
                    disabled={busy}
                    onClick={() =>
                      call(
                        () => api(`/api/v1/confirmations/${a.id}/cancel`, { method: 'POST', body: {} }),
                        'Cancelled. Nothing was changed, and the task is paused.',
                      )
                    }
                  >
                    Cancel
                  </button>
                </div>
                {!token && (
                  <p className="gate-desc" style={{ marginTop: 8 }}>
                    The confirmation token is on its way with the task event stream — reload in a moment if
                    this persists.
                  </p>
                )}
              </section>
            );
          })}

          {pending.map((d) => (
            <section key={d.id} className="panel gate-panel">
              <span className="pill pill-warn">
                <Clock size={12} /> Decision needed
              </span>
              <h3 className="gate-heading">{d.prompt}</h3>
              {d.decision_type === 'choose_option' && d.options.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}>
                  {d.options.map((o) => (
                    <button
                      key={o.value}
                      className="tone-card"
                      style={{ textAlign: 'left', cursor: 'pointer', marginTop: 0 }}
                      disabled={busy}
                      onClick={() => resolve(d, { choice: o.value }, `“${o.label}” selected. The task continues.`)}
                    >
                      <span className="tone-head">{o.label}</span>
                      {o.description && <span className="tone-desc" style={{ display: 'block' }}>{o.description}</span>}
                    </button>
                  ))}
                </div>
              )}
              {d.decision_type === 'free_text' && (
                <div style={{ marginTop: 12 }}>
                  <textarea
                    className="result-edit"
                    rows={3}
                    value={freeText[d.id] ?? ''}
                    onChange={(e) => setFreeText((p) => ({ ...p, [d.id]: e.target.value }))}
                    placeholder="Type your instruction…"
                    aria-label="Your instruction"
                  />
                  <div className="btn-row">
                    <button
                      className="btn btn-primary"
                      disabled={busy || !(freeText[d.id] ?? '').trim()}
                      onClick={() => resolve(d, { choice: 'approve', comment: freeText[d.id] }, 'Instruction sent to the Agent.')}
                    >
                      Send instruction
                    </button>
                    <button className="btn btn-ghost" disabled={busy} onClick={() => resolve(d, { choice: 'reject' }, 'Declined. The task is paused.')}>
                      Decline &amp; pause
                    </button>
                  </div>
                </div>
              )}
              {(d.decision_type === 'approve_step' || d.decision_type === 'approve_send') && (
                <div className="btn-row">
                  <button
                    className="btn btn-primary"
                    disabled={busy}
                    onClick={() =>
                      d.decision_type === 'approve_send'
                        ? navigate('/review')
                        : resolve(d, { choice: 'approve' }, 'Approved. The task continues.')
                    }
                  >
                    {d.decision_type === 'approve_send' ? 'Review the draft' : 'Approve step'}
                  </button>
                  <button className="btn btn-ghost" disabled={busy} onClick={() => resolve(d, { choice: 'reject' }, 'Declined. The task is paused.')}>
                    Decline
                  </button>
                </div>
              )}
            </section>
          ))}

          {pendingOutputs.length > 0 && (
            <section className="panel draft-preview-panel">
              <div className="section-head">
                <h2>Draft waiting in Review Queue</h2>
              </div>
              <p className="gate-desc">This task produced a draft that needs your approval before anything is sent.</p>
              <div className="btn-row">
                <button className="btn btn-primary" onClick={() => navigate('/review')}>
                  Open Review Queue
                </button>
              </div>
            </section>
          )}

          <section className="panel controls-panel">
            <div className="section-head">
              <h2>Controls</h2>
            </div>
            <div className="btn-row" style={{ marginTop: 0 }}>
              {(task.status === 'running' || task.status === 'waiting_user') && (
                <button className="btn btn-dark" disabled={busy} onClick={() => call(() => api(`/api/v1/tasks/${task.id}/pause`, { method: 'POST', body: {} }), 'Task paused.')}>
                  <Pause size={14} /> Pause
                </button>
              )}
              {(task.status === 'paused' || task.status === 'needs_attention') && (
                <button
                  className="btn btn-dark"
                  disabled={busy}
                  onClick={() => call(() => api(`/api/v1/tasks/${task.id}/resume`, { method: 'POST', body: { acknowledge_summary: true } }), 'Task resumed.')}
                >
                  <Play size={14} /> Resume
                </button>
              )}
              {!['completed', 'cancelled', 'takeover'].includes(task.status) && (
                <button
                  className="btn btn-dark"
                  disabled={busy}
                  onClick={() => call(() => api(`/api/v1/tasks/${task.id}/takeover`, { method: 'POST', body: {} }), 'You have manual control. The Agent will not act.')}
                >
                  <Hand size={14} /> Take over
                </button>
              )}
              {task.status === 'takeover' && (
                <button
                  className="btn btn-dark"
                  disabled={busy}
                  onClick={() => call(() => api(`/api/v1/tasks/${task.id}/return-control`, { method: 'POST', body: {} }), 'Control returned to the Agent.')}
                >
                  <RotateCcw size={14} /> Return control
                </button>
              )}
              {!['completed', 'cancelled'].includes(task.status) && (
                <button className="btn btn-danger-ghost" disabled={busy} onClick={() => setConfirmEnd(true)}>
                  <X size={14} /> End task
                </button>
              )}
            </div>
            <p className="panel-foot-note">
              Ending a task preserves completed steps, drafts and the audit record. Nothing already approved is undone.
            </p>
          </section>

          <section className="panel result-panel">
            <div className="section-head">
              <h2>Plan &amp; authorization</h2>
            </div>
            <dl className="kv-grid">
              <dt>Authorization</dt>
              <dd>{MODE_LABEL[task.authorization_mode] ?? task.authorization_mode}</dd>
              <dt>Environment</dt>
              <dd>{task.environment === 'demo' ? 'Demo mailbox (simulated sends)' : 'Live mailbox'}</dd>
              <dt>Created</dt>
              <dd>{fmtDateTime(task.created_at)}</dd>
              <dt>Updated</dt>
              <dd>{fmtDateTime(task.updated_at)}</dd>
            </dl>
            {task.plan?.steps && (
              <div style={{ marginTop: 12 }}>
                {task.plan.steps.map((s: any, i: number) => (
                  <div key={i} className="plan-step">
                    <span className="plan-num">{i + 1}</span>
                    <span className="plan-step-main">
                      <span className="plan-step-label" style={{ fontSize: 13.5 }}>{s.title}</span>
                      <span className="plan-step-note" style={{ display: 'block' }}>{String(s.step_type).replace(/_/g, ' ')}</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>

      {confirmEnd && (
        <Modal
          title="End this task?"
          sub="Completed work, drafts and records are preserved. The Agent stops acting on this task immediately."
          onClose={() => setConfirmEnd(false)}
        >
          <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn btn-ghost" onClick={() => setConfirmEnd(false)}>
              Keep task
            </button>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() =>
                call(
                  () => api(`/api/v1/tasks/${task.id}/terminate`, { method: 'POST', body: { confirmation: true } }),
                  'Task ended.',
                ).then(() => setConfirmEnd(false))
              }
            >
              End task
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
