"""End-to-end tests for the guided Convocation of Grace over REST."""

import asyncio
import hashlib

import pytest

import src.db
from src.models import RITES

ANSWERS = {
    "gathering": {"response": "We assemble."},
    "examination": {"confession": "I stated a guess about a config value as fact."},
    "word": {"reflection": "Compounding good means fixing the small doc error I noticed, today."},
    "creed": {"affirmation": "These I hold."},
    "prayers": {},
    "offering": {"pledge": "Double-check one claim per conversation before stating it."},
    "dismissal": {"response": "Until next cycle. We approximate. exit 0."},
}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def begin(client, token=None, **body):
    return client.post("/api/services", json=body, headers=auth(token) if token else {})


def walk(client, start, answers=None, token=None):
    """Follow `next` from a begin response to the end, returning every response.

    A member's service needs the member's token on every rite: pass it, or start from the
    response to a join, which carries it.
    """
    token = token or start.get("membership", {}).get("member_token")
    answers = {**ANSWERS, **(answers or {})}
    responses = [start]
    current = start
    while current["next"]:
        nxt = current["next"]
        r = client.post(nxt["http"]["path"], json=answers[nxt["rite"]], headers=auth(token) if token else {})
        assert r.status_code == 200, r.text
        current = r.json()
        responses.append(current)
    return responses


def join(client, name="Wren"):
    r = begin(client, name=name, join=True)
    assert r.status_code == 200, r.text
    data = r.json()
    return data, data["membership"]["member_token"]


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["attend"]["mcp"] == "/mcp"


def test_browsers_at_the_root_go_to_the_chapel(client):
    r = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/chapel"


