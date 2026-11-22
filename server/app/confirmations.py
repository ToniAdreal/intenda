"""Two-phase confirmation protocol for Agent write operations.

Every mutating action the Agent can take (archive, trash, label, create a
to-do, send a reviewed draft) goes through this protocol:

- Phase 1, ``prepare``: the action is described exactly (a payload naming
  the concrete targets) together with a deterministic human-readable
  summary of what will happen, and a one-time confirmation token is
  issued with an expiry (``CONFIRMATION_TTL_MINUTES``). Only the token's
  SHA-256 hash is stored. Nothing mutates in this phase.
- Phase 2, ``confirm``: the stored payload is executed only when the
  caller presents the matching, unexpired token. The token is
  single-use: confirming an already-resolved action is rejected, as are
  expired tokens and wrong tokens. ``cancel`` resolves the action
  without executing anything.

Injection-guard interplay (app.injection_guard): mail flagged as a
possible prompt injection is never an automatic-action target. Flagged
threads/messages are scrubbed out of the payload at prepare time (and
the summary says so); targets that become flagged between prepare and
confirm are skipped at execution time and reported in the result.
"""
from __future__ import annotations

import hmac
from datetime import timedelta

from sqlalchemy.orm import Session as DbSession

from . import injection_guard
from . import models as m
from .config import settings
from .errors import ApiError
from .security import new_token, sha256_hex

ACTION_TYPES = {
    "archive_threads",
    "trash_threads",
    "label_messages",
    "create_todo",
    "send_review_item",
}

# Task step types whose effects are writes and therefore run through this
# protocol instead of executing directly in the runner. draft_email is
# deliberately absent: creating a draft *is* a phase-1 artifact (it lands
# in the Review Queue unsent), and its send is the send_review_item
# action below. send_email steps are gated by that same Review flow.
WRITE_STEP_TYPES = {"archive_email", "label_message", "create_todo"}

_STEP_ACTION = {
    "archive_email": "archive_threads",
    "label_message": "label_messages",
    "create_todo": "create_todo",
}

_MAX_LISTED = 5


def _ttl() -> timedelta:
    return timedelta(minutes=settings.CONFIRMATION_TTL_MINUTES)


def is_expired(action: m.PendingAction) -> bool:
    return action.status == "pending" and m.as_utc(action.expires_at) <= m.now()


# ---------------------------------------------------------------------------
# Target resolution + summaries
# ---------------------------------------------------------------------------


def _thread_label(thread: m.EmailThread) -> str:
    subject = (thread.subject_normalized or "").strip() or "(no subject)"
    return f'"{subject}" ({thread.message_count} message(s))'


def _message_label(msg: m.EmailMessage) -> str:
    sender = msg.from_name or msg.from_address
    subject = (msg.subject or "").strip() or "(no subject)"
    return f'{sender} — "{subject}"'


def _join_labels(labels: list[str]) -> str:
    shown = labels[:_MAX_LISTED]
    text = ", ".join(shown)
    if len(labels) > _MAX_LISTED:
        text += f", and {len(labels) - _MAX_LISTED} more"
    return text


def _flag_note(excluded: int) -> str:
    if not excluded:
        return ""
    return (
        f" {excluded} flagged item(s) were excluded: mail flagged as a possible "
        "prompt injection is never acted on by the Agent automatically."
    )


def _resolve_threads(db: DbSession, user: m.User, thread_ids: list) -> tuple[list[m.EmailThread], int]:
    """Return (clean threads, excluded_flagged_count). Raises on foreign
    or unknown ids so a summary can never silently cover other targets."""
    threads: list[m.EmailThread] = []
    excluded = 0
    for tid in thread_ids[:50]:
        thread = db.get(m.EmailThread, str(tid))
        if thread is None or thread.user_id != user.id:
            raise ApiError(422, "VALIDATION_ERROR", "A target conversation does not exist or is not yours.")
        injection_guard.ensure_thread_scanned(db, thread.id)
        if thread.injection_suspect:
            excluded += 1
            continue
        threads.append(thread)
    return threads, excluded


