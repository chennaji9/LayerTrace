"""Browser sign-in without exposing API credentials to browser storage."""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from apps.control_plane.auth import (
    create_session_cookie,
    verify_session_cookie,
    verify_session_csrf,
)
from apps.control_plane.config import get_settings

SESSION_SECONDS = 12 * 60 * 60


def _next_path(value: str | None) -> str:
    value = value or "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or "\r" in value or "\n" in value:
        return "/"
    return value


def register_session_routes(app, templates: Jinja2Templates) -> None:
    router = APIRouter(include_in_schema=False)

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        next_path = _next_path(request.query_params.get("next"))
        if verify_session_cookie(request.cookies.get("layertrace_session", "")):
            return RedirectResponse(next_path, status_code=303)
        nonce = secrets.token_urlsafe(32)
        settings = get_settings()
        response = templates.TemplateResponse(
            request,
            "login/index.html",
            {
                "page": "login", "mode": settings.mode, "lab_enabled": settings.lab_enabled,
                "login_nonce": nonce, "next_path": next_path, "login_error": None,
                "authentication_enabled": bool(settings.api_token_list),
            },
        )
        response.headers["Cache-Control"] = "no-store"
        response.set_cookie(
            "layertrace_login_nonce", nonce, max_age=600, httponly=True,
            secure=request.url.scheme == "https", samesite="strict", path="/login",
        )
        return response

    @router.post("/login", response_class=HTMLResponse)
    async def sign_in(request: Request):
        body = await request.body()
        if len(body) > 16 * 1024:
            return HTMLResponse("登录请求过大", status_code=413)
        fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        def get(name: str) -> str:
            return fields.get(name, [""])[0]

        nonce = get("login_nonce")
        cookie_nonce = request.cookies.get("layertrace_login_nonce", "")
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if (
            not nonce or not cookie_nonce or not hmac.compare_digest(nonce, cookie_nonce)
            or (origin is not None and origin != expected_origin)
        ):
            return HTMLResponse("登录页面已过期，请重新打开登录页。", status_code=403)
        token = get("token").strip()
        csrf = secrets.token_urlsafe(32)
        try:
            session_cookie = create_session_cookie(token, csrf_token=csrf)
        except (ValueError, TypeError):
            session_cookie = None
        if not session_cookie:
            settings = get_settings()
            response = templates.TemplateResponse(
                request,
                "login/index.html",
                {
                    "page": "login", "mode": settings.mode, "lab_enabled": settings.lab_enabled,
                    "login_nonce": nonce, "next_path": _next_path(get("next")),
                    "login_error": "访问令牌无效，请检查后重试。",
                    "authentication_enabled": bool(settings.api_token_list),
                },
                status_code=401,
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        response = RedirectResponse(_next_path(get("next")), status_code=303)
        response.headers["Cache-Control"] = "no-store"
        for name, value, httponly in (
            ("layertrace_session", session_cookie, True),
            ("layertrace_csrf", csrf, False),
        ):
            response.set_cookie(
                name, value, max_age=SESSION_SECONDS, httponly=httponly,
                secure=request.url.scheme == "https", samesite="lax", path="/",
            )
        response.delete_cookie("layertrace_login_nonce", path="/login")
        return response

    @router.post("/logout")
    async def sign_out(request: Request):
        csrf_header = request.headers.get("x-csrf-token", "")
        csrf_cookie = request.cookies.get("layertrace_csrf", "")
        if not (
            csrf_header and csrf_cookie and hmac.compare_digest(csrf_header, csrf_cookie)
            and verify_session_csrf(request.cookies.get("layertrace_session", ""), csrf_header)
        ):
            return HTMLResponse("会话已过期，请重新登录。", status_code=403)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("layertrace_session", path="/")
        response.delete_cookie("layertrace_csrf", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
