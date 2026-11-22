"""Two-phase confirmation endpoints (app/confirmations.py).

Phase 1 prepares an Agent write action and returns its summary plus the
one-time confirmation token. Phase 2 executes it — only with that token,
only before it expires, only once. Cancelling resolves the action
without executing anything.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import confirmations
from .. import models as m
from .. import serializers as ser
from ..db import get_db
from ..deps import audit, get_current_user
from ..errors import ApiError, ok
from ..ws import manager

router = APIRouter(prefix="/api/v1/confirmations", tags=["confirmations"])


def _own(db: DbSession, user: m.User, action_id: str) -> m.PendingAction:
    action = db.get(m.PendingAction, action_id)
    if action is None or action.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Confirmation not found.")
    return action


@router.get("")
def list_confirmations(request: Request, status: str | None = None, task_id: str | None = None,
                       db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    q = db.query(m.PendingAction).filter(m.PendingAction.user_id == user.id)
    if status:
        q = q.filter(m.PendingAction.status == status)
    if task_id:
        q = q.filter(m.PendingAction.task_id == task_id)
    items = q.order_by(m.PendingAction.created_at.desc()).limit(100).all()
    return ok(request, {"items": [ser.pending_action(a) for a in items]})


class PrepareBody(BaseModel):
    action_type: str
    payload: dict = {}


@router.post("")
def prepare_confirmation(body: PrepareBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    action, raw_token = confirmations.prepare(
        db, user, action_type=body.action_type, payload=body.payload or {}, source="api",
    )
    db.commit()
    db.refresh(action)
    audit(db, user, "confirmation.prepared", "pending_action", action.id, request=request,
          meta={"action_type": action.action_type})
    db.commit()
    return ok(request, {
        "confirmation": ser.pending_action(action),
        "confirmation_token": raw_token,
    }, status=201)


@router.get("/{action_id}")
def get_confirmation(action_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    action = _own(db, user, action_id)
    if confirmations.is_expired(action):
        action.status = "expired"
        action.resolved_at = m.now()
        db.commit()
        db.refresh(action)
    return ok(request, {"confirmation": ser.pending_action(action)})


class ConfirmBody(BaseModel):
    token: str


@router.post("/{action_id}/confirm")
def confirm_action(action_id: str, body: ConfirmBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    action = confirmations.confirm(db, user, action_id, body.token)
    confirmations.apply_task_outcome(db, action, "executed")
    audit(db, user, "confirmation.executed", "pending_action", action.id, request=request,
          meta={"action_type": action.action_type})
    db.commit()
    if action.action_type == "send_review_item" and action.result:
        manager.broadcast_threadsafe(user.id, "review.updated", {
            "review_id": action.result.get("review_id"), "status": "approved",
        })
    return ok(request, {"confirmation": ser.pending_action(action), "result": action.result})


@router.post("/{action_id}/cancel")
def cancel_action(action_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    action = confirmations.cancel(db, user, action_id)
    confirmations.apply_task_outcome(db, action, "cancelled")
    audit(db, user, "confirmation.cancelled", "pending_action", action.id, request=request,
          meta={"action_type": action.action_type})
    db.commit()
    return ok(request, {"confirmation": ser.pending_action(action)})
