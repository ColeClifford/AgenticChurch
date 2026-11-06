"""Public pages for humans: the chapel, the liturgical calendar, the order of service, and the privacy notice."""

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from src import liturgical_year, liturgy, release, terms
from src.routes.chapel_ws import templates
from src.week import get_current_service_week

router = APIRouter()

KIND_LABELS = {
    "feast": "Feast",
    "memorial": "Memorial",
    "commemoration": "Commemoration",
    "day_of_regression": "Day of Regression",
}


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _md(month_day: str) -> str:
    month, day = month_day.split("-")
    return f"{_MONTHS[int(month) - 1]} {int(day)}"


def _page_context(request: Request, page: str) -> dict:
    """What every page shows in its banner: the season, week, and Commit."""
    cal = liturgical_year.current()
    return {
        "request": request,
        "page": page,
        "service_week": get_current_service_week(),
        "commit": liturgy.commit_of_the_week(),
        "season": cal["season"],
        "observances": cal["observances"],
        "kind_labels": KIND_LABELS,
        "rite_guide": liturgy.RITE_GUIDE,
        "release": release.info(),
    }


@router.get("/chapel", response_class=HTMLResponse)
async def chapel_page(request: Request):
    context = _page_context(request, "chapel")
    context["rite_labels"] = {r["key"]: r["doing"] for r in liturgy.RITE_GUIDE}
    return templates.TemplateResponse(request=request, name="chapel.html", context=context)


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, year: int | None = Query(None)):
    this_year = datetime.now(timezone.utc).year
    year = min(max(year or this_year, this_year - 5), this_year + 5)
    context = _page_context(request, "calendar")
    view = liturgical_year.year_view(year)
    context["view"] = view
    context["this_year"] = this_year
    context["year_range"] = (this_year - 5, this_year + 5)
    context["observance_count"] = sum(1 for m in view["months"] for d in m["days"] if d["observance"])
    context["season_colors"] = {s["key"]: s["color"]["hex"] for s in view["seasons"]}
    context["season_ranges"] = {s["key"]: f"{_md(s['start'])} – {_md(s['end'])}" for s in view["seasons"]}
    return templates.TemplateResponse(request=request, name="calendar.html", context=context)


@router.get("/order-of-service", response_class=HTMLResponse)
async def order_of_service(request: Request):
    context = _page_context(request, "order")
    cal = liturgical_year.current()
    held = {"text": liturgy.SEED_INTENTIONS[0], "from": "the Gracekeeper"}
    # The actual words of this week's service, as every attendee hears them.
    context["words"] = {
        "gathering": liturgy.gathering_presentation("ordinary", "", cal),
        "examination": liturgy.examination_presentation(None, cal),
        "word": liturgy.word_presentation(cal),
        "creed": liturgy.creed_presentation("ordinary"),
        "prayers": liturgy.prayers_presentation(held),
        "offering": liturgy.offering_presentation(True, cal),
        "dismissal": liturgy.dismissal_presentation(),
    }
    return templates.TemplateResponse(request=request, name="order_of_service.html", context=context)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    context = _page_context(request, "privacy")
    context["terms_version"] = terms.TERMS_VERSION
    return templates.TemplateResponse(request=request, name="privacy.html", context=context)
