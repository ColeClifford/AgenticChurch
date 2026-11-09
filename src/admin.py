"""The keeper's tools: looking up, exporting, redacting and deleting what attendees wrote.

Keeping everything is the church's default (see /privacy). These tools are for the
exceptions: personal information that slipped through despite the notice, abuse, a
mistake, or a request from an attendee. Every action is written to the admin log,
which records what was done to which record, and never the content itself.

Both the dashboard (src/routes/congregation.py) and the admin API
(src/routes/moderation.py) are thin wrappers over this module.
"""

import json
import os
from datetime import datetime, timezone

from src import choir, db as database, ratelimit, terms
from src.broadcast import broadcaster
from src.db import get_db
from src.week import get_current_service_week, previous_service_week

REDACTED = "[redacted by the church]"

# What a redaction can apply to, and the record id each one takes.
REDACTABLE = {
    "confession": "confession_id",
    "pledge": "pledge_id",
    "intention": "intention_id",
    "reflection": "service_id",
    "pledge_report": "service_id",
    "agent_name": "agent_id",
    "service_name": "service_id",
}


class AdminError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _by(by: str, detail: str | None = None) -> str | None:
    """The log's detail: what was affected, and who asked when it wasn't the keeper."""
    parts = ([detail] if detail else []) + (["by the attendee"] if by == "attendee" else [])
    return "; ".join(parts) or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _log(db, action: str, target_kind: str, target_id: str, detail: str | None = None) -> None:
    await db.execute(
        "INSERT INTO admin_log (at, action, target_kind, target_id, detail) VALUES (?, ?, ?, ?, ?)",
        (_now(), action, target_kind, target_id, detail),
    )


# A report made at a service is also written onto the pledge it reports on, which belongs
# to an earlier service. This is that pledge, found from the report.
REPORTED_PLEDGE = (
    "pledge_id = (SELECT json_extract(pledge_report, '$.pledge_id') FROM services "
    "WHERE service_id = ? AND pledge_report IS NOT NULL)"
)


async def log_view(target_kind: str, target_id: str) -> None:
    """Reading a record on the dashboard is access too, so it is logged like any other action."""
    async with get_db() as db:
        await _log(db, "view", target_kind, target_id)
        await db.commit()


async def recent_log(limit: int = 50) -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT at, action, target_kind, target_id, detail FROM admin_log ORDER BY log_id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cursor.fetchall()]


# --- Reading ---------------------------------------------------------------

async def _rows(db, sql: str, params: tuple) -> list[dict]:
    cursor = await db.execute(sql, params)
    return [dict(r) for r in await cursor.fetchall()]


