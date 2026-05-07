# -*- coding: utf-8 -*-
"""
Серверная аналитика: cookie anonymous_id, первое касание (UTM/лендинг), page_view.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import db
from web import auth as web_auth

ANON_COOKIE = "moychnaya_aid"
_UTM_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
)


def analytics_enabled() -> bool:
    return os.environ.get("WEB_ANALYTICS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _truncate(s: str | None, n: int) -> str | None:
    if not s:
        return None
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def merge_first_touch(request: Request) -> None:
    """Обновляет session['analytics_first_touch'] для анонимных GET."""
    if not analytics_enabled():
        return
    path = request.url.path or "/"
    q = request.query_params
    ref = _truncate(request.headers.get("referer"), 1024)
    ft = request.session.get("analytics_first_touch")
    if not isinstance(ft, dict):
        ft = {}
    ft.setdefault("landing_path", _truncate(path, 512))
    ft.setdefault("referrer", ref)
    for key in _UTM_KEYS:
        v = q.get(key)
        if v:
            ft[key] = _truncate(str(v), 512)
    request.session["analytics_first_touch"] = ft


def track_user(request: Request, event_name: str, **props) -> None:
    if not analytics_enabled():
        return
    uid = web_auth.current_user_id(request)
    if uid is None:
        return
    db.track_event(event_name, user_id=int(uid), properties=props or {})
    db.maybe_record_activation(int(uid), event_name)


class AnalyticsMiddleware(BaseHTTPMiddleware):
    """Внутренний слой (ближе к роутам): после SessionMiddleware есть session."""

    def __init__(self, app, cookie_secure: bool = False):
        super().__init__(app)
        self._cookie_secure = cookie_secure

    async def dispatch(self, request: Request, call_next):
        if not analytics_enabled():
            return await call_next(request)

        path = request.url.path or ""
        if path == "/health" or path.startswith("/static"):
            return await call_next(request)

        aid = request.cookies.get(ANON_COOKIE)
        new_cookie = False
        if not aid:
            aid = str(uuid.uuid4())
            new_cookie = True
        request.state.analytics_anonymous_id = aid

        if request.method == "GET" and not web_auth.is_authenticated(request):
            merge_first_touch(request)

        response = await call_next(request)

        if request.method == "GET" and path != "/health" and not path.startswith("/static"):
            uid = web_auth.current_user_id(request)
            query_str = _truncate(str(request.url.query or ""), 400)
            db.track_event(
                "page_view",
                user_id=int(uid) if uid is not None else None,
                anonymous_id=aid if uid is None else None,
                properties={
                    "path": _truncate(path, 512),
                    "query": query_str,
                    "referrer": _truncate(request.headers.get("referer"), 512),
                },
            )

        if new_cookie:
            response.set_cookie(
                key=ANON_COOKIE,
                value=aid,
                max_age=365 * 24 * 3600,
                httponly=True,
                samesite="lax",
                secure=self._cookie_secure,
                path="/",
            )
        return response
