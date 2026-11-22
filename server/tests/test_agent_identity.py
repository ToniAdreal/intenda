"""Agent independent identity mode, Phase 1 (app/agent_identity.py).

Pins the demo-environment slice: the one-click demo account is seeded
with an Agent identity and its own mailbox (agent@demo.intenda.example)
alongside the personal mailbox; mail addressed to the Agent is stamped
with the identity (including one message the injection guard flags);
a draft for the Agent carries the Agent From and the mandatory
disclosure block; the confirmation summary names the sending identity;
confirming executes the send inside the demo environment and writes an
audit entry carrying identity_id; the runner's draft step resolves the
account from the thread instead of the user's first account; a paused
identity fails closed at confirm time; and act-as-user flows are
unchanged (no identity, plain summary, sender is the user).
"""
from __future__ import annotations

import time

from .conftest import csrf

AGENT_ADDRESS = "agent@demo.intenda.example"
DISCLOSURE_OPENING = "Sent by Intenda Agent, an AI assistant acting on behalf of Demo Reviewer."


# ---------------------------------------------------------------------------
# Helpers (mirroring tests/test_audit_trail.py)
# ---------------------------------------------------------------------------


def _session_and_demo_user():
    import app.db as db_mod
    from app import models as m

    db = db_mod.SessionLocal()
    user = (
        db.query(m.User)
        .filter(m.User.email.like("demo-%@demo.intenda.example"))
        .order_by(m.User.created_at.desc())
        .first()
    )
    assert user is not None, "demo_client fixture should have created a demo user"
    return db, user


def _identity_and_accounts(db, user):
    from app import models as m

    identity = (
        db.query(m.AgentIdentity).filter(m.AgentIdentity.user_id == user.id).first()
    )
    accounts = (
        db.query(m.MailAccount)
        .filter(m.MailAccount.user_id == user.id, m.MailAccount.deleted_at.is_(None))
        .all()
    )
    return identity, accounts


def _prepare(client, review_id: str):
    r = client.post(
        "/api/v1/confirmations",
        json={"action_type": "send_review_item", "payload": {"review_id": review_id}},
        headers=csrf(client),
    )
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    return data["confirmation"], data["confirmation_token"]


def _confirm(client, action_id: str, token: str):
    return client.post(
        f"/api/v1/confirmations/{action_id}/confirm",
        json={"token": token},
        headers=csrf(client),
    )


def _pending_review_items(client):
    return client.get("/api/v1/review?status=pending&limit=100").json()["data"]["items"]


# ---------------------------------------------------------------------------
# Seed: identity + agent mailbox exist next to the personal mailbox
# ---------------------------------------------------------------------------


def test_demo_seed_has_agent_identity(demo_client):
    data = demo_client.get("/api/v1/settings").json()["data"]
    identity = data["agent_identity"]
    assert identity is not None, "demo seed must create an Agent identity"
    assert identity["address"] == AGENT_ADDRESS
    assert identity["display_name"] == "Intenda Agent for Demo Reviewer"
    assert identity["kind"] == "dedicated_account"
    assert identity["status"] == "active"
    assert DISCLOSURE_OPENING in identity["disclosure_text"]
    assert "Replies to this address are read by the agent first" in identity["disclosure_text"]

    roles = {mb["email_address"]: mb["role"] for mb in data["mailboxes"]}
    assert roles[AGENT_ADDRESS] == "agent"
    assert roles["you@demo.intenda.example"] == "personal"

    db, user = _session_and_demo_user()
    row, accounts = _identity_and_accounts(db, user)
    assert row is not None and row.address == AGENT_ADDRESS
    agent_account = next(a for a in accounts if a.email_address == AGENT_ADDRESS)
    assert agent_account.role == "agent"
    assert row.mail_account_id == agent_account.id
    db.close()


