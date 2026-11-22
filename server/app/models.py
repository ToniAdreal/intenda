"""Database models implementing Specification Sections 4.8 and 7.6.

UUID primary keys, JSON columns validated at the API boundary, ownership and
status indexes. One extension beyond the spec schema: tasks.display_id holds
the stable human-facing task reference (AG-2048, AG-2051, AG-2054, ...) that
the seed plan in Section 7.11 requires.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; coerce them to UTC-aware for compares."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OnboardingState(Base):
    __tablename__ = "onboarding_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(48), default="NOT_STARTED", nullable=False)
    completed_steps: Mapped[list] = mapped_column(JSON, default=list)
    mailbox_choice: Mapped[str | None] = mapped_column(String(32))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MailAccount(Base):
    __tablename__ = "mail_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # demo|google|microsoft
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(String(16), default="demo")  # demo|live
    status: Mapped[str] = mapped_column(String(32), default="connected")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_account_id: Mapped[str | None] = mapped_column(String(128))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_cursor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailThread(Base):
    __tablename__ = "email_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mail_account_id: Mapped[str] = mapped_column(ForeignKey("mail_accounts.id"), index=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(128))
    subject_normalized: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str | None] = mapped_column(Text)
    participants: Mapped[list] = mapped_column(JSON, default=list)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_trashed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Rolled up from the messages by app.injection_guard: True when any
    # message in the thread was flagged as a possible prompt injection.
    injection_suspect: Mapped[bool] = mapped_column(Boolean, default=False)
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mail_account_id: Mapped[str] = mapped_column(ForeignKey("mail_accounts.id"), index=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("email_threads.id"), index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    internet_message_id: Mapped[str | None] = mapped_column(String(255))
    in_reply_to: Mapped[str | None] = mapped_column(String(255))
    references_header: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str | None] = mapped_column(Text)
    to_recipients: Mapped[list] = mapped_column(JSON, default=list)
    cc_recipients: Mapped[list] = mapped_column(JSON, default=list)
    bcc_recipients: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html_sanitized: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_trashed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Prompt-injection guard (app.injection_guard): injection_flags is
    # NULL until the message has been scanned, then a list of flag dicts
    # (empty when clean); injection_suspect mirrors bool(flags).
    injection_suspect: Mapped[bool] = mapped_column(Boolean, default=False)
    injection_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    provider_payload_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailAttachment(Base):
    __tablename__ = "email_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    message_id: Mapped[str] = mapped_column(ForeignKey("email_messages.id"), index=True)
    provider_attachment_id: Mapped[str | None] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    storage_key: Mapped[str | None] = mapped_column(Text)
    is_inline: Mapped[bool] = mapped_column(Boolean, default=False)
    content_id: Mapped[str | None] = mapped_column(String(128))
    scan_status: Mapped[str] = mapped_column(String(32), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailLabel(Base):
    __tablename__ = "email_labels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    mail_account_id: Mapped[str] = mapped_column(ForeignKey("mail_accounts.id"), index=True)
    provider_label_id: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="user")
    color: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailMessageLabel(Base):
    __tablename__ = "email_message_labels"

    message_id: Mapped[str] = mapped_column(ForeignKey("email_messages.id"), primary_key=True)
    label_id: Mapped[str] = mapped_column(ForeignKey("email_labels.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mail_account_id: Mapped[str] = mapped_column(ForeignKey("mail_accounts.id"))
    thread_id: Mapped[str | None] = mapped_column(ForeignKey("email_threads.id"))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("email_messages.id"))
    message_id: Mapped[str | None] = mapped_column(ForeignKey("email_messages.id"))
    to_recipients: Mapped[list] = mapped_column(JSON, default=list)
    cc_recipients: Mapped[list] = mapped_column(JSON, default=list)
    bcc_recipients: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mail_account_id: Mapped[str] = mapped_column(ForeignKey("mail_accounts.id"), index=True)
    mode: Mapped[str] = mapped_column(String(32), default="initial")
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    status: Mapped[str] = mapped_column(String(32), default="running")
    phase: Mapped[str] = mapped_column(String(64), default="connecting")
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int | None] = mapped_column(Integer)
    cursor_before: Mapped[str | None] = mapped_column(Text)
    cursor_after: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(Text, default="New conversation")
    status: Mapped[str] = mapped_column(String(32), default="active")
    context_scope: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[dict | None] = mapped_column(JSON)
    model_provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="complete")
    client_message_id: Mapped[str | None] = mapped_column(String(64))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("user_id", "display_id", name="uq_task_user_display"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    display_id: Mapped[str] = mapped_column(String(16), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("agent_conversations.id"))
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    authorization_mode: Mapped[str] = mapped_column(String(48), default="confirm_milestones")
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    context_scope: Mapped[dict] = mapped_column(JSON, default=dict)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    current_step_id: Mapped[str | None] = mapped_column(String(36))
    execution_lock: Mapped[str | None] = mapped_column(String(32))
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class TaskStep(Base):
    __tablename__ = "task_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    input_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    output_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_id: Mapped[str | None] = mapped_column(String(36))
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(64), default="")
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "sequence", name="uq_task_event_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("task_steps.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    decision_type: Mapped[str] = mapped_column(String(48), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    response: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PendingAction(Base):
    """Two-phase confirmation protocol (app/confirmations.py).

    Phase 1 (prepare) stores the exact action payload plus a human-readable
    summary of what will happen and a one-time confirmation token (only its
    SHA-256 hash is kept). Nothing mutates at prepare time. Phase 2
    (confirm) executes the stored payload only when presented with the
    matching, unexpired token; the token is single-use. Status flow:
    pending -> executed | cancelled | expired.
    """

    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("task_steps.id"))
    source: Mapped[str] = mapped_column(String(32), default="api")  # api|task_step
    action_type: Mapped[str] = mapped_column(String(48), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEntry(Base):
    """Agent action audit trail (app/audit_trail.py).

    One immutable row per action the Agent actually executed — or was
    stopped from executing: what was done (``action_type`` +
    ``description``), which mail it was based on (``thread_ids`` /
    ``message_ids`` / ``subjects`` / ``source_basis``), who approved it
    (``approval_type`` + ``approval_id``: a PendingAction confirmation, a
    Decision, a Review Queue approval, the user acting manually, or none
    for standing-authorization runs), and how it ended (``outcome``:
    executed | skipped | failed, with ``reason`` for the latter two —
    notably ``injection_suspect`` when the prompt-injection guard stopped
    it). Deliberately separate from AuditLog, which records account and
    session security events without task/thread/approval structure.
    """

    __tablename__ = "audit_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    actor: Mapped[str] = mapped_column(String(16), default="agent")  # agent|user|system
    action_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("task_steps.id"))
    thread_ids: Mapped[list] = mapped_column(JSON, default=list)
    message_ids: Mapped[list] = mapped_column(JSON, default=list)
    subjects: Mapped[list] = mapped_column(JSON, default=list)
    source_basis: Mapped[list] = mapped_column(JSON, default=list)
    approval_type: Mapped[str] = mapped_column(String(32), default="none")  # confirmation|decision|review|user|none
    approval_id: Mapped[str | None] = mapped_column(String(36))
    approval_summary: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(16), default="executed", index=True)  # executed|skipped|failed
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    draft_id: Mapped[str] = mapped_column(ForeignKey("email_drafts.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    agent_note: Mapped[str | None] = mapped_column(Text)
    source_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Automation(Base):
    __tablename__ = "automations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[dict] = mapped_column(JSON, default=dict)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    authorization_mode: Mapped[str] = mapped_column(String(48), default="confirm_milestones")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    trigger_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="user")  # user|agent|system
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), default="")
    resource_id: Mapped[str | None] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16), default="demo")
    result: Mapped[str] = mapped_column(String(32), default="success")
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_contact_user_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    company: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Todo(Base):
    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("email_messages.id"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_by: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(48), default="info")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    resource_type: Mapped[str | None] = mapped_column(String(48))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    appearance: Mapped[str] = mapped_column(String(16), default="night")
    default_authorization_mode: Mapped[str] = mapped_column(
        String(48), default="confirm_milestones"
    )
    agent_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    notification_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    privacy_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", "operation", name="uq_idem"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(96), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pkce_verifier_ciphertext: Mapped[str | None] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