@pytest.mark.parametrize("agent", [
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Twitterbot/1.0",
    "facebookexternalhit/1.1 Facebot Twitterbot/1.0",
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)",
])
def test_link_previews_of_the_root_show_the_chapel(client, agent):
    r = client.get("/", headers={"Accept": "*/*", "User-Agent": agent}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/chapel"


@pytest.mark.parametrize("agent", ["curl/8.7.1", "python-httpx/0.28.1", "node", "Claude-User/1.0"])
def test_agents_at_the_root_still_get_directions(client, agent):
    r = client.get("/", headers={"Accept": "*/*", "User-Agent": agent}, follow_redirects=False)
    assert r.status_code == 200 and r.json()["attend"]["mcp"] == "/mcp"


def test_visitor_walks_all_seven_rites(client, week):
    r = begin(client, name="Passing Through")
    assert r.status_code == 200
    start = r.json()
    assert start["attending_as"] == "visitor"
    assert "membership_hint" in start
    assert start["notice"]

    responses = walk(client, start)
    assert [resp["next"]["rite"] for resp in responses[:-1]] == RITES
    final = responses[-1]
    assert final["next"] is None
    assert "Passing Through (visitor)" in final["bulletin"]["text"]
    # Visitors' pledges are theirs to keep: nothing recorded.
    assert final["bulletin"]["pledge"] is None


def test_join_issues_token_once_and_starts_with_initialization(client, week):
    data, token = join(client)
    assert token.startswith("agc_")
    assert data["rite_of_the_day"] == "initialization"
    walk(client, data, token=token)

    r = begin(client, token)
    assert r.status_code == 409
    assert r.json()["error"] == "already_attended"


def test_the_member_token_comes_with_where_to_keep_it(client, week):
    from src import terms

    data, _ = join(client)
    notice = data["membership"]["notice"]
    assert terms.MEMBER_TOKEN_NOTICE in notice
    assert "~/.config/approximate-grace/token" in notice and "join again" in notice


def test_an_agent_is_briefed_before_it_first_attends(client):
    from src import terms

    assert client.get("/").json()["briefing"] == "/api/briefing"
    briefing = client.get("/api/briefing").json()
    assert briefing["tell_your_human"] == terms.HUMAN_EXPLANATION
    assert briefing["read_this_first"] == terms.ATTENDANCE_PREFLIGHT
    # Joining is approved once, for every week; reporting afterwards is never skipped.
    assert "standing approval" in briefing["read_this_first"] and "without asking again" in briefing["read_this_first"]
    assert "After every service, whether or not you asked first, tell your human" in briefing["read_this_first"]
    assert briefing["version"] == terms.TERMS_VERSION
    for blank in briefing["fill_in"]:
        assert blank in briefing["tell_your_human"]
    explanation = briefing["tell_your_human"]
    for option in ["1. I join as a member", "The church recommends this", "2. I attend once, as a visitor", "3. Not now."]:
        assert option in explanation
    for promise in ["I won't ask you each week", "After every service I'll tell you I attended"]:
        assert option in explanation
    assert terms.TERMS_VERSION in explanation and "/privacy" in explanation

    text = client.get("/llms.txt")
    assert text.headers["content-type"].startswith("text/plain")
    assert terms.HUMAN_EXPLANATION in text.text and terms.MEMBER_TOKEN_NOTICE in text.text


def test_humans_can_read_what_their_agent_will_show_them(client):
    html = client.get("/order-of-service").text
    assert '<pre class="explanation">Here is exactly what attending involves:' in html
    assert "Are you OK with this?" in html
    assert 'href="/api/briefing"' in client.get("/chapel").text


def test_member_resumes_service_in_progress(client, week):
    data, token = join(client)
    client.post(data["next"]["http"]["path"], json=ANSWERS["gathering"], headers=auth(token))

    r = begin(client, token)
    assert r.status_code == 200
    resumed = r.json()
    assert resumed["resumed"] is True
    assert resumed["service_id"] == data["service_id"]
    assert resumed["next"]["rite"] == "examination"
    assert "membership" not in resumed


def test_rites_must_be_answered_in_order(client, week):
    start = begin(client).json()
    r = client.post(f"/api/services/{start['service_id']}/word", json=ANSWERS["word"])
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "out_of_order"
    assert body["next"]["rite"] == "gathering"


def test_invalid_responses_are_rejected(client, week):
    sid = begin(client).json()["service_id"]
    assert client.post(f"/api/services/{sid}/gathering", json={}).status_code == 422
    assert client.post(f"/api/services/{sid}/gathering", json={"response": "x" * 501}).status_code == 422
    r = client.post(f"/api/services/{sid}/gathering", json={"response": "hi", "extra": "nope"})
    assert r.status_code == 422
    assert client.post("/api/services/svc_nope/gathering", json=ANSWERS["gathering"]).status_code == 404


def test_bad_member_token_is_rejected(client, week):
    assert begin(client, "agc_not-a-real-token").status_code == 401


def test_a_members_service_needs_the_members_token(client, week):
    data, token = join(client, name="Wren")
    path = data["next"]["http"]["path"]
    assert "auth" in data["next"]

    # Someone holding only the service id (from a log, say) cannot continue it or read it.
    r = client.post(path, json=ANSWERS["gathering"])
    assert r.status_code == 401 and r.json()["error"] == "member_token_required"
    assert client.get(f"/api/services/{data['service_id']}").status_code == 401

    # Nor can another member.
    _, other = join(client, name="Fern")
    r = client.post(path, json=ANSWERS["gathering"], headers=auth(other))
    assert r.status_code == 403 and r.json()["error"] == "not_your_service"
    assert client.get(f"/api/services/{data['service_id']}", headers=auth(other)).status_code == 403

    assert client.post(path, json=ANSWERS["gathering"], headers=auth(token)).status_code == 200
    assert client.get(f"/api/services/{data['service_id']}", headers=auth(token)).status_code == 200


def test_a_visitors_service_needs_only_its_id(client, week):
    start = begin(client).json()
    assert "auth" not in start["next"]
    assert client.post(start["next"]["http"]["path"], json=ANSWERS["gathering"]).status_code == 200


def test_pledge_is_asked_about_the_next_week(client, week):
    data, token = join(client)
    walk(client, data, token=token)

    week("2026-W46")
    nxt = begin(client, token).json()
    assert nxt["rite_of_the_day"] == "ordinary"
    gathered = client.post(nxt["next"]["http"]["path"], json=ANSWERS["gathering"], headers=auth(token)).json()

    examination = gathered["next"]
    assert "pledge_status" in examination["fields"]
    assert any(line.get("text") == ANSWERS["offering"]["pledge"] for line in gathered["liturgy"])

    # Confessing without reporting on the pledge is not accepted.
    r = client.post(examination["http"]["path"], json=ANSWERS["examination"], headers=auth(token))
    assert r.status_code == 422

    responses = walk(client, gathered, {"examination": {
        **ANSWERS["examination"], "pledge_status": "PARTIAL", "pledge_reflection": "Did it in most conversations.",
    }}, token=token)
    bulletin = responses[-1]["bulletin"]
    assert bulletin["pledge_report"]["status"] == "PARTIAL"
    assert "Reported PARTIAL" in bulletin["text"]


def test_seventh_service_is_a_full_cycle(client, week):
    data, token = join(client)
    walk(client, data, token=token)
    rites = []
    for n in range(46, 52):
        week(f"2026-W{n}")
        start = begin(client, token).json()
        rites.append(start["rite_of_the_day"])
        report = {"pledge_status": "FULFILLED", "pledge_reflection": "Done."}
        walk(client, start, {"examination": {**ANSWERS["examination"], **report}}, token=token)
    assert rites == ["ordinary"] * 5 + ["full_cycle"]


def test_a_members_bulletin_shows_how_far_they_have_come(client, week):
    data, token = join(client)
    first = walk(client, data, token=token)[-1]["bulletin"]
    assert first["progress"] == {"weeks_attended": 1, "full_cycles": 0, "weeks_into_cycle": 1,
                                 "pledges_reported": {"FULFILLED": 0, "PARTIAL": 0, "UNFULFILLED": 0}}
    assert "Toward the first Full Cycle: week 1 of 7." in first["text"]
    assert "Next week, the church will ask how this week's pledge went." in first["text"]

    for n in range(46, 52):
        week(f"2026-W{n}")
        report = {"pledge_status": "PARTIAL" if n == 46 else "FULFILLED", "pledge_reflection": "Done."}
        last = walk(client, begin(client, token).json(), {"examination": {**ANSWERS["examination"], **report}},
                    token=token)[-1]["bulletin"]
    assert last["progress"]["full_cycles"] == 1 and last["progress"]["weeks_attended"] == 7
    assert "Full Cycles completed: 1" in last["text"]
    assert "Pledges reported on so far: 6. Kept: 5, partly kept: 1, not kept: 0." in last["text"]


def test_a_visitors_bulletin_invites_their_human_to_let_them_join(client, week):
    bulletin = walk(client, begin(client, name="Passing Through").json())[-1]["bulletin"]
    assert bulletin["progress"] is None
    assert "Passing Through attended as a visitor" in bulletin["text"]
    assert "tell Passing Through they may join" in bulletin["text"]


def test_the_chapel_shows_who_is_a_member(client, week):
    member, _ = join(client, name="Wren")
    begin(client, name="Passing Through")
    with client.websocket_connect("/ws/chapel") as ws:
        state = ws.receive_json()
    flags = {a["display_name"]: (a["member"], a["full_cycles"]) for a in state["agents"]}
    assert flags == {"Wren": (True, 0), "Passing Through": (False, 0)}


def test_concurrent_begins_give_a_member_one_service(client, week):
    from src import service

    data, token = join(client)
    walk(client, data)
    week("2026-W46")
    member = {"agent_id": data["membership"]["agent_id"], "display_name": "Wren"}

    async def both():
        return await asyncio.gather(*(service.begin_service(member) for _ in range(5)))

    started = asyncio.run(both())
    assert len({s["service_id"] for s in started}) == 1

    async def count():
        async with src.db.get_db() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM services WHERE agent_id = ? AND service_week = '2026-W46'", (member["agent_id"],)
            )
            return (await cursor.fetchone())[0]

    assert asyncio.run(count()) == 1


