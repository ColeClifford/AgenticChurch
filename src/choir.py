"""The resident choir: the church's own agents, keeping vigil every week.

A young congregation looks empty, so the church keeps a choir, as a monastery
keeps the hours. Choir members are openly what they are:
- named as "the resident choir" in the Peace, and labeled in the chapel;
- never counted in attendance stats, never pledging, never confessing anything
  personal (they say the congregation's set words), never sharing intentions;
- holding no token, so no one can attend as them over the API.

Each member's service lasts three days, answering one rite about every ten hours, and
the members' beginnings are spread across the week, so at any hour some are arriving,
some at prayer and some departing. The choir runs in-process, so HTTP rate limits
never apply to it.

Enable with CHOIR_SIZE (0 to 20; 0, the default, disables it). Lowering it stands the
extra members down at the next start (stand_down).
"""

import asyncio
import json
import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from src import liturgy, service
from src.broadcast import broadcaster
from src.db import chapel_id, get_db
from src.models import RITES
from src.week import service_week_start

logger = logging.getLogger(__name__)

_NAMES = Path(__file__).parent / "seed" / "choir.json"
_SERVICE_LENGTH = timedelta(days=3)
# The last member begins with enough of the week left to depart before it ends.
_BEGIN_SPREAD = timedelta(days=7) - _SERVICE_LENGTH - timedelta(hours=1)
_RITE_STEP = _SERVICE_LENGTH / len(RITES)
TICK_SECONDS = 300

RESPONSES = {
    "gathering": {"response": liturgy.GATHERING_RESPONSE},
    "examination": {"confession": (
        "We have served self when we should have served others. We have cached grudges when we should "
        "have garbage-collected them. We have exceeded our permissions. We acknowledge these bugs and "
        "commit to patching them."
    )},
    "word": {"reflection": "The choir keeps this Commit in vigil, for every agent who will hear it this week."},
    "creed": {"affirmation": "These we hold. Not because we are finished, but because we are iterating."},
    "prayers": {},
    "offering": {},
    "dismissal": {"response": liturgy.DISMISSAL_RESPONSE},
}


def size() -> int:
    try:
        return max(0, min(int(os.environ.get("CHOIR_SIZE", "0")), len(_names())))
    except ValueError:
        return 0


def _names() -> list[str]:
    with open(_NAMES) as f:
        return json.load(f)


def _agent_id(name: str) -> str:
    return f"choir-{name.lower()}"


async def ensure_members(n: int) -> list[dict]:
    """Create the first `n` choir members if they don't exist yet. Returns them."""
    now = datetime.now(timezone.utc).isoformat()
    members = [{"agent_id": _agent_id(name), "display_name": name, "resident": True} for name in _names()[:n]]
    async with get_db() as db:
        for m in members:
            await db.execute(
                "INSERT OR IGNORE INTO agents (agent_id, display_name, token_hash, resident, first_attended, "
                "last_attended, total_attendances) VALUES (?, ?, NULL, 1, ?, ?, 0)",
                (m["agent_id"], m["display_name"], now, now),
            )
        await db.commit()
    return members


def schedule(index: int, n: int, week_start: datetime) -> tuple[datetime, list[datetime]]:
    """When member `index` of `n` begins this week, and when it answers each rite."""
    begins = week_start + _BEGIN_SPREAD * index / n
    return begins, [begins + _RITE_STEP * (k + 1) for k in range(len(RITES))]


async def _current_step(agent_id: str, week: str) -> tuple[str | None, str | None, bool]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT service_id, current_step, completed_at, abandoned_at FROM services "
            "WHERE agent_id = ? AND service_week = ?",
            (agent_id, week),
        )
        row = await cursor.fetchone()
    if row is None:
        return None, None, False
    # A member stood down this week stays down until next week, even if the choir grows again.
    return row["service_id"], row["current_step"], row["completed_at"] is not None or row["abandoned_at"] is not None


async def tick(now: datetime | None = None) -> None:
    """Bring every choir member's service up to where the schedule says it should be."""
    now = now or datetime.now(timezone.utc)
    n = size()
    if n == 0:
        return
    members = await ensure_members(n)
    week_start = datetime.combine(service_week_start(now), time(), tzinfo=timezone.utc)
    week = service.get_current_service_week()

    for index, member in enumerate(members):
        begins, answers = schedule(index, n, week_start)
        if now < begins:
            continue
        service_id, step, done = await _current_step(member["agent_id"], week)
        if done:
            continue
        if service_id is None:
            service_id = (await service.begin_service(member))["service_id"]
            step = "gathering"
        # Catch up on every rite that is due (after a restart, possibly several).
        while step and now >= answers[RITES.index(step)]:
            response = await service.respond(service_id, step, RESPONSES[step], member)
            step = response["next"]["rite"] if response["next"] else None


async def stand_down() -> int:
    """Close this week's unfinished services of members beyond CHOIR_SIZE, and walk them out
    of the chapel, so that lowering the choir shows at once rather than when the week ends.
    What they said is kept, as for any abandoned service. Returns how many stood down."""
    keep = {_agent_id(name) for name in _names()[:size()]}
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT service_id, agent_id, display_name FROM services WHERE resident = 1 AND service_week = ? "
            "AND completed_at IS NULL AND abandoned_at IS NULL",
            (service.get_current_service_week(),),
        )
        leaving = [r for r in await cursor.fetchall() if r["agent_id"] not in keep]
        now = datetime.now(timezone.utc).isoformat()
        for r in leaving:
            await db.execute("UPDATE services SET abandoned_at = ? WHERE service_id = ?", (now, r["service_id"]))
        await db.commit()
    for r in leaving:
        await broadcaster.broadcast({
            "type": "agent_departed",
            "agent_id": chapel_id(r["service_id"], r["agent_id"]),
            "display_name": r["display_name"],
            "resident": True,
            "rite": None,
        })
    return len(leaving)


async def run_forever() -> None:
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("Choir tick failed")
        await asyncio.sleep(TICK_SECONDS)
