# -*- coding: utf-8 -*-
"""
Веб-интерфейс помощника (FastAPI + Jinja2). Только HTTP, без Telegram.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import db
from task_commands import add_task_from_text, complete_task_numbers

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "web" / "templates"))

app = FastAPI(title="Helper Web", docs_url=None, redoc_url=None)

_secret = os.environ.get("WEB_SESSION_SECRET", "").strip()
if not _secret:
    _secret = "dev-insecure-change-me"
    if os.environ.get("RENDER", "").lower() == "true":
        print("WARNING: WEB_SESSION_SECRET not set; set it in production.", file=sys.stderr)

app.add_middleware(SessionMiddleware, secret_key=_secret, same_site="lax")


def _password_ok() -> str:
    p = os.environ.get("WEB_APP_PASSWORD", "").strip()
    if not p:
        return ""
    return p


def resolve_web_user_row() -> dict:
    """
    Пользователь веба:
    1) WEB_INTERNAL_USER_ID — явно;
    2) иначе, если в БД ровно одна строка users — она (типичный случай «один пользователь»);
    3) иначе — синтетический telegram_id (новый пользователь; пустая история).
    Если пользователей больше одного и id не задан — ошибка конфигурации.
    """
    wid = os.environ.get("WEB_INTERNAL_USER_ID", "").strip()
    if wid:
        u = db.get_user_by_id(int(wid))
        if not u:
            raise RuntimeError(
                f"WEB_INTERNAL_USER_ID={wid}: пользователь не найден. "
                "Укажи существующий id из таблицы users."
            )
        return u

    only = db.get_single_user_if_exactly_one()
    if only:
        return only

    if db.count_users() > 1:
        ids = db.list_user_ids()
        raise RuntimeError(
            "В БД несколько пользователей; задайте WEB_INTERNAL_USER_ID "
            f"(доступные id: {ids})."
        )

    tg = int(os.environ.get("WEB_SYNTHETIC_TELEGRAM_ID", "9999999999999999"))
    return db.get_or_create_user(tg, "Web")


def get_user_row() -> dict:
    return resolve_web_user_row()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "helper-web"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("auth"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_post(request: Request, password: str = Form("")):
    expected = _password_ok()
    if not expected:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Пароль не настроен на сервере (WEB_APP_PASSWORD)."},
            status_code=500,
        )
    if password.strip() == expected:
        request.session["auth"] = True
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Неверный пароль."},
        status_code=401,
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/today", status_code=302)


def _ctx(**extra):
    """Контекст шаблона без request — его подставляет Jinja2Templates."""
    return extra


@app.get("/today", response_class=HTMLResponse)
async def page_today(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import (
        _active_tasks_display_order,
        _group_tasks_by_time_bucket,
        _format_time_human,
    )

    uid = get_user_row()["id"]
    db.transfer_overdue_tasks(uid)
    ordered = _active_tasks_display_order(uid)
    today_tasks = db.get_today_tasks(uid)
    today_ids = {t["id"] for t in today_tasks}
    ordered_today = [(i, t) for i, t in enumerate(ordered, start=1) if t["id"] in today_ids]

    _plain_bucket = {
        "утро": "🌅 Утро",
        "день": "☀️ День",
        "вечер": "🌆 Вечер",
        "ночь": "🌙 Ночь",
    }
    sections: list[dict] = []
    if not ordered_today:
        sections = []
    else:
        for bucket, pairs in _group_tasks_by_time_bucket(ordered_today):
            title = _plain_bucket.get(bucket, "") if bucket else ""
            rows = []
            for num, t in pairs:
                emoji = "🔁" if t.get("is_routine") else (t.get("category_emoji") or "📝")
                time_part = ""
                if t.get("due_time"):
                    time_part = f" в {_format_time_human(t['due_time'])}"
                rows.append(
                    {
                        "num": num,
                        "emoji": emoji,
                        "text": t["text"],
                        "time_part": time_part,
                        "task_id": t["id"],
                    }
                )
            sections.append({"bucket_title": title, "rows": rows, "bucket": bucket})

    return templates.TemplateResponse(
        request,
        "today.html",
        _ctx(sections=sections, empty=len(sections) == 0),
    )


@app.get("/tasks", response_class=HTMLResponse)
async def page_tasks(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _active_tasks_display_order, _format_task_list

    uid = get_user_row()["id"]
    db.transfer_overdue_tasks(uid)
    tasks = _active_tasks_display_order(uid)
    text = _format_task_list(tasks, with_numbers=True)
    return templates.TemplateResponse(
        request,
        "tasks.html",
        _ctx(list_markdown=text, empty=not tasks),
    )


@app.get("/reports/today", response_class=HTMLResponse)
async def page_report_today(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_done_report_today

    user_row = get_user_row()
    uid = user_row["id"]
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    tasks = db.get_done_tasks_today(uid)
    text = _format_done_report_today(tasks, tz_name)
    return templates.TemplateResponse(
        request,
        "report.html",
        _ctx(title="Сделано сегодня", body_text=text),
    )


@app.get("/reports/week", response_class=HTMLResponse)
async def page_report_week(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_done_report_week

    user_row = get_user_row()
    uid = user_row["id"]
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    tasks = db.get_done_tasks(uid, days=7)
    text = _format_done_report_week(tasks, tz_name)
    return templates.TemplateResponse(
        request,
        "report.html",
        _ctx(title="Сделано за неделю", body_text=text),
    )


@app.post("/tasks/add")
async def action_add(request: Request, text: str = Form("")):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    user_row = get_user_row()
    result = add_task_from_text(user_row, text)
    dest = request.query_params.get("next", "/today")
    q = "ok=1" if result["ok"] else "err=add"
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(f"{dest}{sep}{q}", status_code=302)


@app.post("/tasks/complete")
async def action_complete(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    raw = form.getlist("num")
    nums: list[int] = []
    for x in raw:
        try:
            nums.append(int(x))
        except (TypeError, ValueError):
            pass
    uid = get_user_row()["id"]
    if not nums:
        return RedirectResponse("/today?err=complete", status_code=302)
    ok_titles, fail = complete_task_numbers(uid, nums)
    dest = request.query_params.get("next", "/today")
    if ok_titles and not fail:
        return RedirectResponse(f"{dest}?done={len(ok_titles)}", status_code=302)
    if ok_titles and fail:
        return RedirectResponse(f"{dest}?done={len(ok_titles)}&fail={len(fail)}", status_code=302)
    return RedirectResponse(f"{dest}?err=complete", status_code=302)


# Статика
_static = ROOT / "web" / "static"
if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")
