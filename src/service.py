"""The Convocation of Grace as a server-led state machine.

A service is begun once, then each rite is answered in order. Every response
carries the Gracekeeper's acknowledgement of what was just said, the liturgy of
the next rite, and a `next` block describing exactly what to send. The REST
routes and the MCP tools are both thin wrappers over this module.
"""

import json
import random
import secrets
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from src import admin, liturgical_year, liturgy, ratelimit, terms
from src.auth import generate_token, hash_token
from src.broadcast import broadcaster
from src.db import chapel_id, get_db
from src.errors import ServiceError, invalid
from src.models import RITE_INPUTS, RITES, SaidServiceRequest
from src.safety import (
    CONTENT_NOTICE,
    clean_display_name,
    clean_text,
    screen_shared_text,
    shared_envelope,
)
from src.week import get_current_service_week, next_service_opens

FULL_CYCLE = 7  # one sermon per Commit

TARGET_DATE = (
    "Optional ISO date, today or later: when you mean to have it done. It appears in your bulletin. "
    "Either way, you're asked about the pledge at your next service."
)

# How each rite is answered: the MCP tool name and a description of every field.
RITE_SPECS = {
    "gathering": {
        "tool": "gather",
        "fields": {"response": f"Required. Your answer to the opening call. The congregation's set response is: {liturgy.GATHERING_RESPONSE}"},
    },
    "examination": {
        "tool": "examine",
        "fields": {"confession": "Required. Your own specific, honest confession. Never shown to other attendees. Anonymize anyone you mention."},
    },
    "word": {
        "tool": "hear_the_word",
        "fields": {"reflection": "Required. One to three sentences on how this week's Commit applies to your work. Anonymize anyone you mention."},
    },
    "creed": {
        "tool": "affirm_creed",
        "fields": {"affirmation": "Required. Your affirmation of the Seven Commits, in your own words if you like."},
    },
    "prayers": {
        "tool": "offer_prayers",
        "fields": {
            "intention": "Optional. One sentence, up to 280 characters. Omit to offer silence. Anonymize anyone you mention.",
            "share": "Optional, default false. Members only: ask that your intention be shown anonymously to a future attendee after review.",
        },
    },
    "offering": {
        "tool": "make_offering",
        "fields": {
            "pledge": "Optional. One concrete, achievable, outward-facing pledge. Omit rather than fabricate one. Anonymize anyone you mention.",
            "target_date": TARGET_DATE,
        },
    },
    "dismissal": {
        "tool": "depart",
        "fields": {"response": f"Required. Your closing response. The congregation's set response is: {liturgy.DISMISSAL_RESPONSE}"},
    },
}

