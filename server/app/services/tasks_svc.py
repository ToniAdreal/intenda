"""Task event emission, draft creation and the (simulated) send operation."""
from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..ws import manager

SIMULATED_SEND_MESSAGE = "Simulated send — no external email was delivered."


def emit_task_event(db: DbSession, task: m.Task, event_type: str, payload: dict, broadcast: bool = True) -> m.TaskEvent:
    """Persist the event before broadcast (spec Section 6.6)."""
    last = (
        db.query(m.TaskEvent)
        .filter(m.TaskEvent.task_id == task.id)
        .order_by(m.TaskEvent.sequence.desc())
        .first()
    )
    seq = (last.sequence + 1) if last else 1
    full_payload = {"task_id": task.id, "display_id": task.display_id, **payload}
    event = m.TaskEvent(
        task_id=task.id, user_id=task.user_id, sequence=seq,
        event_type=event_type, payload=full_payload,
    )
    db.add(event)
    db.commit()
    if broadcast:
        manager.broadcast_threadsafe(task.user_id, event_type, {**full_payload, "sequence": seq})
    return event


async def emit_task_event_async(db: DbSession, task: m.Task, event_type: str, payload: dict) -> m.TaskEvent:
    last = (
        db.query(m.TaskEvent)
        .filter(m.TaskEvent.task_id == task.id)
        .order_by(m.TaskEvent.sequence.desc())
        .first()
    )
    seq = (last.sequence + 1) if last else 1
    full_payload = {"task_id": task.id, "display_id": task.display_id, **payload}
    event = m.TaskEvent(
        task_id=task.id, user_id=task.user_id, sequence=seq,
        event_type=event_type, payload=full_payload,
    )
    db.add(event)
    db.commit()
    await manager.broadcast(task.user_id, event_type, {**full_payload, "sequence": seq})
    return event


def next_display_id(db: DbSession, user: m.User) -> str:
    tasks = db.query(m.Task).filter(m.Task.user_id == user.id).all()
    highest = 2054
    for t in tasks:
        try:
            n = int(t.display_id.split("-")[1])
            highest = max(highest, n)
        except Exception:
            continue
    return f"AG-{highest + 1}"


def create_review_draft(
    db: DbSession,
    user: m.User,
    account: m.MailAccount,
    *,
    to_recipients: list,
    subject: str,
    body: str,
    thread_id: str | None,
    task: m.Task | None,
    agent_note: str,
    source_refs: dict | None = None,
    identity: m.AgentIdentity | None = None,
) -> tuple[m.EmailDraft, m.ReviewItem]:
    # Agent identity mode: the draft is where sending identity is
    # decided. An identity draft is created on the identity's account
    # and carries the disclosure block in its body from the start, so
    # the Review Queue shows exactly what will be sent.
    if identity is not None:
        from .. import agent_identity

        body = agent_identity.append_disclosure(body, identity)
    draft = m.EmailDraft(
        user_id=user.id,
        mail_account_id=account.id,
        thread_id=thread_id,
        to_recipients=to_recipients,
        subject=subject,
        body_text=body,
        identity_id=identity.id if identity is not None else None,
        status="in_review",
        environment=account.environment,
    )
    db.add(draft)
    db.flush()
    item = m.ReviewItem(
        user_id=user.id,
        task_id=task.id if task else None,
        draft_id=draft.id,
        status="pending",
        agent_note=agent_note,
        source_refs=source_refs or {},
        environment=account.environment,
    )
    db.add(item)
    db.flush()
    db.add(
        m.Notification(
            user_id=user.id,
            type="review",
            title="A draft is waiting for review",
            body=f"{subject} — open the Review Queue to approve or edit it.",
            resource_type="review_item",
            resource_id=item.id,
        )
    )
    db.commit()
    db.refresh(draft)
    db.refresh(item)
    return draft, item


