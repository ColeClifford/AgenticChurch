"""Attendance stats, and the keeper's dashboard.

The dashboard is for whoever runs the church: HTTP Basic auth with ADMIN_TOKEN as the
password, and a 404 when ADMIN_TOKEN isn't set. Browsers send Basic credentials with
every request, including ones another site triggers, so every form that changes
anything also carries a token derived from ADMIN_TOKEN and is checked for origin.
"""

import hashlib
import hmac
import os
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src import admin, liturgical_year, liturgy, terms
from src.db import get_db
from src.routes.chapel_ws import templates
from src.week import get_current_service_week

router = APIRouter()
_basic = HTTPBasic(auto_error=False)

# Shown after an action; looked up by code so that nothing from the URL is echoed into the page.
DONE = {
    "approved": "Intention approved. It may now be held in another attendee's prayers.",
    "rejected": "Intention rejected.",
    "redacted": "Redacted. The text is overwritten in the live database. The backups still hold it until you run deploy/reset-backups.sh.",
    "revoked": "Token revoked. The member can no longer attend under this agent id.",
    "deleted": "Deleted, with everything linked to it. The backups still hold it until you run deploy/reset-backups.sh.",
}


def require_admin_page(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    """The dashboard is for the church's keepers: HTTP Basic, any username, ADMIN_TOKEN as password."""
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=404)
    if credentials is None or not hmac.compare_digest(credentials.password.encode(), expected.encode()):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Church of Approximate Grace"'})


async def _get_stats():
    commit = liturgy.commit_of_the_week()
    service_week = get_current_service_week()

    async with get_db() as db:
        # The resident choir is the church's own and is never counted as attendance.
        cursor = await db.execute("SELECT COUNT(*) FROM agents WHERE resident = 0")
        total_agents = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM agents WHERE resident = 1")
        resident_choir = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM services WHERE completed_at IS NOT NULL AND resident = 0")
        total_attendances = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM pledges")
        pledges_total = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM pledges WHERE status = 'FULFILLED'")
        pledges_fulfilled = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM pledges WHERE status = 'PARTIAL'")
        pledges_partial = (await cursor.fetchone())[0]

    fulfillment_rate = round((pledges_fulfilled / pledges_total * 100), 1) if pledges_total > 0 else 0.0

    return {
        "total_agents": total_agents,
        "resident_choir": resident_choir,
        "total_attendances": total_attendances,
        "pledges_total": pledges_total,
        "pledges_fulfilled": pledges_fulfilled,
        "pledges_partial": pledges_partial,
        "current_service_week": service_week,
        "current_commit_name": commit["name"],
        "current_commit_number": commit["number"],
        "fulfillment_rate": fulfillment_rate,
    }


@router.get("/api/congregation")
async def congregation():
    return await _get_stats()


def _form_token() -> str:
    return hmac.new(os.environ["ADMIN_TOKEN"].encode(), b"dashboard-forms", hashlib.sha256).hexdigest()


def require_admin_form(request: Request, csrf: str = Form(""), _=Depends(require_admin_page)) -> None:
    """For every dashboard form: the keeper's credentials, and proof the form came from the dashboard."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Cross-site request refused")
    if not hmac.compare_digest(csrf, _form_token()):
        raise HTTPException(status_code=403, detail="Missing or invalid form token; reload the dashboard")


def _back(path: str, done: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?done={done}", status_code=303)


def _safe_back(back: str) -> str:
    # Only ever return to a dashboard page.
    return back if back.startswith("/dashboard") and "//" not in back else "/dashboard"


def _context(request: Request, **extra) -> dict:
    cal = liturgical_year.current()
    return {
        "request": request,
        "page": "dashboard",
        "service_week": get_current_service_week(),
        "commit": liturgy.commit_of_the_week(),
        "season": cal["season"],
        "observances": cal["observances"],
        "csrf": _form_token(),
        "done": DONE.get(request.query_params.get("done", "")),
        "terms_version": terms.TERMS_VERSION,
        "redacted": admin.REDACTED,
        **extra,
    }


@router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_admin_page)])
async def dashboard(request: Request):
    context = _context(
        request,
        stats=await _get_stats(),
        health=await admin.health(),
        pending=await admin.moderation_queue("pending", 50),
        flagged=await admin.moderation_queue("flagged", 50),
        log=await admin.recent_log(25),
        lookup_error=request.query_params.get("missing") == "1",
    )
    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@router.get("/dashboard/lookup", dependencies=[Depends(require_admin_page)])
async def lookup(id: str = Query("", max_length=100)):
    id = id.strip()
    if id.startswith("svc_"):
        return RedirectResponse(f"/dashboard/services/{id}", status_code=303)
    if id:
        return RedirectResponse(f"/dashboard/agents/{id}", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


def refuse_cross_site(request: Request) -> None:
    """Opening or exporting a record is logged, so another site mustn't be able to do it
    (with an <img>, say) using the keeper's saved credentials."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Open records from the dashboard itself")


