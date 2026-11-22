/* Intenda API client — talks to the FastAPI backend (same origin in
   production; Vite proxies /api to localhost:8000 in dev). Handles the
   success/error envelope, the double-submit CSRF token, SSE streaming for
   the Agent, and the WebSocket channel. */

export class ApiError extends Error {
  status: number;
  code: string;
  retryable: boolean;
  constructor(status: number, code: string, message: string, retryable = false) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

function csrfToken(): string | null {
  const m = document.cookie.match(/(?:^|;\s*)intenda_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

const MUTATING = new Set(['POST', 'PATCH', 'PUT', 'DELETE']);

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: unknown; idempotencyKey?: string } = {},
): Promise<T> {
  const method = opts.method ?? 'GET';
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (MUTATING.has(method)) {
    const token = csrfToken();
    if (token) headers['X-CSRF-Token'] = token;
  }
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'Could not reach the server. Check your connection and retry.', true);
  }
  const text = await res.text();
  let json: any = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(res.status, 'BAD_RESPONSE', 'The server returned an unexpected response.');
  }
  if (!res.ok || json?.ok === false) {
    const err = json?.error ?? {};
    throw new ApiError(res.status, err.code ?? 'ERROR', err.message ?? 'Request failed.', !!err.retryable);
  }
  return json.data as T;
}

export function idemKey(): string {
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/* ---------------- SSE agent streaming ---------------- */

export interface AgentStreamHandlers {
  onDelta?: (text: string) => void;
  onAnnotation?: (annotation: any) => void;
  onCompleted?: (message: any) => void;
  onError?: (message: any) => void;
}

export async function streamAgentMessage(
  conversationId: string,
  content: string,
  context: Record<string, unknown>,
  handlers: AgentStreamHandlers,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = csrfToken();
  if (token) headers['X-CSRF-Token'] = token;
  const res = await fetch(`/api/v1/agent/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers,
    credentials: 'same-origin',
    body: JSON.stringify({ content, stream: true, context }),
  });
  if (!res.ok || !res.body) {
    let msg = 'The Agent request failed.';
    try {
      const j = await res.json();
      msg = j?.error?.message ?? msg;
    } catch { /* keep default */ }
    handlers.onError?.({ content: msg });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const rawEvent = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = 'message';
      let data = '';
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!data) continue;
      let payload: any;
      try {
        payload = JSON.parse(data);
      } catch {
        continue;
      }
      if (event === 'message.delta') handlers.onDelta?.(payload.delta ?? '');
      else if (event === 'message.annotation') handlers.onAnnotation?.(payload.annotation ?? (payload.annotation_type ? { type: payload.annotation_type, plan: payload.plan } : undefined));
      else if (event === 'message.completed') handlers.onCompleted?.(payload.message);
      else if (event === 'message.error') handlers.onError?.(payload.message);
    }
  }
}

/* ---------------- WebSocket ---------------- */

export function connectWs(onEvent: (event: any) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: number | undefined;

  const open = () => {
    if (closed) return;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${window.location.host}/api/v1/ws`);
    ws.onopen = () => {
      attempt = 0;
      ws?.send(JSON.stringify({ type: 'client.hello', timestamp: new Date().toISOString() }));
    };
    ws.onmessage = (ev) => {
      try {
        onEvent(JSON.parse(ev.data));
      } catch { /* ignore malformed */ }
    };
    ws.onclose = () => {
      if (closed) return;
      attempt += 1;
      const delay = Math.min(1000 * 2 ** attempt, 30000);
      timer = window.setTimeout(open, delay);
    };
    ws.onerror = () => ws?.close();
  };
  open();
  return () => {
    closed = true;
    if (timer) window.clearTimeout(timer);
    ws?.close();
  };
}

/* ---------------- shared types (mirror server serializers) ---------------- */

export interface Me {
  id: string;
  name: string;
  email: string;
  status: string;
  created_at: string;
}

export interface OnboardingStatus {
  state: string;
  mailbox_choice: string | null;
  completed_steps: string[];
  last_error_code: string | null;
  mail_accounts: MailAccount[];
}

export interface SyncJob {
  id: string;
  mail_account_id: string;
  mode: string;
  environment: string;
  status: string;
  phase: string;
  processed_count: number;
  total_count: number;
  percent: number;
  simulated: boolean;
  error_code: string | null;
}

export interface MailAccount {
  id: string;
  provider: string;
  email_address: string;
  display_name: string | null;
  environment: 'demo' | 'live';
  status: string;
  scopes: string[];
  last_sync_at: string | null;
  created_at: string;
}

export interface Person {
  name: string | null;
  email: string;
}

export interface InjectionFlag {
  field: string;
  pattern: string;
  reason: string;
  excerpt?: string;
}