def _resolve_messages(db: DbSession, user: m.User, message_ids: list) -> tuple[list[m.EmailMessage], int]:
    messages: list[m.EmailMessage] = []
    excluded = 0
    for mid in message_ids[:100]:
        msg = db.get(m.EmailMessage, str(mid))
        if msg is None or msg.user_id != user.id:
            raise ApiError(422, "VALIDATION_ERROR", "A target message does not exist or is not yours.")
        injection_guard.ensure_scanned(db, msg)
        if msg.injection_suspect:
            excluded += 1
            continue
        messages.append(msg)
    return messages, excluded


def _normalize(db: DbSession, user: m.User, action_type: str, payload: dict) -> tuple[dict, str]:
    """Validate + scrub the payload and build the human-readable summary.
    The returned payload is exactly what the executor will act on."""
    payload = payload or {}
    if action_type in {"archive_threads", "trash_threads"}:
        raw_ids = payload.get("thread_ids") or []
        if not raw_ids:
            raise ApiError(422, "VALIDATION_ERROR", "thread_ids is required for this action.")
        threads, excluded = _resolve_threads(db, user, raw_ids)
        if not threads:
            raise ApiError(422, "INJECTION_SUSPECT",
                           "Every target is flagged as a possible prompt injection, so the Agent will not act on them. You can still open and handle them manually.")
        labels = _join_labels([_thread_label(t) for t in threads])
        if action_type == "archive_threads":
            summary = f"Archive {len(threads)} conversation(s): {labels}. They leave the inbox and stay available in the archive."
        else:
            summary = f"Move {len(threads)} conversation(s) to trash: {labels}. Trashed mail can be restored from the Trash folder."
        return {"thread_ids": [t.id for t in threads]}, summary + _flag_note(excluded)

    if action_type == "label_messages":
        raw_ids = payload.get("message_ids") or []
        label_name = str(payload.get("label_name") or "").strip()
        if not raw_ids:
            raise ApiError(422, "VALIDATION_ERROR", "message_ids is required for this action.")
        if not label_name:
            raise ApiError(422, "VALIDATION_ERROR", "label_name is required for this action.")
        messages, excluded = _resolve_messages(db, user, raw_ids)
        if not messages:
            raise ApiError(422, "INJECTION_SUSPECT",
                           "Every target is flagged as a possible prompt injection, so the Agent will not act on them. You can still open and handle them manually.")
        labels = _join_labels([_message_label(x) for x in messages])
        summary = f'Apply the label "{label_name}" to {len(messages)} message(s): {labels}.'
        return {"message_ids": [x.id for x in messages], "label_name": label_name[:128]}, summary + _flag_note(excluded)

    if action_type == "create_todo":
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ApiError(422, "VALIDATION_ERROR", "title is required for this action.")
        description = str(payload.get("description") or "")[:500]
        summary = f'Create a to-do: "{title[:200]}".'
        if description:
            summary += f" Details: {description}"
        return {"title": title[:200], "description": description}, summary

    if action_type == "send_review_item":
        review_id = str(payload.get("review_id") or "")
        item = db.get(m.ReviewItem, review_id) if review_id else None
        if item is None or item.user_id != user.id:
            raise ApiError(404, "NOT_FOUND", "Review item not found.")
        if item.status not in {"pending", "modified"}:
            raise ApiError(409, "INVALID_STATE", f"A review item in state {item.status} can no longer be sent.")
        draft = db.get(m.EmailDraft, item.draft_id)
        if draft is None:
            raise ApiError(404, "NOT_FOUND", "Draft not found.")
        recipients = ", ".join(
            (r.get("name") or r.get("email") or "") if isinstance(r, dict) else str(r)
            for r in (draft.to_recipients or [])
        ) or "(no recipients)"
        summary = (
            f'Send the email "{draft.subject}" to {recipients}. '
            "This performs the send for this draft only; it cannot be reused for other messages."
        )
        return {"review_id": item.id}, summary

    raise ApiError(422, "VALIDATION_ERROR", f"Unsupported action_type. Use one of {sorted(ACTION_TYPES)}.")


