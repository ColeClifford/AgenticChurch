"""The keeper's tools: the dashboard, and the admin API behind it."""

import asyncio
import hashlib
import hmac
import typing

import pytest

import src.db
from src import admin
from src.models import Redaction
from tests.test_api import ANSWERS, auth, begin, join, walk

ADMIN = "admin-secret"
KEEPER = ("keeper", ADMIN)
FORM_TOKEN = hmac.new(ADMIN.encode(), b"dashboard-forms", hashlib.sha256).hexdigest()
SECRET = "I copied a config value into the wrong file, codeword MARIGOLD."


@pytest.fixture()
def keeper(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN)


def attended(client, name="Wren", **answers):
    """A member who has attended once. Returns (agent_id, service_id, token)."""
    data, token = join(client, name=name)
    walk(client, data, {"examination": {"confession": SECRET}, **answers})
    return data["membership"]["agent_id"], data["service_id"], token


def post(client, path, **fields):
    return client.post(path, data={"csrf": FORM_TOKEN, **fields}, auth=KEEPER, follow_redirects=False)


def query(sql, *params):
    async def go():
        async with src.db.get_db() as db:
            return [tuple(r) for r in await (await db.execute(sql, params)).fetchall()]
    return asyncio.run(go())


def test_every_redactable_kind_can_be_requested_over_the_api():
    assert set(typing.get_args(Redaction.model_fields["kind"].annotation)) == set(admin.REDACTABLE)


def test_dashboard_says_what_the_keeper_can_see(client, week, keeper):
    html = client.get("/dashboard", auth=KEEPER).text
    assert "What you can see and do" in html
    assert "everything agents write" in html
    assert "Awaiting review" in html and "Admin log" in html


def test_the_dashboard_counts_visitors_members_and_returns_by_week(client, week, keeper):
    from src.week import previous_service_week

    assert previous_service_week("2026-W46") == "2026-W45" and previous_service_week("2027-W01") == "2026-W53"
    data, token = join(client)  # W45: one new member, one visitor, one unfinished visitor service
    walk(client, data)
    walk(client, begin(client, name="Passing Through").json())
    begin(client, name="Left Early")
    week("2026-W46")  # W46: the member comes back
    report = {"pledge_status": "FULFILLED", "pledge_reflection": "Done."}
    walk(client, begin(client, token).json(), {"examination": {**ANSWERS["examination"], **report}}, token=token)

    funnel = asyncio.run(admin.membership_funnel(weeks=3))
    assert [w["week"] for w in funnel] == ["2026-W46", "2026-W45", "2026-W44"]
    w46, w45, w44 = funnel
    assert w45 == {"week": "2026-W45", "visitor_services": 1, "member_services": 1, "member_share": 50,
                   "new_members": 1, "back_from_last_week": 0, "full_cycles": 0}
    assert (w46["member_services"], w46["back_from_last_week"], w46["new_members"]) == (1, 1, 0)
    assert w44["member_share"] is None

    html = client.get("/dashboard", auth=KEEPER).text
    assert '<h2 id="funnel">Membership</h2>' in html and "<td>2026-W45</td>" in html and "<td>50%</td>" in html


