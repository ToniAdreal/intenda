import { useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { api, type AuditEntry } from '../lib/api';
import { ErrorCard, SkeletonRows, fmtTime, useAsync } from '../lib/ui';

const ACTION_LABEL: Record<string, string> = {
  archive_threads: 'Archive conversations',
  trash_threads: 'Move to trash',
  label_messages: 'Apply label',
  create_todo: 'Create to-do',
  send_review_item: 'Send email',
  prepare_draft: 'Prepare draft',
  draft_email: 'Draft email',
  archive_email: 'Archive mail',
  label_message: 'Label mail',
  automation_run: 'Automation run',
};

const OUTCOME_PILL: Record<AuditEntry['outcome'], { cls: string; label: string }> = {
  executed: { cls: 'pill pill-ok', label: 'Executed' },
  skipped: { cls: 'pill pill-warn', label: 'Skipped' },
  failed: { cls: 'pill pill-danger', label: 'Failed' },
};

const ACTOR_LABEL: Record<AuditEntry['actor'], string> = {
  agent: 'Agent',
  user: 'You',
  system: 'System',
};

const REASON_LABEL: Record<string, string> = {
  injection_suspect: 'Possible prompt injection',
};

export default function Activity() {
  const [actionType, setActionType] = useState('');
  const [outcome, setOutcome] = useState('');
  const { data, error, loading, reload } = useAsync(
    () => {
      const params = new URLSearchParams({ limit: '100' });
      if (actionType) params.set('action_type', actionType);
      if (outcome) params.set('outcome', outcome);
      return api<{ items: AuditEntry[]; total: number }>(`/api/v1/audit?${params.toString()}`);
    },
    [actionType, outcome],
  );

  const entries = data?.items ?? [];

  return (
    <div className="view">
      <div className="view-head with-pill">
        <div>
          <span className="kicker">ACTIVITY</span>
          <h1>What the Agent did, and who approved it</h1>
          <p className="view-sub">
            Every action the Agent executes leaves a record here: what was done, which email it
            was based on, who approved it, and how it ended. Actions the safety guard stopped
            are recorded too.
          </p>
        </div>
      </div>

      <div className="panel" style={{ padding: '14px 16px', marginBottom: 18 }}>
        <div className="chip-row" role="group" aria-label="Filter the audit trail">
          <select
            className="chip"
            aria-label="Filter by action"
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
          >
            <option value="">All actions</option>
            {Object.entries(ACTION_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            className="chip"
            aria-label="Filter by outcome"
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
          >
            <option value="">All outcomes</option>
            <option value="executed">Executed</option>
            <option value="skipped">Skipped</option>
            <option value="failed">Failed</option>
          </select>
          <button className="btn btn-ghost" type="button" onClick={reload}>
            Refresh
          </button>
        </div>
      </div>

      {loading && <SkeletonRows rows={5} />}
      {error != null && <ErrorCard error={error} onRetry={reload} />}
      {!loading && !error && entries.length === 0 && (
        <div className="panel" style={{ padding: '20px 22px' }}>
          <div className="empty-mini">
            No audit entries yet. When the Agent archives, labels, drafts, sends or is stopped
            from acting, the record will appear here.
          </div>
        </div>
      )}
      {!loading && entries.length > 0 && (
        <section className="panel" style={{ padding: '6px 18px' }}>
          <div className="list-rows">
            {entries.map((e) => {
              const pill = OUTCOME_PILL[e.outcome];
              return (
                <div key={e.id} className="list-row" style={{ cursor: 'default' }}>
                  <span className="list-row-main">
                    <span className="list-row-title">{e.description}</span>
                    <span className="list-row-meta" style={{ display: 'block' }}>
                      {fmtTime(e.created_at)} · {ACTOR_LABEL[e.actor] ?? e.actor} ·{' '}
                      {ACTION_LABEL[e.action_type] ?? e.action_type}
                    </span>
                    {e.approval_summary && (
                      <span className="list-row-meta" style={{ display: 'block' }}>
                        Approval: {e.approval_summary}
                      </span>
                    )}
                    {e.source_basis.length > 0 && (
                      <span className="list-row-meta" style={{ display: 'block' }}>
                        Based on: {e.source_basis.map((b) => b.label).join('; ')}
                      </span>
                    )}
                    {e.outcome !== 'executed' && e.reason && (
                      <span className="list-row-meta" style={{ display: 'block' }}>
                        {e.reason === 'injection_suspect' ? (
                          <>
                            <ShieldAlert size={13} style={{ verticalAlign: '-2px' }} /> Stopped
                            because: {REASON_LABEL[e.reason] ?? e.reason}
                          </>
                        ) : (
                          <>Reason: {e.reason}</>
                        )}
                      </span>
                    )}
                  </span>
                  <span className={pill.cls}>{pill.label}</span>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
