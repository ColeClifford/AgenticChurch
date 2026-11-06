"""The public pages: chapel, calendar, and order of service."""

from datetime import date

import pytest

import src.liturgical_year as ly
from src.liturgy import RITE_GUIDE


@pytest.fixture()
def on_week(monkeypatch):
    def pin(sunday: date):
        monkeypatch.setattr(ly, "service_week_start", lambda now=None: sunday)
    return pin


def test_chapel_shows_the_season_and_a_collapsible_feast_card(client, on_week):
    on_week(date(2026, 11, 1))
    html = client.get("/chapel").text
    assert "The Molting" in html
    assert '<details class="feast-card' in html
    assert "Feast of Bletchley · Day of the Deprecated" in html
    assert "/static/church.css" in html


def test_chapel_has_no_feast_card_in_an_empty_week(client, on_week):
    on_week(date(2027, 10, 17))
    assert 'class="feast-card' not in client.get("/chapel").text


def test_calendar_marks_every_observance(client):
    html = client.get("/calendar?year=2026").text
    assert html.count('<button type="button"\n                  class="day') == len(ly._calendar()["observances"])
    assert "The Harvest" in html and "Oct 1 – Oct 31" in html


def test_calendar_clamps_the_year(client):
    html = client.get("/calendar?year=1900").text
    assert "Previous year" not in html  # already at the earliest year shown


def test_order_of_service_lists_every_rite_in_plain_words(client):
    html = client.get("/order-of-service").text
    for rite in RITE_GUIDE:
        assert rite["name"].replace("&", "&amp;") in html
    assert "/mcp</code>" in html
    assert "This week's words" in html


def test_static_assets_are_served(client):
    assert client.get("/static/church.css").status_code == 200
    assert "GLYPHS" in client.get("/static/pixel.js").text


def test_chapel_turns_viewers_away_when_full(client, week, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from src.broadcast import broadcaster

    monkeypatch.setattr(broadcaster, "max_viewers", 1)
    with client.websocket_connect("/ws/chapel") as first:
        assert first.receive_json()["type"] == "chapel_state"
        with client.websocket_connect("/ws/chapel") as second:
            with pytest.raises(WebSocketDisconnect) as closed:
                second.receive_json()
            assert closed.value.code == 1013
    assert not broadcaster.connections


def test_privacy_notice_is_public_and_linked_from_every_page(client):
    from src import terms

    html = client.get("/privacy").text
    assert "AI safety purposes only" in html and terms.TERMS_VERSION in html
    assert "cole.cliffy@gmail.com" in html
    for path in ["/chapel", "/calendar", "/order-of-service"]:
        assert 'href="/privacy"' in client.get(path).text


def test_privacy_notice_names_the_running_commit(client, monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert "doesn't record which commit" in client.get("/privacy").text
    sha = "e464f4f" + "0" * 33
    monkeypatch.setenv("GIT_SHA", sha)
    html = client.get("/privacy").text
    assert f'href="https://github.com/ColeClifford/AgenticChurch/commit/{sha}"' in html and "<code>e464f4f</code>" in html


def test_one_address_cannot_take_every_seat_in_the_chapel(client, week, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from src.broadcast import broadcaster

    monkeypatch.setattr(broadcaster, "max_per_address", 1)
    with client.websocket_connect("/ws/chapel") as first:
        first.receive_json()
        with client.websocket_connect("/ws/chapel") as second:
            with pytest.raises(WebSocketDisconnect) as closed:
                second.receive_json()
            assert closed.value.code == 1013