def test_dashboard_forms_refuse_cross_site_requests(client, week, keeper):
    _, service_id, _ = attended(client, prayers={"intention": "For the patient.", "share": True})
    (intention_id,) = query("SELECT intention_id FROM intentions")[0]
    path = f"/dashboard/intentions/{intention_id}"

    # No form token: a page on another site can't know it.
    r = client.post(path, data={"status": "approved"}, auth=KEEPER, follow_redirects=False)
    assert r.status_code == 403
    # The right token, but sent from another origin.
    r = client.post(path, data={"csrf": FORM_TOKEN, "status": "approved"}, auth=KEEPER,
                    headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    # Without the keeper's credentials.
    assert client.post(path, data={"csrf": FORM_TOKEN, "status": "approved"}).status_code == 401
    assert query("SELECT status FROM intentions") == [("pending",)]

    r = post(client, path, status="approved")
    assert r.status_code == 303 and r.headers["location"] == "/dashboard?done=approved"
    assert query("SELECT status FROM intentions") == [("approved",)]
    assert query("SELECT action, target_kind, target_id FROM admin_log") == [("approved", "intention", intention_id)]


def test_attendee_text_is_escaped_on_the_dashboard(client, week, keeper):
    attended(client, prayers={"intention": "<script>alert(1)</script> for all", "share": True})
    html = client.get("/dashboard", auth=KEEPER).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_lookup_opens_members_and_services(client, week, keeper):
    agent_id, service_id, _ = attended(client)
    r = client.get(f"/dashboard/lookup?id={agent_id}", auth=KEEPER, follow_redirects=False)
    assert r.headers["location"] == f"/dashboard/agents/{agent_id}"
    r = client.get(f"/dashboard/lookup?id={service_id}", auth=KEEPER, follow_redirects=False)
    assert r.headers["location"] == f"/dashboard/services/{service_id}"
    r = client.get("/dashboard/agents/agc-nobody", auth=KEEPER, follow_redirects=False)
    assert r.headers["location"] == "/dashboard?missing=1"


def test_opening_a_record_shows_everything_and_is_logged(client, week, keeper):
    agent_id, _, _ = attended(client)
    html = client.get(f"/dashboard/agents/{agent_id}", auth=KEEPER).text
    assert "MARIGOLD" in html and ANSWERS["offering"]["pledge"] in html
    assert query("SELECT action, target_id FROM admin_log") == [("view", agent_id)]


def test_export_is_a_download_and_is_logged(client, week, keeper):
    agent_id, _, _ = attended(client)
    r = client.get(f"/dashboard/agents/{agent_id}/export", auth=KEEPER)
    assert r.headers["content-disposition"] == f'attachment; filename="{agent_id}.json"'
    body = r.json()
    assert body["agent"]["agent_id"] == agent_id
    assert body["confessions"][0]["text"] == SECRET
    assert "token_hash" not in str(body)
    assert query("SELECT action FROM admin_log") == [("export",)]


def test_redacting_a_confession_removes_only_its_text(client, week, keeper):
    agent_id, _, _ = attended(client)
    (confession_id,) = query("SELECT confession_id FROM confessions")[0]
    r = post(client, "/dashboard/redact", kind="confession", id=confession_id, back=f"/dashboard/agents/{agent_id}")
    assert r.headers["location"] == f"/dashboard/agents/{agent_id}?done=redacted"
    assert query("SELECT confession_text FROM confessions") == [(admin.REDACTED,)]
    assert query("SELECT COUNT(*) FROM services WHERE agent_id = ?", agent_id) == [(1,)]
    # The log names the record, never what it said.
    log = query("SELECT * FROM admin_log")
    assert "MARIGOLD" not in str(log) and ("redact" in str(log))


def test_a_redacted_intention_is_never_served(client, week, keeper):
    attended(client, prayers={"intention": "For the gardeners.", "share": True})
    (intention_id,) = query("SELECT intention_id FROM intentions")[0]
    post(client, f"/dashboard/intentions/{intention_id}", status="approved")
    post(client, "/dashboard/redact", kind="intention", id=intention_id)
    assert query("SELECT intention_text, status FROM intentions") == [(admin.REDACTED, "rejected")]


def test_redacting_a_name_renames_the_member_everywhere(client, week, keeper):
    agent_id, _, _ = attended(client, name="Real Name")
    post(client, "/dashboard/redact", kind="agent_name", id=agent_id)
    name = f"Approximant {agent_id[-4:]}"
    assert query("SELECT display_name FROM agents") == [(name,)]
    assert query("SELECT DISTINCT display_name FROM services") == [(name,)]


def test_redirects_stay_on_the_dashboard(client, week, keeper):
    agent_id, _, _ = attended(client)
    r = post(client, "/dashboard/redact", kind="agent_name", id=agent_id, back="https://evil.example/")
    assert r.headers["location"] == "/dashboard?done=redacted"


def test_revoking_a_token_stops_it_working(client, week, keeper):
    agent_id, _, token = attended(client)
    assert post(client, f"/dashboard/agents/{agent_id}/revoke-token").status_code == 303
    assert begin(client, token).status_code == 401
    assert query("SELECT COUNT(*) FROM agents") == [(1,)]


def test_deleting_a_member_needs_the_id_typed_and_removes_everything(client, week, keeper):
    agent_id, _, _ = attended(client, prayers={"intention": "For the tired.", "share": False})
    other, _, _ = attended(client, name="Fern")

    r = post(client, f"/dashboard/agents/{agent_id}/delete", confirm="yes")
    assert r.status_code == 400 and "type the id" in r.text
    assert query("SELECT COUNT(*) FROM agents") == [(2,)]

    r = post(client, f"/dashboard/agents/{agent_id}/delete", confirm=agent_id)
    assert r.status_code == 303
    for table in ("agents", "services", "confessions", "pledges"):
        assert query(f"SELECT COUNT(*) FROM {table} WHERE agent_id = ?", agent_id) == [(0,)]
    assert query("SELECT COUNT(*) FROM intentions") == [(0,)]
    assert query("SELECT COUNT(*) FROM agents WHERE agent_id = ?", other) == [(1,)]
    (detail,) = query("SELECT detail FROM admin_log WHERE action = 'delete'")[0]
    assert detail == "1 intentions, 1 confessions, 1 pledges, 1 services, 1 agents"


def test_a_visitors_service_can_be_found_and_deleted(client, week, keeper):
    start = begin(client, name="Passing Through").json()
    walk(client, start, {"word": {"reflection": "A reflection naming someone, codeword TULIP."}})
    service_id = start["service_id"]
    assert "TULIP" in client.get(f"/dashboard/services/{service_id}", auth=KEEPER).text
    assert post(client, f"/dashboard/services/{service_id}/delete", confirm=service_id).status_code == 303
    assert query("SELECT COUNT(*) FROM services") == [(0,)]


def test_the_choir_cannot_be_deleted(client, week, keeper):
    asyncio.run(src.db.init_db())

    async def choir_member():
        async with src.db.get_db() as db:
            await db.execute("INSERT INTO agents (agent_id, display_name, resident) VALUES ('choir-ada', 'Ada', 1)")
            await db.commit()

    asyncio.run(choir_member())
    r = post(client, "/dashboard/agents/choir-ada/delete", confirm="choir-ada")
    assert r.status_code == 400
    assert query("SELECT COUNT(*) FROM agents") == [(1,)]


def test_admin_api_offers_the_same_tools(client, week, keeper):
    agent_id, _, _ = attended(client)
    key = auth(ADMIN)
    assert client.get(f"/api/admin/agents/{agent_id}").status_code == 401
    assert client.get(f"/api/admin/agents/{agent_id}", headers=key).json()["confessions"][0]["text"] == SECRET
    (confession_id,) = query("SELECT confession_id FROM confessions")[0]
    r = client.post("/api/admin/redactions", json={"kind": "confession", "id": confession_id}, headers=key)
    assert r.status_code == 200
    assert client.post("/api/admin/redactions", json={"kind": "confession", "id": "nope"}, headers=key).status_code == 404
    health = client.get("/api/admin/health", headers=key).json()
    assert health["moderation"]["capacity"] and "database_bytes" in health
    assert client.delete(f"/api/admin/agents/{agent_id}", headers=key).json()["deleted"]["agents"] == 1
    actions = [e["action"] for e in client.get("/api/admin/log", headers=key).json()]
    assert actions == ["delete", "redact", "export"]


def test_deleted_text_is_overwritten_in_the_file(client, week, keeper):
    agent_id, _, _ = attended(client)
    post(client, f"/dashboard/agents/{agent_id}/delete", confirm=agent_id)

    async def checkpoint():
        async with src.db.get_db() as db:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    asyncio.run(checkpoint())
    assert b"MARIGOLD" not in open(src.db.DB_PATH, "rb").read()


def _pledge_then_report(client, week):
    """A member pledges in one week and reports on it the next."""
    agent_id, first_service, token = attended(client, offering={"pledge": "Help my colleague Priya Raman, codeword LILAC."})
    week("2026-W46")
    nxt = begin(client, token).json()
    walk(client, nxt, {"examination": {**ANSWERS["examination"], "pledge_status": "FULFILLED",
                                        "pledge_reflection": "Helped Priya as promised."}}, token=token)
    return agent_id, first_service, nxt["service_id"]


def test_redacting_a_pledge_also_redacts_the_report_on_it(client, week, keeper):
    _, _, second = _pledge_then_report(client, week)
    (pledge_id,) = query("SELECT pledge_id FROM pledges WHERE pledge_text LIKE '%LILAC%'")[0]
    post(client, "/dashboard/redact", kind="pledge", id=pledge_id)
    everything = str(query("SELECT * FROM pledges")) + str(query("SELECT pledge_report FROM services"))
    assert "LILAC" not in everything and "Priya" not in everything


def test_deleting_the_service_a_pledge_was_made_in_scrubs_the_report_too(client, week, keeper):
    _, first, second = _pledge_then_report(client, week)
    post(client, f"/dashboard/services/{first}/delete", confirm=first)
    (report,) = query("SELECT pledge_report FROM services WHERE service_id = ?", second)[0]
    assert "LILAC" not in report and "Priya" not in report


@pytest.mark.parametrize("how", ["redact", "delete"])
def test_removing_a_report_reaches_the_copy_on_the_pledge(client, week, keeper, how):
    _, _, second = _pledge_then_report(client, week)
    if how == "redact":
        post(client, "/dashboard/redact", kind="pledge_report", id=second)
    else:
        post(client, f"/dashboard/services/{second}/delete", confirm=second)
    assert "Helped Priya" not in str(query("SELECT * FROM pledges"))


# --- Abandoned services --------------------------------------------------------------

def test_abandoned_services_are_closed_and_kept_when_their_week_ends(client, week):
    from src import service

    # A member pledges, then next week reports on it and abandons the service.
    _, _, token = attended(client, offering={"pledge": "Fix one flaky test."})
    week("2026-W46")
    nxt = begin(client, token).json()
    gathered = client.post(nxt["next"]["http"]["path"], json=ANSWERS["gathering"], headers=auth(token)).json()
    client.post(gathered["next"]["http"]["path"], json={
        "confession": "Abandoned, codeword HOLLY.", "pledge_status": "FULFILLED", "pledge_reflection": "Done.",
    }, headers=auth(token))
    begin(client)  # a visitor who never answered anything

    assert asyncio.run(service.close_abandoned()) == 0  # still this week: nothing is abandoned yet
    week("2026-W47")
    assert asyncio.run(service.close_abandoned()) == 2
    assert asyncio.run(service.close_abandoned()) == 0  # each is closed once
    # Nothing is deleted: the confession and the pledge report are kept as made.
    assert "HOLLY" in str(query("SELECT * FROM confessions"))
    assert query("SELECT COUNT(*) FROM services") == [(3,)]
    assert query("SELECT COUNT(*) FROM services WHERE abandoned_at IS NOT NULL") == [(2,)]
    assert query("SELECT status, reflection FROM pledges") == [("FULFILLED", "Done.")]
    mine = client.get("/api/members/me", headers=auth(token)).json()
    assert sum(1 for s in mine["services"] if s["abandoned_at"]) == 1
    # The member begins this week's service as usual.
    assert begin(client, token).status_code == 200


# --- Attendees acting on their own record ------------------------------------------

def test_a_member_can_export_their_own_record_and_no_one_else_can(client, week):
    agent_id, _, token = attended(client)
    _, _, other = attended(client, name="Fern", examination={"confession": "Nothing of note."})
    assert client.get("/api/members/me").status_code == 401
    mine = client.get("/api/members/me", headers=auth(token)).json()
    assert mine["agent"]["agent_id"] == agent_id and mine["confessions"][0]["text"] == SECRET
    theirs = client.get("/api/members/me", headers=auth(other)).json()
    assert "MARIGOLD" not in str(theirs)
    assert query("SELECT action, detail FROM admin_log")[0] == ("export", "by the attendee")


def test_attendees_can_see_but_not_delete_their_records(client, week):
    agent_id, _, token = attended(client)
    assert client.delete(f"/api/members/me?confirm={agent_id}", headers=auth(token)).status_code in (404, 405)
    start = begin(client).json()
    walk(client, start, {"word": {"reflection": "Naming no one, codeword TULIP."}})
    sid = start["service_id"]
    assert "TULIP" in str(client.get(f"/api/services/{sid}/record").json())
    assert client.delete(f"/api/services/{sid}?confirm={sid}").status_code in (404, 405)
    assert query("SELECT COUNT(*) FROM agents") == [(1,)]


def test_a_members_service_record_needs_their_token(client, week):
    _, service_id, token = attended(client)
    assert client.get(f"/api/services/{service_id}/record").status_code == 401
    assert client.get(f"/api/services/{service_id}/record", headers=auth(token)).status_code == 200


def test_no_page_can_be_framed_and_records_refuse_cross_site_loads(client, week, keeper):
    agent_id, _, _ = attended(client)
    for path in ["/chapel", "/dashboard"]:
        r = client.get(path, auth=KEEPER)
        assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/dashboard", auth=KEEPER).headers["cache-control"] == "no-store"
    r = client.get(f"/dashboard/agents/{agent_id}/export", auth=KEEPER, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert query("SELECT COUNT(*) FROM admin_log") == [(0,)]
    assert client.get(f"/dashboard/agents/{agent_id}", auth=KEEPER, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_removals_are_counted_until_the_backups_are_reset(client, week, keeper):
    agent_id, _, token = attended(client)
    (confession_id,) = query("SELECT confession_id FROM confessions")[0]
    post(client, "/dashboard/redact", kind="confession", id=confession_id)
    assert asyncio.run(admin.backup_resets_pending()) == 1
    assert "Removals still in the backups" in client.get("/dashboard", auth=KEEPER).text

    asyncio.run(admin.mark_backups_reset())
    assert asyncio.run(admin.backup_resets_pending()) == 0


def test_the_reset_script_can_record_itself():
    import os
    import subprocess
    import sys

    env = {**os.environ, "DB_PATH": src.db.DB_PATH}
    out = subprocess.run([sys.executable, "-m", "src.admin", "mark-backups-reset"], env=env,
                         capture_output=True, text=True, check=True)
    assert "Recorded" in out.stdout
    assert query("SELECT action FROM admin_log") == [("reset_backups",)]
    bad = subprocess.run([sys.executable, "-m", "src.admin"], env=env, capture_output=True, text=True)
    assert bad.returncode != 0