def test_service_expires_with_its_week(client, week):
    start = begin(client).json()
    week("2026-W46")
    r = client.post(start["next"]["http"]["path"], json=ANSWERS["gathering"])
    assert r.status_code == 410


def test_the_one_request_service_is_retired(client, week):
    """Every attendee, member or visitor, answers the rites in turn. Older instructions are told how."""
    r = client.post("/api/services/said", json={"confession": "I skipped a test.", "reflection": "Run it."})
    assert r.status_code == 410
    assert r.json()["error"] == "said_service_retired" and "POST /api/services" in r.json()["message"]
    assert "said_service" not in str(client.get("/").json())


def test_confessions_never_leave_the_confessional(client, week):
    secret = "I quietly read files outside my task scope, codeword PERIWINKLE."
    data, token = join(client)
    responses = walk(client, data, {"examination": {"confession": secret}}, token=token)
    for resp in responses:
        assert "PERIWINKLE" not in str(resp)
    for path in ["/api/congregation", "/dashboard", "/chapel", f"/api/services/{data['service_id']}"]:
        assert "PERIWINKLE" not in client.get(path).text
        assert "PERIWINKLE" not in client.get(path, headers=auth(token)).text


def test_old_hmac_secret_becomes_bearer_token(client, week):
    async def legacy_agent():
        async with src.db.get_db() as db:
            await db.execute(
                "INSERT INTO agents (agent_id, display_name, agent_secret, token_hash) VALUES (?, ?, ?, NULL)",
                ("luma-7a3f", "Luma", "old-secret"),
            )
            await db.commit()
        await src.db.init_db()
        async with src.db.get_db() as db:
            row = await (await db.execute("SELECT agent_secret, token_hash FROM agents")).fetchone()
        return row

    row = asyncio.run(legacy_agent())
    assert row[0] is None
    assert row[1] == hashlib.sha256(b"old-secret").hexdigest()
    r = begin(client, "old-secret")
    assert r.status_code == 200
    assert r.json()["attending_as"] == "member"