export interface MailMessage {
  id: string;
  thread_id: string;
  mail_account_id: string;
  from_address: string;
  from_name: string | null;
  to_recipients: Person[];
  cc_recipients: Person[];
  subject: string;
  body_text?: string;
  snippet: string;
  is_read: boolean;
  is_starred: boolean;
  is_draft: boolean;
  is_sent: boolean;
  is_trashed: boolean;
  has_attachment: boolean;
  injection_suspect?: boolean;
  injection_flags?: InjectionFlag[];
  environment: 'demo' | 'live';
  created_at: string;
  attachments?: Attachment[];
}

export interface Attachment {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  scan_status: string;
}

export interface ThreadItem {
  id: string;
  subject: string;
  snippet: string | null;
  participants: Person[];
  last_message_at: string;
  message_count: number;
  unread_count: number;
  is_archived: boolean;
  is_trashed: boolean;
  injection_suspect?: boolean;
  environment: 'demo' | 'live';
  latest_message?: MailMessage;
}

export interface Draft {
  id: string;
  thread_id: string | null;
  source_message_id: string | null;
  message_id: string | null;
  mail_account_id: string;
  to_recipients: Person[];
  cc_recipients: Person[];
  subject: string;
  body_text: string;
  status: string;
  revision: number;
  environment: 'demo' | 'live';
  updated_at: string;
  created_at: string;
}

export interface TaskStep {
  id: string;
  sequence: number;
  step_type: string;
  title: string;
  description: string | null;
  status: string;
  risk_level: string;
  requires_approval: boolean;
  output_refs: Record<string, any>;
  error_code: string | null;
}

export interface TaskItem {
  id: string;
  display_id: string;
  goal: string;
  status: string;
  authorization_mode: string;
  plan: any;
  context_scope: any;
  progress_percent: number;
  current_step_id: string | null;
  environment: 'demo' | 'live';
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface DecisionOption {
  value: string;
  label: string;
  description?: string;
}

export interface Decision {
  id: string;
  task_id: string;
  step_id: string | null;
  decision_type: string;
  prompt: string;
  options: DecisionOption[];
  status: string;
  response: Record<string, unknown> | null;
  created_at: string;
}

/** Two-phase confirmation protocol: a prepared Agent write action.
 * Phase 1 (prepare) changes nothing; the action executes only when
 * confirmed with its one-time token before `expires_at`. */
export interface PendingAction {
  id: string;
  task_id: string | null;
  step_id: string | null;
  source: string;
  action_type: string;
  summary: string;
  payload: Record<string, any>;
  status: 'pending' | 'executed' | 'cancelled' | 'expired';
  result: Record<string, any> | null;
  expires_at: string;
  resolved_at: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: string;
  actor: 'agent' | 'user' | 'system';
  action_type: string;
  description: string;
  task_id: string | null;
  step_id: string | null;
  thread_ids: string[];
  message_ids: string[];
  subjects: string[];
  source_basis: { kind: string; id: string; label: string; thread_id?: string }[];
  approval_type: 'confirmation' | 'decision' | 'review' | 'user' | 'none';
  approval_id: string | null;
  approval_summary: string | null;
  outcome: 'executed' | 'skipped' | 'failed';
  reason: string | null;
  detail: Record<string, any>;
  environment: 'demo' | 'live';
  created_at: string;
}

export interface ReviewItem {
  id: string;
  task_id: string | null;
  task_display_id?: string | null;
  draft_id: string;
  status: string;
  agent_note: string | null;
  source_refs: Record<string, unknown>;
  revision: number;
  environment: 'demo' | 'live';
  created_at: string;
  draft: Draft;
}

export interface Automation {
  id: string;
  name: string;
  trigger: { type: string; schedule?: string };
  conditions: Record<string, string>;
  actions: { type: string }[];
  authorization_mode: string;
  enabled: boolean;
  revision: number;
  last_run_at: string | null;
  created_at: string;
}

export interface BriefingDecision {
  kind: 'review' | 'decision';
  id: string;
  review_id?: string;
  decision_id?: string;
  task_id: string | null;
  task_display_id: string | null;
  subject: string;
  recipient: string | null;
  recommendation: string | null;
  options?: DecisionOption[];
  status: string;
  created_at: string;
}

export interface Briefing {
  environment: 'demo' | 'live' | 'none';
  has_mailbox: boolean;
  metrics: {
    new_mail: number;
    needs_decision: number;
    in_progress: number;
    delivered: number;
    sent: number;
  };
  decisions: BriefingDecision[];
  tasks: TaskItem[];
  recent_messages: MailMessage[];
  recent_activity: { id: string; action: string; resource_type: string | null; result: string; created_at: string }[];
  todos: { id: string; title: string; due_at: string | null; status: string }[];
  badges: { unread: number; review: number; decisions: number; tasks_active: number; notifications: number };
}
