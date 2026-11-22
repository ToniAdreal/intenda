"""Automation endpoints (spec 7.4.9, Section 2.5.6). Automations may create
drafts and to-dos, but never send email without Review Queue approval."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from .. import serializers as ser
from ..db import get_db
from ..deps import audit, get_current_user
from ..errors import ApiError, ok
from ..helpers import paginate

router = APIRouter(prefix="/api/v1/automations", tags=["automations"])

TRIGGER_TYPES = {"scheduled", "new_matching_email", "manual"}
ACTION_TYPES = {"summarize", "create_todo", "prepare_draft", "notify"}


def _own(db: DbSession, user: m.User, automation_id: str) -> m.Automation:
    a = db.get(m.Automation, automation_id)
    if a is None or a.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Automation not found.")
    return a


def _validate(trigger: dict, actions: list) -> None:
    if not isinstance(trigger, dict) or trigger.get("type") not in TRIGGER_TYPES:
        raise ApiError(422, "VALIDATION_ERROR", "Trigger type must be scheduled, new_matching_email or manual.", {"field": "trigger"})
    if trigger.get("type") == "scheduled" and trigger.get("schedule") not in {"daily", "weekly"}:
        raise ApiError(422, "VALIDATION_ERROR", "Scheduled automations need schedule=daily or weekly.", {"field": "trigger.schedule"})
    if not actions:
        raise ApiError(422, "VALIDATION_ERROR", "At least one action is required.", {"field": "actions"})
    for action in actions:
        if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
            raise ApiError(422, "VALIDATION_ERROR", f"Unsupported action type. Use one of {sorted(ACTION_TYPES)}.", {"field": "actions"})


@router.get("")
def list_automations(request: Request, enabled: bool | None = None, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    q = db.query(m.Automation).filter(m.Automation.user_id == user.id)
    if enabled is not None:
        q = q.filter(m.Automation.enabled.is_(enabled))
    items = q.order_by(m.Automation.created_at).all()
    return ok(request, {"items": [ser.automation(a) for a in items]})


class AutomationCreateBody(BaseModel):
    name: str
    trigger: dict
    conditions: dict = {}
    actions: list = []
    authorization_mode: str = "confirm_milestones"


@router.post("")
def create_automation(body: AutomationCreateBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    _validate(body.trigger, body.actions)
    a = m.Automation(
        user_id=user.id, name=body.name.strip()[:160], trigger=body.trigger,
        conditions=body.conditions or {}, actions=body.actions,
        authorization_mode=body.authorization_mode, enabled=False,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    audit(db, user, "automation.created", "automation", a.id, request=request)
    db.commit()
    return ok(request, {"automation": ser.automation(a)}, status=201)


@router.get("/{automation_id}")
def get_automation(automation_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    runs = (
        db.query(m.AutomationRun)
        .filter(m.AutomationRun.automation_id == a.id)
        .order_by(m.AutomationRun.started_at.desc())
        .limit(20)
        .all()
    )
    return ok(request, {"automation": ser.automation(a), "runs": [ser.automation_run(r) for r in runs]})


class AutomationPatchBody(BaseModel):
    revision: int
    name: str | None = None
    trigger: dict | None = None
    conditions: dict | None = None
    actions: list | None = None


@router.patch("/{automation_id}")
def patch_automation(automation_id: str, body: AutomationPatchBody, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    if body.revision != a.revision:
        raise ApiError(409, "REVISION_CONFLICT", "This automation changed elsewhere. Reload the latest version.")
    if body.trigger is not None or body.actions is not None:
        _validate(body.trigger if body.trigger is not None else a.trigger,
                  body.actions if body.actions is not None else a.actions)
    if body.name is not None:
        a.name = body.name.strip()[:160]
    if body.trigger is not None:
        a.trigger = body.trigger
    if body.conditions is not None:
        a.conditions = body.conditions
    if body.actions is not None:
        a.actions = body.actions
    a.revision += 1
    db.commit()
    db.refresh(a)
    return ok(request, {"automation": ser.automation(a)})


@router.post("/{automation_id}/enable")
def enable_automation(automation_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    _validate(a.trigger, a.actions)
    a.enabled = True
    db.commit()
    return ok(request, {"automation": ser.automation(a)})


@router.post("/{automation_id}/disable")
def disable_automation(automation_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    a.enabled = False
    db.commit()
    return ok(request, {"automation": ser.automation(a)})


@router.post("/{automation_id}/duplicate")
def duplicate_automation(automation_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    copy = m.Automation(
        user_id=user.id, name=f"{a.name} (copy)", trigger=a.trigger, conditions=a.conditions,
        actions=a.actions, authorization_mode=a.authorization_mode, enabled=False,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return ok(request, {"automation": ser.automation(copy)}, status=201)


def _matches(msg: m.EmailMessage, conditions: dict) -> bool:
    if conditions.get("sender") and conditions["sender"].lower() not in (msg.from_address or "").lower():
        return False
    if conditions.get("subject_contains") and conditions["subject_contains"].lower() not in (msg.subject or "").lower():
        return False
    if conditions.get("unread") and msg.is_read:
        return False
    return True


@router.post("/{automation_id}/run")
def run_automation(automation_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    run = m.AutomationRun(automation_id=a.id, user_id=user.id, status="running",
                          trigger_payload={"manual": True})
    db.add(run)
    db.commit()
    db.refresh(run)
    messages = (
        db.query(m.EmailMessage)
        .filter(m.EmailMessage.user_id == user.id, m.EmailMessage.is_draft.is_(False),
                m.EmailMessage.is_trashed.is_(False), m.EmailMessage.is_sent.is_(False))
        .order_by(m.EmailMessage.created_at.desc())
        .limit(100)
        .all()
    )
    # Scan-on-read backstop so the exclusion below also covers mail that
    # predates ingest-time scanning.
    from .. import injection_guard

    if any([injection_guard.ensure_scanned(db, x) for x in messages]):
        db.commit()
    matched_all = [x for x in messages if _matches(x, a.conditions or {})]
    # Automatic-processing exclusion: messages flagged as possible prompt
    # injections are never drafted from, summarised or turned into to-dos
    # by an automation. The user can still open and act on them manually.
    matched = [x for x in matched_all if not x.injection_suspect]
    skipped_flagged = len(matched_all) - len(matched)
    results: list[dict] = []
    account = (
        db.query(m.MailAccount)
        .filter(m.MailAccount.user_id == user.id, m.MailAccount.deleted_at.is_(None))
        .order_by(m.MailAccount.created_at)
        .first()
    )
    for action in a.actions or []:
        atype = action.get("type")
        if atype == "summarize":
            results.append({"type": atype, "summary": f"{len(matched)} message(s) matched. Latest: "
                            + (matched[0].subject if matched else "none")})
        elif atype == "create_todo":
            if matched:
                todo = m.Todo(user_id=user.id, title=f"Review: {matched[0].subject}"[:200],
                              description=f"Created by automation '{a.name}'.",
                              source_message_id=matched[0].id, created_by="agent")
                db.add(todo)
                db.flush()
                results.append({"type": atype, "todo_id": todo.id})
            else:
                results.append({"type": atype, "skipped": "no matching message"})
        elif atype == "prepare_draft":
            if matched and account is not None:
                from ..services import tasks_svc

                src = matched[0]
                draft, item = tasks_svc.create_review_draft(
                    db, user, account,
                    to_recipients=[{"name": src.from_name or src.from_address, "email": src.from_address}],
                    subject=f"Re: {src.subject}",
                    body="Hi,\n\nThanks for your message. I am reviewing it and will follow up shortly.\n\nBest regards",
                    thread_id=src.thread_id, task=None,
                    agent_note=f"Draft prepared by automation '{a.name}'. It will not be sent without your Review Queue approval.",
                    source_refs={"message_id": src.id, "automation_id": a.id},
                )
                results.append({"type": atype, "draft_id": draft.id, "review_id": item.id})
            else:
                results.append({"type": atype, "skipped": "no matching message"})
        elif atype == "notify":
            db.add(m.Notification(user_id=user.id, type="automation",
                                  title=f"Automation ran: {a.name}",
                                  body=f"{len(matched)} message(s) matched.",
                                  resource_type="automation", resource_id=a.id))
            results.append({"type": atype, "notified": True})
    a.last_run_at = m.now()
    if not matched:
        run.status = "needs_attention"
        run.result_payload = {"matched": 0, "actions": results,
                              "skipped_injection_suspect": skipped_flagged,
                              "note": "No messages matched the conditions, so no action was taken."}
    else:
        run.status = "success"
        run.result_payload = {"matched": len(matched), "actions": results,
                              "skipped_injection_suspect": skipped_flagged}
    run.completed_at = m.now()
    db.commit()
    db.refresh(run)
    audit(db, user, "automation.run", "automation", a.id, request=request, meta={"status": run.status})
    # Agent action audit trail: one entry per state-changing action the
    # automation executed, plus a skipped entry for flagged mail the
    # injection guard excluded from the run.
    from .. import audit_trail

    trail_common = dict(
        actor="agent",
        approval_type="none",
        approval_summary=f"Automation '{a.name}' runs under your automation settings.",
        environment="demo",
    )
    if skipped_flagged:
        flagged_msgs = [x for x in matched_all if x.injection_suspect]
        audit_trail.record(
            db, user, action_type="automation_run",
            description=(
                f"Automation '{a.name}' skipped {skipped_flagged} message(s) flagged "
                "as a possible prompt injection."
            ),
            outcome="skipped", reason="injection_suspect",
            message_ids=[x.id for x in flagged_msgs],
            detail={"automation_id": a.id, "run_id": run.id},
            **trail_common,
        )
    for res in results:
        if res.get("todo_id"):
            audit_trail.record(
                db, user, action_type="create_todo",
                description=f"Automation '{a.name}' created a to-do from \"{matched[0].subject}\".",
                outcome="executed",
                message_ids=[matched[0].id],
                detail={"automation_id": a.id, "run_id": run.id, "todo_id": res["todo_id"]},
                **trail_common,
            )
        elif res.get("review_id"):
            audit_trail.record(
                db, user, action_type="prepare_draft",
                description=(
                    f"Automation '{a.name}' prepared a reply draft for \"{matched[0].subject}\". "
                    "It will not be sent without your Review Queue approval."
                ),
                outcome="executed",
                thread_ids=[matched[0].thread_id], message_ids=[matched[0].id],
                detail={"automation_id": a.id, "run_id": run.id,
                        "draft_id": res.get("draft_id"), "review_id": res["review_id"]},
                **trail_common,
            )
    db.commit()
    return ok(request, {"run": ser.automation_run(run)}, status=201)


@router.get("/{automation_id}/runs")
def list_runs(automation_id: str, request: Request, limit: int = 50, cursor: str | None = None,
              db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    q = (
        db.query(m.AutomationRun)
        .filter(m.AutomationRun.automation_id == a.id)
        .order_by(m.AutomationRun.started_at.desc())
    )
    items, next_cursor, total = paginate(q, limit, cursor)
    return ok(request, {"items": [ser.automation_run(r) for r in items], "next_cursor": next_cursor, "total": total})


@router.delete("/{automation_id}")
def delete_automation(automation_id: str, request: Request, db: DbSession = Depends(get_db), user: m.User = Depends(get_current_user)):
    a = _own(db, user, automation_id)
    db.query(m.AutomationRun).filter(m.AutomationRun.automation_id == a.id).delete()
    db.delete(a)
    audit(db, user, "automation.deleted", "automation", automation_id, request=request)
    db.commit()
    return ok(request, {"deleted": True})
