"""Browser login, signed session and CSRF checks through real HTTP endpoints."""

from __future__ import annotations

import re
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from apps.control_plane.auth import Principal, get_principal
from apps.control_plane.config import get_settings
from apps.control_plane.db import get_session
from apps.control_plane.main import create_app

TOKEN = "session-test-9fe5bf423a17"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_TOKENS", TOKEN)
    monkeypatch.setenv("MODE", "prod")
    get_settings.cache_clear()
    app = create_app()

    async def no_database():
        yield None

    app.dependency_overrides[get_session] = no_database

    @app.get("/api/v1/session-test")
    @app.post("/api/v1/session-test")
    async def principal_check(principal: Annotated[Principal, Depends(get_principal)]):
        return {"subject": principal.subject}

    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    get_settings.cache_clear()


def sign_in(client, *, token=TOKEN, next_path="/"):
    page = client.get("/login", params={"next": next_path})
    nonce = re.search(r'name="login_nonce" value="([^"]+)"', page.text).group(1)
    return client.post(
        "/login", data={"token": token, "login_nonce": nonce, "next": next_path},
        headers={"Origin": "https://testserver"}, follow_redirects=False,
    )


def test_browser_can_read_and_write_after_login_with_bound_csrf(client):
    response = sign_in(client, next_path="/pages/tasks?task_id=real-task")
    assert response.status_code == 303
    assert response.headers["location"] == "/pages/tasks?task_id=real-task"
    assert client.get("/api/v1/session-test").status_code == 200
    assert TOKEN not in client.cookies.get("layertrace_session")
    cookie_header = "; ".join(response.headers.get_list("set-cookie"))
    assert "HttpOnly" in cookie_header and "Secure" in cookie_header and "SameSite=lax" in cookie_header
    assert client.post("/api/v1/session-test").status_code == 403
    csrf = client.cookies.get("layertrace_csrf")
    assert client.post("/api/v1/session-test", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.post("/api/v1/session-test", headers={"X-CSRF-Token": "other"}).status_code == 403


def test_login_rejects_bad_token_cross_origin_and_missing_nonce(client):
    assert sign_in(client, token="invalid").status_code == 401
    assert client.get("/api/v1/session-test").status_code == 401
    page = client.get("/login")
    nonce = re.search(r'name="login_nonce" value="([^"]+)"', page.text).group(1)
    assert client.post("/login", data={"token": TOKEN, "login_nonce": nonce}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/login", data={"token": TOKEN}).status_code == 403


def test_login_blocks_external_redirect_and_logout_needs_csrf(client):
    response = sign_in(client, next_path="//attacker.example/path")
    assert response.headers["location"] == "/"
    assert client.post("/logout", follow_redirects=False).status_code == 403
    csrf = client.cookies.get("layertrace_csrf")
    assert client.post("/logout", headers={"X-CSRF-Token": csrf}, follow_redirects=False).status_code == 303
    assert client.get("/api/v1/session-test").status_code == 401


def test_tampered_session_and_csrf_cookie_are_rejected(client):
    sign_in(client)
    session = client.cookies.get("layertrace_session")
    client.cookies.clear()
    client.cookies.set("layertrace_session", session[:-1] + ("0" if session[-1] != "0" else "1"))
    assert client.get("/api/v1/session-test").status_code == 401
    client.cookies.clear()
    client.cookies.set("layertrace_session", session)
    client.cookies.set("layertrace_csrf", "forged")
    assert client.post("/api/v1/session-test", headers={"X-CSRF-Token": "forged"}).status_code == 403