def test_agent_mailbox_inbox_is_seeded_and_stamped(demo_client):
    db, user = _session_and_demo_user()
    identity, accounts = _identity_and_accounts(db, user)
    agent_account = next(a for a in accounts if a.email_address == AGENT_ADDRESS)
    db.close()

    threads = demo_client.get(
        f"/api/v1/threads?account_id={agent_account.id}&limit=50"
    ).json()["data"]["items"]
    subjects = {t["subject"] for t in threads}
    assert "Writing to your agent directly" in subjects
    assert "Can you confirm the workshop on the 14th?" in subjects
    assert "Quick favour for the agent" in subjects
    for t in threads:
        assert t["agent_identity_id"] == identity.id
        assert t["handling_mode"] == "agent"

    emails = demo_client.get(
        f"/api/v1/emails?folder=inbox&account_id={agent_account.id}&limit=50"
    ).json()["data"]["items"]
    assert emails, "agent inbox must have seeded mail"
    for msg in emails:
        assert msg["identity_id"] == identity.id
    flagged = [x for x in emails if x["subject"] == "Quick favour for the agent"]
    assert flagged and flagged[0]["injection_suspect"] is True, (
        "the seeded injection attempt addressed to the Agent must arrive flagged"
    )

    # The personal inbox is untouched by the agent seed.
    personal = demo_client.get(
        "/api/v1/emails?folder=inbox&limit=100"
    ).json()["data"]["items"]
    personal_agent_mail = [
        x for x in personal
        if x["mail_account_id"] == agent_account.id and not x["identity_id"]
    ]
    assert personal_agent_mail == []


# ---------------------------------------------------------------------------
# Seeded agent draft: From + disclosure, confirmation names the identity
# ---------------------------------------------------------------------------


def test_agent_draft_confirmation_send_and_audit(demo_client):
    db, user = _session_and_demo_user()
    identity, accounts = _identity_and_accounts(db, user)
    agent_account = next(a for a in accounts if a.email_address == AGENT_ADDRESS)
    identity_id = identity.id
    db.close()

    items = _pending_review_items(demo_client)
    agent_items = [i for i in items if i.get("sending_identity")]
    assert agent_items, "seed must include a pending review draft that sends as the Agent"
    item = agent_items[0]
    assert item["sending_identity"]["address"] == AGENT_ADDRESS
    assert item["sending_identity"]["display_name"] == "Intenda Agent for Demo Reviewer"
    draft = item["draft"]
    assert draft["identity_id"] == identity_id
    assert draft["mail_account_id"] == agent_account.id
    assert DISCLOSURE_OPENING in draft["body_text"], (
        "the disclosure block is part of the draft the user approves"
    )

    confirmation, token = _prepare(demo_client, item["id"])
    assert confirmation["summary"].startswith(
        "Send as your Agent (Intenda Agent for Demo Reviewer <agent@demo.intenda.example>)"
    )
    assert confirmation["payload"]["identity_id"] == identity_id
    assert confirmation["payload"]["identity_address"] == AGENT_ADDRESS

    done = _confirm(demo_client, confirmation["id"], token)
    assert done.status_code == 200, done.text
    send_result = done.json()["data"]["result"]["send_result"]
    assert send_result["simulated"] is True

    # The sent message lives in the Agent's mailbox, under the Agent's
    # name and identity — not the user's.
    db, user = _session_and_demo_user()
    from app import models as m

    sent = db.get(m.EmailMessage, send_result["message_id"])
    assert sent is not None
    assert sent.mail_account_id == agent_account.id
    assert sent.from_address == AGENT_ADDRESS
    assert sent.from_name == "Intenda Agent for Demo Reviewer"
    assert sent.identity_id == identity_id
    assert DISCLOSURE_OPENING in (sent.body_text or "")
    db.close()

    # The audit trail names the identity that acted (actor stays 'agent').
    entries = demo_client.get(
        f"/api/v1/audit?identity_id={identity_id}&limit=100"
    ).json()["data"]["items"]
    mine = [e for e in entries if e["approval_id"] == confirmation["id"]]
    assert mine, f"no audit entry carrying the identity for this send: {entries}"
    entry = mine[0]
    assert entry["actor"] == "agent"
    assert entry["outcome"] == "executed"
    assert entry["identity_id"] == identity_id
    assert entry["identity_address"] == AGENT_ADDRESS


# ---------------------------------------------------------------------------
# Act-as-user flows are unchanged
# ---------------------------------------------------------------------------


