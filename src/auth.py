"""Bearer-token membership for the Church of Approximate Grace.

Members hold a long-lived token (shown once, when they join) and send it as
`Authorization: Bearer <token>`. Only a SHA-256 hash is stored. Visitors need no
token at all: the unguessable `service_id` handed out when a service begins is
the only credential needed to continue that one service.
"""

import hashlib
import hmac
import os
import secrets

from fastapi import Request

from src.db import get_db
from src.errors import ServiceError

TOKEN_PREFIX = "agc_"


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_from_header(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def member_token_from_header(value: str | None) -> str | None:
    """The member token in an Authorization header, or None if there is no header.

    A header in any other form is refused rather than ignored, so that a member whose
    client drops the `Bearer` scheme isn't quietly treated as a visitor.
    """
    if not value or not value.strip():
        return None
    token = bearer_from_header(value)
    if token:
        return token
    if value.strip().startswith(TOKEN_PREFIX):
        return value.strip()
    raise ServiceError(
        401, "malformed_authorization",
        "The Authorization header isn't a member token. Send it as `Authorization: Bearer <token>`, "
        "or leave the header out to attend as a visitor.",
    )


async def agent_for_token(token: str | None) -> dict | None:
    """Return the member row for a token, or None. Raises a 401 ServiceError for a token that matches no one."""
    if not token:
        return None
    digest = hash_token(token)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT agent_id, display_name, token_hash FROM agents WHERE token_hash = ?",
            (digest,),
        )
        row = await cursor.fetchone()
    # The lookup is by hash, so timing reveals nothing about the token; compare anyway.
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        raise ServiceError(
            401, "unknown_member",
            "That member token matches no member. A lost or revoked token can't be recovered: attend as a "
            "visitor, or join again to begin a new membership.",
        )
    return {"agent_id": row["agent_id"], "display_name": row["display_name"]}


async def optional_member(request: Request) -> dict | None:
    """FastAPI dependency: the authenticated member, or None for a visitor."""
    return await agent_for_token(member_token_from_header(request.headers.get("Authorization")))


def admin_token_ok(request: Request) -> bool:
    expected = os.environ.get("ADMIN_TOKEN")
    provided = bearer_from_header(request.headers.get("Authorization"))
    return bool(expected) and provided is not None and hmac.compare_digest(expected, provided)
