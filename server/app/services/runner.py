"""Background runner: simulated demo sync + task step execution.

Runs inside the FastAPI process (spec 7.1: background runner integrated into
the same process for the demo workload). Step side effects are deterministic
operations over the user's own mailbox data (read, summarize-by-extraction,
draft from the approved plan, to-do, archive). Sending is never executed
here: a send step completes only through Review Queue approval (spec 6.2).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timezone

from ..config import settings
from ..db import SessionLocal
from .. import audit_trail
from .. import confirmations
from .. import models as m
from ..errors import ApiError
from ..helpers import get_onboarding
from ..ws import manager
from . import gmail, graphmail, tasks_svc

log = logging.getLogger("intenda.runner")

SYNC_PHASES = [
    ("connecting", 5),
    ("reading_metadata", 25),
    ("syncing_messages", 60),
    ("building_threads", 85),
    ("preparing_agent_context", 100),
]


async def _run_live_sync(
    db,
    job: m.SyncJob,
    account: m.MailAccount,
    *,
    sync,
    auth_error: type[Exception],
    failed_code: str,
    label: str,
) -> None:
    """Run a real provider sync for a live account's job, mirroring the
    demo path's completion bookkeeping (job counts, cursor, onboarding
    transition, sync.completed broadcast) with simulated=False. ``sync``
    is the provider service's sync_account coroutine; ``auth_error`` is
    its dead-grant exception, mapped to TOKEN_REFRESH_FAILED with the
    account parked at action_required; any other failure maps to
    ``failed_code`` with the account back to connected."""
    job.phase = "syncing_messages"
    db.commit()
    try:
        fetched, cursor = await sync(db, account, job.mode)
    except auth_error:
        log.warning("%s sync for job %s failed: token refresh rejected", label, job.id)
        job.status = "failed"
        job.phase = "failed"
        job.error_code = "TOKEN_REFRESH_FAILED"
        job.completed_at = m.now()
        account.status = "action_required"
        db.commit()
        return
    except Exception:
        log.exception("%s sync for job %s failed", label, job.id)
        job.status = "failed"
        job.phase = "failed"
        job.error_code = failed_code
        job.completed_at = m.now()
        account.status = "connected"
        db.commit()
        return
    job.status = "completed"
    job.phase = "completed"
    job.processed_count = fetched
    job.total_count = fetched
    job.completed_at = m.now()
    job.cursor_after = cursor
    account.status = "connected"
    user = db.get(m.User, job.user_id)
    if user is not None:
        ob = get_onboarding(db, user)
        if ob.state == "SYNCING":
            ob.state = "READY"
            steps = list(ob.completed_steps or [])
            if "sync" not in steps:
                steps.append("sync")
                ob.completed_steps = steps
    db.commit()
    await manager.broadcast(job.user_id, "sync.completed", {
        "job_id": job.id, "account_id": job.mail_account_id,
        "counts": {"messages": fetched}, "completed_at": m.now().isoformat(),
        "simulated": False,
    })


async def _advance_live_google_sync(db, job: m.SyncJob, account: m.MailAccount) -> None:
    """Run a real Gmail sync for a live Google account's job."""
    await _run_live_sync(
        db, job, account,
        sync=gmail.sync_account,
        auth_error=gmail.GmailAuthError,
        failed_code="GMAIL_SYNC_FAILED",
        label="gmail",
    )


async def _advance_live_microsoft_sync(db, job: m.SyncJob, account: m.MailAccount) -> None:
    """Run a real Microsoft Graph sync for a live Microsoft account's job."""
    await _run_live_sync(
        db, job, account,
        sync=graphmail.sync_account,
        auth_error=graphmail.GraphAuthError,
        failed_code="GRAPH_SYNC_FAILED",
        label="microsoft graph",
    )


