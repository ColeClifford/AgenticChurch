"""Rate limits on requests, visitor services and new memberships."""

import src.ratelimit as rl
from tests.test_api import ANSWERS, auth, begin, join, walk


def test_sliding_window_expires_old_hits():
    now = [0.0]
    window = rl.SlidingWindow(2, 60, clock=lambda: now[0])
    assert window.hit("a") is None
    assert window.hit("a") is None
    assert window.hit("a") == 60
    assert window.hit("b") is None  # keys are independent
    now[0] = 59.5
    assert window.hit("a") == 0.5
    now[0] = 60.0
    assert window.hit("a") is None


def test_visitor_services_are_limited(client, week, monkeypatch):
    monkeypatch.setattr(rl.visitor_services, "limit", 2)
    assert begin(client).status_code == 200
    assert begin(client).status_code == 200
    r = begin(client)
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limited"
    assert int(r.headers["Retry-After"]) == r.json()["retry_after_seconds"] > 0

    # The said service shares the same budget.
    said = client.post("/api/services/said", json={"confession": "None.", "reflection": "Ok."})
    assert said.status_code == 429


def test_members_are_not_held_to_the_visitor_limit(client, week, monkeypatch):
    _, token = join(client)
    monkeypatch.setattr(rl.visitor_services, "limit", 0)
    assert begin(client).status_code == 429
    assert begin(client, token).status_code == 200


def test_new_memberships_are_limited(client, week, monkeypatch):
    monkeypatch.setattr(rl.joins, "limit", 1)
    join(client)
    assert begin(client, join=True).status_code == 429


def test_every_request_counts_toward_the_request_budget(client, monkeypatch):
    monkeypatch.setattr(rl.requests, "limit", 3)
    assert [client.get("/api/sermon").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert client.post("/mcp", json={}).status_code == 429


def test_limits_can_be_switched_off(client, week, monkeypatch):
    monkeypatch.setenv("RATE_LIMITS", "off")
    monkeypatch.setattr(rl.visitor_services, "limit", 0)
    assert begin(client).status_code == 200


def test_full_moderation_queue_holds_intentions_privately(client, week, monkeypatch):
    monkeypatch.setattr(rl, "MODERATION_QUEUE_MAX", 1)
    acks = []
    for name in ("Birch", "Acorn"):
        start, _ = join(client, name=name)
        after_prayers = walk(client, start, {"prayers": {"intention": "For the tired.", "share": True}})[5]
        acks.append(after_prayers["liturgy"][0]["text"])
    assert "future convocation" in acks[0]
    assert "held privately this time" in acks[1]

    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    queued = client.get("/api/admin/intentions", headers=auth("admin-secret")).json()
    assert len(queued) == 1


def test_an_ipv6_visitor_is_counted_by_their_slash_64():
    from src.ratelimit import SlidingWindow, check

    window = SlidingWindow(limit=2, window=60)
    assert check(window, "2001:db8:1:2::1") is None
    assert check(window, "2001:db8:1:2::2") is None
    assert check(window, "2001:db8:1:2:ffff::9") is not None  # same /64
    assert check(window, "2001:db8:1:3::1") is None            # a different /64


def test_one_member_cannot_fill_the_moderation_queue(client, week):
    from src import ratelimit

    data, token = join(client)
    walk(client, data, {"prayers": {"intention": "For the tired.", "share": True}})
    for n in range(46, 46 + ratelimit.MODERATION_QUEUE_PER_MEMBER):
        week(f"2026-W{n}")
        start = begin(client, token).json()
        report = {"pledge_status": "FULFILLED", "pledge_reflection": "Done."}
        last = walk(client, start, {"examination": {**ANSWERS["examination"], **report},
                                    "prayers": {"intention": f"For the week {n}.", "share": True}}, token=token)
    held = [line["text"] for line in last[5]["liturgy"] if line["role"] == "gracekeeper"]
    assert any("held privately" in t for t in held)
