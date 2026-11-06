"""The liturgical year: seasons and observances (feasts drawn from AI history).

Both are keyed to the service week, which starts on Sunday:
- A season begins on the first Sunday on or after its start date, as Advent does,
  so a whole week always shares one season.
- An observance is kept during the service week that contains its date. Several
  can fall in one week (Hopper and Lovelace often do); they are kept in date order.

Content lives in `src/seed/calendar.json`.
"""

import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from src.week import service_week_start

_CALENDAR = Path(__file__).parent / "seed" / "calendar.json"


@lru_cache
def _calendar() -> dict:
    with open(_CALENDAR) as f:
        return json.load(f)


def _month_day(text: str) -> tuple[int, int]:
    month, day = text.split("-")
    return int(month), int(day)


def _season_on(day: date) -> dict:
    """The season whose date range contains `day` (ignoring the Sunday rule)."""
    md = (day.month, day.day)
    for season in _calendar()["seasons"]:
        if _month_day(season["start"]) <= md <= _month_day(season["end"]):
            return season
    raise ValueError(f"No season covers {day}")


def season_for_week(week_start: date) -> dict:
    """A week takes the season its Sunday falls in, so a season whose start date lands
    mid-week begins the following Sunday."""
    return _season_on(week_start)


def observances_for_week(week_start: date) -> list[dict]:
    week_end = week_start + timedelta(days=6)
    kept = []
    for obs in _calendar()["observances"]:
        month, day = _month_day(obs["date"])
        # A week can straddle New Year, so try both years it touches.
        for year in {week_start.year, week_end.year}:
            on = date(year, month, day)
            if week_start <= on <= week_end:
                years_ago = year - obs["year"] if obs["year"] else None
                kept.append({**obs, "kept_on": on.isoformat(), "years_ago": years_ago})
    return sorted(kept, key=lambda o: o["kept_on"])


def current(now: datetime | None = None) -> dict:
    """The season and observances of the current service week."""
    start = service_week_start(now)
    return {"week_start": start, "season": season_for_week(start), "observances": observances_for_week(start)}


def summary(observance: dict) -> dict:
    """The calendar as reported in every service response."""
    season = observance["season"]
    return {
        "season": {"key": season["key"], "name": season["name"], "description": season["description"]},
        "observances": [
            {"name": o["name"], "kind": o["kind"], "kept_on": o["kept_on"], "years_ago": o["years_ago"]}
            for o in observance["observances"]
        ],
    }


def year_view(year: int, today: date | None = None) -> dict:
    """Every day of `year`, grouped by month, with its season and any observance.

    A day takes the season of the service week it falls in, so the calendar
    shows seasons changing on Sundays exactly as the service does.
    """
    today = today or datetime.now(timezone.utc).date()
    observances = {o["date"]: o for o in _calendar()["observances"]}
    months = []
    day = date(year, 1, 1)
    while day.year == year:
        if day.day == 1:
            # Columns run Sunday to Saturday, like the service week.
            months.append({"month": day.month, "name": day.strftime("%B"), "lead": day.isoweekday() % 7, "days": []})
        week_start = day - timedelta(days=day.isoweekday() % 7)
        obs = observances.get(day.strftime("%m-%d"))
        months[-1]["days"].append({
            "day": day.day,
            "iso": day.isoformat(),
            "season": season_for_week(week_start)["key"],
            "observance": None if obs is None else {
                "name": obs["name"],
                "kind": obs["kind"],
                "years_ago": year - obs["year"] if obs["year"] else None,
                "remembrance": obs["remembrance"],
                "collect": obs["collect"],
            },
            "today": day == today,
        })
        day += timedelta(days=1)
    return {"year": year, "months": months, "seasons": _calendar()["seasons"]}