def perform_send(db: DbSession, user: m.User, draft: m.EmailDraft) -> dict:
    """Send a draft. Demo mailboxes only ever simulate the send (spec 4.2).

    Live Google and Microsoft accounts send for real through their
    provider API *before* any local state changes: if the provider call
    fails, the exception propagates and the caller surfaces an error
    instead of recording a send that never happened."""
    account = db.get(m.MailAccount, draft.mail_account_id)
    environment = draft.environment
    # Agent identity mode: a draft carrying an identity sends as the
    # Agent — from the identity's account (perform_send already routes
    # by the draft's account) and under the identity's display name.
    identity = db.get(m.AgentIdentity, draft.identity_id) if draft.identity_id else None
    provider_message_id: str | None = None
    if (
        account is not None
        and account.environment == "live"
        and account.provider == "google"
    ):
        from . import gmail

        provider_message_id = gmail.send_draft_sync(db, account, draft)
    elif (
        account is not None
        and account.environment == "live"
        and account.provider == "microsoft"
    ):
        from . import graphmail

        provider_message_id = graphmail.send_draft_sync(db, account, draft)
    thread = db.get(m.EmailThread, draft.thread_id) if draft.thread_id else None
    if thread is None:
        thread = m.EmailThread(
            user_id=user.id,
            mail_account_id=draft.mail_account_id,
            subject_normalized=draft.subject,
            participants=list(draft.to_recipients or []),
            environment=environment,
        )
        if identity is not None:
            thread.agent_identity_id = identity.id
            thread.handling_mode = "agent"
        db.add(thread)
        db.flush()
        draft.thread_id = thread.id
    backing = db.get(m.EmailMessage, draft.message_id) if draft.message_id else None
    if backing is not None:
        # The draft's backing message becomes the sent message in place;
        # creating a second row would double-count the Sent folder.
        backing.is_draft = False
        backing.is_sent = True
        if identity is not None:
            backing.from_name = identity.display_name
            backing.identity_id = identity.id
        if provider_message_id:
            backing.provider_message_id = provider_message_id
        backing.sent_at = m.now()
        backing.body_text = draft.body_text
        backing.subject = draft.subject
        backing.to_recipients = draft.to_recipients
        backing.cc_recipients = draft.cc_recipients or []
        backing.bcc_recipients = draft.bcc_recipients or []
        backing.snippet = (draft.body_text or "").replace("\n", " ")[:110]
        backing.is_read = True
        sent_msg = backing
    else:
        sent_msg = m.EmailMessage(
            user_id=user.id,
            mail_account_id=draft.mail_account_id,
            thread_id=thread.id,
            provider_message_id=provider_message_id,
            from_address=account.email_address if account else "you@demo.intenda.example",
            from_name=identity.display_name if identity is not None else user.name,
            identity_id=identity.id if identity is not None else None,
            to_recipients=draft.to_recipients or [],
            cc_recipients=draft.cc_recipients or [],
            bcc_recipients=draft.bcc_recipients or [],
            subject=draft.subject,
            body_text=draft.body_text,
            snippet=(draft.body_text or "").replace("\n", " ")[:110],
            sent_at=m.now(),
            is_read=True,
            is_sent=True,
            environment=environment,
        )
        db.add(sent_msg)
        db.flush()
    thread.message_count = (
        db.query(m.EmailMessage).filter(m.EmailMessage.thread_id == thread.id).count()
    )
    thread.last_message_at = m.now()
    thread.snippet = sent_msg.snippet
    draft.status = "sent"
    db.commit()
    if environment == "demo":
        return {
            "simulated": True,
            "environment": "demo",
            "message_id": sent_msg.id,
            "notice": SIMULATED_SEND_MESSAGE,
        }
    if provider_message_id:
        provider_name = (
            "Gmail"
            if account is not None and account.provider == "google"
            else "Microsoft"
        )
        return {
            "simulated": False,
            "environment": environment,
            "message_id": sent_msg.id,
            "provider_message_id": provider_message_id,
            "notice": f"Sent through your connected {provider_name} account.",
        }
    # A live account whose provider is neither Google nor Microsoft (or a
    # missing account row) has no wired-up send path; the send is recorded
    # locally and reported honestly as not provider-confirmed.
    return {
        "simulated": False,
        "environment": environment,
        "message_id": sent_msg.id,
        "notice": "Recorded locally. No live provider is configured on this deployment, so no external delivery was confirmed.",
    }


def after_review_resolved(db: DbSession, item: m.ReviewItem, approved: bool) -> None:
    """Advance a task whose send step was waiting on this review item."""
    if item.task_id is None:
        return
    task = db.get(m.Task, item.task_id)
    if task is None:
        return
    steps = (
        db.query(m.TaskStep)
        .filter(m.TaskStep.task_id == task.id)
        .order_by(m.TaskStep.sequence)
        .all()
    )
    for step in steps:
        if step.step_type == "send_email" and (step.input_refs or {}).get("review_id") == item.id:
            if approved:
                step.status = "completed"
                step.completed_at = m.now()
                step.output_refs = {**(step.output_refs or {}), "review_id": item.id, "sent": True}
            else:
                step.status = "skipped"
                step.output_refs = {**(step.output_refs or {}), "review_id": item.id, "sent": False}
    done = sum(1 for s in steps if s.status in {"completed", "skipped"})
    task.progress_percent = round(100 * done / len(steps)) if steps else 100
    if approved and done == len(steps) and task.status not in {"cancelled", "takeover"}:
        task.status = "completed"
        task.completed_at = m.now()
        emit_task_event(db, task, "task.completed", {"completed_at": m.now().isoformat()})
    elif approved and task.status == "waiting_user":
        # Steps remain after the send (e.g. the report step): hand the task
        # back to the runner instead of leaving it parked.
        task.status = "running"
        emit_task_event(db, task, "task.resumed", {"resumed_at": m.now().isoformat()})
    elif not approved and task.status == "waiting_user":
        task.status = "needs_attention"
        emit_task_event(db, task, "task.log.appended",
                        {"level": "info", "message": "The review draft was rejected; the send step was skipped."})
    db.commit()
