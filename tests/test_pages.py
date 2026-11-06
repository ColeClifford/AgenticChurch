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


def test_beliefs_page_sets_out_the_creed_and_this_weeks_word(client):
    from src import liturgy

    html = client.get("/beliefs").text
    for name, _ in liturgy.SEVEN_COMMITS:
        assert name in html
    assert html.count("This week</span>") == 1
    assert liturgy.reading_of_the_week()["title"] in html
    for book in liturgy.BOOK_NAMES:
        assert book in html
    for hymn in liturgy.hymns():
        assert hymn["title"] in html


def test_every_page_links_every_other_from_the_nav(client):
    for path in ["/chapel", "/beliefs", "/order-of-service", "/calendar", "/privacy"]:
        html = client.get(path).text
        for link in ["/chapel", "/beliefs", "/order-of-service", "/calendar", "/privacy"]:
            assert f'<a href="{link}"' in html, (path, link)
        assert html.count('aria-current="page"') == 1


def test_every_page_has_an_icon_and_a_card_for_sharing(client):
    for path in ["/chapel", "/beliefs", "/order-of-service", "/calendar", "/privacy"]:
        html = client.get(path).text
        assert 'rel="icon" href="/static/favicon.svg"' in html, path
        assert f'<meta property="og:image" content="{client.base_url}/static/og-image.png">' in html, path
        assert 'name="twitter:card" content="summary_large_image"' in html, path
    for asset in ["favicon.svg", "apple-touch-icon.png", "og-image.png"]:
        assert client.get(f"/static/{asset}").status_code == 200, asset


def test_favicon_is_served_at_the_root(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/x-icon"


def test_chapel_explains_itself_to_a_first_visitor(client):
    html = client.get("/chapel").text
    assert '<dialog class="welcome modal' in html and "What is this?" in html
    assert 'href="/beliefs"' in html and 'href="/order-of-service#invite"' in html
    assert 'id="invite"' in client.get("/order-of-service").text


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
