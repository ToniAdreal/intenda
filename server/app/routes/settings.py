"""Settings, data export/deletion and contacts/to-dos endpoints (7.4.11, 7.4.12)."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from .. import serializers as ser
from ..config import settings as app_settings
from ..db import get_db
from ..deps import audit, get_current_user
from ..errors import ApiError, ok
from ..helpers import clear_session_cookies, get_settings_row, paginate
from ..security import verify_password
from ..services import agent as agent_svc

router = APIRouter(prefix="/api/v1", tags=["settings"])

EXPORTS: dict[str, str] = {}


def _settings_payload(db: DbSession, user: m.User) -> dict:
    row = get_settings_row(db, user)
    accounts = (
        db.query(m.MailAccount)
        .filter(m.MailAccount.user_id == user.id, m.MailAccount.deleted_at.is_(None))
        .all()
    )
    from .. import agent_identity

    identity = agent_identity.identity_for_user(db, user.id)
    return {
        "profile": {"name": user.name, "email": user.email, "created_at": ser.utc_iso(user.created_at)},
        "appearance": {"mode": row.appearance},
        "agent": {
            **(row.agent_preferences or {}),
            "default_authorization_mode": row.default_authorization_mode,
            "model_provider": app_settings.LLM_PROVIDER_NAME,
            "model_id": app_settings.LLM_MODEL,
            "model_available": agent_svc.model_available(),
            "data_use": "Only email context you explicitly select is sent to the model. Mailbox content is never used to train a model.",
        },
        "notifications": row.notification_preferences or {},
        "privacy": row.privacy_preferences or {},
        "mailboxes": [ser.mail_account(a) for a in accounts],
        "agent_identity": ser.agent_identity(identity) if identity is not None else None,
        "about": {"app": "Intenda", "version": "1.0.0",
                  "environment": "demo" if any(a.environment == "demo" for a in accounts) else "live"},
    }


@router.get("/settings")
def get_settings(request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    return ok(request, _settings_payload(db, user))


class SettingsPatchBody(BaseModel):
    profile: dict | None = None
    appearance: dict | str | None = None
    agent: dict | None = None
    notifications: dict | None = None
    privacy: dict | None = None


@router.patch("/settings")
def patch_settings(body: SettingsPatchBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    row = get_settings_row(db, user)
    if body.profile and body.profile.get("name"):
        user.name = str(body.profile["name"])[:120]
    if body.appearance is not None:
        mode = body.appearance.get("mode") if isinstance(body.appearance, dict) else body.appearance
        if mode not in {"day", "night"}:
            raise ApiError(422, "VALIDATION_ERROR", "Appearance mode must be day or night.", {"field": "appearance"})
        row.appearance = mode
    if body.agent:
        prefs = dict(row.agent_preferences or {})
        for key in ("response_style", "context_permission"):
            if key in body.agent:
                prefs[key] = body.agent[key]
        row.agent_preferences = prefs
        if body.agent.get("default_authorization_mode") in {
            "confirm_every_step", "confirm_milestones", "confirm_before_send",
        }:
            row.default_authorization_mode = body.agent["default_authorization_mode"]
    if body.notifications is not None:
        merged = dict(row.notification_preferences or {})
        merged.update(body.notifications)
        row.notification_preferences = merged
    if body.privacy is not None:
        merged = dict(row.privacy_preferences or {})
        merged.update(body.privacy)
        row.privacy_preferences = merged
    db.commit()
    audit(db, user, "settings.updated", "user_settings", row.id, request=request)
    db.commit()
    return ok(request, {"settings": _settings_payload(db, user)})


class ExportBody(BaseModel):
    format: str = "json"


@router.post("/settings/data-export")
def data_export(body: ExportBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    if body.format != "json":
        raise ApiError(422, "VALIDATION_ERROR", "Only JSON export is supported in v1.0.")
    messages = db.query(m.EmailMessage).filter(m.EmailMessage.user_id == user.id).all()
    threads = db.query(m.EmailThread).filter(m.EmailThread.user_id == user.id).all()
    drafts = db.query(m.EmailDraft).filter(m.EmailDraft.user_id == user.id).all()
    tasks = db.query(m.Task).filter(m.Task.user_id == user.id).all()
    contacts = db.query(m.Contact).filter(m.Contact.user_id == user.id).all()
    todos = db.query(m.Todo).filter(m.Todo.user_id == user.id).all()
    audit_rows = db.query(m.AuditLog).filter(m.AuditLog.user_id == user.id).all()
    payload = {
        "exported_for": {"name": user.name, "email": user.email},
        "settings": _settings_payload(db, user),
        "threads": [ser.thread(t) for t in threads],
        "messages": [ser.message(x) for x in messages],
        "drafts": [ser.draft(d) for d in drafts],
        "tasks": [ser.task(t) for t in tasks],
        "contacts": [ser.contact(c) for c in contacts],
        "todos": [ser.todo(t) for t in todos],
        "audit_log": [
            {"action": a.action, "resource_type": a.resource_type, "result": a.result,
             "created_at": ser.utc_iso(a.created_at)} for a in audit_rows
        ],
    }
    job_id = str(uuid.uuid4())
    EXPORTS[job_id] = json.dumps(payload, indent=2)
    audit(db, user, "settings.data_export", "user", user.id, request=request)
    db.commit()
    return ok(request, {"export_job": {"id": job_id, "status": "completed",
                                       "download_url": f"/api/v1/settings/data-export/{job_id}/download"}}, status=201)


@router.get("/settings/data-export/{job_id}")
def data_export_status(job_id: str, request: Request, user: m.User = Depends(get_current_user)):
    if job_id not in EXPORTS:
        raise ApiError(404, "NOT_FOUND", "Export job not found.")
    return ok(request, {"status": "completed",
                        "download_url": f"/api/v1/settings/data-export/{job_id}/download"})


@router.get("/settings/data-export/{job_id}/download")
def data_export_download(job_id: str, user: m.User = Depends(get_current_user)):
    content = EXPORTS.get(job_id)
    if content is None:
        raise ApiError(404, "NOT_FOUND", "Export job not found.")
    return JSONResponse(
        content=json.loads(content),
        headers={"Content-Disposition": f'attachment; filename="intenda-export-{job_id}.json"'},
    )


class DeleteDemoBody(BaseModel):
    confirm: bool = False


@router.post("/settings/delete-demo-data")
def delete_demo_data(body: DeleteDemoBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    if not body.confirm:
        raise ApiError(422, "VALIDATION_ERROR", "confirm=true is required.")
    counts: dict[str, int] = {}
    demo_accounts = (
        db.query(m.MailAccount)
        .filter(m.MailAccount.user_id == user.id, m.MailAccount.environment == "demo")
        .all()
    )
    account_ids = [a.id for a in demo_accounts]
    if account_ids:
        msg_ids = [x.id for x in db.query(m.EmailMessage).filter(m.EmailMessage.mail_account_id.in_(account_ids)).all()]
        if msg_ids:
            counts["attachments"] = db.query(m.EmailAttachment).filter(m.EmailAttachment.message_id.in_(msg_ids)).delete(synchronize_session=False)
            counts["messages"] = db.query(m.EmailMessage).filter(m.EmailMessage.id.in_(msg_ids)).delete(synchronize_session=False)
        counts["threads"] = db.query(m.EmailThread).filter(m.EmailThread.mail_account_id.in_(account_ids)).delete(synchronize_session=False)
        counts["drafts"] = db.query(m.EmailDraft).filter(m.EmailDraft.mail_account_id.in_(account_ids)).delete(synchronize_session=False)
        counts["sync_jobs"] = db.query(m.SyncJob).filter(m.SyncJob.mail_account_id.in_(account_ids)).delete(synchronize_session=False)
    demo_tasks = db.query(m.Task).filter(m.Task.user_id == user.id, m.Task.environment == "demo").all()
    task_ids = [t.id for t in demo_tasks]
    if task_ids:
        review_ids = [r.id for r in db.query(m.ReviewItem).filter(m.ReviewItem.task_id.in_(task_ids)).all()]
        counts["review_items"] = db.query(m.ReviewItem).filter(m.ReviewItem.task_id.in_(task_ids)).delete(synchronize_session=False)
        counts["decisions"] = db.query(m.Decision).filter(m.Decision.task_id.in_(task_ids)).delete(synchronize_session=False)
        counts["task_events"] = db.query(m.TaskEvent).filter(m.TaskEvent.task_id.in_(task_ids)).delete(synchronize_session=False)
        counts["task_steps"] = db.query(m.TaskStep).filter(m.TaskStep.task_id.in_(task_ids)).delete(synchronize_session=False)
        counts["tasks"] = db.query(m.Task).filter(m.Task.id.in_(task_ids)).delete(synchronize_session=False)
        orphan_drafts = db.query(m.EmailDraft).filter(m.EmailDraft.user_id == user.id, m.EmailDraft.environment == "demo").all()
        for d in orphan_drafts:
            db.delete(d)
    for a in demo_accounts:
        db.delete(a)
    counts["mail_accounts"] = len(demo_accounts)
    ob = db.query(m.OnboardingState).filter(m.OnboardingState.user_id == user.id).first()
    if ob is not None:
        ob.state = "ACCOUNT_CREATED"
        ob.mailbox_choice = None
        ob.completed_steps = ["account"]
    audit(db, user, "settings.demo_data_deleted", "user", user.id, request=request, meta=counts)
    db.commit()
    return ok(request, {"deleted_counts": counts})


class DeleteAccountBody(BaseModel):
    password: str
    confirmation_text: str


@router.delete("/settings/account")
def delete_account(body: DeleteAccountBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    if body.confirmation_text != "DELETE":
        raise ApiError(422, "VALIDATION_ERROR", "Type DELETE to confirm account deletion.", {"field": "confirmation_text"})
    if not verify_password(user.password_hash, body.password):
        raise ApiError(403, "PASSWORD_MISMATCH", "Password is incorrect.")
    user.status = "deleted"
    user.deleted_at = m.now()
    db.query(m.Session).filter(m.Session.user_id == user.id).update({"revoked_at": m.now()})
    audit(db, user, "settings.account_deleted", "user", user.id, request=request)
    db.commit()
    resp = ok(request, {"account_deleted": True})
    return clear_session_cookies(resp)


# --------------------------------------------------------- notifications

@router.get("/notifications")
def list_notifications(request: Request, unread_only: bool = False, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    q = db.query(m.Notification).filter(m.Notification.user_id == user.id)
    if unread_only:
        q = q.filter(m.Notification.read_at.is_(None))
    items = q.order_by(m.Notification.created_at.desc()).limit(50).all()
    unread = db.query(m.Notification).filter(
        m.Notification.user_id == user.id, m.Notification.read_at.is_(None)
    ).count()
    return ok(request, {
        "items": [
            {"id": n.id, "type": n.type, "title": n.title, "body": n.body,
             "resource_type": n.resource_type, "resource_id": n.resource_id,
             "read_at": ser.utc_iso(n.read_at), "created_at": ser.utc_iso(n.created_at)}
            for n in items
        ],
        "unread_count": unread,
    })


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    n = db.get(m.Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Notification not found.")
    if n.read_at is None:
        n.read_at = m.now()
        db.commit()
    return ok(request, {"read": True})


# ------------------------------------------------------------- contacts

@router.get("/contacts")
def list_contacts(request: Request, q: str | None = None, limit: int = 50, cursor: str | None = None,
                  db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    query = db.query(m.Contact).filter(m.Contact.user_id == user.id)
    if q:
        like = f"%{q}%"
        query = query.filter((m.Contact.name.ilike(like)) | (m.Contact.email.ilike(like)))
    query = query.order_by(m.Contact.name)
    items, next_cursor, total = paginate(query, limit, cursor)
    return ok(request, {"items": [ser.contact(c) for c in items], "next_cursor": next_cursor, "total": total})


class ContactBody(BaseModel):
    name: str
    email: str
    company: str | None = None
    notes: str | None = None


@router.post("/contacts")
def create_contact(body: ContactBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    email = body.email.strip().lower()
    existing = db.query(m.Contact).filter(m.Contact.user_id == user.id, m.Contact.email == email).first()
    if existing is not None:
        raise ApiError(409, "CONTACT_EXISTS", "A contact with this email already exists.")
    c = m.Contact(user_id=user.id, name=body.name.strip(), email=email, company=body.company, notes=body.notes)
    db.add(c)
    db.commit()
    db.refresh(c)
    return ok(request, {"contact": ser.contact(c)}, status=201)


class ContactPatchBody(BaseModel):
    name: str | None = None
    email: str | None = None
    company: str | None = None
    notes: str | None = None


@router.patch("/contacts/{contact_id}")
def patch_contact(contact_id: str, body: ContactPatchBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    c = db.get(m.Contact, contact_id)
    if c is None or c.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    if body.name is not None:
        c.name = body.name
    if body.email is not None:
        c.email = body.email.strip().lower()
    if body.company is not None:
        c.company = body.company
    if body.notes is not None:
        c.notes = body.notes
    db.commit()
    db.refresh(c)
    return ok(request, {"contact": ser.contact(c)})


@router.delete("/contacts/{contact_id}")
def delete_contact(contact_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    c = db.get(m.Contact, contact_id)
    if c is None or c.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    db.delete(c)
    db.commit()
    return ok(request, {"deleted": True})


# ---------------------------------------------------------------- todos

@router.get("/todos")
def list_todos(request: Request, status: str | None = None, due_before: str | None = None,
               db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    q = db.query(m.Todo).filter(m.Todo.user_id == user.id)
    if status:
        q = q.filter(m.Todo.status == status)
    items = q.order_by(m.Todo.due_at).all()
    return ok(request, {"items": [ser.todo(t) for t in items]})


class TodoBody(BaseModel):
    title: str
    description: str | None = None
    due_at: str | None = None
    source_task_id: str | None = None


@router.post("/todos")
def create_todo(body: TodoBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    from datetime import datetime

    due = None
    if body.due_at:
        try:
            due = datetime.fromisoformat(body.due_at.replace("Z", "+00:00"))
        except ValueError:
            raise ApiError(422, "VALIDATION_ERROR", "due_at must be ISO 8601.")
    t = m.Todo(user_id=user.id, title=body.title.strip()[:200], description=body.description,
               due_at=due, source_task_id=body.source_task_id, created_by="user")
    db.add(t)
    db.commit()
    db.refresh(t)
    return ok(request, {"todo": ser.todo(t)}, status=201)


class TodoPatchBody(BaseModel):
    title: str | None = None
    description: str | None = None
    due_at: str | None = None
    status: str | None = None


@router.patch("/todos/{todo_id}")
def patch_todo(todo_id: str, body: TodoPatchBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    from datetime import datetime

    t = db.get(m.Todo, todo_id)
    if t is None or t.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "To-do not found.")
    if body.title is not None:
        t.title = body.title
    if body.description is not None:
        t.description = body.description
    if body.due_at is not None:
        try:
            t.due_at = datetime.fromisoformat(body.due_at.replace("Z", "+00:00"))
        except ValueError:
            raise ApiError(422, "VALIDATION_ERROR", "due_at must be ISO 8601.")
    if body.status is not None:
        if body.status not in {"open", "completed", "cancelled"}:
            raise ApiError(422, "VALIDATION_ERROR", "Status must be open, completed or cancelled.")
        t.status = body.status
        t.completed_at = m.now() if body.status == "completed" else None
    db.commit()
    db.refresh(t)
    return ok(request, {"todo": ser.todo(t)})


@router.delete("/todos/{todo_id}")
def delete_todo(todo_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    t = db.get(m.Todo, todo_id)
    if t is None or t.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "To-do not found.")
    db.delete(t)
    db.commit()
    return ok(request, {"deleted": True})