async def _advance_syncs(db) -> None:
    jobs = db.query(m.SyncJob).filter(m.SyncJob.status == "running").all()
    duration_ms = max(500, 5 * settings.DEMO_SYNC_DELAY_MS)
    for job in jobs:
        account = db.get(m.MailAccount, job.mail_account_id)
        if (
            account is not None
            and account.environment == "live"
            and account.provider == "google"
        ):
            await _advance_live_google_sync(db, job, account)
            continue
        if (
            account is not None
            and account.environment == "live"
            and account.provider == "microsoft"
        ):
            await _advance_live_microsoft_sync(db, job, account)
            continue
        started = job.started_at
        if started is None:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_ms = (m.now() - started).total_seconds() * 1000
        frac = min(1.0, elapsed_ms / duration_ms)
        total = job.total_count or 0
        job.processed_count = int(total * frac)
        phase = SYNC_PHASES[-1][0]
        for name, upto in SYNC_PHASES:
            if frac * 100 <= upto:
                phase = name
                break
        job.phase = phase
        account = db.get(m.MailAccount, job.mail_account_id)
        if frac >= 1.0:
            job.status = "completed"
            job.phase = "completed"
            job.processed_count = total
            job.completed_at = m.now()
            job.cursor_after = f"demo-cursor-{total}"
            if account is not None:
                account.status = "connected"
                account.last_sync_at = m.now()
                account.sync_cursor = job.cursor_after
            user = db.get(m.User, job.user_id)
            if user is not None:
                ob = get_onboarding(db, user)
                if ob.state == "SYNCING":
                    ob.state = "READY"
                    steps = list(ob.completed_steps or [])
                    if "sync" not in steps:
                        steps.append("sync")
                        ob.completed_steps = steps
            db.commit()
            await manager.broadcast(job.user_id, "sync.completed", {
                "job_id": job.id, "account_id": job.mail_account_id,
                "counts": {"messages": total}, "completed_at": m.now().isoformat(),
                "simulated": job.environment == "demo",
            })
        else:
            db.commit()
            await manager.broadcast(job.user_id, "sync.progress", {
                "job_id": job.id, "phase": job.phase,
                "processed": job.processed_count, "total": total,
                "percent": round(frac * 100), "simulated": job.environment == "demo",
            })


def _gate_required(task: m.Task, step: m.TaskStep) -> str | None:
    """Return the decision type if this step must wait for the user."""
    if step.step_type == "wait_for_approval":
        return "approve_step"
    mode = task.authorization_mode
    if mode == "confirm_every_step":
        return "approve_step"
    if mode == "confirm_milestones":
        if (step.input_refs or {}).get("milestone") or step.risk_level == "high":
            return "approve_milestone"
    return None


def _context_threads(db, task: m.Task) -> list[m.EmailThread]:
    ids = (task.context_scope or {}).get("thread_ids") or []
    threads = []
    for tid in ids:
        t = db.get(m.EmailThread, tid)
        if t is not None and t.user_id == task.user_id:
            threads.append(t)
    return threads


def _execute_step(db, task: m.Task, step: m.TaskStep, user: m.User) -> dict:
    """Perform the step's internal effect and return output_refs."""
    threads = _context_threads(db, task)
    refs = step.input_refs or {}
    if step.step_type == "read_context":
        count = 0
        for t in threads:
            count += db.query(m.EmailMessage).filter(m.EmailMessage.thread_id == t.id).count()
        return {"threads_read": len(threads), "messages_read": count}
    if step.step_type == "summarize":
        lines = []
        for t in threads:
            latest = (
                db.query(m.EmailMessage)
                .filter(m.EmailMessage.thread_id == t.id)
                .order_by(m.EmailMessage.created_at.desc())
                .first()
            )
            if latest is not None:
                lines.append(f"{t.subject_normalized}: {(latest.snippet or '')[:160]}")
        return {"summary": " ".join(lines)[:900] or "No messages in the selected context."}
    if step.step_type == "draft_email":
        account = (
            db.query(m.MailAccount)
            .filter(m.MailAccount.user_id == user.id, m.MailAccount.deleted_at.is_(None))
            .first()
        )
        to_list = refs.get("to")
        subject = refs.get("subject")
        body = refs.get("body")
        thread_id = threads[0].id if threads else None
        if not to_list and threads:
            others = [p for p in (threads[0].participants or []) if p.get("email") != account.email_address]
            to_list = others[:1]
        if not subject:
            subject = f"Re: {threads[0].subject_normalized}" if threads else "Follow-up"
        if not body:
            chosen = refs.get("chosen_option_label")
            goal_line = task.goal
            body = (
                f"Hi,\n\nFollowing up on {threads[0].subject_normalized if threads else 'our conversation'}.\n\n"
                + (f"I would like to confirm {chosen}.\n\n" if chosen else "")
                + "Please let me know if anything on your side needs adjusting, and I will take care of it right away.\n\nBest regards"
            )
            if goal_line:
                body += f"\n\n(Context: {goal_line[:200]})"
        draft, item = tasks_svc.create_review_draft(
            db, user, account,
            to_recipients=to_list or [], subject=subject, body=body,
            thread_id=thread_id, task=task,
            agent_note=f"Draft prepared by task {task.display_id} from the approved plan. Review, edit or reject it here; nothing is sent before your approval.",
            source_refs={"thread_id": thread_id, "task_display_id": task.display_id},
        )
        return {"draft_id": draft.id, "review_id": item.id}
    if step.step_type == "create_todo":
        todo = m.Todo(
            user_id=user.id,
            source_task_id=task.id,
            title=str(refs.get("title") or task.goal)[:200],
            description=str(refs.get("description") or "")[:500] or None,
            created_by="agent",
        )
        db.add(todo)
        db.commit()
        db.refresh(todo)
        return {"todo_id": todo.id}
    if step.step_type == "archive_email":
        n = 0
        for t in threads:
            t.is_archived = True
            n += 1
        db.commit()
        return {"threads_archived": n}
    if step.step_type == "label_message":
        return {"labelled": len(threads)}
    if step.step_type == "custom_internal":
        return {"note": refs.get("note") or "Internal step completed."}
    return {"note": "Step completed."}