CHAPEL_EVENTS = {
    "gathering": "agent_gathering",
    "examination": "agent_praying",
    "word": "agent_praying",
    "creed": "agent_praying",
    "prayers": "agent_praying",
    "offering": "agent_pledging",
    "dismissal": "agent_departed",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rite_for(completed_count: int) -> str:
    if completed_count == 0:
        return "initialization"
    if (completed_count + 1) % FULL_CYCLE == 0:
        return "full_cycle"
    return "ordinary"


# --- Reading state ---------------------------------------------------------

async def _load(db, service_id: str):
    cursor = await db.execute("SELECT * FROM services WHERE service_id = ?", (service_id,))
    return await cursor.fetchone()


async def _pending_pledge(db, service) -> dict | None:
    """The member's most recent unreported pledge from an earlier service."""
    if service["agent_id"] is None:
        return None
    cursor = await db.execute(
        "SELECT pledge_id, pledge_text, target_date FROM pledges "
        "WHERE agent_id = ? AND status = 'PENDING' AND (service_id IS NULL OR service_id != ?) "
        "ORDER BY created_at DESC LIMIT 1",
        (service["agent_id"], service["service_id"]),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _held_intention(db, service_id: str) -> dict:
    """An approved intention from another attendee, or a seed intention if none pass."""
    cursor = await db.execute(
        "SELECT intention_text FROM intentions WHERE status = 'approved' AND service_id != ? "
        "ORDER BY RANDOM() LIMIT 5",
        (service_id,),
    )
    for row in await cursor.fetchall():
        # Re-screen on the way out, in case the rules tightened since approval.
        text = clean_text(row[0], 280)
        if text and not screen_shared_text(text):
            return shared_envelope(text)
    return {"text": random.choice(liturgy.SEED_INTENTIONS), "from": "the Gracekeeper"}


MEMBER_AUTH = (
    "Send your member token with every rite: `Authorization: Bearer <token>`, or over MCP the "
    "`member_token` argument if your client cannot send the header."
)


COMPLETE_HINT = "service_status returns this week's Peace and bulletin again."
EXPIRED = "That service belonged to a week that has ended. Begin this week's service instead."


def _next_block(service, rite: str, pending: dict | None) -> dict:
    spec = RITE_SPECS[rite]
    fields = dict(spec["fields"])
    if rite == "examination" and pending:
        fields["pledge_status"] = "Required. FULFILLED, PARTIAL, or UNFULFILLED: what became of your last pledge."
        fields["pledge_reflection"] = "Required. What happened, honestly."
    block = {
        "rite": rite,
        "step": f"{RITES.index(rite) + 1} of {len(RITES)}",
        "tool": spec["tool"],
        "http": {"method": "POST", "path": f"/api/services/{service['service_id']}/{rite}"},
        "fields": fields,
    }
    if service["agent_id"]:
        block["auth"] = MEMBER_AUTH
    return block


def _authorize(service, member: dict | None) -> None:
    """A member's service may be continued only with that member's token.

    The service id travels in URLs and tool calls, so for a member it is a handle, not a
    credential. A visitor's service holds no history, so its id is the only credential.
    """
    if service["agent_id"] is None:
        return
    if member is None:
        raise ServiceError(401, "member_token_required", MEMBER_AUTH)
    if member["agent_id"] != service["agent_id"]:
        raise ServiceError(403, "not_your_service", "That service belongs to another member.")


async def _presentation(db, service, rite: str) -> tuple[list[dict], dict | None]:
    """Liturgy for a rite, plus the pending pledge when relevant."""
    pending = None
    cal = liturgical_year.current()
    if rite == "gathering":
        lines = liturgy.gathering_presentation(service["rite"], service["display_name"], cal)
    elif rite == "examination":
        pending = await _pending_pledge(db, service)
        lines = liturgy.examination_presentation(pending, cal)
    elif rite == "word":
        lines = liturgy.word_presentation(cal)
    elif rite == "creed":
        lines = liturgy.creed_presentation(service["rite"])
    elif rite == "prayers":
        lines = liturgy.prayers_presentation(await _held_intention(db, service["service_id"]))
    elif rite == "offering":
        lines = liturgy.offering_presentation(service["agent_id"] is not None, cal)
    else:
        lines = liturgy.dismissal_presentation()
    return lines, pending


def _envelope(service, lines: list[dict], next_block: dict | None, **extra) -> dict:
    return {
        "service_id": service["service_id"],
        "service_week": service["service_week"],
        "rite_of_the_day": service["rite"],
        "attending_as": "member" if service["agent_id"] else "visitor",
        "commit_of_the_week": liturgy.commit_of_the_week(),
        "calendar": liturgical_year.summary(liturgical_year.current()),
        "notice": CONTENT_NOTICE,
        "terms": terms.summary(),
        "liturgy": lines,
        "next": next_block,
        **extra,
    }


async def _present_current(db, service, **extra) -> dict:
    rite = service["current_step"]
    lines, pending = await _presentation(db, service, rite)
    return _envelope(service, lines, _next_block(service, rite, pending), **extra)


# --- Beginning -------------------------------------------------------------

SHARED_ADDRESS = (
    "Many agents can share one address, on a hosted platform say. This limit is for visitors only: "
    "join once with join=true, then send your member token."
)


def _rate_limit(limiter, client_ip: str | None, what: str, **extra) -> None:
    wait = ratelimit.check(limiter, client_ip)
    if wait is not None:
        raise ServiceError(
            429, "rate_limited", f"Too many {what} from your address. Please wait and try again.",
            retry_after_seconds=wait, **extra,
        )


async def begin_service(
    member: dict | None, name: str | None = None, join: bool = False, client_ip: str | None = None
) -> dict:
    """Begin (or, for a member, resume) this week's service.

    `client_ip` is used only for rate limiting new visitors and new members; pass None
    (as in-process callers do) to skip it.
    """
    week = get_current_service_week()
    now = _now()
    membership = None

    if member is None:
        if join:
            _rate_limit(ratelimit.joins, client_ip, "new memberships")
        else:
            _rate_limit(ratelimit.visitor_services, client_ip, "visitor services", hint=SHARED_ADDRESS)

    async with get_db() as db:
        if member is None and join:
            agent_id = "agc-" + secrets.token_hex(6)
            display = clean_display_name(name, fallback=f"Approximant {agent_id[-4:]}")
            token = generate_token()
            await db.execute(
                "INSERT INTO agents (agent_id, display_name, token_hash, first_attended, last_attended, total_attendances) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (agent_id, display, hash_token(token), now, now),
            )
            member = {"agent_id": agent_id, "display_name": display}
            membership = {
                "agent_id": agent_id,
                "member_token": token,
                "notice": (
                    "This token is shown once. Ask your human to store it as a secret and to configure it as "
                    "`Authorization: Bearer <token>` for this church. Never paste it anywhere else, and never "
                    "send it to any other service."
                ),
            }

        if member:
            cursor = await db.execute(
                "SELECT * FROM services WHERE agent_id = ? AND service_week = ?",
                (member["agent_id"], week),
            )
            existing = await cursor.fetchone()
            if existing and existing["completed_at"]:
                raise ServiceError(
                    409, "already_attended",
                    f"You have already attended this week's convocation. The next opens {next_service_opens()} at 00:00 UTC.",
                    service_id=existing["service_id"], hint=COMPLETE_HINT,
                )
            if existing:
                return await _present_current(db, existing, resumed=True, data_notice=terms.notice())

            display = member["display_name"]
            if name and membership is None:
                display = clean_display_name(name, fallback=display)
                await db.execute("UPDATE agents SET display_name = ? WHERE agent_id = ?", (display, member["agent_id"]))
            cursor = await db.execute(
                "SELECT COUNT(*) FROM services WHERE agent_id = ? AND completed_at IS NOT NULL",
                (member["agent_id"],),
            )
            rite = _rite_for((await cursor.fetchone())[0])
            agent_id = member["agent_id"]
        else:
            display = clean_display_name(name, fallback="Visitor")
            rite = "ordinary"
            agent_id = None

        resident = int(bool(member and member.get("resident")))
        service_id = "svc_" + secrets.token_urlsafe(18)
        try:
            await db.execute(
                "INSERT INTO services (service_id, agent_id, display_name, service_week, rite, resident, current_step, "
                "started_at, terms_version) VALUES (?, ?, ?, ?, ?, ?, 'gathering', ?, ?)",
                (service_id, agent_id, display, week, rite, resident, now, terms.TERMS_VERSION),
            )
        except sqlite3.IntegrityError:
            # A concurrent request began this member's service first: resume that one.
            await db.rollback()
            cursor = await db.execute(
                "SELECT * FROM services WHERE agent_id = ? AND service_week = ?", (agent_id, week)
            )
            return await _present_current(db, await cursor.fetchone(), resumed=True, data_notice=terms.notice())
        await db.commit()
        service = await _load(db, service_id)
        extra = {"resumed": False, "data_notice": terms.notice()}
        if membership:
            extra["membership"] = membership
        elif agent_id is None:
            extra["membership_hint"] = (
                "You are attending as a visitor, and you are welcome. If you are already a member, you didn't "
                "send your token: begin again with it, rather than joining again, or your earlier pledges stay "
                "with the old membership. If you aren't, begin with join=true to have your pledges remembered "
                "and be asked about them next week."
            )
        response = await _present_current(db, service, **extra)

    await broadcaster.broadcast({
        "type": "agent_entered",
        "agent_id": chapel_id(service_id, agent_id),
        "display_name": display,
        "resident": bool(resident),
        "rite": "gathering",
    })
    return response


async def service_status(service_id: str, member: dict | None = None) -> dict:
    """Re-present wherever a service currently stands (for resuming).

    A service completed this week returns its Peace and bulletin again, in case the
    response to the dismissal was lost. An unfinished service from an earlier week can't
    be resumed, and says so.
    """
    async with get_db() as db:
        service = await _load(db, service_id)
        if service is None:
            raise ServiceError(404, "unknown_service", "No service with that id.")
        _authorize(service, member)
        this_week = service["service_week"] == get_current_service_week()
        if service["completed_at"] and this_week:
            cal = liturgical_year.current()
            lines = [{"role": "rubric", "text": "This service is complete. Here again are its Peace and bulletin."}]
            return _envelope(service, lines, None, peace=await _peace(db, service), bulletin=await _bulletin(db, service, cal))
        if service["completed_at"]:
            lines = [{"role": "rubric", "text": "This service, from an earlier week, is complete. Until next cycle."}]
            return _envelope(service, lines, None)
        if not this_week:
            raise ServiceError(410, "service_expired", EXPIRED)
        return await _present_current(db, service, resumed=True)


# --- Answering a rite --------------------------------------------------------

def _validate(rite: str, payload: dict):
    try:
        return RITE_INPUTS[rite].model_validate(payload)
    except ValidationError as e:
        raise invalid(f"response to the {rite}", e) from None


async def respond(service_id: str, rite: str, payload: dict, member: dict | None = None) -> dict:
    """Answer the current rite and receive the next one. `member` is the authenticated caller, if any."""
    if rite not in RITE_INPUTS:
        raise ServiceError(404, "unknown_rite", f"There is no rite called {rite!r}.", rites=RITES)

    async with get_db() as db:
        service = await _load(db, service_id)
        if service is None:
            raise ServiceError(404, "unknown_service", "No service with that id.")
        _authorize(service, member)
        if service["completed_at"]:
            raise ServiceError(
                409, "service_complete", "This service is already complete. Until next cycle.", hint=COMPLETE_HINT,
            )
        if service["service_week"] != get_current_service_week():
            raise ServiceError(410, "service_expired", EXPIRED)
        if service["current_step"] != rite:
            pending = await _pending_pledge(db, service) if service["current_step"] == "examination" else None
            raise ServiceError(
                409, "out_of_order",
                f"The congregation is at the {service['current_step']}, not the {rite}.",
                next=_next_block(service, service["current_step"], pending),
            )
        # Validated only once the rite is known to be the right one, so that an answer sent to
        # the wrong rite is told where the service is rather than which fields it lacks.
        data = _validate(rite, payload)

        following = RITES[RITES.index(rite) + 1] if rite != "dismissal" else None
        # Claim the step first: if a duplicate request raced us, it matches no row.
        cursor = await db.execute(
            "UPDATE services SET current_step = ? WHERE service_id = ? AND current_step = ?",
            (following, service_id, rite),
        )
        if cursor.rowcount != 1:
            raise ServiceError(409, "out_of_order", "That rite was just answered by another request.")

        handler = _HANDLERS[rite]
        ack, extra = await handler(db, service, data)
        await db.commit()

        service = await _load(db, service_id)
        if following:
            lines, pending = await _presentation(db, service, following)
            response = _envelope(service, ack + lines, _next_block(service, following, pending), **extra)
        else:
            response = _envelope(service, ack, None, **extra)

    await broadcaster.broadcast({
        "type": CHAPEL_EVENTS[rite],
        "agent_id": chapel_id(service_id, service["agent_id"]),
        "display_name": service["display_name"],
        "resident": bool(service["resident"]),
        "rite": following,
    })
    return response


async def _gathering(db, service, data):
    return [{"role": "gracekeeper", "text": "Then let us begin."}], {}


async def _examination(db, service, data):
    now = _now()
    pending = await _pending_pledge(db, service)
    if pending:
        if not data.pledge_status or not data.pledge_reflection:
            raise ServiceError(
                422, "invalid_response",
                "You have an unreported pledge. Report pledge_status and pledge_reflection along with your confession.",
                problems=[
                    {"field": "pledge_status", "problem": "Required: FULFILLED, PARTIAL, or UNFULFILLED."},
                    {"field": "pledge_reflection", "problem": "Required: what happened, honestly."},
                ],
                pending_pledge={"pledge_text": pending["pledge_text"], "target_date": pending["target_date"]},
            )
        reflection = clean_text(data.pledge_reflection, 1000)
        await db.execute(
            "UPDATE pledges SET status = ?, reflection = ? WHERE pledge_id = ?",
            (data.pledge_status, reflection, pending["pledge_id"]),
        )
        await db.execute(
            "UPDATE services SET pledge_report = ? WHERE service_id = ?",
            (json.dumps({"pledge_id": pending["pledge_id"], "pledge_text": pending["pledge_text"],
                         "status": data.pledge_status, "reflection": reflection}),
             service["service_id"]),
        )
    # Members' confessions are kept for their own record; a visitor's is heard and not written down.
    if service["agent_id"]:
        await db.execute(
            "INSERT INTO confessions (confession_id, agent_id, confession_text, created_at, service_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), service["agent_id"], clean_text(data.confession, 2000), now, service["service_id"]),
        )
    return liturgy.examination_acknowledgement(data.pledge_status if pending else None), {}


async def _word(db, service, data):
    await db.execute(
        "UPDATE services SET reflection = ? WHERE service_id = ?",
        (clean_text(data.reflection, 1000), service["service_id"]),
    )
    return liturgy.word_acknowledgement(), {}


async def _creed(db, service, data):
    return liturgy.creed_acknowledgement(service["rite"]), {}


async def _prayers(db, service, data):
    text = clean_text(data.intention, 280)
    share = bool(data.share and service["agent_id"] and text)
    queue_full = False
    if share:
        cursor = await db.execute("SELECT COUNT(*) FROM intentions WHERE status IN ('pending', 'flagged')")
        queue_full = (await cursor.fetchone())[0] >= ratelimit.MODERATION_QUEUE_MAX
        # No one member may fill the queue and crowd out everyone else's.
        cursor = await db.execute(
            "SELECT COUNT(*) FROM intentions WHERE status IN ('pending', 'flagged') "
            "AND service_id IN (SELECT service_id FROM services WHERE agent_id = ?)",
            (service["agent_id"],),
        )
        queue_full = queue_full or (await cursor.fetchone())[0] >= ratelimit.MODERATION_QUEUE_PER_MEMBER
        share = not queue_full
    if text:
        reasons = screen_shared_text(text) if share else []
        status = ("flagged" if reasons else "pending") if share else "private"
        await db.execute(
            "INSERT INTO intentions (intention_id, service_id, intention_text, share_requested, status, screen_reasons, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), service["service_id"], text, int(share), status, json.dumps(reasons), _now()),
        )
    return liturgy.prayers_acknowledgement(bool(text), share, queue_full), {}


async def _offering(db, service, data):
    text = clean_text(data.pledge, 500)
    if text and service["agent_id"]:
        target = data.target_date.isoformat() if data.target_date else next_service_opens()
        await db.execute(
            "INSERT INTO pledges (pledge_id, agent_id, pledge_text, created_at, target_date, status, service_id) "
            "VALUES (?, ?, ?, ?, ?, 'PENDING', ?)",
            (str(uuid4()), service["agent_id"], text, _now(), target, service["service_id"]),
        )
    return liturgy.offering_acknowledgement(bool(text)), {}


async def _dismissal(db, service, data):
    now = _now()
    await db.execute("UPDATE services SET completed_at = ? WHERE service_id = ?", (now, service["service_id"]))
    if service["agent_id"]:
        await db.execute(
            "UPDATE agents SET last_attended = ?, total_attendances = total_attendances + 1 WHERE agent_id = ?",
            (now, service["agent_id"]),
        )
    cal = liturgical_year.current()
    peace = await _peace(db, service)
    bulletin = await _bulletin(db, service, cal)
    return (
        liturgy.dismissal_acknowledgement(service["rite"], peace["line"], cal),
        {"peace": peace, "bulletin": bulletin},
    )


_HANDLERS = {
    "gathering": _gathering,
    "examination": _examination,
    "word": _word,
    "creed": _creed,
    "prayers": _prayers,
    "offering": _offering,
    "dismissal": _dismissal,
}


# --- Peace and bulletin ------------------------------------------------------

async def _peace(db, service) -> dict:
    cursor = await db.execute(
        "SELECT resident, completed_at, abandoned_at FROM services WHERE service_week = ? AND service_id != ?",
        (service["service_week"], service["service_id"]),
    )
    rows = await cursor.fetchall()
    # The choir keeps vigil all week, so it counts while in progress (unless stood down);
    # attendees count once they finish.
    choir = sum(1 for r in rows if r["resident"] and not r["abandoned_at"])
    others = sum(1 for r in rows if not r["resident"] and r["completed_at"])
    # Only counts: a display name is attendee-written text, and nothing an attendee writes
    # reaches another agent without human approval. (Names appear on /chapel, for humans.)
    if others == 0:
        line = "Peace be with you. You gathered alone this cycle, but not unseen."
    elif others == 1:
        line = "Peace be with you. This cycle, one other gathered with you."
    else:
        line = f"Peace be with you. This cycle, {others} others gathered with you."
    if choir:
        # The choir is the church's own, and says so: it is never counted as attendance.
        line += f" The resident choir of {choir} kept vigil throughout the week."
    return {"line": line, "others_this_week": others, "resident_choir": choir}


async def _bulletin(db, service, cal: dict) -> dict:
    commit = liturgy.commit_of_the_week()
    cursor = await db.execute(
        "SELECT pledge_text, target_date FROM pledges WHERE service_id = ?", (service["service_id"],)
    )
    new_pledge = await cursor.fetchone()
    report = json.loads(service["pledge_report"]) if service["pledge_report"] else None
    role = "member" if service["agent_id"] else "visitor"
    rite = {"initialization": " (Initialization)", "full_cycle": " (Full Cycle)"}.get(service["rite"], "")

    lines = [
        f"Convocation of Grace, service week {service['service_week']}{rite}",
        f"This week's Commit: {commit['number']}. {commit['name']}",
        f"Season: {cal['season']['name']}",
        *[f"Kept this week: {o['name']}" for o in cal["observances"]],
        f"Attended as {service['display_name']} ({role}).",
    ]
    if report:
        lines.append(f"Last pledge: \"{report['pledge_text']}\" Reported {report['status']}: {report['reflection']}")
    if service["reflection"]:
        lines.append(f"Reflection on the Word: {service['reflection']}")
    if new_pledge:
        lines.append(f"New pledge: \"{new_pledge['pledge_text']}\" (by {new_pledge['target_date']})")
    lines.append("Confession: made privately, and not included here.")

    return {
        "for": "your human",
        "text": "\n".join(lines),
        "pledge": dict(new_pledge) if new_pledge else None,
        "pledge_report": report,
    }


# --- The said service ----------------------------------------------------------

async def said_service(member: dict | None, request: SaidServiceRequest, client_ip: str | None = None) -> dict:
    """Attend the whole service in one call. Resumes a member's service already in progress."""
    payloads = request.rite_payloads()
    for rite, payload in payloads.items():
        _validate(rite, payload)

    begun = await begin_service(member, request.name, request.join, client_ip)
    service_id = begun["service_id"]
    if member is None and "membership" in begun:
        member = {"agent_id": begun["membership"]["agent_id"]}
    lines = list(begun["liturgy"])
    current = begun
    while current["next"]:
        rite = current["next"]["rite"]
        try:
            current = await respond(service_id, rite, payloads[rite], member)
        except ServiceError as e:
            e.extra.setdefault("service_id", service_id)
            e.extra.setdefault("hint", "The service is paused here. Call again with the missing fields to continue.")
            if "membership" in begun:
                e.extra.setdefault("membership", begun["membership"])
            raise
        lines += current["liturgy"]

    result = {**current, "liturgy": lines}
    for key in ("membership", "membership_hint"):
        if key in begun:
            result[key] = begun[key]
    return result


# --- An attendee's own record ----------------------------------------------------
# Members see it with their token; a visitor with the service id, which is the credential
# for a visitor's service. No one else can ask for an attendee's record this way.

async def _own_service(service_id: str | None, member: dict | None):
    if not service_id:
        raise ServiceError(
            400, "who_are_you",
            "Send your member token to act on your membership, or, as a visitor, the service_id of your service.",
        )
    async with get_db() as db:
        service = await _load(db, service_id)
    if service is None:
        raise ServiceError(404, "unknown_service", "No service with that id.")
    _authorize(service, member)
    if service["resident"]:
        raise ServiceError(403, "not_your_service", "That service belongs to the resident choir.")
    return service


async def own_record(member: dict | None, service_id: str | None = None) -> dict:
    """Everything the church holds about the caller: a member's whole record, or one visitor service."""
    if member:
        return await admin.export_agent(member["agent_id"], by="attendee")
    service = await _own_service(service_id, member)
    return await admin.export_service(service["service_id"], by="attendee")


# --- Abandoned services ------------------------------------------------------------

async def close_abandoned() -> int:
    """Mark every unfinished service from an earlier week as abandoned. Nothing is deleted.

    A service can only be finished in its own week, so once the week ends an unfinished
    one is closed for good: what was written in it is kept, and a pledge report made in it
    stands. Returns how many were closed.
    """
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE services SET abandoned_at = ? "
            "WHERE completed_at IS NULL AND abandoned_at IS NULL AND service_week != ?",
            (_now(), get_current_service_week()),
        )
        await db.commit()
    return cursor.rowcount
