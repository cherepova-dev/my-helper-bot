# -*- coding: utf-8 -*-
"""
Веб-интерфейс помощника (FastAPI + Jinja2). Только HTTP, без Telegram.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import ai_module
import db
from bot_v2 import HELP_TEXT
from web.web_copy import (
    FUTURE_WEEK_VIEW,
    JOB_1_HOW,
    JOB_1_SHORT,
    JOB_1_TITLE,
    JOB_2_HOW,
    JOB_2_SHORT,
    JOB_2_TITLE,
    JOB_3_HOW,
    JOB_3_SHORT,
    JOB_3_TITLE,
    PING_HELP_HTML,
)
from categories import builtin_keywords_for_name, keywords_text_to_json
from web.report_html import report_text_to_html
from task_commands import (
    add_project_task_from_text,
    add_task_from_text,
    apply_edit_phrase,
    apply_reschedule_phrase,
    complete_task_ids,
    delete_task_by_id,
    delete_task_by_number,
    move_task_tasks_page_by_id,
    reschedule_task_by_id,
    routine_snooze_from_today_plan,
    set_task_category_by_id,
    set_task_repeat_day_by_id,
    set_task_routine_kind_by_id,
    set_task_time_bucket_by_id,
    uncomplete_done_today_by_id,
    update_task_text_by_id,
)

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "web" / "templates"))

# Не гонять transfer_overdue_tasks на каждый GET (дорого при PostgreSQL и большом списке задач).
_transfer_overdue_last: dict[int, float] = {}
_TRANSFER_OVERDUE_INTERVAL = float(os.environ.get("WEB_TRANSFER_OVERDUE_INTERVAL_SEC", "90"))


def _maybe_transfer_overdue(uid: int) -> None:
    if _TRANSFER_OVERDUE_INTERVAL <= 0:
        db.transfer_overdue_tasks(uid)
        return
    import time

    now = time.monotonic()
    last = _transfer_overdue_last.get(uid, 0.0)
    if now - last < _TRANSFER_OVERDUE_INTERVAL:
        return
    _transfer_overdue_last[uid] = now
    if len(_transfer_overdue_last) > 4000:
        _transfer_overdue_last.clear()
        _transfer_overdue_last[uid] = now
    db.transfer_overdue_tasks(uid)


def _self_ping_url_and_interval() -> tuple[str | None, int]:
    """
    Периодический GET публичного /health, чтобы хостинг чаще получал входящий трафик.
    WEB_SELF_PING_URL или RENDER_EXTERNAL_URL — база без /health.
    WEB_SELF_PING_INTERVAL_SEC: пусто → 840 с; «0» — выключить даже при URL.
    """
    base = (
        os.environ.get("WEB_SELF_PING_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    ).strip().rstrip("/")
    raw = os.environ.get("WEB_SELF_PING_INTERVAL_SEC", "").strip()
    if not base:
        return None, 0
    if raw == "0":
        return None, 0
    if raw == "":
        return base, 840
    try:
        n = int(raw)
        return (base, n) if n > 0 else (None, 0)
    except ValueError:
        return base, 840


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    base, interval = _self_ping_url_and_interval()
    task = None
    if interval > 0 and base:
        health_url = base.rstrip("/") + "/health"

        async def _ping_loop() -> None:
            import httpx

            await asyncio.sleep(12)
            async with httpx.AsyncClient() as client:
                while True:
                    try:
                        await client.get(health_url, timeout=25.0)
                    except Exception:
                        pass
                    await asyncio.sleep(interval)

        task = asyncio.create_task(_ping_loop())
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Helper Web", docs_url=None, redoc_url=None, lifespan=_app_lifespan
)

_secret = os.environ.get("WEB_SESSION_SECRET", "").strip()
if not _secret:
    _secret = "dev-insecure-change-me"
    if os.environ.get("RENDER", "").lower() == "true":
        print("WARNING: WEB_SESSION_SECRET not set; set it in production.", file=sys.stderr)

app.add_middleware(SessionMiddleware, secret_key=_secret, same_site="lax")


def _consume_flash(request: Request) -> dict:
    """Забирает одноразовое сообщение из сессии при рендере шаблона (после Session)."""
    return {
        "msg": request.session.pop("flash_msg", None),
        "kind": request.session.pop("flash_kind", "ok"),
    }


templates.env.globals["consume_flash"] = _consume_flash


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


def _ctx(**extra):
    """Контекст шаблона без request — его подставляет Jinja2Templates."""
    return extra


def _greeting_hour() -> str:
    from datetime import datetime

    h = datetime.now().hour
    if 5 <= h < 12:
        return "Доброе утро"
    if 12 <= h < 17:
        return "Добрый день"
    if 17 <= h < 23:
        return "Добрый вечер"
    return "Доброй ночи"


_FLASH_PATHS = frozenset(
    {
        "/",
        "/today",
        "/actions",
        "/tasks",
        "/routines",
        "/categories",
        "/help",
        "/reports/today",
        "/reports/week",
        "/reports/projects",
        "/projects",
        "/settings",
    }
)


def _flash_allowed(path: str) -> bool:
    if path in _FLASH_PATHS:
        return True
    if path.startswith("/projects/"):
        first = path[len("/projects/") :].strip("/").split("/")[0]
        return bool(first) and first.isdigit()
    return False


def _category_choices(uid: int) -> list[dict]:
    rows = db.get_categories(uid)
    out = [
        {"name": r["name"], "emoji": ((r.get("emoji") or "📝").strip() or "📝")}
        for r in rows
    ]
    if not any(c["name"] == "Другое" for c in out):
        out.append({"name": "Другое", "emoji": "📝"})
    return out


def _task_row_emoji(t: dict) -> str:
    if t.get("is_routine"):
        return (t.get("category_emoji") or "🔁").strip() or "🔁"
    if t.get("project_id"):
        return ((t.get("project_emoji") or "📁").strip() or "📁")
    return (t.get("category_emoji") or "📝").strip() or "📝"


def _repeat_day_codes(rd: str | None) -> list[str]:
    s = (rd or "").strip()
    if not s:
        return []
    if s.lower() == "ежедневно":
        return []
    up = s.upper()
    if up.startswith("N_DAYS:") or up.startswith("BIWEEK:"):
        return []
    return [p.strip() for p in s.lower().split(",") if p.strip()]


def _repeat_interval_for_row(rd: str | None) -> str:
    s = (rd or "").strip().upper()
    if s.startswith("N_DAYS:"):
        try:
            return str(int(s.split(":", 1)[1].strip()))
        except (ValueError, IndexError):
            return ""
    return ""


def _category_edit_rows(uid: int) -> list[dict]:
    import json

    result: list[dict] = []
    for r in db.get_categories(uid):
        kw_cell = (r.get("keywords") or "").strip()
        if kw_cell:
            try:
                kws = json.loads(kw_cell)
                kw_edit = ", ".join(str(x) for x in kws) if isinstance(kws, list) else kw_cell
            except json.JSONDecodeError:
                kw_edit = kw_cell
        else:
            hint = builtin_keywords_for_name(r["name"])
            kw_edit = ", ".join(hint) if hint else ""
        result.append(
            {
                "id": r["id"],
                "emoji": r["emoji"],
                "name": r["name"],
                "keywords_edit": kw_edit,
            }
        )
    return result


def _flash_redirect(request: Request, dest: str, message: str, ok: bool) -> RedirectResponse:
    path = dest.split("?", 1)[0].rstrip("/") or "/"
    if _flash_allowed(path):
        request.session["flash_msg"] = message
        request.session["flash_kind"] = "ok" if ok else "err"
    return RedirectResponse(dest, status_code=302)


@app.get("/", response_class=HTMLResponse)
async def page_home(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)

    user_row = get_user_row()
    uid = user_row["id"]
    _maybe_transfer_overdue(uid)
    # Без _active_tasks_display_order: на главной нужны только числа (COUNT / len «сегодня»).
    today_tasks = db.get_today_tasks(uid)
    n_today = len(today_tasks)
    n_tasks = db.count_active_tasks(uid)
    n_routines = db.count_active_routines(uid)
    n_done_today = db.count_done_tasks_today(uid)
    n_projects = db.count_user_projects(uid)
    name = (user_row.get("first_name") or "").strip() or "друг"
    return templates.TemplateResponse(
        request,
        "home.html",
        _ctx(
            greeting=_greeting_hour(),
            user_name=name,
            n_today=n_today,
            n_tasks=n_tasks,
            n_routines=n_routines,
            n_done_today=n_done_today,
            n_projects=n_projects,
            job_1_title=JOB_1_TITLE,
            job_1_short=JOB_1_SHORT,
            job_1_how=JOB_1_HOW,
            job_2_title=JOB_2_TITLE,
            job_2_short=JOB_2_SHORT,
            job_2_how=JOB_2_HOW,
            job_3_title=JOB_3_TITLE,
            job_3_short=JOB_3_SHORT,
            job_3_how=JOB_3_HOW,
        ),
    )


def _web_today_bucket_key(t: dict) -> str:
    """Три блока на вебе: утро / день / вечер (вечер + ночь + без блока)."""
    from bot_v2 import _task_time_bucket

    b = _task_time_bucket(t)
    if b == "утро":
        return "утро"
    if b == "день":
        return "день"
    return "вечер"


def _user_local_hour(tz_name: str) -> int:
    from datetime import datetime

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo((tz_name or "Europe/Moscow").strip() or "Europe/Moscow")
        return datetime.now(tz).hour
    except Exception:
        return datetime.now().hour


def _show_today_bucket(bucket: str, hour: int, n_rows: int) -> bool:
    """Скрывать пустые блоки утро/день, если соответствующее время суток уже прошло."""
    if n_rows > 0:
        return True
    if bucket == "утро":
        return hour < 12
    if bucket == "день":
        return hour < 17
    return True


@app.get("/today", response_class=HTMLResponse)
async def page_today(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _active_tasks_display_order, _format_time_human

    user_row = get_user_row()
    uid = user_row["id"]
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    local_hour = _user_local_hour(tz_name)
    _maybe_transfer_overdue(uid)
    ordered = _active_tasks_display_order(uid)
    today_tasks = db.get_today_tasks(uid)
    today_ids = {t["id"] for t in today_tasks}
    ordered_today = [(i, t) for i, t in enumerate(ordered, start=1) if t["id"] in today_ids]

    _titles = {
        "утро": "🌅 Утро",
        "день": "☀️ День",
        "вечер": "🌆 Вечер",
    }
    _order = ("утро", "день", "вечер")
    sections: list[dict] = []
    if not ordered_today:
        sections = []
    else:
        buckets: dict[str, list[tuple[int, dict]]] = {k: [] for k in _order}
        for pair in ordered_today:
            buckets[_web_today_bucket_key(pair[1])].append(pair)

        def _sort_key(p: tuple[int, dict]) -> tuple:
            t = p[1]
            ti = (t.get("due_time") or "").strip()
            return (0 if ti else 1, ti or "99:99", t.get("id", 0))

        for bucket in _order:
            pairs = sorted(buckets[bucket], key=_sort_key)
            rows = []
            for _num, t in pairs:
                emoji = _task_row_emoji(t)
                time_part = ""
                if t.get("due_time"):
                    time_part = f"в {_format_time_human(t['due_time'])}"
                pl = ""
                if t.get("project_title"):
                    pe = (t.get("project_emoji") or "📁").strip() or "📁"
                    pl = f"{pe} {t['project_title']}".strip()
                rd = (t.get("repeat_day") or "").strip()
                rows.append(
                    {
                        "emoji": emoji,
                        "text": t["text"],
                        "time_part": time_part,
                        "task_id": t["id"],
                        "is_routine": bool(t.get("is_routine")),
                        "category_name": (t.get("category_name") or "").strip(),
                        "repeat_day": rd,
                        "repeat_day_codes": _repeat_day_codes(rd),
                        "repeat_interval": _repeat_interval_for_row(rd),
                        "project_label": pl,
                        "kebab_remove_from_plan": bool(t.get("is_routine")),
                    }
                )
            if not _show_today_bucket(bucket, local_hour, len(rows)):
                continue
            sections.append(
                {
                    "bucket_title": _titles[bucket],
                    "bucket_key": bucket,
                    "rows": rows,
                    "bucket": bucket,
                }
            )

    return templates.TemplateResponse(
        request,
        "today.html",
        _ctx(
            sections=sections,
            empty=len(ordered_today) == 0,
            next_url="/today",
            category_choices=_category_choices(uid),
            kebab_hide_schedule=True,
        ),
    )


@app.get("/tasks", response_class=HTMLResponse)
async def page_tasks(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _active_tasks_display_order, _format_date_human, _format_time_human

    uid = get_user_row()["id"]
    _maybe_transfer_overdue(uid)
    tasks = _active_tasks_display_order(uid)
    numbered = list(enumerate(tasks, start=1))

    def _row_dict(num: int, t: dict) -> dict:
        emoji = _task_row_emoji(t)
        time_part = ""
        if t.get("due_time"):
            time_part = f"в {_format_time_human(t['due_time'])}"
        dd = t.get("due_date")
        date_human = ""
        if dd and not t.get("is_routine"):
            date_human = _format_date_human(dd)
        right_bits = [x for x in (time_part, date_human) if x]
        date_right = " · ".join(right_bits)
        pl = ""
        if t.get("project_title"):
            pe = (t.get("project_emoji") or "📁").strip() or "📁"
            pl = f"{pe} {t['project_title']}".strip()
        rd = (t.get("repeat_day") or "").strip()
        return {
            "task_id": t["id"],
            "emoji": emoji,
            "text": t["text"],
            "date_right": date_right,
            "is_routine": bool(t.get("is_routine")),
            "category_name": (t.get("category_name") or "").strip(),
            "repeat_day": rd,
            "repeat_day_codes": _repeat_day_codes(rd),
            "repeat_interval": _repeat_interval_for_row(rd),
            "project_label": pl,
            "has_project": bool(pl),
        }

    from collections import defaultdict

    buckets: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for num, t in numbered:
        if t.get("is_routine"):
            buckets[("routine",)].append((num, t))
        elif t.get("due_date"):
            buckets[("date", t["due_date"])].append((num, t))
        else:
            buckets[("nodate",)].append((num, t))

    date_keys = sorted((k for k in buckets if k[0] == "date"), key=lambda k: k[1])
    section_order = list(date_keys)
    if ("nodate",) in buckets:
        section_order.append(("nodate",))
    if ("routine",) in buckets:
        section_order.append(("routine",))

    task_sections: list[dict] = []
    for sk in section_order:
        if sk[0] == "date":
            title = _format_date_human(sk[1])
        elif sk[0] == "nodate":
            title = "Без срока"
        else:
            title = "Рутины"
        pairs = buckets[sk]
        if sk[0] == "date":
            sec_kind = "date"
            sec_date = sk[1]
        elif sk[0] == "nodate":
            sec_kind = "nodate"
            sec_date = ""
        else:
            sec_kind = "routine"
            sec_date = ""
        task_sections.append(
            {
                "section_title": title,
                "section_kind": sec_kind,
                "section_date": sec_date,
                "rows": [_row_dict(num, t) for num, t in pairs],
            }
        )

    return templates.TemplateResponse(
        request,
        "tasks.html",
        _ctx(
            task_sections=task_sections,
            empty=len(tasks) == 0,
            next_url="/tasks",
            category_choices=_category_choices(uid),
        ),
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
    db.attach_project_labels(uid, tasks)
    text = _format_done_report_today(tasks, tz_name)
    body_html = report_text_to_html(text)
    return templates.TemplateResponse(
        request,
        "report.html",
        _ctx(title="Сделано сегодня", body_html=body_html),
    )


@app.get("/help", response_class=HTMLResponse)
async def page_help(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "help.html",
        _ctx(
            help_text=HELP_TEXT.replace("*", ""),
            job_1_title=JOB_1_TITLE,
            job_1_short=JOB_1_SHORT,
            job_1_how=JOB_1_HOW,
            job_2_title=JOB_2_TITLE,
            job_2_short=JOB_2_SHORT,
            job_2_how=JOB_2_HOW,
            job_3_title=JOB_3_TITLE,
            job_3_short=JOB_3_SHORT,
            job_3_how=JOB_3_HOW,
            ping_help_html=PING_HELP_HTML,
            future_week_view=FUTURE_WEEK_VIEW,
        ),
    )


@app.get("/projects", response_class=HTMLResponse)
async def page_projects(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    raw = db.list_projects(uid)
    counts = db.count_active_tasks_by_project(uid)
    project_rows: list[dict] = []
    for p in raw:
        pid = int(p["id"])
        project_rows.append(
            {
                "id": pid,
                "title": (p.get("title") or "").strip(),
                "emoji": ((p.get("emoji") or "📁").strip() or "📁"),
                "n_active": counts.get(pid, 0),
            }
        )
    return templates.TemplateResponse(
        request,
        "projects.html",
        _ctx(projects=project_rows, empty=len(project_rows) == 0),
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def page_project_detail(request: Request, project_id: int):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    proj = db.get_project(uid, project_id)
    if not proj:
        return RedirectResponse("/projects", status_code=302)
    from bot_v2 import _format_date_human, _format_time_human

    _maybe_transfer_overdue(uid)
    tasks = db.get_active_tasks_for_project(uid, project_id)
    db.attach_project_labels(uid, tasks)
    rows: list[dict] = []
    for t in tasks:
        emoji = _task_row_emoji(t)
        tp = ""
        if t.get("due_time"):
            tp = f"в {_format_time_human(t['due_time'])}"
        dd = t.get("due_date")
        dh = _format_date_human(dd) if dd else ""
        date_right = " · ".join([x for x in (tp, dh) if x])
        rows.append(
            {
                "task_id": t["id"],
                "emoji": emoji,
                "text": t["text"],
                "date_right": date_right,
                "is_routine": False,
                "category_name": (t.get("category_name") or "").strip(),
                "repeat_day": "",
                "repeat_day_codes": [],
                "repeat_interval": "",
                "has_project": False,
                "project_label": "",
            }
        )
    done_tasks = db.get_done_tasks_for_project(uid, project_id)
    done_rows: list[dict] = []
    for t in done_tasks:
        ca = t.get("completed_at")
        done_label = ""
        if ca:
            raw = str(ca).replace("Z", "")
            ds = raw[:10]
            if len(ds) == 10 and ds[4] == "-":
                done_label = _format_date_human(ds)
            else:
                done_label = raw[:16]
        done_rows.append(
            {"text": (t.get("text") or "").strip(), "done_label": done_label}
        )
    next_url = f"/projects/{project_id}"
    w_start_utc, w_end_utc, w_mon, w_sun = db.user_calendar_week_bounds_utc(uid)
    n_done_week = db.count_done_tasks_in_project_between(uid, project_id, w_start_utc, w_end_utc)
    n_done_all = db.count_done_tasks_in_project_all(uid, project_id)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        _ctx(
            project=proj,
            project_id=project_id,
            rows=rows,
            empty=len(rows) == 0,
            done_rows=done_rows,
            next_url=next_url,
            category_choices=_category_choices(uid),
            project_stats={
                "active": len(rows),
                "done_week": n_done_week,
                "done_all": n_done_all,
                "week_range": f"{_format_date_human(w_mon)} — {_format_date_human(w_sun)}",
            },
        ),
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
            _ctx(
                sections=[],
                empty=True,
                next_url="/routines",
                category_choices=_category_choices(uid),
            ),
        )
    _titles = {
        "утро": "🌅 Утро",
        "день": "☀️ День",
        "вечер": "🌆 Вечер",
        "ночь": "🌙 Ночь",
        "": "◻ Без времени суток",
    }
    _fixed = ("утро", "день", "вечер", "ночь", "")
    indexed = [(i, t) for i, t in enumerate(routine_tasks, start=1)]
    grouped: dict[str, list[tuple[int, dict]]] = {}
    for bucket, group in _group_tasks_by_time_bucket(indexed):
        grouped[bucket] = group
    sections: list[dict] = []
    for bucket in _fixed:
        group = grouped.get(bucket, [])
        title = _titles.get(bucket, _titles[""])
        drop_bucket = "none" if bucket == "" else bucket
        rows = []
        for _num, t in group:
            rd = (t.get("repeat_day") or "").strip()
            repeat_label = db.format_repeat_day_display(t.get("repeat_day"))
            emoji = _task_row_emoji(t)
            rows.append(
                {
                    "task_id": t["id"],
                    "emoji": emoji,
                    "text": t["text"],
                    "repeat_label": repeat_label,
                    "is_routine": True,
                    "category_name": (t.get("category_name") or "").strip(),
                    "repeat_day": rd,
                    "repeat_day_codes": _repeat_day_codes(rd),
                    "repeat_interval": _repeat_interval_for_row(rd),
                }
            )
        sections.append(
            {
                "bucket_title": title,
                "rows": rows,
                "bucket": bucket,
                "drop_bucket": drop_bucket,
            }
        )
    return templates.TemplateResponse(
        request,
        "routines.html",
        _ctx(
            sections=sections,
            empty=False,
            next_url="/routines",
            category_choices=_category_choices(uid),
        ),
    )


@app.get("/actions", response_class=HTMLResponse)
async def page_actions(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    done = db.get_done_tasks_today(uid)
    done_items = [{"text": t.get("text", ""), "task_id": t["id"]} for t in done]
    return templates.TemplateResponse(
        request,
        "actions.html",
        _ctx(done_items=done_items),
    )


@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    s = db.get_settings(uid)
    try:
        n = int(s.get("max_tasks_per_day") or 7)
    except (TypeError, ValueError):
        n = 7
    n = max(1, min(50, n))
    return templates.TemplateResponse(
        request,
        "settings.html",
        _ctx(max_tasks_per_day=n),
    )


@app.post("/settings")
async def action_settings(request: Request, max_tasks_per_day: str = Form("7")):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    try:
        n = int(max_tasks_per_day)
    except (TypeError, ValueError):
        n = 7
    n = max(1, min(50, n))
    db.update_settings(uid, max_tasks_per_day=n)
    return _flash_redirect(request, "/settings", "Настройки сохранены.", True)


@app.get("/reports/week", response_class=HTMLResponse)
async def page_report_week(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_done_report_week

    user_row = get_user_row()
    uid = user_row["id"]
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    tasks, mon, sun, start_utc, end_utc = db.get_done_tasks_calendar_week(uid)
    db.attach_project_labels(uid, tasks)
    habits = db.routine_completion_counts_between(uid, start_utc, end_utc)
    habit_lines = [(str(r.get("text") or ""), int(r["c"])) for r in habits if r.get("c")]
    text = _format_done_report_week(
        tasks,
        tz_name,
        week_mon=mon,
        week_sun=sun,
        habit_counts=habit_lines,
    )
    body_html = report_text_to_html(text)
    return templates.TemplateResponse(
        request,
        "report.html",
        _ctx(title="Сделано за неделю", body_html=body_html),
    )


@app.get("/reports/projects", response_class=HTMLResponse)
async def page_reports_projects(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_date_human

    uid = get_user_row()["id"]
    raw = db.list_projects(uid)
    counts = db.count_active_tasks_by_project(uid)
    w_start_utc, w_end_utc, w_mon, w_sun = db.user_calendar_week_bounds_utc(uid)
    week_lbl = f"{_format_date_human(w_mon)} — {_format_date_human(w_sun)}"
    report_rows: list[dict] = []
    for p in raw:
        pid = int(p["id"])
        report_rows.append(
            {
                "id": pid,
                "title": (p.get("title") or "").strip(),
                "emoji": ((p.get("emoji") or "📁").strip() or "📁"),
                "n_active": counts.get(pid, 0),
                "n_done_week": db.count_done_tasks_in_project_between(uid, pid, w_start_utc, w_end_utc),
                "n_done_all": db.count_done_tasks_in_project_all(uid, pid),
            }
        )
    return templates.TemplateResponse(
        request,
        "reports_projects.html",
        _ctx(
            report_rows=report_rows,
            empty=len(report_rows) == 0,
            week_label=week_lbl,
        ),
    )


@app.post("/tasks/add")
async def action_add(request: Request, text: str = Form("")):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    user_row = get_user_row()
    result = add_task_from_text(user_row, text)
    dest = request.query_params.get("next", "/today")
    path = dest.split("?", 1)[0].rstrip("/") or "/"
    if _flash_allowed(path):
        return _flash_redirect(request, dest, result["message"], result["ok"])
    q = "ok=1" if result["ok"] else "err=add"
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(f"{dest}{sep}{q}", status_code=302)


@app.post("/projects/create")
async def action_project_create(
    request: Request, title: str = Form(""), emoji: str = Form("📁")
):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    em = (emoji or "📁").strip() or "📁"
    row = db.create_project(uid, title, em)
    if row:
        return _flash_redirect(request, f"/projects/{row['id']}", "Проект создан.", True)
    return _flash_redirect(request, "/projects", "Укажи название проекта.", False)


@app.post("/projects/{project_id}/update")
async def action_project_update(
    request: Request,
    project_id: int,
    title: str = Form(""),
    emoji: str = Form("📁"),
):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    dest = f"/projects/{project_id}"
    updated = db.update_project(uid, project_id, title.strip(), (emoji or "").strip())
    if updated:
        return _flash_redirect(request, dest, "Проект обновлён.", True)
    return _flash_redirect(request, dest, "Укажи название проекта.", False)


@app.post("/projects/{project_id}/add_task")
async def action_project_add_task(
    request: Request, project_id: int, text: str = Form("")
):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    user_row = get_user_row()
    result = add_project_task_from_text(user_row, project_id, text)
    dest = f"/projects/{project_id}"
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/projects/{project_id}/delete")
async def action_project_delete(request: Request, project_id: int):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    ok = db.delete_project(uid, project_id)
    msg = (
        "Проект удалён. Задачи остались в списке, но без привязки к проекту."
        if ok
        else "Не удалось удалить проект."
    )
    return _flash_redirect(request, "/projects", msg, ok)


@app.post("/tasks/complete")
async def action_complete(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    raw = form.getlist("task_id")
    ids: list[int] = []
    for x in raw:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    uid = get_user_row()["id"]
    dest = request.query_params.get("next", "/today")
    path = dest.split("?", 1)[0].rstrip("/") or "/"
    flash_dest = _flash_allowed(path)

    if not ids:
        if flash_dest:
            return _flash_redirect(request, dest, "Отметь галочками задачи в списке.", False)
        return RedirectResponse(f"{dest}?err=complete", status_code=302)
    ok_titles, fail = complete_task_ids(uid, ids)
    if flash_dest:
        if ok_titles and not fail:
            return _flash_redirect(
                request, dest, f"Отмечено выполненным: {len(ok_titles)}.", True
            )
        if ok_titles and fail:
            return _flash_redirect(
                request,
                dest,
                f"Частично: {len(ok_titles)} ок, не найдены или не отмечены: {fail}.",
                False,
            )
        return _flash_redirect(request, dest, "Не удалось отметить выбранное.", False)
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
async def action_uncomplete(request: Request, task_id: int = Form(...)):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = uncomplete_done_today_by_id(uid, task_id)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


@app.post("/tasks/delete_id")
async def action_delete_id(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/today"),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = delete_task_by_id(uid, task_id)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/routine_snooze_today")
async def action_routine_snooze_today(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/today"),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = routine_snooze_from_today_plan(uid, task_id)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/update_text")
async def action_update_text(
    request: Request,
    task_id: int = Form(...),
    text: str = Form(""),
    next: str = Form("/today"),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = update_task_text_by_id(uid, task_id, text)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/reschedule_id")
async def action_reschedule_id(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/today"),
    due_date: str = Form(""),
    preset: str = Form(""),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    preset = (preset or "").strip()
    due = (due_date or "").strip()
    if preset == "nodate":
        result = move_task_tasks_page_by_id(uid, task_id, "nodate", None)
        if _wants_json(request):
            return JSONResponse(result)
        return _flash_redirect(request, next, result["message"], result["ok"])
    if preset == "today":
        due = db.user_local_date_offset(uid, 0)
    elif preset == "tomorrow":
        due = db.user_local_date_offset(uid, 1)
    elif preset == "plus2":
        due = db.user_local_date_offset(uid, 2)
    if not due:
        err = {"ok": False, "message": "Укажи дату."}
        if _wants_json(request):
            return JSONResponse(err)
        return _flash_redirect(request, next, err["message"], False)
    result = reschedule_task_by_id(uid, task_id, due)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/drag_move")
async def action_drag_move(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/tasks"),
    mode: str = Form(...),
    bucket: str = Form(""),
    section_kind: str = Form(""),
    section_date: str = Form(""),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    mode = (mode or "").strip().lower()
    if mode == "today_bucket":
        result = set_task_time_bucket_by_id(uid, task_id, bucket)
    elif mode == "tasks_section":
        d = (section_date or "").strip() or None
        result = move_task_tasks_page_by_id(uid, task_id, section_kind, d)
    else:
        result = {"ok": False, "message": "Неизвестный режим переноса."}
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/set_category")
async def action_set_category(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/tasks"),
    category_name: str = Form(""),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = set_task_category_by_id(uid, task_id, category_name)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/set_repeat_day")
async def action_set_repeat_day(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/routines"),
    repeat_day: str = Form(""),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    result = set_task_repeat_day_by_id(uid, task_id, repeat_day)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/set_routine_kind")
async def action_set_routine_kind(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/tasks"),
    make_routine: str = Form("0"),
):
    if not request.session.get("auth"):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    flag = (make_routine or "").strip().lower() in ("1", "true", "yes", "on")
    result = set_task_routine_kind_by_id(uid, task_id, flag)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.get("/categories", response_class=HTMLResponse)
async def page_categories(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    return templates.TemplateResponse(
        request,
        "categories.html",
        _ctx(rows=_category_edit_rows(uid)),
    )


@app.post("/categories/save_row")
async def categories_save_row(
    request: Request,
    category_id: int = Form(...),
    emoji: str = Form(""),
    name: str = Form(""),
    keywords_text: str = Form(""),
):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    name = (name or "").strip()
    if not name:
        return _flash_redirect(request, "/categories", "Название не может быть пустым.", False)
    old = db.get_category_by_id(category_id, uid)
    if old and (old.get("name") or "").strip().lower() != name.lower():
        if _category_name_exists(uid, name):
            return _flash_redirect(request, "/categories", "Категория с таким именем уже есть.", False)
    kw_json = keywords_text_to_json(keywords_text)
    row = db.update_category_row(
        uid,
        category_id,
        emoji=(emoji or "📝").strip(),
        name=name,
        keywords=kw_json,
    )
    msg = "Сохранено." if row else "Не удалось сохранить."
    return _flash_redirect(request, "/categories", msg, bool(row))


@app.post("/categories/add")
async def categories_add(
    request: Request,
    emoji: str = Form("📝"),
    name: str = Form(""),
    keywords_text: str = Form(""),
):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    name = (name or "").strip()
    if not name:
        return _flash_redirect(request, "/categories", "Укажи название категории.", False)
    if _category_name_exists(uid, name):
        return _flash_redirect(request, "/categories", "Категория с таким именем уже есть.", False)
    kw_json = keywords_text_to_json(keywords_text)
    row = db.add_category_row(uid, (emoji or "📝").strip(), name, kw_json)
    return _flash_redirect(request, "/categories", "Категория добавлена." if row else "Не удалось добавить.", bool(row))


@app.post("/categories/delete")
async def categories_delete(request: Request, category_id: int = Form(...)):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row()["id"]
    ok = db.delete_category_row(uid, category_id)
    return _flash_redirect(
        request,
        "/categories",
        "Удалено." if ok else "Нельзя удалить: есть активные задачи с этой категорией или строка не найдена.",
        ok,
    )


def _category_name_exists(uid: int, name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return any((r.get("name") or "").strip().lower() == n for r in db.get_categories(uid))


@app.post("/tasks/voice")
async def action_voice(request: Request, file: UploadFile = File(...)):
    if not request.session.get("auth"):
        if "application/json" in (request.headers.get("accept") or ""):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    wants_json = "application/json" in (request.headers.get("accept") or "")
    dest = request.query_params.get("next", "/")
    body = await file.read()
    if not body:
        if wants_json:
            return JSONResponse({"ok": False, "message": "Пустой файл."})
        return _flash_redirect(request, dest, "Пустой файл.", False)
    raw_name = file.filename or ""
    suf = Path(raw_name).suffix.lower()
    if suf not in (".ogg", ".oga", ".webm", ".wav", ".mp3", ".m4a", ".mp4"):
        suf = ".webm"
    text = ai_module.transcribe_voice(body, suffix=suf)
    if not text:
        msg = "Не удалось распознать речь (проверьте ключ API и формат аудио)."
        if wants_json:
            return JSONResponse({"ok": False, "message": msg})
        return _flash_redirect(request, dest, msg, False)
    user_row = get_user_row()
    result = add_task_from_text(user_row, text)
    if wants_json:
        return JSONResponse(
            {
                "ok": result["ok"],
                "message": result["message"],
                "transcript": text if result["ok"] else None,
            }
        )
    return _flash_redirect(request, dest, result["message"], result["ok"])


# Статика
_static = ROOT / "web" / "static"
if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")
