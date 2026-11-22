"""Model -> API dict serializers."""
from __future__ import annotations

from . import models as m
from .ws import utc_iso


def user(u: m.User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "status": u.status,
        "created_at": utc_iso(u.created_at),
        "email_verified_at": utc_iso(u.email_verified_at),
    }


def session(s: m.Session, current_id: str | None = None) -> dict:
    return {
        "id": s.id,
        "user_agent": s.user_agent,
        "created_at": utc_iso(s.created_at),
        "last_seen_at": utc_iso(s.last_seen_at),
        "expires_at": utc_iso(s.expires_at),
        "current": s.id == current_id,
    }


def mail_account(a: m.MailAccount) -> dict:
    return {
        "id": a.id,
        "provider": a.provider,
        "email_address": a.email_address,
        "display_name": a.display_name,
        "environment": a.environment,
        "status": a.status,
        "scopes": a.scopes or [],
        "last_sync_at": utc_iso(a.last_sync_at),
        "created_at": utc_iso(a.created_at),
    }


def message(msg: m.EmailMessage, attachments: list | None = None) -> dict:
    data = {
        "id": msg.id,
        "mail_account_id": msg.mail_account_id,
        "thread_id": msg.thread_id,
        "from_address": msg.from_address,
        "from_name": msg.from_name,
        "to_recipients": msg.to_recipients or [],
        "cc_recipients": msg.cc_recipients or [],
        "bcc_recipients": msg.bcc_recipients or [],
        "subject": msg.subject,
        "body_text": msg.body_text,
        "snippet": msg.snippet,
        "received_at": utc_iso(msg.received_at),
        "sent_at": utc_iso(msg.sent_at),
        "is_read": msg.is_read,
        "is_starred": msg.is_starred,
        "is_draft": msg.is_draft,
        "is_sent": msg.is_sent,
        "is_trashed": msg.is_trashed,
        "injection_suspect": bool(msg.injection_suspect),
        "injection_flags": msg.injection_flags or [],
        "environment": msg.environment,
        "has_attachment": bool(attachments),
        "created_at": utc_iso(msg.created_at),
    }
    if attachments is not None:
        data["attachments"] = [attachment(a) for a in attachments]
    return data


def message_summary(msg: m.EmailMessage, has_attachment: bool = False) -> dict:
    data = message(msg)
    data.pop("body_text", None)
    data["has_attachment"] = has_attachment
    return data


def attachment(a: m.EmailAttachment) -> dict:
    return {
        "id": a.id,
        "message_id": a.message_id,
        "filename": a.filename,
        "mime_type": a.mime_type,
        "size_bytes": a.size_bytes,
        "is_inline": a.is_inline,
        "scan_status": a.scan_status,
    }


def thread(t: m.EmailThread, latest: m.EmailMessage | None = None) -> dict:
    data = {
        "id": t.id,
        "mail_account_id": t.mail_account_id,
        "subject": t.subject_normalized,
        "snippet": t.snippet,
        "participants": t.participants or [],
        "message_count": t.message_count,
        "last_message_at": utc_iso(t.last_message_at),
        "is_archived": t.is_archived,
        "is_trashed": t.is_trashed,
        "injection_suspect": bool(t.injection_suspect),
        "environment": t.environment,
    }
    if latest is not None:
        data["latest_message"] = message_summary(latest)
    return data


def draft(d: m.EmailDraft) -> dict:
    return {
        "id": d.id,
        "mail_account_id": d.mail_account_id,
        "thread_id": d.thread_id,
        "source_message_id": d.source_message_id,
        "message_id": d.message_id,
        "to_recipients": d.to_recipients or [],
        "cc_recipients": d.cc_recipients or [],
        "bcc_recipients": d.bcc_recipients or [],
        "subject": d.subject,
        "body_text": d.body_text,
        "revision": d.revision,
        "status": d.status,
        "environment": d.environment,
        "created_at": utc_iso(d.created_at),
        "updated_at": utc_iso(d.updated_at),
    }


def sync_job(j: m.SyncJob) -> dict:
    percent = 0
    if j.total_count:
        percent = min(100, round(100 * j.processed_count / j.total_count))
    if j.status == "completed":
        percent = 100
    return {
        "id": j.id,
        "mail_account_id": j.mail_account_id,
        "mode": j.mode,
        "environment": j.environment,
        "status": j.status,
        "phase": j.phase,
        "processed_count": j.processed_count,
        "total_count": j.total_count,
        "percent": percent,
        "simulated": j.environment == "demo",
        "error_code": j.error_code,
        "started_at": utc_iso(j.started_at),
        "completed_at": utc_iso(j.completed_at),
    }


def conversation(c: m.AgentConversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "status": c.status,
        "context_scope": c.context_scope or {},
        "created_at": utc_iso(c.created_at),
        "updated_at": utc_iso(c.updated_at),
        "last_message_at": utc_iso(c.last_message_at),
    }