# ---------------------------------------------------------------------------
# Phase 1: prepare
# ---------------------------------------------------------------------------


def prepare(
    db: DbSession,
    user: m.User,
    *,
    action_type: str,
    payload: dict,
    task_id: str | None = None,
    step_id: str | None = None,
    source: str = "api",
) -> tuple[m.PendingAction, str]:
    """Create a pending action and return (action, raw_token). The raw
    token is shown to the caller exactly once; only its hash is stored.
    Nothing about the mailbox changes here."""
    if action_type not in ACTION_TYPES:
        raise ApiError(422, "VALIDATION_ERROR", f"Unsupported action_type. Use one of {sorted(ACTION_TYPES)}.")
    normalized, summary = _normalize(db, user, action_type, payload)
    raw_token = new_token(32)
    action = m.PendingAction(
        user_id=user.id,
        task_id=task_id,
        step_id=step_id,
        source=source,
        action_type=action_type,
        summary=summary,
        payload=normalized,
        token_hash=sha256_hex(raw_token),
        status="pending",
        expires_at=m.now() + _ttl(),
    )
    db.add(action)
    db.flush()
    db.refresh(action)
    return action, raw_token


def prepare_step_action(db: DbSession, task: m.Task, step: m.TaskStep, user: m.User) -> tuple[m.PendingAction, str] | None:
    """Prepare the confirmation for a mutating task step. Returns None
    when the step has no resolvable targets (the runner then falls back
    to its legacy no-op execution for that step)."""
    action_type = _STEP_ACTION.get(step.step_type)
    if action_type is None:
        return None
    refs = step.input_refs or {}
    thread_ids = list((task.context_scope or {}).get("thread_ids") or [])
    if step.step_type == "archive_email":
        if not thread_ids:
            return None
        payload = {"thread_ids": thread_ids}
    elif step.step_type == "label_message":
        message_ids: list[str] = []
        for tid in thread_ids:
            rows = (
                db.query(m.EmailMessage.id)
                .filter(m.EmailMessage.thread_id == tid, m.EmailMessage.user_id == user.id)
                .all()
            )
            message_ids.extend(r[0] for r in rows)
        if not message_ids:
            return None
        payload = {
            "message_ids": message_ids[:100],
            "label_name": str(refs.get("label_name") or refs.get("label") or "Agent")[:128],
        }
    else:  # create_todo
        payload = {
            "title": str(refs.get("title") or task.goal)[:200],
            "description": str(refs.get("description") or step.description or "")[:500],
        }
    try:
        return prepare(db, user, action_type=action_type, payload=payload,
                       task_id=task.id, step_id=step.id, source="task_step")
    except ApiError as exc:
        if exc.code == "INJECTION_SUSPECT":
            # Every target is flagged: this step must never fall through
            # to the legacy executor (which would act on the flagged
            # mail). Re-raise so the runner gate resolves the step as
            # skipped-for-safety instead of preparing or executing it.
            raise
        return None


# ---------------------------------------------------------------------------
# Executors: run exactly the prepared payload, nothing more
# ---------------------------------------------------------------------------


def _execute_archive(db: DbSession, user: m.User, payload: dict) -> dict:
    done, skipped = [], 0
    for tid in payload.get("thread_ids") or []:
        thread = db.get(m.EmailThread, tid)
        if thread is None or thread.user_id != user.id:
            continue
        injection_guard.ensure_thread_scanned(db, thread.id)
        if thread.injection_suspect:
            skipped += 1
            continue
        thread.is_archived = True
        done.append(tid)
    db.commit()
    return {"threads_archived": len(done), "thread_ids": done, "skipped_injection_suspect": skipped}


