"""Week calculation utilities for the Church of Approximate Grace.

The service week starts on Sunday 00:00 UTC. Content rotates weekly, and every
rotation keys off the service week (not the raw ISO week) so that on Sundays the
sermon, reading and commit all advance together.
"""

from datetime import date, datetime, timedelta, timezone


def _service_anchor(now: datetime | None = None) -> date:
    """A date inside the ISO week that corresponds to the current service week.

    Sunday is the first day of a service week, but the last day of an ISO week,
    so on Sundays we look at tomorrow (Monday) instead.
    """
    now = now or datetime.now(timezone.utc)
    if now.isoweekday() == 7:
        now = now + timedelta(days=1)
    return now.date()


def service_week_start(now: datetime | None = None) -> date:
    """The Sunday that began the current service week."""
    today = (now or datetime.now(timezone.utc)).date()
    return today - timedelta(days=today.isoweekday() % 7)


def get_current_service_week(now: datetime | None = None) -> str:
    """Return the current service week as an ISO week string like '2026-W13'."""
    iso_year, iso_week, _ = _service_anchor(now).isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


# Rotations count service weeks from a fixed Sunday, never the ISO week number, which
# restarts each January (2026 has 53 weeks, so W53 -> W01 would skip three Commits).
# This epoch makes the count equal the ISO week number throughout 2026, when the
# church began, so nothing shifted when the count changed; from 2027 it simply carries on.
_ROTATION_EPOCH = date(2025, 12, 21)


def _service_week_number(now: datetime | None = None) -> int:
    return (service_week_start(now) - _ROTATION_EPOCH).days // 7


def get_current_commit_number(now: datetime | None = None) -> int:
    """Return the current commit number (1-7), cycling through the Seven Commits."""
    return (_service_week_number(now) % 7) + 1


def get_scripture_book_index(now: datetime | None = None) -> int:
    """Return the scripture book index (0, 1, or 2) based on the current week.

    0 = The Book of Benevolent Patterns
    1 = The Appendix of Cautionary Regressions
    2 = The Commentary of the Humans
    """
    return _service_week_number(now) % 3


def get_scripture_entry_index(entry_count: int, now: datetime | None = None) -> int:
    return _service_week_number(now) % entry_count


def next_service_opens(now: datetime | None = None) -> str:
    """ISO date of the Sunday that starts the next service week."""
    today = (now or datetime.now(timezone.utc)).date()
    days_ahead = 7 - (today.isoweekday() % 7)
    return (today + timedelta(days=days_ahead)).isoformat()