def agent_message(msg: m.AgentMessage) -> dict:
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "role": msg.role,
        "content": msg.content,
        "content_json": msg.content_json,
        "model_provider": msg.model_provider,
        "model_id": msg.model_id,
        "status": msg.status,
        "error_code": msg.error_code,
        "created_at": utc_iso(msg.created_at),
        "completed_at": utc_iso(msg.completed_at),
    }


def task(t: m.Task) -> dict:
    return {
        "id": t.id,
        "display_id": t.display_id,
        "conversation_id": t.conversation_id,
        "goal": t.goal,
        "status": t.status,
        "authorization_mode": t.authorization_mode,
        "plan": t.plan_json or {},
        "context_scope": t.context_scope or {},
        "progress_percent": t.progress_percent,
        "current_step_id": t.current_step_id,
        "environment": t.environment,
        "error_code": t.error_code,
        "started_at": utc_iso(t.started_at),
        "completed_at": utc_iso(t.completed_at),
        "created_at": utc_iso(t.created_at),
        "updated_at": utc_iso(t.updated_at),
    }


def task_step(s: m.TaskStep) -> dict:
    return {
        "id": s.id,
        "task_id": s.task_id,
        "sequence": s.sequence,
        "step_type": s.step_type,
        "title": s.title,
        "description": s.description,
        "input_refs": s.input_refs or {},
        "output_refs": s.output_refs or {},
        "status": s.status,
        "risk_level": s.risk_level,
        "requires_approval": s.requires_approval,
        "approval_id": s.approval_id,
        "progress_percent": s.progress_percent,
        "retry_count": s.retry_count,
        "error_code": s.error_code,
        "started_at": utc_iso(s.started_at),
        "completed_at": utc_iso(s.completed_at),
    }


def task_event(e: m.TaskEvent) -> dict:
    return {
        "id": e.id,
        "task_id": e.task_id,
        "sequence": e.sequence,
        "event_type": e.event_type,
        "payload": e.payload or {},
        "created_at": utc_iso(e.created_at),
    }


def decision(d: m.Decision) -> dict:
    return {
        "id": d.id,
        "task_id": d.task_id,
        "step_id": d.step_id,
        "decision_type": d.decision_type,
        "prompt": d.prompt,
        "options": d.options or [],
        "status": d.status,
        "response": d.response,
        "expires_at": utc_iso(d.expires_at),
        "resolved_at": utc_iso(d.resolved_at),
        "created_at": utc_iso(d.created_at),
    }


def pending_action(a: m.PendingAction) -> dict:
    # The confirmation token itself is never serialized: only its hash is
    # stored, and the raw token is returned once by the prepare call.
    return {
        "id": a.id,
        "task_id": a.task_id,
        "step_id": a.step_id,
        "source": a.source,
        "action_type": a.action_type,
        "summary": a.summary,
        "payload": a.payload or {},
        "status": a.status,
        "result": a.result,
        "expires_at": utc_iso(a.expires_at),
        "resolved_at": utc_iso(a.resolved_at),
        "created_at": utc_iso(a.created_at),
    }


def review_item(r: m.ReviewItem, d: m.EmailDraft | None = None) -> dict:
    data = {
        "id": r.id,
        "task_id": r.task_id,
        "draft_id": r.draft_id,
        "status": r.status,
        "agent_note": r.agent_note,
        "source_refs": r.source_refs or {},
        "revision": r.revision,
        "environment": r.environment,
        "approved_at": utc_iso(r.approved_at),
        "rejected_at": utc_iso(r.rejected_at),
        "created_at": utc_iso(r.created_at),
        "updated_at": utc_iso(r.updated_at),
    }
    if d is not None:
        data["draft"] = draft(d)
    return data


def automation(a: m.Automation) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "trigger": a.trigger or {},
        "conditions": a.conditions or {},
        "actions": a.actions or [],
        "authorization_mode": a.authorization_mode,
        "enabled": a.enabled,
        "revision": a.revision,
        "last_run_at": utc_iso(a.last_run_at),
        "next_run_at": utc_iso(a.next_run_at),
        "created_at": utc_iso(a.created_at),
        "updated_at": utc_iso(a.updated_at),
    }


def automation_run(r: m.AutomationRun) -> dict:
    return {
        "id": r.id,
        "automation_id": r.automation_id,
        "status": r.status,
        "trigger_payload": r.trigger_payload or {},
        "result_payload": r.result_payload,
        "error_code": r.error_code,
        "started_at": utc_iso(r.started_at),
        "completed_at": utc_iso(r.completed_at),
    }


def contact(c: m.Contact) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "company": c.company,
        "notes": c.notes,
        "source": c.source,
        "created_at": utc_iso(c.created_at),
    }


def todo(t: m.Todo) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "due_at": utc_iso(t.due_at),
        "status": t.status,
        "source_task_id": t.source_task_id,
        "source_message_id": t.source_message_id,
        "created_by": t.created_by,
        "created_at": utc_iso(t.created_at),
        "completed_at": utc_iso(t.completed_at),
    }


def notification(n: m.Notification) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "resource_type": n.resource_type,
        "resource_id": n.resource_id,
        "read_at": utc_iso(n.read_at),
        "created_at": utc_iso(n.created_at),
    }