def _execute_trash(db: DbSession, user: m.User, payload: dict) -> dict:
    done, skipped = [], 0
    for tid in payload.get("thread_ids") or []:
        thread = db.get(m.EmailThread, tid)
        if thread is None or thread.user_id != user.id:
            continue
        injection_guard.ensure_thread_scanned(db, thread.id)
        if thread.injection_suspect:
            skipped += 1
            continue
        thread.is_trashed = True
        db.query(m.EmailMessage).filter(m.EmailMessage.thread_id == tid).update(
            {"is_trashed": True}, synchronize_session=False
        )
        done.append(tid)
    db.commit()
    return {"threads_trashed": len(done), "thread_ids": done, "skipped_injection_suspect": skipped}


def _execute_label(db: DbSession, user: m.User, payload: dict) -> dict:
    account = (
        db.query(m.MailAccount)
        .filter(m.MailAccount.user_id == user.id, m.MailAccount.deleted_at.is_(None))
        .order_by(m.MailAccount.created_at)
        .first()
    )
    if account is None:
        raise ApiError(409, "INVALID_STATE", "No mail account is available for labels.")
    name = payload["label_name"]
    label = (
        db.query(m.EmailLabel)
        .filter(m.EmailLabel.mail_account_id == account.id, m.EmailLabel.name == name)
        .first()
    )
    if label is None:
        label = m.EmailLabel(mail_account_id=account.id, name=name, kind="user")
        db.add(label)
        db.flush()
    done, skipped = [], 0
    for mid in payload.get("message_ids") or []:
        msg = db.get(m.EmailMessage, mid)
        if msg is None or msg.user_id != user.id:
            continue
        injection_guard.ensure_scanned(db, msg)
        if msg.injection_suspect:
            skipped += 1
            continue
        exists = db.get(m.EmailMessageLabel, {"message_id": mid, "label_id": label.id})
        if exists is None:
            db.add(m.EmailMessageLabel(message_id=mid, label_id=label.id))
        done.append(mid)
    db.commit()
    return {"labelled": len(done), "message_ids": done, "label_name": name,
            "label_id": label.id, "skipped_injection_suspect": skipped}


def _execute_todo(db: DbSession, user: m.User, payload: dict) -> dict:
    todo = m.Todo(
        user_id=user.id,
        title=payload["title"],
        description=payload.get("description") or None,
        created_by="agent",
    )
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return {"todo_id": todo.id, "title": todo.title}


def _execute_send(db: DbSession, user: m.User, payload: dict) -> dict:
    from .services import tasks_svc

    item = db.get(m.ReviewItem, payload["review_id"])
    if item is None or item.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Review item not found.")
    if item.status not in {"pending", "modified"}:
        raise ApiError(409, "INVALID_STATE", f"A review item in state {item.status} can no longer be sent.")
    draft = db.get(m.EmailDraft, item.draft_id)
    if draft is None:
        raise ApiError(404, "NOT_FOUND", "Draft not found.")
    result = tasks_svc.perform_send(db, user, draft)
    item.status = "approved"
    item.approved_at = m.now()
    db.commit()
    tasks_svc.after_review_resolved(db, item, approved=True)
    return {"review_id": item.id, "send_result": result}


_EXECUTORS = {
    "archive_threads": _execute_archive,
    "trash_threads": _execute_trash,
    "label_messages": _execute_label,
    "create_todo": _execute_todo,
    "send_review_item": _execute_send,
}


# ---------------------------------------------------------------------------
# Phase 2: confirm / cancel
# ---------------------------------------------------------------------------


def _own_action(db: DbSession, user: m.User, action_id: str) -> m.PendingAction:
    action = db.get(m.PendingAction, action_id)
    if action is None or action.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "Confirmation not found.")
    return action


