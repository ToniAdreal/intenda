"""Review Queue endpoints (spec 7.4.8, Section 6.8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from .. import serializers as ser
from ..db import get_db
from ..deps import audit, get_current_user
from ..errors import ApiError, ok
from ..helpers import idempotency_lookup, idempotency_store, paginate
from ..services import tasks_svc
from ..ws import manager

router = APIRouter(prefix="/api/v1/review", tags=["review"])


def _own_item(db: DbSession, user: m.User, review_id: str) -> m.ReviewItem:
    item = db.get(m.ReviewItem, review_id)
    if item is None or item.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Review item not found.")
    return item


@router.get("")
def list_review(request: Request, status: str | None = None, limit: int = 50, cursor: str | None = None,
                db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    q = db.query(m.ReviewItem).filter(m.ReviewItem.user_id == user.id)
    if status:
        q = q.filter(m.ReviewItem.status == status)
    q = q.order_by(m.ReviewItem.created_at.desc())
    items, next_cursor, total = paginate(q, limit, cursor)
    out = []
    for item in items:
        draft = db.get(m.EmailDraft, item.draft_id)
        task = db.get(m.Task, item.task_id) if item.task_id else None
        data = ser.review_item(item, draft)
        data["task_display_id"] = task.display_id if task else None
        data["authorization_mode"] = task.authorization_mode if task else None
        out.append(data)
    return ok(request, {"items": out, "next_cursor": next_cursor, "total": total})


@router.get("/{review_id}")
def get_review(review_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    item = _own_item(db, user, review_id)
    draft = db.get(m.EmailDraft, item.draft_id)
    task = db.get(m.Task, item.task_id) if item.task_id else None
    source_context = None
    if draft is not None and draft.thread_id:
        thread = db.get(m.EmailThread, draft.thread_id)
        if thread is not None:
            source_context = ser.thread(thread)
    data = ser.review_item(item, draft)
    data["task_display_id"] = task.display_id if task else None
    data["authorization_mode"] = task.authorization_mode if task else None
    return ok(request, {
        "review_item": data,
        "draft": ser.draft(draft) if draft else None,
        "source_context": source_context,
        "agent_note": item.agent_note,
    })


class ReviewPatchBody(BaseModel):
    revision: int
    draft_changes: dict = {}


@router.patch("/{review_id}")
def patch_review(review_id: str, body: ReviewPatchBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    item = _own_item(db, user, review_id)
    if item.status in {"approved", "rejected"}:
        raise ApiError(409, "INVALID_STATE", f"A review item in state {item.status} can no longer be edited.")
    if body.revision != item.revision:
        latest_draft = db.get(m.EmailDraft, item.draft_id)
        raise ApiError(409, "REVISION_CONFLICT", "This review item changed elsewhere. Reload the latest version.",
                       {"latest": ser.review_item(item, latest_draft)})
    draft = db.get(m.EmailDraft, item.draft_id)
    changes = body.draft_changes or {}
    from .mail import _norm_recipients, _validate_recipients

    if "to" in changes:
        draft.to_recipients = _norm_recipients(changes["to"])
        _validate_recipients(draft.to_recipients)
    if "cc" in changes:
        draft.cc_recipients = _norm_recipients(changes["cc"])
    if "bcc" in changes:
        draft.bcc_recipients = _norm_recipients(changes["bcc"])
    if "subject" in changes:
        draft.subject = str(changes["subject"])
    if "body" in changes:
        draft.body_text = str(changes["body"])
    draft.revision += 1
    item.revision = draft.revision
    if item.status == "pending":
        item.status = "modified"
    if draft.message_id:
        msg = db.get(m.EmailMessage, draft.message_id)
        if msg is not None:
            msg.to_recipients = draft.to_recipients
            msg.subject = draft.subject
            msg.body_text = draft.body_text
            msg.snippet = (draft.body_text or "").replace("\n", " ")[:110]
    db.commit()
    manager.broadcast_threadsafe(user.id, "review.updated",
                                 {"review_id": item.id, "status": item.status, "revision": item.revision})
    return ok(request, {"review_item": ser.review_item(item, draft), "draft": ser.draft(draft)})


class ApproveBody(BaseModel):
    revision: int
    confirm_send: bool = False


@router.post("/{review_id}/approve")
def approve_review(review_id: str, body: ApproveBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    item = _own_item(db, user, review_id)
    if item.status in {"approved", "rejected"}:
        prior = idempotency_lookup(db, user.id, request.headers.get("idempotency-key"), "review.approve")
        if prior is not None:
            return ok(request, prior)
        raise ApiError(409, "INVALID_STATE", f"Review item is already {item.status}.")
    if not body.confirm_send:
        raise ApiError(422, "CONFIRMATION_REQUIRED", "Approve & Send requires confirm_send=true after the final confirmation.")
    if body.revision != item.revision:
        raise ApiError(409, "REVISION_CONFLICT", "This review item changed elsewhere. Reload the latest version.")
    draft = db.get(m.EmailDraft, item.draft_id)
    if draft is None:
        raise ApiError(404, "NOT_FOUND", "Draft not found.")
    result = tasks_svc.perform_send(db, user, draft)
    item.status = "approved"
    item.approved_at = m.now()
    db.commit()
    tasks_svc.after_review_resolved(db, item, approved=True)
    audit(db, user, "review.approved_sent", "review_item", item.id, request=request,
          environment=item.environment, meta={"simulated": result["simulated"]})
    # Agent action audit trail: this send was based on the draft's source
    # thread and approved by the user in the Review Queue.
    from .. import audit_trail

    recipients = ", ".join(
        (r.get("name") or r.get("email") or "") if isinstance(r, dict) else str(r)
        for r in (draft.to_recipients or [])
    ) or "(no recipients)"
    audit_trail.record(
        db, user, actor="user", action_type="send_review_item",
        description=f'Sent the email "{draft.subject}" to {recipients}.',
        outcome="executed",
        task_id=item.task_id,
        thread_ids=[draft.thread_id] if draft.thread_id else [],
        approval_type="review", approval_id=item.id,
        approval_summary="You approved this draft in the Review Queue (Approve & Send).",
        detail={"simulated": result["simulated"], "message_id": result.get("message_id")},
        environment=item.environment,
    )
    db.commit()
    manager.broadcast_threadsafe(user.id, "review.updated",
                                 {"review_id": item.id, "status": item.status, "revision": item.revision})
    payload = {"review_item": ser.review_item(item, draft), "send_result": result}
    idempotency_store(db, user.id, request.headers.get("idempotency-key"), "review.approve",
                      {"review_id": item.id}, payload)
    return ok(request, payload)


class RejectBody(BaseModel):
    reason: str | None = None


@router.post("/{review_id}/reject")
def reject_review(review_id: str, body: RejectBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    item = _own_item(db, user, review_id)
    if item.status in {"approved", "rejected"}:
        raise ApiError(409, "INVALID_STATE", f"Review item is already {item.status}.")
    draft = db.get(m.EmailDraft, item.draft_id)
    item.status = "rejected"
    item.rejected_at = m.now()
    if body.reason:
        item.agent_note = f"{item.agent_note or ''}\nRejected by you: {body.reason}".strip()
    if draft is not None:
        draft.status = "rejected"
    db.commit()
    tasks_svc.after_review_resolved(db, item, approved=False)
    task = db.get(m.Task, item.task_id) if item.task_id else None
    audit(db, user, "review.rejected", "review_item", item.id, request=request, environment=item.environment)
    db.commit()
    manager.broadcast_threadsafe(user.id, "review.updated",
                                 {"review_id": item.id, "status": item.status, "revision": item.revision})
    return ok(request, {"review_item": ser.review_item(item, draft), "task": ser.task(task) if task else None})


class ReturnBody(BaseModel):
    comment: str | None = None


@router.post("/{review_id}/return-to-task")
def return_to_task(review_id: str, body: ReturnBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    item = _own_item(db, user, review_id)
    if item.status in {"approved", "rejected"}:
        raise ApiError(409, "INVALID_STATE", f"Review item is already {item.status}.")
    item.status = "returned"
    draft = db.get(m.EmailDraft, item.draft_id)
    task = db.get(m.Task, item.task_id) if item.task_id else None
    if task is not None and task.status not in {"completed", "cancelled", "failed"}:
        task.status = "needs_attention"
        tasks_svc.emit_task_event(db, task, "task.log.appended", {
            "level": "info",
            "message": f"Draft returned from Review Queue. {body.comment or ''}".strip(),
        })
    audit(db, user, "review.returned", "review_item", item.id, request=request, environment=item.environment)
    db.commit()
    return ok(request, {"review_item": ser.review_item(item, draft), "task": ser.task(task) if task else None})
