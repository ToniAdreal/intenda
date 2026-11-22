"""Agent action audit trail endpoints (app/audit_trail.py).

Read-only: the trail is written at the real execution points
(confirmations, the task runner, the Review Queue, automation runs) and
only ever read here. Reading it writes nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from .. import serializers as ser
from ..db import get_db
from ..deps import get_current_user
from ..errors import ok
from ..helpers import decode_cursor, encode_cursor, paginate

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("")
def list_audit(request: Request, task_id: str | None = None, thread_id: str | None = None,
               action_type: str | None = None, outcome: str | None = None,
               actor: str | None = None, identity_id: str | None = None,
               limit: int = 50, cursor: str | None = None,
               db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    q = db.query(m.AuditEntry).filter(m.AuditEntry.user_id == user.id)
    if task_id:
        q = q.filter(m.AuditEntry.task_id == task_id)
    if action_type:
        q = q.filter(m.AuditEntry.action_type == action_type)
    if outcome:
        q = q.filter(m.AuditEntry.outcome == outcome)
    if actor:
        q = q.filter(m.AuditEntry.actor == actor)
    if identity_id:
        q = q.filter(m.AuditEntry.identity_id == identity_id)
    q = q.order_by(m.AuditEntry.created_at.desc())

    def render(rows):
        # Denormalize the identity address at read time so the Activity
        # page can show which correspondent identity acted.
        out = []
        for e in rows:
            data = ser.audit_entry(e)
            data["identity_address"] = None
            if e.identity_id:
                identity = db.get(m.AgentIdentity, e.identity_id)
                data["identity_address"] = (
                    identity.address if identity is not None else "(deleted identity)"
                )
            out.append(data)
        return out

    if thread_id:
        # thread_ids is a JSON list column; membership filtering happens
        # in Python over the (bounded, newest-first) candidate set, with
        # the same offset-cursor shape helpers.paginate returns.
        limit = min(max(limit or 50, 1), 200)
        offset = decode_cursor(cursor)
        rows = [e for e in q.limit(1000).all() if thread_id in (e.thread_ids or [])]
        total = len(rows)
        items = rows[offset:offset + limit]
        next_cursor = encode_cursor(offset + limit) if offset + limit < total else None
        return ok(request, {"items": render(items),
                            "next_cursor": next_cursor, "total": total})
    items, next_cursor, total = paginate(q, limit, cursor)
    return ok(request, {"items": render(items),
                        "next_cursor": next_cursor, "total": total})
