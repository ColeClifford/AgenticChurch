"""The one shape every error an attendee sees takes: `{"error", "message", ...}`.

Service errors may add `next` (what to send instead), `hint`, or other fields. Over REST
the HTTP status is `status`; over MCP the same dict is the text of the tool error.
"""

from pydantic import ValidationError


class ServiceError(Exception):
    """A request the church cannot accept. `status` is the HTTP status to use."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.extra}


def problems(errors: list[dict], skip: tuple[str, ...] = ()) -> list[dict]:
    """Pydantic or FastAPI validation errors as `[{"field", "problem"}]`."""
    out = []
    for err in errors:
        loc = [str(p) for p in err["loc"] if str(p) not in skip]
        out.append({"field": ".".join(loc) or "body", "problem": err["msg"]})
    return out


def invalid(what: str, e: ValidationError) -> ServiceError:
    return ServiceError(422, "invalid_response", f"That {what} could not be accepted.", problems=problems(e.errors()))
