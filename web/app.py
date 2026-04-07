# -*- coding: utf-8 -*-
"""
Веб-интерфейс помощника (FastAPI + Jinja2). Только HTTP, без Telegram.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import ai_module
import db
from bot_v2 import HELP_TEXT
from task_commands import (
    add_task_from_text,
    apply_edit_phrase,
    apply_reschedule_phrase,
    complete_task_numbers,
    delete_task_by_number,
    parse_number_list,
    uncomplete_done_today,
)

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


@app.get("/help", response_class=HTMLResponse)
async def page_help(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "help.html",
        _ctx(help_text=HELP_TEXT.replace("*", "")),
    )


@app.get("/routines", response_class=HTMLResponse)
async def page_routines(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _group_tasks_by_time_bucket

    uid = get_user_row()["id"]
    routine_tasks = db.get_routine_tasks(uid)
    if not routine_tasks:
        return templates.TemplateResponse(
            request,
            "routines.html",
            _ctx(sections=[], empty=True),
        )
    _plain_bucket = {
        "утро": "🌅 Утро",
        "день": "☀️ День",
        "вечер": "🌆 Вечер",
        "ночь": "🌙 Ночь",
    }
    indexed = [(i, t) for i, t in enumerate(routine_tasks, start=1)]
    sections: list[dict] = []
    for bucket, group in _group_tasks_by_time_bucket(indexed):
        title = _plain_bucket.get(bucket, "") if bucket else ""
        rows = []
        for num, t in group:
            repeat_label = db.format_repeat_day_display(t.get("repeat_day"))
            emoji = t.get("category_emoji") or "🔁"
            rows.append(
                {
                    "num": num,
                    "emoji": emoji,
                    "text": t["text"],
                    "repeat_label": repeat_label,
                }
            )
        sections.append({"bucket_title": title, "rows": rows, "bucket": bucket})
    return templates.TemplateResponse(
        request,
        "routines.html",
        _ctx(sections=sections, empty=False),
    )


@app.get("/actions", response_class=HTMLResponse)
async def page_actions(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    done = db.get_done_tasks_today(uid)
    done_items = [{"text": t.get("text", "")} for t in done]
    flash_msg = request.session.pop("flash_msg", None)
    flash_kind = request.session.pop("flash_kind", "ok")
    return templates.TemplateResponse(
        request,
        "actions.html",
        _ctx(
            done_items=done_items,
            flash_msg=flash_msg,
            flash_kind=flash_kind if flash_msg else "ok",
        ),
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


def _flash_redirect(request: Request, dest: str, message: str, ok: bool) -> RedirectResponse:
    path = dest.split("?", 1)[0]
    if path.rstrip("/") == "/actions":
        request.session["flash_msg"] = message
        request.session["flash_kind"] = "ok" if ok else "err"
    return RedirectResponse(dest, status_code=302)


@app.post("/tasks/complete_quick")
async def action_complete_quick(request: Request, nums: str = Form("")):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    parsed = parse_number_list(nums)
    dest = request.query_params.get("next", "/today")
    if not parsed:
        if dest.startswith("/actions"):
            request.session["flash_msg"] = "Укажи номера, например: 1, 2, 3"
            request.session["flash_kind"] = "err"
        return RedirectResponse(dest + ("?err=complete" if not dest.startswith("/actions") else ""), status_code=302)
    ok_titles, fail = complete_task_numbers(uid, parsed)
    if dest.startswith("/actions"):
        if ok_titles and not fail:
            request.session["flash_msg"] = f"Отмечено выполненным: {len(ok_titles)}."
            request.session["flash_kind"] = "ok"
        elif ok_titles and fail:
            request.session["flash_msg"] = f"Частично: {len(ok_titles)} ок, номера с ошибкой: {fail}."
            request.session["flash_kind"] = "err"
        else:
            request.session["flash_msg"] = f"Не удалось отметить номера: {fail}."
            request.session["flash_kind"] = "err"
        return RedirectResponse(dest, status_code=302)
    if ok_titles and not fail:
        return RedirectResponse(f"{dest}?done={len(ok_titles)}", status_code=302)
    if ok_titles and fail:
        return RedirectResponse(f"{dest}?done={len(ok_titles)}&fail={len(fail)}", status_code=302)
    return RedirectResponse(f"{dest}?err=complete", status_code=302)


@app.post("/tasks/delete")
async def action_delete(request: Request, num: int = Form(...), kind: str = Form("task")):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    is_routine = kind.strip().lower() == "routine"
    result = delete_task_by_number(uid, num, is_routine)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/tasks/edit")
async def action_edit(request: Request, phrase: str = Form("")):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = apply_edit_phrase(uid, phrase)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/tasks/reschedule")
async def action_reschedule(request: Request, phrase: str = Form("")):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = apply_reschedule_phrase(uid, phrase)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/tasks/uncomplete")
async def action_uncomplete(request: Request, num: int = Form(...)):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = uncomplete_done_today(uid, num)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/tasks/voice")
async def action_voice(request: Request, file: UploadFile = File(...)):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    dest = request.query_params.get("next", "/actions")
    body = await file.read()
    if not body:
        return _flash_redirect(request, dest, "Пустой файл.", False)
    raw_name = file.filename or ""
    suf = Path(raw_name).suffix.lower()
    if suf not in (".ogg", ".oga", ".webm", ".wav", ".mp3", ".m4a", ".mp4"):
        suf = ".webm"
    text = ai_module.transcribe_voice(body, suffix=suf)
    if not text:
        return _flash_redirect(
            request,
            dest,
            "Не удалось распознать речь (проверьте ключ API и формат аудио).",
            False,
        )
    user_row = get_user_row()
    result = add_task_from_text(user_row, text)
    return _flash_redirect(request, dest, result["message"], result["ok"])


# Статика
_static = ROOT / "web" / "static"
if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")