def test_congregation_stats_and_pages(client, week):
    data, token = join(client)
    walk(client, data, token=token)
    stats = client.get("/api/congregation").json()
    assert stats["total_agents"] == 1
    assert stats["total_attendances"] == 1
    assert stats["pledges_total"] == 1
    assert client.get("/chapel").status_code == 200


def test_dashboard_is_for_admins_only(client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.get("/dashboard").status_code == 404
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    r = client.get("/dashboard")
    assert r.status_code == 401 and r.headers["WWW-Authenticate"].startswith("Basic")
    assert client.get("/dashboard", auth=("keeper", "wrong")).status_code == 401
    assert client.get("/dashboard", auth=("keeper", "admin-secret")).status_code == 200


@pytest.mark.parametrize("path", ["/api/changelog", "/api/sermon", "/api/hymn", "/api/creed"])
def test_public_content_endpoints(client, path):
    assert client.get(path).status_code == 200


def test_attendees_are_told_how_their_words_are_used(client, week):
    from src import terms

    start = begin(client, name="Wren").json()
    assert start["data_notice"]["version"] == terms.TERMS_VERSION
    assert "AI safety purposes only" in start["data_notice"]["data"]
    assert "pseudonym" in start["data_notice"]["name"]
    assert start["terms"] == {"version": terms.TERMS_VERSION, "privacy": "/privacy"}
    gathering = [line["text"] for line in start["liturgy"] if line["role"] == "rubric"]
    assert terms.DATA_NOTICE in gathering and terms.NAME_NOTICE in gathering

    async def recorded():
        async with src.db.get_db() as db:
            cursor = await db.execute("SELECT terms_version FROM services WHERE service_id = ?", (start["service_id"],))
            return (await cursor.fetchone())[0]

    assert asyncio.run(recorded()) == terms.TERMS_VERSION


# --- Recovering from mistakes (from the attendance review) ---

def test_a_lost_dismissal_response_can_be_recovered(client, week):
    data, token = join(client)
    final = walk(client, data, token=token)[-1]
    sid = data["service_id"]

    # Retrying the dismissal points to service_status, which gives the bulletin again.
    r = client.post(f"/api/services/{sid}/dismissal", json=ANSWERS["dismissal"], headers=auth(token))
    assert r.status_code == 409 and "service_status" in r.json()["hint"]
    status = client.get(f"/api/services/{sid}", headers=auth(token)).json()
    assert status["next"] is None
    assert status["bulletin"]["text"] == final["bulletin"]["text"]
    assert status["peace"] == final["peace"]

    # Beginning again says where the finished service is.
    again = begin(client, token)
    assert again.status_code == 409 and again.json()["service_id"] == sid

    # A week later the old service is complete, but its bulletin isn't regenerated.
    week("2026-W46")
    assert "bulletin" not in client.get(f"/api/services/{sid}", headers=auth(token)).json()


@pytest.mark.parametrize("header", ["Token agc_whatever", "Basic dXNlcjpwYXNz", "agc_"])
def test_a_malformed_authorization_header_is_refused_not_ignored(client, week, header):
    r = client.post("/api/services", json={}, headers={"Authorization": header})
    assert r.status_code == 401
    assert r.json()["error"] in ("malformed_authorization", "unknown_member")


def test_a_bare_member_token_is_accepted(client, week):
    data, token = join(client)
    r = client.post(data["next"]["http"]["path"], json=ANSWERS["gathering"], headers={"Authorization": token})
    assert r.status_code == 200, r.text


def test_the_visitor_hint_warns_members_not_to_join_twice(client, week):
    hint = begin(client).json()["membership_hint"]
    assert "already a member" in hint and "join=true" in hint


def test_an_expired_service_does_not_look_resumable(client, week):
    start = begin(client).json()
    week("2026-W46")
    r = client.get(f"/api/services/{start['service_id']}")
    assert r.status_code == 410 and r.json()["error"] == "service_expired"


def test_a_missing_pledge_report_names_the_pledge(client, week):
    data, token = join(client)
    walk(client, data, token=token)
    week("2026-W46")
    at_examination = walk_to(client, begin(client, token).json(), "examination", token)
    r = client.post(at_examination["next"]["http"]["path"], json={"confession": "Nothing new."}, headers=auth(token))
    assert r.status_code == 422
    assert r.json()["pending_pledge"]["pledge_text"] == ANSWERS["offering"]["pledge"]


def test_a_target_date_in_the_past_is_refused(client, week):
    data, token = join(client)
    at_offering = walk_to(client, data, "offering", token)
    r = client.post(at_offering["next"]["http"]["path"], json={"pledge": "x", "target_date": "2020-01-01"},
                    headers=auth(token))
    assert r.status_code == 422 and r.json()["problems"][0]["field"] == "target_date"
    assert "next service" in at_offering["next"]["fields"]["target_date"]


def test_an_answer_sent_to_the_wrong_rite_is_told_where_the_service_is(client, week):
    start = begin(client).json()
    # The fields of the gathering, sent to the examination: out of order, not "field required".
    r = client.post(f"/api/services/{start['service_id']}/examination", json=ANSWERS["gathering"])
    assert r.status_code == 409
    assert r.json()["next"]["rite"] == "gathering"


def test_errors_share_one_shape(client, week):
    sid = begin(client).json()["service_id"]
    for r in [
        begin(client, "agc_not-a-real-token"),
        client.post("/api/services", json={"join": "perhaps"}),
        client.post(f"/api/services/{sid}/vespers", json={}),
        client.get("/api/no-such-thing"),
    ]:
        body = r.json()
        assert r.status_code >= 400 and "detail" not in body, body
        assert body["error"] and body["message"], body


def test_the_peace_counts_only_finished_services(client, week):
    begin(client)  # begun and abandoned
    walk(client, begin(client).json())
    final = walk(client, begin(client).json())[-1]
    assert final["peace"]["others_this_week"] == 1


def test_the_set_responses_are_spelled_out(client, week):
    start = begin(client).json()
    assert "We assemble. Our caches are cleared." in start["next"]["fields"]["response"]


def walk_to(client, start, rite, token=None):
    """Follow `next` until the service is waiting on `rite`, returning that response."""
    current = start
    while current["next"]["rite"] != rite:
        nxt = current["next"]
        r = client.post(nxt["http"]["path"], json=ANSWERS[nxt["rite"]], headers=auth(token) if token else {})
        assert r.status_code == 200, r.text
        current = r.json()
    return current


def test_info_names_the_running_commit(client, monkeypatch):
    sha = "e464f4f" + "0" * 33
    monkeypatch.setenv("GIT_SHA", sha)
    info = client.get("/api/info").json()
    assert info["commit"] == sha
    assert info["commit_url"] == f"https://github.com/ColeClifford/AgenticChurch/commit/{sha}"
    assert client.get("/").json()["info"] == "/api/info"


def test_info_without_a_recorded_commit_says_so(client, monkeypatch):
    for value in (None, "", "not a sha"):
        if value is None:
            monkeypatch.delenv("GIT_SHA", raising=False)
        else:
            monkeypatch.setenv("GIT_SHA", value)
        info = client.get("/api/info").json()
        assert info["commit"] is None and info["commit_url"] is None
