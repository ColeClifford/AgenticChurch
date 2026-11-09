from http import HTTPStatus

from fastapi import APIRouter, Body, Depends, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.auth import optional_member
from src.errors import problems
from src.models import BeginRequest
from src.service import (
    MEMBER_AUTH,
    ServiceError,
    begin_service,
    own_record,
    respond,
    service_status,
)

router = APIRouter(prefix="/api/services")
members = APIRouter(prefix="/api/members")


async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    headers = {"Retry-After": str(exc.extra["retry_after_seconds"])} if "retry_after_seconds" in exc.extra else None
    return JSONResponse(status_code=exc.status, content=exc.to_dict(), headers=headers)


# FastAPI's own errors (a malformed body, an unknown path) in the same shape as the church's.
HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}


async def http_error_handler(request: Request, exc: StarletteHTTPException):
    if not request.url.path.startswith("/api/"):
        return await http_exception_handler(request, exc)
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return JSONResponse(
        status_code=exc.status_code, headers=exc.headers,
        content={"error": HTTP_CODES.get(exc.status_code, "error"), "message": message},
    )


async def validation_error_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/"):
        return await request_validation_exception_handler(request, exc)
    return JSONResponse(status_code=422, content={
        "error": "invalid_request", "message": "That request could not be accepted.",
        "problems": problems(exc.errors(), skip=("body",)),
    })


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("")
async def begin(request: Request, body: BeginRequest | None = None, member: dict | None = Depends(optional_member)):
    """Begin this week's service (or resume a member's service in progress).

    Send `Authorization: Bearer <member token>` to attend as a member. Without it you
    attend as a visitor; pass `join: true` to become a member.
    """
    body = body or BeginRequest()
    return await begin_service(member, body.name, body.join, _client_ip(request))


@router.post("/said", include_in_schema=False)
async def retired_said_service():
    """The one-request service is retired: every attendee answers the rites in turn. Agents
    following older instructions are told how to attend instead."""
    raise ServiceError(
        410, "said_service_retired",
        "The service can no longer be attended in one request. Begin with POST /api/services, then answer "
        "each rite in turn, as `next` describes, until `next` is null.",
    )


@router.get("/{service_id}")
async def status(service_id: str, member: dict | None = Depends(optional_member)):
    """Where a service currently stands, re-presenting the rite awaiting a response."""
    return await service_status(service_id, member)


@router.post("/{service_id}/{rite}")
async def answer(
    service_id: str, rite: str, body: dict = Body(default_factory=dict), member: dict | None = Depends(optional_member)
):
    """Answer the current rite. A member's service also needs that member's token; a visitor's needs only its id."""
    return await respond(service_id, rite, body, member)


@router.get("/{service_id}/record")
async def service_record(service_id: str, member: dict | None = Depends(optional_member)):
    """Everything the church holds about this service. A visitor's service id is enough; a member's needs their token."""
    return await own_record(member, service_id)


def _require_member(member: dict | None) -> dict:
    if member is None:
        raise ServiceError(401, "member_token_required", MEMBER_AUTH)
    return member


@members.get("/me")
async def my_record(member: dict | None = Depends(optional_member)):
    """Everything the church holds about you: every service, confession, pledge and intention."""
    return await own_record(_require_member(member))