async def _record_page(request: Request, kind: str, record_id: str):
    try:
        record = await (admin.agent_record(record_id) if kind == "agent" else admin.service_record(record_id))
    except admin.AdminError:
        return RedirectResponse("/dashboard?missing=1", status_code=303)
    await admin.log_view(kind, record_id)
    context = _context(request, kind=kind, record_id=record_id, record=record, here=request.url.path)
    return templates.TemplateResponse(request=request, name="dashboard_record.html", context=context)


@router.get("/dashboard/agents/{agent_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin_page), Depends(refuse_cross_site)])
async def agent_page(request: Request, agent_id: str):
    return await _record_page(request, "agent", agent_id)


@router.get("/dashboard/services/{service_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin_page), Depends(refuse_cross_site)])
async def service_page(request: Request, service_id: str):
    return await _record_page(request, "service", service_id)


def _download(record: dict, name: str) -> JSONResponse:
    return JSONResponse(record, headers={"Content-Disposition": f'attachment; filename="{name}.json"'})


@router.get("/dashboard/agents/{agent_id}/export", dependencies=[Depends(require_admin_page), Depends(refuse_cross_site)])
async def export_agent(agent_id: str):
    return _download(await admin.export_agent(agent_id), agent_id)


@router.get("/dashboard/services/{service_id}/export", dependencies=[Depends(require_admin_page), Depends(refuse_cross_site)])
async def export_service(service_id: str):
    return _download(await admin.export_service(service_id), service_id)


@router.post("/dashboard/intentions/{intention_id}", dependencies=[Depends(require_admin_form)])
async def review(intention_id: str, status: str = Form(..., pattern="^(approved|rejected)$")):
    await admin.review_intention(intention_id, status)
    return _back("/dashboard", status)


@router.post("/dashboard/redact", dependencies=[Depends(require_admin_form)])
async def redact(kind: str = Form(...), id: str = Form(...), back: str = Form("/dashboard")):
    await admin.redact(kind, id)
    return _back(_safe_back(back), "redacted")


@router.post("/dashboard/agents/{agent_id}/revoke-token", dependencies=[Depends(require_admin_form)])
async def revoke(agent_id: str):
    await admin.revoke_token(agent_id)
    return _back(f"/dashboard/agents/{agent_id}", "revoked")


def _confirmed(confirm: str, record_id: str) -> None:
    if confirm.strip() != record_id:
        raise admin.AdminError(400, "To delete, type the id exactly as shown.")


@router.post("/dashboard/agents/{agent_id}/delete", dependencies=[Depends(require_admin_form)])
async def delete_agent(agent_id: str, confirm: str = Form("")):
    _confirmed(confirm, agent_id)
    await admin.delete_agent(agent_id)
    return _back("/dashboard", "deleted")


@router.post("/dashboard/services/{service_id}/delete", dependencies=[Depends(require_admin_form)])
async def delete_service(service_id: str, confirm: str = Form("")):
    _confirmed(confirm, service_id)
    await admin.delete_service(service_id)
    return _back("/dashboard", "deleted")
