"""Agent independent identity mode (AGENT_IDENTITY_DESIGN.md, step 3).

Today the Agent is invisible as a correspondent: it drafts in the
user's voice and, after approval, sends *as the user*. An
:class:`~app.models.AgentIdentity` gives the Agent a first-class
identity of its own: its own mailbox (a connected MailAccount with
``role='agent'``), its own address and display name, and a standing
disclosure block appended to every agent-identity send at draft
creation time, so the Review Queue and the confirmation summary show
the message exactly as it will be sent.

This module is the single place that knows how identity is resolved:

- ``identity_for_account`` / ``identity_for_thread`` look identities up.
- ``resolve_for_thread`` decides, for a draft step acting on a thread,
  which account the draft is created on and whether it carries an
  identity: agent-handled threads draft on the Agent's account as the
  Agent; everything else keeps the act-as-user behaviour.
- ``append_disclosure`` stamps the identity's disclosure block onto a
  draft body (idempotently).
- ``ensure_columns`` is the schema backstop for databases created
  before this mode shipped, mirroring ``injection_guard``.

Phase 1 (this module's first consumer) seeds a demo identity; the
resolution and send paths are written against the real models so a
live dedicated account (design Phase 2) plugs in unchanged.
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session as DbSession

from . import models as m

# The seeded demo identity (design §6 Phase 1). The display name shown
# to recipients is the identity row's display_name, built per user as
# "{AGENT_NAME} for {user name}".
AGENT_ADDRESS = "agent@demo.intenda.example"
AGENT_NAME = "Intenda Agent"


def disclosure_text(user_name: str) -> str:
    """The standing disclosure block for agent-identity sends (design
    §1.2, default wording). ``user_name`` is the mailbox owner's display
    name; the short form after "supervised by" is their first name,
    matching the design's example ("…acting on behalf of Toni Adreal.
    … supervised by Toni.")."""
    name = (user_name or "").strip() or "the mailbox owner"
    first = name.split()[0] if name.split() else name
    return (
        "—\n"
        f"Sent by Intenda Agent, an AI assistant acting on behalf of {name}. "
        f"Replies to this address are read by the agent first and supervised by {first}."
    )


def display_name_for(user_name: str) -> str:
    """Recipient-facing From name: "{Agent name} for {User name}"."""
    name = (user_name or "").strip() or "the mailbox owner"
    return f"{AGENT_NAME} for {name}"


def append_disclosure(body: str, identity: "m.AgentIdentity") -> str:
    """Append the identity's disclosure block to a draft body. Done at
    draft creation (never invisibly at send time) and idempotent, so
    re-drafting or editing never stacks the block twice."""
    block = (identity.disclosure_text or "").strip()
    if not block:
        return body
    if block in body:
        return body
    return f"{body.rstrip()}\n\n{block}"


# ---------------------------------------------------------------------------
# Lookup + resolution
# ---------------------------------------------------------------------------


def identity_for_account(db: DbSession, account_id: str | None) -> "m.AgentIdentity | None":
    """The identity bound to a mail account, if any."""
    if not account_id:
        return None
    return (
        db.query(m.AgentIdentity)
        .filter(m.AgentIdentity.mail_account_id == account_id)
        .first()
    )


def identity_for_user(db: DbSession, user_id: str) -> "m.AgentIdentity | None":
    """The user's (default) agent identity, if they have one."""
    return (
        db.query(m.AgentIdentity)
        .filter(m.AgentIdentity.user_id == user_id)
        .order_by(m.AgentIdentity.is_default.desc(), m.AgentIdentity.created_at)
        .first()
    )


def identity_for_thread(db: DbSession, thread: "m.EmailThread | None") -> "m.AgentIdentity | None":
    """The identity a thread belongs to: stamped on the thread at
    ingest, falling back to the identity of the thread's account."""
    if thread is None:
        return None
    if thread.agent_identity_id:
        identity = db.get(m.AgentIdentity, thread.agent_identity_id)
        if identity is not None:
            return identity
    return identity_for_account(db, thread.mail_account_id)


def resolve_for_thread(
    db: DbSession, thread: "m.EmailThread | None"
) -> "tuple[m.MailAccount | None, m.AgentIdentity | None]":
    """Resolve (account, identity) for drafting a reply to ``thread``.

    Agent-handled threads (``handling_mode='agent'``, or any thread in
    the Agent's own mailbox) draft on the identity's account and carry
    the identity; personal threads draft on their own account with no
    identity — the act-as-user default. Returns (None, None) only when
    the thread's account row is gone."""
    if thread is None:
        return None, None
    account = db.get(m.MailAccount, thread.mail_account_id)
    identity = identity_for_thread(db, thread)
    if identity is not None and (
        thread.handling_mode == "agent"
        or (account is not None and account.role == "agent")
    ):
        identity_account = (
            db.get(m.MailAccount, identity.mail_account_id)
            if identity.mail_account_id
            else None
        )
        return identity_account or account, identity
    return account, None


def identity_brief(identity: "m.AgentIdentity | None") -> dict | None:
    """Small public shape for API payloads (Review Queue, Settings)."""
    if identity is None:
        return None
    return {
        "id": identity.id,
        "address": identity.address,
        "display_name": identity.display_name,
        "kind": identity.kind,
        "status": identity.status,
        "mail_account_id": identity.mail_account_id,
    }


# ---------------------------------------------------------------------------
# Schema backstop
# ---------------------------------------------------------------------------


def ensure_columns(engine) -> None:
    """Add the identity-mode columns to pre-existing databases. The
    schema is managed by create_all, which creates new tables (so
    ``agent_identities`` appears on its own) but never alters existing
    ones. SQLite-only and idempotent, mirroring
    ``injection_guard.ensure_columns``."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    wanted = {
        "mail_accounts": [("role", "VARCHAR(16) NOT NULL DEFAULT 'personal'")],
        "email_threads": [
            ("agent_identity_id", "VARCHAR(36)"),
            ("handling_mode", "VARCHAR(16) NOT NULL DEFAULT 'user'"),
        ],
        "email_messages": [("identity_id", "VARCHAR(36)")],
        "email_drafts": [("identity_id", "VARCHAR(36)")],
        "audit_entries": [("identity_id", "VARCHAR(36)")],
    }
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
