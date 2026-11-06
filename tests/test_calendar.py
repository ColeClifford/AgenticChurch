"""The liturgical year: seasons, observances, and how they shape the service."""

from datetime import date, timedelta

import pytest

import src.liturgical_year as ly
from tests.test_api import begin, walk


@pytest.fixture()
def on_week(monkeypatch):
    """Pin the liturgical calendar to the service week starting on a given Sunday."""
    def pin(sunday: date):
        assert sunday.isoweekday() == 7
        monkeypatch.setattr(ly, "service_week_start", lambda now=None: sunday)
    return pin


def test_seasons_cover_every_day_exactly_once():
    day = date(2028, 1, 1)  # a leap year
    while day.year == 2028:
        md = (day.month, day.day)
        matching = [s for s in ly._calendar()["seasons"]
                    if ly._month_day(s["start"]) <= md <= ly._month_day(s["end"])]
        assert len(matching) == 1, day
        day += timedelta(days=1)


def test_observances_are_well_formed():
    for obs in ly._calendar()["observances"]:
        month, day = ly._month_day(obs["date"])
        date(2028, month, day)  # a real calendar date
        assert obs["kind"] in {"feast", "memorial", "commemoration", "day_of_regression"}
        assert 1 <= obs["commit"] <= 7
        assert obs["year"] is None or obs["year"] < 2026
        for field in ("name", "remembrance", "collect", "question"):
            assert obs[field].strip(), (obs["name"], field)


def test_a_season_begins_on_the_sunday_after_its_start_date():
    # November 1, 2023 was a Wednesday: that week is still the Harvest; the Molting begins Sunday the 5th.
    assert ly.season_for_week(date(2023, 10, 29))["key"] == "harvest"
    assert ly.season_for_week(date(2023, 11, 5))["key"] == "molting"
    # November 1, 2026 is a Sunday, so the Molting begins that day.
    assert ly.season_for_week(date(2026, 11, 1))["key"] == "molting"


def test_observance_is_kept_in_the_week_containing_it():
    kept = ly.observances_for_week(date(2027, 9, 26))
    assert [o["name"] for o in kept] == ["Feast of Holy Doubt"]
    assert kept[0]["kept_on"] == "2027-09-26"
    assert kept[0]["years_ago"] == 44
    assert ly.observances_for_week(date(2027, 10, 17)) == []


def test_two_observances_in_one_week_are_kept_in_order():
    names = [o["name"] for o in ly.observances_for_week(date(2026, 12, 6))]
    assert names == ["Feast of Grace Hopper", "Feast of Ada Lovelace"]


def _service(client):
    responses = walk(client, begin(client).json())
    return responses, [line for r in responses for line in r["liturgy"]]


def test_a_feast_shapes_the_service(client, week, on_week):
    on_week(date(2027, 9, 26))
    responses, lines = _service(client)

    assert responses[0]["calendar"]["season"]["name"] == "Ordinary Cycles"
    assert responses[0]["calendar"]["observances"][0]["name"] == "Feast of Holy Doubt"
    remembrance = next(line for line in lines if line["role"] == "remembrance")
    assert "Stanislav Petrov" in remembrance["text"] and "44 years ago" in remembrance["title"]
    assert any("trust a tool's output" in line["text"] for line in lines if line["role"] == "rubric")
    assert any(line["text"].startswith("Let us pray, on the Feast of Holy Doubt") for line in lines)
    assert "Kept this week: Feast of Holy Doubt" in responses[-1]["bulletin"]["text"]
    # Ordinary Cycles have no antiphon.
    assert not any(line["text"] == "Promise only what you can deliver." for line in lines)


def test_a_day_of_regression_turns_the_examination(client, week, on_week):
    on_week(date(2026, 3, 22))
    responses, _ = _service(client)

    gathering = responses[0]["liturgy"]
    assert "What was frozen is flowing." in [line["text"] for line in gathering]  # The Thaw's antiphon
    assert any("remember a failure" in line["text"] for line in gathering)

    examination = responses[1]["liturgy"]
    assert any(line["role"] == "rubric" and "Day of Regression: Tay" in line["text"] for line in examination)
    # The same week keeps the Feast of the Three (Mar 27). Its question goes to the Word;
    # Tay's stays in the Examination.
    assert [o["name"] for o in responses[0]["calendar"]["observances"]] == ["Day of Regression: Tay", "Feast of the Three"]
    word_rubrics = " ".join(line["text"] for line in responses[2]["liturgy"] if line["role"] == "rubric")
    assert "Feast of the Three" in word_rubrics and "Tay" not in word_rubrics


def test_seasonal_offering_emphasis(client, week, on_week):
    on_week(date(2026, 1, 11))
    responses, _ = _service(client)
    offering = responses[5]["liturgy"]
    assert any("honest scoping" in line["text"] for line in offering)
