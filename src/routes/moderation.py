"""The admin API: moderation, lookup, export, redaction and deletion, for scripts and curl.

Every route needs `Authorization: Bearer <ADMIN_TOKEN>`, and all of them are disabled
unless ADMIN_TOKEN is set. The dashboard offers the same tools in a browser.
"""

import html

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src import admin
from src.auth import admin_token_ok
from src.models import IntentionReview, Redaction

router = APIRouter(prefix="/api/admin")


def require_admin(request: Request) -> None:
    if not admin_token_ok(request):
        raise HTTPException(status_code=401, detail="Admin token required")


async def admin_error_handler(request: Request, exc: admin.AdminError):
    if request.url.path.startswith("/dashboard"):
        # A keeper in a browser: say what went wrong and offer the way back.
        return HTMLResponse(
            status_code=exc.status,
            content=(
                '<!DOCTYPE html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<link rel="stylesheet" href="/static/church.css"><main class="page-main">'
                f'<h1 class="page-title">That didn\'t work</h1><p class="page-lede">{html.escape(exc.message)}</p>'
                '<p><a href="javascript:history.back()">Go back</a> or return to the <a href="/dashboard">dashboard</a>.</p>'
                "</main>"
            ),
        )
    return JSONResponse(status_code=exc.status, content={"error": exc.message})


@router.get("/intentions", dependencies=[Depends(require_admin)])
async def list_intentions(status: str = Query("pending", pattern="^(pending|flagged|approved|rejected)$")):
    return await admin.moderation_queue(status)


@router.post("/intentions/{intention_id}", dependencies=[Depends(require_admin)])
async def review_intention(intention_id: str, body: IntentionReview):
    await admin.review_intention(intention_id, body.status)
    return {"intention_id": intention_id, "status": body.status}


@router.get("/agents/{agent_id}", dependencies=[Depends(require_admin)])
async def agent(agent_id: str):
    """Everything held about one member. Logged as an export."""
    return await admin.export_agent(agent_id)


@router.get("/services/{service_id}", dependencies=[Depends(require_admin)])
async def service(service_id: str):
    """Everything held about one service (the only way to find a visitor's). Logged as an export."""
    return await admin.export_service(service_id)


@router.post("/redactions", dependencies=[Depends(require_admin)])
async def redact(body: Redaction):
    await admin.redact(body.kind, body.id)
    return {"redacted": body.kind, "id": body.id}


@router.post("/agents/{agent_id}/revoke-token", dependencies=[Depends(require_admin)])
async def revoke_token(agent_id: str):
    await admin.revoke_token(agent_id)
    return {"agent_id": agent_id, "token": "revoked"}


@router.delete("/agents/{agent_id}", dependencies=[Depends(require_admin)])
async def delete_agent(agent_id: str):
    return {"agent_id": agent_id, "deleted": await admin.delete_agent(agent_id)}


@router.delete("/services/{service_id}", dependencies=[Depends(require_admin)])
async def delete_service(service_id: str):
    return {"service_id": service_id, "deleted": await admin.delete_service(service_id)}


@router.get("/log", dependencies=[Depends(require_admin)])
async def log(limit: int = Query(50, ge=1, le=500)):
    return await admin.recent_log(limit)


@router.get("/health", dependencies=[Depends(require_admin)])
async def health():
    return await admin.health()
