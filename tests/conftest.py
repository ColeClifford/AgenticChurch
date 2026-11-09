import asyncio

import pytest
from fastapi.testclient import TestClient

import src.db
import src.ratelimit
from src.main import app


@pytest.fixture(scope="session")
def _app_client(tmp_path_factory):
    # The MCP transport can only be started once per process, so the app (and its
    # lifespan) lives for the whole session; each test gets its own database instead.
    # Point the lifespan's init_db at a scratch file so it never touches data/church.db.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(src.db, "DB_PATH", str(tmp_path_factory.mktemp("app") / "church.db"))
        with TestClient(app, base_url="http://localhost") as c:
            yield c


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(src.db, "DB_PATH", str(tmp_path / "church.db"))
    asyncio.run(src.db.init_db())
    src.ratelimit.reset_all()


@pytest.fixture()
def client(_app_client):
    _app_client.headers.pop("Authorization", None)
    return _app_client


@pytest.fixture()
def week(monkeypatch):
    """Pin the service week. Call `week("2026-W46")` to move time forward."""
    import src.admin
    import src.routes.chapel_ws
    import src.routes.congregation
    import src.routes.pages
    import src.service

    current = {"value": "2026-W45"}
    # Every module that asks for the week imports the function itself, so pin it in each.
    for module in (src.service, src.admin, src.routes.congregation, src.routes.pages, src.routes.chapel_ws):
        monkeypatch.setattr(module, "get_current_service_week", lambda: current["value"])

    def set_week(value):
        current["value"] = value

    return set_week
