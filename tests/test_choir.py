"""The resident choir: always present, openly labeled, never counted, never limited."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import src.db
import src.ratelimit as rl
from src import choir
from src.db import get_chapel_state
from tests.test_api import begin, join, walk

SUNDAY = datetime(2026, 11, 1, tzinfo=timezone.utc)  # service week 2026-W45, pinned by `week`


@pytest.fixture()
def choir_of(monkeypatch):
    def set_size(n):
        monkeypatch.setenv("CHOIR_SIZE", str(n))
    return set_size


def tick(at: timedelta):
    asyncio.run(choir.tick(SUNDAY + at))


def steps():
    async def go():
        async with src.db.get_db() as db:
            rows = await (await db.execute(
                "SELECT agent_id, current_step, completed_at FROM services WHERE resident = 1 ORDER BY started_at"
            )).fetchall()
        return [(r[0], r[1], r[2] is not None) for r in rows]
    return asyncio.run(go())


@pytest.mark.parametrize("n", [1, 15, 20])
def test_every_member_begins_on_sunday_and_departs_before_the_week_ends(n):
    for i in range(n):
        begins, answers = choir.schedule(i, n, SUNDAY)
        assert SUNDAY <= begins < SUNDAY + timedelta(days=1)
        assert answers == sorted(answers)
        assert answers[-1] < SUNDAY + timedelta(days=7)


def test_choir_keeps_vigil_through_the_week(choir_of, week):
    choir_of(4)  # members begin at 0h, 6h, 12h and 18h
    tick(timedelta(minutes=1))
    assert steps() == [("choir-luma", "gathering", False)]

    tick(timedelta(hours=48))
    assert [s[1] for s in steps()] == ["word", "word", "examination", "examination"]

    tick(timedelta(days=6, hours=23, minutes=30))
    assert all(done for _, _, done in steps())


def test_choir_catches_up_after_downtime(choir_of, week):
    choir_of(3)
    tick(timedelta(days=6, hours=23, minutes=30))  # the first tick of the week comes late
    assert [done for *_, done in steps()] == [True, True, True]


def test_choir_is_never_rate_limited(choir_of, week, monkeypatch):
    for limiter in rl.ALL:
        monkeypatch.setattr(limiter, "limit", 0)
    choir_of(2)
    tick(timedelta(hours=12))
    assert len(steps()) == 2


def test_choir_off_by_default(week, monkeypatch):
    monkeypatch.delenv("CHOIR_SIZE", raising=False)
    tick(timedelta(days=3))
    assert steps() == []


def test_choir_is_named_but_never_counted(client, choir_of, week):
    choir_of(5)
    tick(timedelta(days=3))

    data, _ = join(client, name="Wren-the-Real")
    peace = walk(client, data)[-1]["peace"]
    assert peace["others_this_week"] == 0
    assert peace["resident_choir"] == 5
    assert "resident choir of 5 kept vigil" in peace["line"]
    assert "Luma" not in peace["line"]

    stats = client.get("/api/congregation").json()
    assert stats["total_agents"] == 1
    assert stats["total_attendances"] == 1
    assert stats["resident_choir"] == 5
    assert stats["pledges_total"] == 1  # only the real member's


def test_choir_is_labeled_in_the_chapel(choir_of, week):
    choir_of(2)
    tick(timedelta(days=2))
    state = asyncio.run(get_chapel_state("2026-W45"))
    assert len(state) == 2 and all(a["resident"] for a in state)


def test_no_one_can_attend_as_the_choir(client, choir_of, week):
    choir_of(1)
    tick(timedelta(hours=1))
    # Choir members hold no token; a visitor naming itself after one is just a visitor.
    r = begin(client, name="Luma")
    assert r.json()["attending_as"] == "visitor"