# Step types whose execution changes state and therefore leaves an audit
# trail entry (app/audit_trail.py). Read-only steps (read_context,
# summarize, custom_internal) are deliberately absent. The write step
# types in confirmations.WRITE_STEP_TYPES normally park on the
# confirmation protocol — whose executions are audited there — and only
# reach _execute_step through the legacy no-target fallback.
_AUDITED_STEP_TYPES = {"draft_email", "create_todo", "archive_email", "label_message"}


def _record_step_audit(db, task: m.Task, step: m.TaskStep, user: m.User,
                       output: dict | None, outcome: str = "executed",
                       reason: str | None = None) -> None:
    """Audit one mutating step execution, linking the Decision that
    gated it when there was one."""
    approval_type, approval_id, approval_summary = "none", None, None
    decision = db.get(m.Decision, step.approval_id) if step.approval_id else None
    if decision is not None and decision.status == "approved":
        approval_type, approval_id = "decision", decision.id
        approval_summary = f"You approved this step through a {decision.decision_type} decision."
    elif outcome == "executed":
        approval_summary = f"Ran under the task's '{task.authorization_mode}' authorization mode."
    verb = "executed" if outcome == "executed" else "failed"
    audit_trail.record(
        db, user, actor="agent", action_type=step.step_type,
        description=f"Task {task.display_id} step {verb}: {step.title}",
        outcome=outcome, reason=reason,
        task_id=task.id, step_id=step.id,
        thread_ids=list((task.context_scope or {}).get("thread_ids") or []),
        approval_type=approval_type, approval_id=approval_id,
        approval_summary=approval_summary,
        detail=output or {}, environment=task.environment,
    )


async def _confirmation_gate(db, task: m.Task, step: m.TaskStep, user: m.User) -> bool:
    """Two-phase confirmation gate for mutating steps (app/confirmations).

    Returns True when the step is parked on the protocol (waiting for the
    user's token confirmation) or was resolved by it, so the caller must
    not execute the step directly. Returns False only when the step has
    no resolvable targets and the legacy no-op execution may proceed.
    The raw token travels in the task.confirmation.required event, which
    only the task owner's clients can read."""
    cid = (step.input_refs or {}).get("confirmation_id")
    if cid:
        action = db.get(m.PendingAction, cid)
        if action is not None and action.status == "pending" and not confirmations.is_expired(action):
            if step.status != "waiting_user":
                step.status = "waiting_user"
                task.status = "waiting_user"
                task.current_step_id = step.id
                db.commit()
            return True
        if action is not None and action.status == "pending":
            # Expired while parked: supersede it with a fresh action below.
            action.status = "expired"
            action.resolved_at = m.now()
            db.commit()
        elif action is not None:
            # Executed/cancelled outcomes are applied synchronously by
            # the confirm/cancel endpoints; a still-current step means
            # that already happened, so there is nothing to execute here.
            return True
    try:
        prepared = confirmations.prepare_step_action(db, task, step, user)
    except ApiError as exc:
        if exc.code == "INJECTION_SUSPECT":
            # Every target is flagged as a possible prompt injection:
            # resolve the step as skipped-for-safety rather than letting
            # it execute against flagged mail.
            step.status = "completed"
            step.completed_at = m.now()
            step.output_refs = {
                **(step.output_refs or {}),
                "skipped_injection_suspect": True,
                "note": "Skipped: every target is flagged as a possible prompt injection.",
            }
            # The audit trail shows what the guard stopped, not just
            # what ran (app/audit_trail.py).
            audit_trail.record(
                db, user, actor="agent", action_type=step.step_type,
                description=(
                    f"Task {task.display_id} step not executed: {step.title}. "
                    "Every target is flagged as a possible prompt injection."
                ),
                outcome="skipped", reason="injection_suspect",
                task_id=task.id, step_id=step.id,
                thread_ids=list((task.context_scope or {}).get("thread_ids") or []),
                approval_type="none",
                approval_summary="Stopped by the prompt-injection guard before a confirmation was prepared.",
                detail={"skipped_injection_suspect": True},
                environment=task.environment,
            )
            db.commit()
            await tasks_svc.emit_task_event_async(db, task, "task.step.completed",
                                                  {"step_id": step.id, "output_refs": step.output_refs})
            return True
        raise
    if prepared is None:
        return False
    action, raw_token = prepared
    step.input_refs = {**(step.input_refs or {}), "confirmation_id": action.id}
    step.status = "waiting_user"
    task.status = "waiting_user"
    task.current_step_id = step.id
    db.commit()
    await tasks_svc.emit_task_event_async(db, task, "task.waiting_user",
                                          {"reason": "Confirmation required", "decision_id": None,
                                           "confirmation_id": action.id})
    await tasks_svc.emit_task_event_async(db, task, "task.confirmation.required", {
        "confirmation_id": action.id, "step_id": step.id,
        "action_type": action.action_type, "summary": action.summary,
        "confirmation_token": raw_token,
        "expires_at": m.as_utc(action.expires_at).isoformat(),
    })
    return True