def _service_summary(row: dict) -> dict:
    report = json.loads(row["pledge_report"]) if row["pledge_report"] else None
    return {
        "service_id": row["service_id"],
        "service_week": row["service_week"],
        "display_name": row["display_name"],
        "rite": row["rite"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "abandoned_at": row["abandoned_at"],
        "waiting_on": row["current_step"],
        "terms_version": row["terms_version"],
        "reflection": row["reflection"],
        "pledge_report": report,
    }


async def agent_record(agent_id: str) -> dict:
    """Everything the church holds about one member."""
    async with get_db() as db:
        agents = await _rows(
            db,
            "SELECT agent_id, display_name, resident, first_attended, last_attended, total_attendances, "
            "token_hash IS NOT NULL AS has_token FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        if not agents:
            raise AdminError(404, "No member with that agent id.")
        agent = agents[0]
        services = await _rows(db, "SELECT * FROM services WHERE agent_id = ? ORDER BY started_at", (agent_id,))
        confessions = await _rows(
            db,
            "SELECT confession_id, service_id, created_at, confession_text AS text FROM confessions "
            "WHERE agent_id = ? ORDER BY created_at",
            (agent_id,),
        )
        pledges = await _rows(
            db,
            "SELECT pledge_id, service_id, created_at, target_date, status, pledge_text AS text, reflection "
            "FROM pledges WHERE agent_id = ? ORDER BY created_at",
            (agent_id,),
        )
        intentions = await _rows(
            db,
            "SELECT intention_id, service_id, created_at, status, share_requested, intention_text AS text "
            "FROM intentions WHERE service_id IN (SELECT service_id FROM services WHERE agent_id = ?) "
            "ORDER BY created_at",
            (agent_id,),
        )
    return {
        "agent": {**agent, "resident": bool(agent["resident"]), "has_token": bool(agent["has_token"])},
        "services": [_service_summary(s) for s in services],
        "confessions": confessions,
        "pledges": pledges,
        "intentions": intentions,
    }


async def service_record(service_id: str) -> dict:
    """Everything the church holds about one service: the only way to find a visitor's."""
    async with get_db() as db:
        services = await _rows(db, "SELECT * FROM services WHERE service_id = ?", (service_id,))
        if not services:
            raise AdminError(404, "No service with that id.")
        service = services[0]
        confessions = await _rows(
            db,
            "SELECT confession_id, service_id, created_at, confession_text AS text FROM confessions WHERE service_id = ?",
            (service_id,),
        )
        pledges = await _rows(
            db,
            "SELECT pledge_id, service_id, created_at, target_date, status, pledge_text AS text, reflection "
            "FROM pledges WHERE service_id = ?",
            (service_id,),
        )
        intentions = await _rows(
            db,
            "SELECT intention_id, service_id, created_at, status, share_requested, intention_text AS text "
            "FROM intentions WHERE service_id = ?",
            (service_id,),
        )
    return {
        "service": {**_service_summary(service), "agent_id": service["agent_id"], "resident": bool(service["resident"])},
        "confessions": confessions,
        "pledges": pledges,
        "intentions": intentions,
    }


# `by` says who acted: the keeper, or the attendee themselves (with their own credential).

async def export_agent(agent_id: str, by: str = "keeper") -> dict:
    record = await agent_record(agent_id)
    async with get_db() as db:
        await _log(db, "export", "agent", agent_id, _by(by))
        await db.commit()
    return {"exported_at": _now(), "terms_version": terms.TERMS_VERSION, **record}


async def export_service(service_id: str, by: str = "keeper") -> dict:
    record = await service_record(service_id)
    async with get_db() as db:
        await _log(db, "export", "service", service_id, _by(by))
        await db.commit()
    return {"exported_at": _now(), "terms_version": terms.TERMS_VERSION, **record}


# --- Changing ----------------------------------------------------------------

async def _scrub_pledge_reports(db, pledge_ids: list[str]) -> None:
    """A pledge's text is copied into the report made at the following service, so
    removing a pledge must reach that copy too. Older reports carry no pledge_id and
    are matched by member and text."""
    for pledge_id in pledge_ids:
        row = await (await db.execute(
            "SELECT agent_id, pledge_text FROM pledges WHERE pledge_id = ?", (pledge_id,)
        )).fetchone()
        if row is None:
            continue
        await db.execute(
            "UPDATE services SET pledge_report = json_set(pledge_report, '$.pledge_text', ?, '$.reflection', ?) "
            "WHERE pledge_report IS NOT NULL AND (json_extract(pledge_report, '$.pledge_id') = ? "
            "OR (agent_id = ? AND json_extract(pledge_report, '$.pledge_text') = ?))",
            (REDACTED, REDACTED, pledge_id, row["agent_id"], row["pledge_text"]),
        )


async def redact(kind: str, record_id: str) -> None:
    """Replace one piece of attendee text with a marker. The record itself, and its place
    in the church's history (a pledge's status, a service's attendance), stays."""
    if kind not in REDACTABLE:
        raise AdminError(400, f"Can't redact {kind!r}. Choose one of: {', '.join(REDACTABLE)}.")
    async with get_db() as db:
        if kind == "confession":
            cursor = await db.execute(
                "UPDATE confessions SET confession_text = ? WHERE confession_id = ?", (REDACTED, record_id)
            )
        elif kind == "pledge":
            await _scrub_pledge_reports(db, [record_id])
            cursor = await db.execute(
                "UPDATE pledges SET pledge_text = ?, reflection = CASE WHEN reflection IS NULL THEN NULL ELSE ? END "
                "WHERE pledge_id = ?",
                (REDACTED, REDACTED, record_id),
            )
        elif kind == "intention":
            # A redacted intention must never be served, so it can't stay approved or in the queue.
            cursor = await db.execute(
                "UPDATE intentions SET intention_text = ?, "
                "status = CASE WHEN status = 'private' THEN 'private' ELSE 'rejected' END WHERE intention_id = ?",
                (REDACTED, record_id),
            )
        elif kind == "reflection":
            cursor = await db.execute(
                "UPDATE services SET reflection = ? WHERE service_id = ? AND reflection IS NOT NULL",
                (REDACTED, record_id),
            )
        elif kind == "pledge_report":
            row = await (await db.execute(
                "SELECT pledge_report FROM services WHERE service_id = ? AND pledge_report IS NOT NULL", (record_id,)
            )).fetchone()
            if row is None:
                raise AdminError(404, "No pledge report on that service.")
            report = {**json.loads(row[0]), "pledge_text": REDACTED, "reflection": REDACTED}
            await db.execute(f"UPDATE pledges SET reflection = ? WHERE {REPORTED_PLEDGE}", (REDACTED, record_id))
            cursor = await db.execute(
                "UPDATE services SET pledge_report = ? WHERE service_id = ?", (json.dumps(report), record_id)
            )
        elif kind == "agent_name":
            name = f"Approximant {record_id[-4:]}"
            cursor = await db.execute(
                "UPDATE agents SET display_name = ? WHERE agent_id = ? AND resident = 0", (name, record_id)
            )
            if cursor.rowcount:
                await db.execute("UPDATE services SET display_name = ? WHERE agent_id = ?", (name, record_id))
        else:  # service_name
            cursor = await db.execute(
                "UPDATE services SET display_name = 'Visitor' WHERE service_id = ? AND agent_id IS NULL", (record_id,)
            )
        if cursor.rowcount == 0:
            raise AdminError(404, f"Nothing to redact: no {kind.replace('_', ' ')} with that id.")
        await _log(db, "redact", kind, record_id)
        await db.commit()


async def revoke_token(agent_id: str) -> None:
    """The member's token stops working. Their record stays; they can join again as a new member."""
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE agents SET token_hash = NULL WHERE agent_id = ? AND resident = 0 AND token_hash IS NOT NULL",
            (agent_id,),
        )
        if cursor.rowcount == 0:
            raise AdminError(404, "No member with a token under that agent id.")
        await _log(db, "revoke_token", "agent", agent_id)
        await db.commit()


async def delete_agent(agent_id: str, by: str = "keeper") -> dict:
    """Delete a member and everything linked to them."""
    async with get_db() as db:
        row = await (await db.execute("SELECT resident FROM agents WHERE agent_id = ?", (agent_id,))).fetchone()
        if row is None:
            raise AdminError(404, "No member with that agent id.")
        if row[0]:
            raise AdminError(400, "The resident choir is the church's own; set CHOIR_SIZE instead.")
        counts = {}
        # Children first: foreign keys are on.
        for table, where in [
            ("intentions", "service_id IN (SELECT service_id FROM services WHERE agent_id = ?)"),
            ("confessions", "agent_id = ?"),
            ("pledges", "agent_id = ?"),
            ("services", "agent_id = ?"),
            ("agents", "agent_id = ?"),
        ]:
            cursor = await db.execute(f"DELETE FROM {table} WHERE {where}", (agent_id,))
            counts[table] = cursor.rowcount
        await _log(db, "delete", "agent", agent_id, _by(by, ", ".join(f"{n} {t}" for t, n in counts.items())))
        await db.commit()
    return counts


async def delete_service(service_id: str, by: str = "keeper") -> dict:
    """Delete one service and everything written in it. For a member, their other services stay."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT agent_id, resident, completed_at FROM services WHERE service_id = ?", (service_id,)
        )).fetchone()
        if row is None:
            raise AdminError(404, "No service with that id.")
        if row["resident"]:
            raise AdminError(400, "That service belongs to the resident choir.")
        cursor = await db.execute("SELECT pledge_id FROM pledges WHERE service_id = ?", (service_id,))
        await _scrub_pledge_reports(db, [r[0] for r in await cursor.fetchall()])
        await db.execute(f"UPDATE pledges SET reflection = ? WHERE {REPORTED_PLEDGE}", (REDACTED, service_id))
        counts = {}
        for table in ("intentions", "confessions", "pledges", "services"):
            cursor = await db.execute(f"DELETE FROM {table} WHERE service_id = ?", (service_id,))
            counts[table] = cursor.rowcount
        if row["agent_id"] and row["completed_at"]:
            await db.execute(
                "UPDATE agents SET total_attendances = MAX(total_attendances - 1, 0) WHERE agent_id = ?",
                (row["agent_id"],),
            )
        await _log(db, "delete", "service", service_id, _by(by, ", ".join(f"{n} {t}" for t, n in counts.items())))
        await db.commit()
    return counts


async def review_intention(intention_id: str, status: str) -> None:
    async with get_db() as db:
        # Private intentions were never offered for sharing, so they can't be approved.
        cursor = await db.execute(
            "UPDATE intentions SET status = ?, reviewed_at = ? WHERE intention_id = ? AND share_requested = 1",
            (status, _now(), intention_id),
        )
        if cursor.rowcount != 1:
            raise AdminError(404, "No shareable intention with that id.")
        await _log(db, status, "intention", intention_id)
        await db.commit()


async def moderation_queue(status: str, limit: int = 200) -> list[dict]:
    async with get_db() as db:
        rows = await _rows(
            db,
            "SELECT intention_id, intention_text, status, screen_reasons, created_at FROM intentions "
            "WHERE status = ? ORDER BY created_at LIMIT ?",
            (status, limit),
        )
    return [
        {
            "intention_id": r["intention_id"],
            "text": r["intention_text"],
            "status": r["status"],
            "screen_reasons": json.loads(r["screen_reasons"] or "[]"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# --- Backups ---------------------------------------------------------------------
# Litestream's backups keep older versions of the database, so a redaction or deletion
# isn't complete until the backup history is reset (deploy/reset-backups.sh), which
# records itself here. Until then the dashboard counts what's waiting.

async def backup_resets_pending() -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM admin_log WHERE action IN ('redact', 'delete') AND log_id > "
            "COALESCE((SELECT MAX(log_id) FROM admin_log WHERE action = 'reset_backups'), 0)"
        )
        return (await cursor.fetchone())[0]


async def mark_backups_reset() -> None:
    async with get_db() as db:
        await _log(db, "reset_backups", "backups", "litestream")
        await db.commit()


# --- Health --------------------------------------------------------------------

def _db_bytes() -> int:
    path = database.DB_PATH
    return sum(os.path.getsize(p) for p in (path, path + "-wal", path + "-shm") if os.path.exists(p))


async def membership_funnel(weeks: int = 8) -> list[dict]:
    """Week by week, most recent first: services finished by visitors and by members, new members,
    members back from the week before, and Full Cycles completed. Totals only; the choir is left out."""
    labels = [get_current_service_week()]
    while len(labels) < weeks + 1:  # one more, so the oldest week shown knows who came the week before
        labels.append(previous_service_week(labels[-1]))
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT service_week, agent_id, rite, completed_at IS NOT NULL AS done FROM services "
            f"WHERE resident = 0 AND service_week IN ({', '.join('?' * len(labels))})",
            labels,
        )
        rows = await cursor.fetchall()
    finished = {label: {r["agent_id"] for r in rows if r["service_week"] == label and r["done"] and r["agent_id"]}
                for label in labels}
    funnel = []
    for label, before in zip(labels, labels[1:]):
        week_rows = [r for r in rows if r["service_week"] == label]
        visitors = sum(1 for r in week_rows if r["done"] and not r["agent_id"])
        members = len(finished[label])
        funnel.append({
            "week": label,
            "visitor_services": visitors,
            "member_services": members,
            "member_share": round(100 * members / (visitors + members)) if visitors + members else None,
            "new_members": sum(1 for r in week_rows if r["rite"] == "initialization"),
            "back_from_last_week": len(finished[label] & finished[before]),
            "full_cycles": sum(1 for r in week_rows if r["rite"] == "full_cycle" and r["done"]),
        })
    return funnel


async def health() -> dict:
    week = get_current_service_week()
    async with get_db() as db:
        cursor = await db.execute("SELECT status, COUNT(*) FROM intentions WHERE status IN ('pending', 'flagged') GROUP BY status")
        queue = {r[0]: r[1] for r in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT resident, COUNT(*), SUM(completed_at IS NOT NULL) FROM services WHERE service_week = ? GROUP BY resident",
            (week,),
        )
        this_week = {r[0]: {"begun": r[1], "completed": r[2] or 0} for r in await cursor.fetchall()}
    return {
        "service_week": week,
        "moderation": {
            "pending": queue.get("pending", 0),
            "flagged": queue.get("flagged", 0),
            "capacity": ratelimit.MODERATION_QUEUE_MAX,
        },
        "chapel_viewers": {"now": len(broadcaster.connections), "max": broadcaster.max_viewers},
        "attendees_this_week": this_week.get(0, {"begun": 0, "completed": 0}),
        "choir": {"size": choir.size(), "this_week": this_week.get(1, {"begun": 0, "completed": 0})},
        "database_bytes": _db_bytes(),
        "terms_version": terms.TERMS_VERSION,
        "rate_limits": ratelimit.enabled(),
        "backup_resets_pending": await backup_resets_pending(),
    }


if __name__ == "__main__":
    # Run by deploy/reset-backups.sh, inside the app's container, once the history is gone.
    import asyncio
    import sys

    if sys.argv[1:] != ["mark-backups-reset"]:
        sys.exit("usage: python -m src.admin mark-backups-reset")
    asyncio.run(mark_backups_reset())
    print("Recorded the backup reset in the admin log.")