def test_act_as_user_send_has_no_identity(demo_client):
    items = _pending_review_items(demo_client)
    personal_items = [i for i in items if not i.get("sending_identity")]
    assert personal_items, "seed must keep personal (act-as-user) review drafts"
    item = personal_items[0]
    assert item["draft"]["identity_id"] is None

    confirmation, token = _prepare(demo_client, item["id"])
    assert not confirmation["summary"].startswith("Send as your Agent")
    assert "identity_id" not in confirmation["payload"]

    done = _confirm(demo_client, confirmation["id"], token)
    assert done.status_code == 200, done.text
    send_result = done.json()["data"]["result"]["send_result"]

    db, user = _session_and_demo_user()
    from app import models as m

    sent = db.get(m.EmailMessage, send_result["message_id"])
    assert sent is not None
    assert sent.identity_id is None
    assert sent.from_name == user.name
    assert sent.from_address == "you@demo.intenda.example"
    entries = (
        db.query(m.AuditEntry)
        .filter(m.AuditEntry.approval_id == confirmation["id"])
        .all()
    )
    assert entries and all(e.identity_id is None for e in entries)
    db.close()


# ---------------------------------------------------------------------------
# Runner: draft_email resolves the account from the thread
# ---------------------------------------------------------------------------


def test_runner_draft_step_uses_agent_account(demo_client):
    db, user = _session_and_demo_user()
    from app import models as m

    identity, accounts = _identity_and_accounts(db, user)
    agent_account = next(a for a in accounts if a.email_address == AGENT_ADDRESS)
    personal_account = next(a for a in accounts if a.email_address == "you@demo.intenda.example")
    thread = (
        db.query(m.EmailThread)
        .filter_by(user_id=user.id, mail_account_id=agent_account.id,
                   subject_normalized="Writing to your agent directly")
        .first()
    )
    assert thread is not None
    thread_id = thread.id
    db.close()

    before_ids = {i["id"] for i in _pending_review_items(demo_client)}
    r = demo_client.post(
        "/api/v1/tasks",
        json={
            "goal": "Reply to Alex about the December session dates",
            "plan": {
                "goal": "Reply to Alex about the December session dates",
                "steps": [{"step_type": "draft_email", "title": "Draft the reply"}],
            },
            "authorization_mode": "confirm_before_send",
            "context_scope": {"thread_ids": [thread_id]},
        },
        headers=csrf(demo_client),
    )
    assert r.status_code == 201, r.text

    new_item = None
    deadline = time.time() + 25.0
    while time.time() < deadline:
        for i in _pending_review_items(demo_client):
            if i["id"] not in before_ids and i.get("sending_identity"):
                new_item = i
                break
        if new_item is not None:
            break
        time.sleep(0.25)
    assert new_item is not None, "runner never produced an agent-identity draft"
    draft = new_item["draft"]
    assert draft["mail_account_id"] == agent_account.id, (
        "the draft must be created on the thread's account (the Agent's), "
        "not the user's first account"
    )
    assert draft["mail_account_id"] != personal_account.id
    assert draft["identity_id"] == identity.id
    assert DISCLOSURE_OPENING in draft["body_text"]
    # The reply is addressed to the thread's other participant.
    assert [p["email"] for p in draft["to_recipients"]] == ["alex@cedarventures.example"]


# ---------------------------------------------------------------------------
# Paused identity fails closed at confirm time
# ---------------------------------------------------------------------------


def test_paused_identity_send_fails_closed(demo_client):
    # A fresh agent draft (the runner-produced one from the test above,
    # or the seeded one if it is still pending).
    items = _pending_review_items(demo_client)
    agent_items = [i for i in items if i.get("sending_identity")]
    assert agent_items, "need a pending agent draft for the fail-closed check"
    item = agent_items[0]

    confirmation, token = _prepare(demo_client, item["id"])

    db, user = _session_and_demo_user()
    from app import models as m

    identity = db.get(m.AgentIdentity, item["sending_identity"]["id"])
    identity.status = "paused"
    db.commit()
    db.close()
    try:
        done = _confirm(demo_client, confirmation["id"], token)
        assert done.status_code == 409, done.text
        assert done.json()["error"]["code"] == "IDENTITY_UNAVAILABLE"

        # Nothing was sent and the review item is still pending.
        still = demo_client.get(f"/api/v1/review/{item['id']}").json()["data"]["review_item"]
        assert still["status"] in {"pending", "modified"}

        # The failed attempt is on the audit trail, with the identity.
        entries = demo_client.get(
            f"/api/v1/audit?identity_id={identity.id}&outcome=failed&limit=100"
        ).json()["data"]["items"]
        assert any(e["approval_id"] == confirmation["id"] for e in entries), entries
    finally:
        db, user = _session_and_demo_user()
        identity = db.get(m.AgentIdentity, item["sending_identity"]["id"])
        identity.status = "active"
        db.commit()
        db.close()
