"""Agent action audit trail.

Every action the Agent executes — or is stopped from executing — leaves
one record in ``audit_entries`` (models.AuditEntry): what was done, which
email or thread it was based on, who approved it, and how it ended. The
user inspects the trail through GET /api/v1/audit and the Activity page.

Recording points (the real execution points, never read-only browsing):

- ``confirmations.confirm`` — two-phase confirmation executions, with
  the PendingAction as the approval linkage. Targets that turn out to be
  injection-flagged at execution time get their own ``skipped`` entry
  with reason ``injection_suspect``, so the trail shows what the guard
  stopped, not just what ran.
- The task runner — mutating step executions (draft creation, to-dos,
  and the legacy direct archive/label paths), with the gating Decision
  as the approval linkage when the step was Decision-gated, and the
  confirmation gate's injection skip (every target flagged) as a
  ``skipped`` entry.
- The Review Queue approve-and-send endpoint — a send the user approved
  manually, linked to the review item.
- Automation runs — the to-dos and review drafts an automation creates,
  plus one ``skipped`` entry when flagged mail was excluded.

``record`` only adds and flushes: the row rides the caller's transaction
and is persisted by the caller's commit, so an action and its audit
record can never drift apart.

Agent identity mode (app/agent_identity.py): entries for actions taken
as the Agent carry ``identity_id`` — which correspondent identity acted
— while ``actor`` stays in {agent, user, system}.
"""
from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from . import models as m

ACTORS = {"agent", "user", "system"}
OUTCOMES = {"executed", "skipped", "failed"}
APPROVAL_TYPES = {"confirmation", "decision", "review", "user", "none"}

_MAX_BASIS = 10


def thread_basis(db: DbSession, thread_ids) -> list[dict]:
    """Source-basis entries naming the threads an action was based on."""
    out: list[dict] = []
    for tid in list(thread_ids or [])[:_MAX_BASIS]:
        thread = db.get(m.EmailThread, str(tid))
        if thread is not None:
            label = (thread.subject_normalized or "").strip() or "(no subject)"
            out.append({"kind": "thread", "id": thread.id, "label": label})
    return out


def message_basis(db: DbSession, message_ids) -> list[dict]:
    """Source-basis entries naming the messages an action was based on."""
    out: list[dict] = []
    for mid in list(message_ids or [])[:_MAX_BASIS]:
        msg = db.get(m.EmailMessage, str(mid))
        if msg is not None:
            sender = msg.from_name or msg.from_address
            subject = (msg.subject or "").strip() or "(no subject)"
            out.append({
                "kind": "message", "id": msg.id, "thread_id": msg.thread_id,
                "label": f'{sender} — "{subject}"',
            })
    return out


def record(
    db: DbSession,
    user: m.User,
    *,
    actor: str,
    action_type: str,
    description: str,
    outcome: str,
    task_id: str | None = None,
    step_id: str | None = None,
    thread_ids: list | None = None,
    message_ids: list | None = None,
    source_basis: list | None = None,
    approval_type: str = "none",
    approval_id: str | None = None,
    approval_summary: str | None = None,
    identity_id: str | None = None,
    reason: str | None = None,
    detail: dict | None = None,
    environment: str = "demo",
) -> m.AuditEntry:
    """Append one audit entry (flush only; the caller commits).

    When ``source_basis`` is not given it is derived from the thread /
    message ids so every entry names the mail it was based on, and
    ``subjects`` mirrors the basis labels for quick display."""
    if actor not in ACTORS:
        raise ValueError(f"unknown audit actor: {actor}")
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown audit outcome: {outcome}")
    if approval_type not in APPROVAL_TYPES:
        raise ValueError(f"unknown audit approval_type: {approval_type}")
    thread_ids = [str(t) for t in (thread_ids or [])]
    message_ids = [str(x) for x in (message_ids or [])]
    if source_basis is None:
        source_basis = thread_basis(db, thread_ids) + message_basis(db, message_ids)
    entry = m.AuditEntry(
        user_id=user.id,
        actor=actor,
        action_type=action_type,
        description=description,
        task_id=task_id,
        step_id=step_id,
        thread_ids=thread_ids,
        message_ids=message_ids,
        subjects=[b.get("label", "") for b in source_basis][:_MAX_BASIS],
        source_basis=source_basis,
        approval_type=approval_type,
        approval_id=approval_id,
        approval_summary=approval_summary,
        identity_id=identity_id,
        outcome=outcome,
        reason=reason,
        detail=detail or {},
        environment=environment,
    )
    db.add(entry)
    db.flush()
    return entry