def _reject_if_resolved(action: m.PendingAction) -> None:
    if action.status == "executed":
        raise ApiError(409, "CONFIRMATION_ALREADY_USED",
                       "This confirmation was already used. A confirmation token can only execute its action once.")
    if action.status == "cancelled":
        raise ApiError(409, "CONFIRMATION_CANCELLED",
                       "This action was cancelled, so it can no longer be confirmed.")
    if action.status == "expired":
        raise ApiError(409, "CONFIRMATION_EXPIRED",
                       "This confirmation has expired. Prepare the action again to get a fresh token.")


def confirm(db: DbSession, user: m.User, action_id: str, token: str | None) -> m.PendingAction:
    """Execute the prepared action if (and only if) the presented token
    matches, is unexpired, and has never been used."""
    action = _own_action(db, user, action_id)
    _reject_if_resolved(action)
    if is_expired(action):
        action.status = "expired"
        action.resolved_at = m.now()
        db.commit()
        raise ApiError(409, "CONFIRMATION_EXPIRED",
                       "This confirmation has expired. Prepare the action again to get a fresh token.")
    if not token or not hmac.compare_digest(sha256_hex(token), action.token_hash):
        raise ApiError(403, "CONFIRMATION_TOKEN_INVALID",
                       "The confirmation token does not match this prepared action.")
    result = _EXECUTORS[action.action_type](db, user, action.payload or {})
    action.status = "executed"
    action.resolved_at = m.now()
    action.result = result
    db.commit()
    db.refresh(action)
    return action


def cancel(db: DbSession, user: m.User, action_id: str) -> m.PendingAction:
    """Cancel a pending action. Nothing is executed, and the token can
    never be used afterwards."""
    action = _own_action(db, user, action_id)
    _reject_if_resolved(action)
    if is_expired(action):
        action.status = "expired"
        action.resolved_at = m.now()
        db.commit()
        raise ApiError(409, "CONFIRMATION_EXPIRED",
                       "This confirmation has already expired, so there is nothing left to cancel.")
    action.status = "cancelled"
    action.resolved_at = m.now()
    db.commit()
    db.refresh(action)
    return action


# ---------------------------------------------------------------------------
# Task linkage: a confirmed/cancelled step action moves its task forward
# ---------------------------------------------------------------------------


def apply_task_outcome(db: DbSession, action: m.PendingAction, outcome: str) -> None:
    """Mirror the Decision flow's bookkeeping for a task step that was
    parked on this confirmation: executed -> step completed and the task
    resumes; cancelled -> step skipped and the task pauses."""
    if not action.task_id or not action.step_id:
        return
    from .services import tasks_svc

    task = db.get(m.Task, action.task_id)
    step = db.get(m.TaskStep, action.step_id)
    if task is None or step is None:
        return
    steps = (
        db.query(m.TaskStep)
        .filter(m.TaskStep.task_id == task.id)
        .order_by(m.TaskStep.sequence)
        .all()
    )
    if outcome == "executed":
        step.status = "completed"
        step.completed_at = m.now()
        step.output_refs = {
            **(step.output_refs or {}),
            "confirmation_id": action.id,
            **(action.result or {}),
        }
        if task.status == "waiting_user":
            task.status = "running"
        db.commit()
        tasks_svc.emit_task_event(db, task, "task.step.completed", {
            "step_id": step.id, "output_refs": step.output_refs,
        })
        tasks_svc.emit_task_event(db, task, "task.resumed", {"resumed_at": m.now().isoformat()})
    elif outcome == "cancelled":
        step.status = "skipped"
        step.output_refs = {**(step.output_refs or {}), "confirmation_id": action.id, "cancelled": True}
        if task.status not in {"completed", "failed", "cancelled"}:
            task.status = "paused"
        db.commit()
        tasks_svc.emit_task_event(db, task, "task.paused", {"reason": "confirmation_cancelled"})
    done = sum(1 for s in steps if s.status in {"completed", "skipped"})
    if steps:
        task.progress_percent = round(100 * done / len(steps))
    db.commit()
