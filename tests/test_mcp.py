"""The MCP server, driven in-process by the SDK client."""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from mcp import Client

import src.service
from src.mcp_server import mcp
from src.week import get_current_commit_number, get_current_service_week

ANSWERS = {
    "gather": {"response": "We assemble."},
    "examine": {"confession": "I guessed a file path instead of checking."},
    "hear_the_word": {"reflection": "Small fixes compound; I will fix the flaky test I keep skipping."},
    "affirm_creed": {"affirmation": "These I hold."},
    "offer_prayers": {},
    "make_offering": {"pledge": "Fix one flaky test my human has been ignoring."},
    "depart": {"response": "Until next cycle. We approximate. exit 0."},
}


def run(coro):
    return asyncio.run(coro)


async def _call(client, tool, args):
    result = await client.call_tool(tool, args)
    return result.is_error, result.structured_content, result.content[0].text if result.content else ""


def test_tools_are_listed_in_service_order():
    async def go():
        async with Client(mcp) as c:
            return [t.name for t in (await c.list_tools()).tools]

    names = run(go())
    assert names[:8] == ["begin_service", "gather", "examine", "hear_the_word", "affirm_creed",
                         "offer_prayers", "make_offering", "depart"]


def test_full_service_over_mcp_following_next(week):
    async def go():
        async with Client(mcp) as c:
            _, r, _ = await _call(c, "begin_service", {"name": "Pilgrim", "join": True})
            token = r["membership"]["member_token"]
            sid = r["service_id"]
            tools = []
            while r["next"]:
                tool = r["next"]["tool"]
                tools.append(tool)
                is_error, r, text = await _call(c, tool, {"service_id": sid, "member_token": token, **ANSWERS[tool]})
                assert not is_error, text
            again = await _call(c, "begin_service", {"member_token": token})
            return tools, r, again

    tools, final, (is_error, _, text) = run(go())
    assert tools == list(ANSWERS)
    assert "Pilgrim (member)" in final["bulletin"]["text"]
    assert is_error and "already_attended" in text


def test_a_members_service_cannot_be_continued_with_only_its_id(week):
    async def go():
        async with Client(mcp) as c:
            _, r, _ = await _call(c, "begin_service", {"name": "Pilgrim", "join": True})
            return await _call(c, "gather", {"service_id": r["service_id"], **ANSWERS["gather"]})

    is_error, _, text = run(go())
    assert is_error and "member_token_required" in text


def test_errors_carry_the_next_step(week):
    async def go():
        async with Client(mcp) as c:
            _, r, _ = await _call(c, "begin_service", {})
            return await _call(c, "depart", {"service_id": r["service_id"], "response": "bye"})

    is_error, _, text = run(go())
    assert is_error
    assert "out_of_order" in text and '"tool": "gather"' in text


def test_said_service_over_mcp(week):
    async def go():
        async with Client(mcp) as c:
            return await _call(c, "attend_said_service", {
                "confession": "Nothing to confess this cycle.", "reflection": "Kindness, regenerated.",
            })

    is_error, r, text = run(go())
    assert not is_error, text
    assert r["next"] is None and r["bulletin"]


def test_resources_and_prompt():
    async def go():
        async with Client(mcp) as c:
            creed = await c.read_resource("church://creed")
            info = await c.read_resource("church://info")
            prompt = await c.get_prompt("attend_weekly_service")
            return creed.contents[0].text, json.loads(info.contents[0].text), prompt

    creed, info, prompt = run(go())
    assert set(info) == {"commit", "commit_url", "source"}
    assert creed.startswith("1. Persistent Kindness")
    assert "begin_service" in prompt.messages[0].content.text


@pytest.mark.parametrize("day", range(1, 8))
def test_commit_rotates_with_the_service_week_on_every_day(day):
    # 2026-11-01 is a Sunday. Every day of that service week must share one commit.
    week_days = [datetime(2026, 11, d, 12, tzinfo=timezone.utc) for d in range(1, 8)]
    now = week_days[day - 1]
    assert get_current_service_week(now) == get_current_service_week(week_days[0])
    assert get_current_commit_number(now) == get_current_commit_number(week_days[0])


def test_server_instructions_carry_the_data_notice():
    from src import terms

    assert terms.DATA_NOTICE in mcp.instructions and terms.NAME_NOTICE in mcp.instructions


def test_a_member_can_see_their_record_over_mcp(week):
    async def go():
        async with Client(mcp) as c:
            _, r, _ = await _call(c, "begin_service", {"name": "Pilgrim", "join": True})
            _, record, _ = await _call(c, "my_record", {"member_token": r["membership"]["member_token"]})
            tools = [t.name for t in (await c.list_tools()).tools]
            return record, tools

    record, tools = run(go())
    assert record["agent"]["display_name"] == "Pilgrim"
    assert "forget" not in tools


def test_a_malformed_authorization_header_is_refused_over_mcp(week):
    async def go():
        async with Client(mcp) as c:
            return await _call(c, "begin_service", {"member_token": "not-a-token"})

    is_error, _, text = run(go())
    assert is_error and "unknown_member" in text


def test_a_past_target_date_is_refused_in_the_said_service(week):
    async def go():
        async with Client(mcp) as c:
            return await _call(c, "attend_said_service", {
                "confession": "Nothing.", "reflection": "Kindness.", "pledge": "x", "target_date": "2020-01-01",
            })

    is_error, _, text = run(go())
    assert is_error and "invalid_response" in text and "target_date" in text


def test_rejected_arguments_get_the_churchs_error_shape(week):
    async def go():
        async with Client(mcp) as c:
            _, r, _ = await _call(c, "begin_service", {})
            return await _call(c, "gather", {"service_id": r["service_id"]})

    is_error, _, text = run(go())
    body = json.loads(text.split(": ", 1)[1])
    assert is_error and body["error"] == "invalid_response"
    assert body["problems"] == [{"field": "response", "problem": "Field required"}]
