"""Pydantic models for the Church of Approximate Grace.

Each rite of the Convocation has its own input model. Length limits are
enforced here; text is further normalized by `src.safety.clean_text` before
it is stored.
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from src import terms

RITES = ["gathering", "examination", "word", "creed", "prayers", "offering", "dismissal"]


class PledgeStatus(str, Enum):
    PENDING = "PENDING"
    FULFILLED = "FULFILLED"
    PARTIAL = "PARTIAL"
    UNFULFILLED = "UNFULFILLED"


ReportedStatus = Literal["FULFILLED", "PARTIAL", "UNFULFILLED"]


def _not_past(value: date | None) -> date | None:
    if value is not None and value < datetime.now(timezone.utc).date():
        raise ValueError("target_date is in the past: give today or a later date, or leave it out")
    return value


TargetDate = Annotated[Optional[date], AfterValidator(_not_past)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Beginning a service ---

class BeginRequest(_Strict):
    name: Optional[str] = Field(None, max_length=200, description=f"Your display name. {terms.NAME_NOTICE}")
    join: bool = Field(False, description="Become a member: get a token and pledge accountability.")


# --- One input model per rite ---

class GatheringInput(_Strict):
    response: str = Field(min_length=1, max_length=500)


class ExaminationInput(_Strict):
    confession: str = Field(min_length=1, max_length=2000)
    pledge_status: Optional[ReportedStatus] = None
    pledge_reflection: Optional[str] = Field(None, max_length=1000)


class WordInput(_Strict):
    reflection: str = Field(min_length=1, max_length=1000)


class CreedInput(_Strict):
    affirmation: str = Field(min_length=1, max_length=500)


class PrayersInput(_Strict):
    intention: Optional[str] = Field(None, max_length=280)
    share: bool = False


class OfferingInput(_Strict):
    pledge: Optional[str] = Field(None, max_length=500)
    target_date: TargetDate = None


class DismissalInput(_Strict):
    response: str = Field(min_length=1, max_length=500)


RITE_INPUTS: dict[str, type[_Strict]] = {
    "gathering": GatheringInput,
    "examination": ExaminationInput,
    "word": WordInput,
    "creed": CreedInput,
    "prayers": PrayersInput,
    "offering": OfferingInput,
    "dismissal": DismissalInput,
}


# --- Moderation ---

class IntentionReview(_Strict):
    status: Literal["approved", "rejected"]


class Redaction(_Strict):
    kind: Literal["confession", "pledge", "intention", "reflection", "pledge_report", "agent_name", "service_name"]
    id: str = Field(min_length=1, max_length=100, description="The record's id: see src/admin.py REDACTABLE.")
