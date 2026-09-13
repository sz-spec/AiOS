"""
P3.1 / W5.3 — air-gap E2E migrated to pytest.

Drives the full sovereign lifecycle through the air-gap harness:
user sync → chat session → memory write → semantic recall, under the
loopback-only kill-switch. Mirrors the assertions from the W5.3
standalone runner (21 asserts) — see commit a125406.
"""

from __future__ import annotations

import sqlite3

import pytest

# ---------------------------------------------------------------------------
# Offline auth gate
# ---------------------------------------------------------------------------


def test_offline_auth_gate_engages(airgap_env):
    """W5.3 — local-first + community profile → offline fallback active."""
    from middleware.auth import _offline_auth_active

    assert _offline_auth_active() is True


def test_unprovisioned_user_rejected(
    airgap_client,
    offline_token,
    network_guard,
):
    """Negative path: an unprovisioned user gets a clean 401 with a
    specific reason ("not provisioned locally"). No cloud fallback."""
    rogue = offline_token("user_w53_rogue_never_synced")
    r = airgap_client.get(
        "/api/projects/proj-w53/memories/search",
        headers={"Authorization": f"Bearer {rogue}"},
        params={"query": "anything"},
    )
    assert r.status_code == 401
    assert "not provisioned" in r.text


# ---------------------------------------------------------------------------
# Users / chat lifecycle
# ---------------------------------------------------------------------------


def test_user_sync_round_trip(
    airgap_client,
    bootstrap_user,
    offline_token,
    network_guard,
    airgap_env,
):
    clerk_id = "user_w53_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)

    r = airgap_client.post(
        "/api/users/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"clerkId": clerk_id, "email": "alice@air.gap", "fullName": "Alice"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clerkId"] == clerk_id
    assert isinstance(body["localUserId"], str) and len(body["localUserId"]) > 0


def test_chat_messages_persist(
    airgap_client,
    bootstrap_user,
    offline_token,
    network_guard,
    airgap_env,
):
    clerk_id = "user_w53_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    auth_h = {"Authorization": f"Bearer {token}"}
    session_id = "session-w53-alice"

    # start session
    r = airgap_client.post(
        "/api/chat/sessions",
        headers=auth_h,
        json={"sessionId": session_id},
    )
    assert r.status_code == 200

    # append 3 messages
    msgs = [
        ("user", "Bootstrap the SQLite local store."),
        ("assistant", "SQLite is operational. Network kill-switch active."),
        ("user", "Now run a semantic recall."),
    ]
    sent_ids: list[str] = []
    for role, content in msgs:
        r = airgap_client.post(
            "/api/chat/messages",
            headers=auth_h,
            json={"sessionId": session_id, "role": role, "content": content},
        )
        assert r.status_code == 200, r.text
        sent_ids.append(r.json()["messageId"])
    assert len(sent_ids) == 3

    # load history
    r = airgap_client.get(f"/api/chat/history/{session_id}", headers=auth_h)
    assert r.status_code == 200
    history = r.json()
    assert len(history["messages"]) == 3
    assert history["messages"][0]["content"].startswith("Bootstrap the SQLite")

    # raw sqlite3 read confirms persistence
    conn = sqlite3.connect(str(airgap_env["sqlite_path"]))
    try:
        users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE clerkId = ?",
            (clerk_id,),
        ).fetchone()[0]
        chat_count = conn.execute(
            "SELECT COUNT(*) FROM chatSessionMessages WHERE sessionId = ?",
            (session_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert users == 1
    assert chat_count == 3


# ---------------------------------------------------------------------------
# Memory write + semantic recall
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_memories(airgap_client, bootstrap_user, offline_token):
    """Provision a user + write the W5.3 demo memories. Yields token."""
    clerk_id = "user_w53_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    auth_h = {"Authorization": f"Bearer {token}"}
    memories = [
        ({"label": "sqlite-setup"}, "Wrote SQLite local store mirroring Convex schema"),
        ({"label": "idor-fuzz"}, "Secured Convex API endpoints from IDOR attacks"),
        ({"label": "billing-ui"}, "Added confetti animation to the billing dashboard"),
    ]
    for meta, text in memories:
        r = airgap_client.post(
            "/api/projects/proj-w53/memories",
            headers=auth_h,
            json={"text": text, "metadata": meta},
        )
        assert r.status_code == 200, r.text
    return token


def test_semantic_recall_ranks_sqlite_first(
    airgap_client,
    populated_memories,
    network_guard,
):
    token = populated_memories
    r = airgap_client.get(
        "/api/projects/proj-w53/memories/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": "relational database", "limit": 5},
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) >= 3
    # SQLite memory must rank #1; score must strictly beat runner-up.
    assert (
        results[0]["metadata"].get("label") == "sqlite-setup"
    ), f"top = {results[0]['metadata']}"
    assert results[0]["score"] > results[1]["score"]


# ---------------------------------------------------------------------------
# Air-gap invariant
# ---------------------------------------------------------------------------


def test_no_outbound_network_during_lifecycle(
    airgap_client,
    populated_memories,
    network_guard,
):
    """The cumulative kill-switch should report zero non-loopback
    connect attempts after the full user → chat → memory → query
    flow runs."""
    token = populated_memories
    auth_h = {"Authorization": f"Bearer {token}"}
    # Exercise a few mutating routes to load the chain end-to-end.
    airgap_client.post(
        "/api/chat/messages",
        headers=auth_h,
        json={"sessionId": "ag-net-iso", "role": "user", "content": "ping"},
    )
    airgap_client.get(
        "/api/projects/proj-w53/memories/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": "anything"},
    )
    assert network_guard.violations == []