async def _process_task(db, task: m.Task) -> None:
    user = db.get(m.User, task.user_id)
    if user is None:
        return
    if task.status == "queued":
        task.status = "running"
        task.started_at = task.started_at or m.now()
        db.commit()
        await tasks_svc.emit_task_event_async(db, task, "task.started", {"started_at": m.now().isoformat()})
        return
    steps = (
        db.query(m.TaskStep)
        .filter(m.TaskStep.task_id == task.id)
        .order_by(m.TaskStep.sequence)
        .all()
    )
    if not steps:
        task.status = "completed"
        task.progress_percent = 100
        task.completed_at = m.now()
        db.commit()
        await tasks_svc.emit_task_event_async(db, task, "task.completed", {"completed_at": m.now().isoformat()})
        return

    # Handle a send step that is waiting on a Review Queue decision. This
    # applies only once the send step is current: it already carries a
    # review_id (linked when its draft step completed) or is parked. A send
    # step that is merely later in the plan must not block earlier steps.
    for step in steps:
        if step.step_type == "send_email" and step.status in {"waiting_user", "pending", "ready"}:
            review_id = (step.input_refs or {}).get("review_id")
            if review_id is None and step.status != "waiting_user":
                continue
            item = db.get(m.ReviewItem, review_id) if review_id else None
            if item is not None and item.status in {"approved", "sent"}:
                step.status = "completed"
                step.completed_at = m.now()
                db.commit()
            elif item is not None and item.status == "rejected":
                step.status = "skipped"
                db.commit()
            elif step.status != "waiting_user":
                # Park the task at the Review Queue gate.
                step.status = "waiting_user"
                task.status = "waiting_user"
                task.current_step_id = step.id
                db.commit()
                await tasks_svc.emit_task_event_async(
                    db, task, "task.waiting_user",
                    {"reason": "Sending requires Review Queue approval", "decision_id": None},
                )
            return

    nxt = next((s for s in steps if s.status in {"pending", "ready", "waiting_user"}), None)
    done = sum(1 for s in steps if s.status in {"completed", "skipped"})
    task.progress_percent = round(100 * done / len(steps))
    if nxt is None:
        if task.status != "completed":
            task.status = "completed"
            task.progress_percent = 100
            task.completed_at = m.now()
            db.commit()
            await tasks_svc.emit_task_event_async(db, task, "task.completed", {"completed_at": m.now().isoformat()})
            db.add(m.Notification(
                user_id=user.id, type="task",
                title=f"{task.display_id} completed",
                body=task.goal[:160], resource_type="task", resource_id=task.id,
            ))
            db.commit()
        return

    # Mutating steps never execute directly: they park on the two-phase
    # confirmation protocol in every authorization mode. (The Decision
    # gates below still cover read-only steps and mode-A approvals.)
    if nxt.step_type in confirmations.WRITE_STEP_TYPES:
        if await _confirmation_gate(db, task, nxt, user):
            return

    if nxt.status == "waiting_user":
        # If the gate decision was approved, release the step and continue.
        decision = db.get(m.Decision, nxt.approval_id) if nxt.approval_id else None
        if decision is not None and decision.status == "approved":
            nxt.status = "pending"
            db.commit()
        else:
            # Still waiting on a decision; nothing to do until it resolves.
            return

    gate = None
    if nxt.approval_id:
        prior = db.get(m.Decision, nxt.approval_id)
        if prior is not None and prior.status == "approved":
            gate = None  # this step's gate was already approved by the user
        else:
            gate = _gate_required(task, nxt)
    else:
        gate = _gate_required(task, nxt)
    if gate is not None:
        existing = (
            db.query(m.Decision)
            .filter(m.Decision.step_id == nxt.id, m.Decision.status == "pending")
            .first()
        )
        if existing is None:
            decision = m.Decision(
                task_id=task.id, step_id=nxt.id, user_id=user.id,
                decision_type=gate,
                prompt=f"Approve this step: {nxt.title}",
                options=[{"value": "approve", "label": "Approve"}, {"value": "reject", "label": "Reject"}],
                status="pending",
            )
            db.add(decision)
            db.flush()
            nxt.status = "waiting_user"
            nxt.approval_id = decision.id
            task.status = "waiting_user"
            task.current_step_id = nxt.id
            db.commit()
            await tasks_svc.emit_task_event_async(db, task, "task.waiting_user",
                                                  {"reason": "Approval required", "decision_id": decision.id})
            await tasks_svc.emit_task_event_async(db, task, "task.decision.required", {
                "decision_id": decision.id, "decision_type": gate,
                "summary": decision.prompt, "options": decision.options,
            })
        return

    # Execute the step.
    nxt.status = "running"
    nxt.started_at = m.now()
    task.current_step_id = nxt.id
    db.commit()
    await tasks_svc.emit_task_event_async(db, task, "task.step.started",
                                          {"step_id": nxt.id, "sequence": nxt.sequence, "title": nxt.title})
    try:
        output = _execute_step(db, task, nxt, user)
        nxt.output_refs = {**(nxt.output_refs or {}), **output}
        nxt.status = "completed"
        nxt.completed_at = m.now()
        nxt.progress_percent = 100
        if nxt.step_type in _AUDITED_STEP_TYPES:
            _record_step_audit(db, task, nxt, user, output)
        db.commit()
        await tasks_svc.emit_task_event_async(db, task, "task.step.completed",
                                              {"step_id": nxt.id, "output_refs": output})
        if "review_id" in output:
            # Link the review to the plan's send step so the Review Queue
            # gate controls exactly that step.
            for s in steps:
                if s.step_type == "send_email" and s.status in {"pending", "ready"}:
                    s.input_refs = {**(s.input_refs or {}), "review_id": output["review_id"]}
                    db.commit()
                    break
            await tasks_svc.emit_task_event_async(db, task, "task.output.created", {
                "output_type": "draft", "output_id": output.get("draft_id"), "review_id": output["review_id"],
            })
            await manager.broadcast(user.id, "review.created", {
                "review_id": output["review_id"], "task_id": task.id,
                "draft_id": output.get("draft_id"),
            })
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("step execution failed: %s", exc)
        nxt.status = "failed"
        nxt.error_code = "STEP_FAILED"
        task.status = "failed"
        task.error_code = "STEP_FAILED"
        if nxt.step_type in _AUDITED_STEP_TYPES:
            _record_step_audit(db, task, nxt, user, None, outcome="failed", reason="STEP_FAILED")
        db.commit()
        await tasks_svc.emit_task_event_async(db, task, "task.step.failed", {
            "step_id": nxt.id, "error_code": "STEP_FAILED",
            "message": "The step could not be completed. You can retry, take over or terminate.",
            "retryable": True,
        })
    done = sum(1 for s in steps if s.status in {"completed", "skipped"})
    task.progress_percent = round(100 * done / len(steps))
    db.commit()
    await tasks_svc.emit_task_event_async(db, task, "task.progress", {
        "percent": task.progress_percent, "current_step_id": nxt.id,
        "summary": f"{done}/{len(steps)} steps completed",
    })


async def runner_loop() -> None:
    log.info("intenda runner started")
    while True:
        try:
            db = SessionLocal()
            try:
                await _advance_syncs(db)
                tasks = (
                    db.query(m.Task)
                    .filter(m.Task.status.in_(["queued", "running"]))
                    .all()
                )
                for task in tasks:
                    await _process_task(db, task)
            finally:
                db.close()
        except Exception:  # pragma: no cover - keep the loop alive
            log.exception("runner iteration failed")
        await asyncio.sleep(0.9)
