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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

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
from web import auth as web_auth
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
    set_task_color_by_id,
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
    # Render free tier засыпает после ~15 минут неактивности; 240 секунд
    # надёжно держит сервис «горячим», не создавая значимой нагрузки.
    if raw == "":
        return base, 240
    try:
        n = int(raw)
        return (base, n) if n > 0 else (None, 0)
    except ValueError:
        return base, 240


def _bootstrap_password_from_env() -> None:
    """
    Одноразовая установка email/пароля существующему пользователю через
    переменные окружения (для случаев, когда нет SSH/Shell к продакшну).

    Включается, если заданы WEB_BOOTSTRAP_EMAIL и WEB_BOOTSTRAP_PASSWORD.
    Применяется только если в БД РОВНО ОДИН пользователь, у которого
    ещё нет password_hash (либо WEB_BOOTSTRAP_FORCE=1 — тогда перезапишет
    в любом случае). После успешной установки пишет в логи и возвращается.

    Безопасно вызывать многократно: если у пользователя уже есть пароль и
    FORCE не задан, ничего не сделает.
    """
    email = (os.environ.get("WEB_BOOTSTRAP_EMAIL", "") or "").strip().lower()
    password = os.environ.get("WEB_BOOTSTRAP_PASSWORD", "") or ""
    if not email or not password:
        return

    err = web_auth.validate_email(email)
    if err:
        print(f"[bootstrap] WEB_BOOTSTRAP_EMAIL невалиден: {err}", file=sys.stderr)
        return
    err = web_auth.validate_password(password)
    if err:
        print(f"[bootstrap] WEB_BOOTSTRAP_PASSWORD невалиден: {err}", file=sys.stderr)
        return

    force = (os.environ.get("WEB_BOOTSTRAP_FORCE", "") or "").lower() in (
        "1",
        "true",
        "yes",
    )

    try:
        # 1) Если такой email уже привязан к пользователю — ничего не делаем.
        existing = db.find_user_by_email(email)
        if existing:
            print(
                f"[bootstrap] email={email} уже привязан к user_id={existing['id']}; "
                "ничего не делаю.",
                file=sys.stderr,
            )
            return

        # 2) Берём единственного пользователя в БД.
        only = db.get_single_user_if_exactly_one()
        if not only:
            n = db.count_users()
            print(
                f"[bootstrap] в БД пользователей: {n}. Bootstrap работает только когда "
                "пользователь в БД ровно один. Удалите лишние строки или используйте "
                "scripts/set_user_password.py с --user-id.",
                file=sys.stderr,
            )
            return

        user_id = int(only["id"])
        already_has_pwd = bool((only.get("password_hash") or "").strip())
        if already_has_pwd and not force:
            print(
                f"[bootstrap] у user_id={user_id} уже стоит пароль; пропускаю. "
                "Чтобы перезаписать — задайте WEB_BOOTSTRAP_FORCE=1.",
                file=sys.stderr,
            )
            return

        pwd_hash = web_auth.hash_password(password)
        db._execute(
            "UPDATE users SET email = %s, password_hash = %s, password_algo = %s "
            "WHERE id = %s",
            (email, pwd_hash, "argon2", user_id),
        )
        print(
            f"[bootstrap] OK: user_id={user_id} email={email} пароль установлен. "
            "Удалите WEB_BOOTSTRAP_EMAIL/PASSWORD из переменных окружения.",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[bootstrap] ошибка: {exc}", file=sys.stderr)


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    try:
        _bootstrap_password_from_env()
    except Exception as exc:
        print(f"[bootstrap] неожиданная ошибка: {exc}", file=sys.stderr)

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


_sentry_dsn = os.environ.get("SENTRY_DSN", "").strip()
if _sentry_dsn:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=_sentry_dsn,
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            environment=os.environ.get("SENTRY_ENV", "production"),
        )
    except Exception as exc:
        print(f"Sentry init failed: {exc}", file=sys.stderr)


app = FastAPI(
    title="Helper Web", docs_url=None, redoc_url=None, lifespan=_app_lifespan
)

_secret = os.environ.get("WEB_SESSION_SECRET", "").strip()
if not _secret:
    _secret = "dev-insecure-change-me"
    if os.environ.get("RENDER", "").lower() == "true":
        print("WARNING: WEB_SESSION_SECRET not set; set it in production.", file=sys.stderr)

_in_prod = os.environ.get("RENDER", "").lower() == "true" or os.environ.get(
    "WEB_HTTPS_ONLY", ""
).lower() in ("1", "true", "yes")

# Сначала добавляются middleware, которые должны выполняться ВНУТРИ session
# (т.к. add_middleware идёт в обратном порядке).
class _OriginCsrfMiddleware(BaseHTTPMiddleware):
    """
    Защита от CSRF: state-changing методы должны иметь Origin/Referer того же
    хоста, что и сам запрос. Браузеры всегда шлют Origin при кросс-сайтовых
    POST/PUT/DELETE, поэтому проверки достаточно для типичных сценариев.
    """

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    async def dispatch(self, request: Request, call_next):
        if request.method in self.SAFE_METHODS:
            return await call_next(request)
        host = request.headers.get("host", "").split(":")[0].lower()
        origin = request.headers.get("origin", "")
        referer = request.headers.get("referer", "")
        if not origin and not referer:
            # Нет Origin/Referer — вероятно, тулинг (curl, тесты, healthcheck).
            # Браузеры всегда шлют Origin на cross-site POST, поэтому CSRF
            # из браузера всё равно будет пойман ниже.
            return await call_next(request)

        allowed_hosts = {host} if host else set()
        for h in os.environ.get("WEB_ALLOWED_HOSTS", "").split(","):
            h = h.strip().lower()
            if h:
                allowed_hosts.add(h)

        from urllib.parse import urlparse

        def _host_of(url: str) -> str:
            try:
                return (urlparse(url).hostname or "").lower()
            except Exception:
                return ""

        check = origin or referer
        if _host_of(check) in allowed_hosts:
            return await call_next(request)

        return JSONResponse(
            {"ok": False, "message": "Forbidden (origin)"}, status_code=403
        )


app.add_middleware(_OriginCsrfMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_secret,
    same_site="lax",
    https_only=_in_prod,
    max_age=30 * 24 * 3600,
)


def _consume_flash(request: Request) -> dict:
    """Забирает одноразовое сообщение из сессии при рендере шаблона (после Session)."""
    return {
        "msg": request.session.pop("flash_msg", None),
        "kind": request.session.pop("flash_kind", "ok"),
    }


templates.env.globals["consume_flash"] = _consume_flash


def _csrf_token_for_template(request: Request) -> str:
    return web_auth.csrf_token(request)


templates.env.globals["csrf_token_for"] = _csrf_token_for_template


def _support_contact() -> str:
    """Контакт для ссылки «Забыли пароль?» до подключения SMTP."""
    return (os.environ.get("SUPPORT_CONTACT", "") or "").strip()


templates.env.globals["support_contact"] = _support_contact


# Контекст текущего запроса: id пользователя из сессии. Хранится в request.state.
def _set_request_user_id(request: Request) -> int | None:
    uid = web_auth.current_user_id(request)
    request.state.user_id = uid
    return uid


def resolve_web_user_row(request: Request | None = None) -> dict:
    """
    Кто залогинен:
    1) сессия (request.session["user_id"]) — основной способ после регистрации;
    2) WEB_INTERNAL_USER_ID — для legacy-режима «одного пользователя»;
    3) иначе, если в БД ровно одна строка users — она;
    4) иначе RuntimeError (новый пользователь должен зарегистрироваться).
    """
    if request is not None:
        uid = web_auth.current_user_id(request)
        if uid is not None:
            u = db.get_user_by_id(uid)
            if u:
                return u

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

        raise RuntimeError(
        "Не удалось определить пользователя: войдите в систему или зарегистрируйтесь."
    )


def get_user_row(request: Request | None = None) -> dict:
    return resolve_web_user_row(request)


def _is_authenticated(request: Request) -> bool:
    """
    Считается аутентифицированным, если в сессии есть user_id.
    Старый флаг auth=True больше не принимается (миграция аутентификации).
    """
    return web_auth.is_authenticated(request)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "helper-web"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "email": ""}
    )


@app.post("/login")
async def login_post(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
):
    email_norm = (email or "").strip().lower()
    ip = web_auth.client_ip(request)
    if web_auth.rate_limit_hit(f"login:{ip}:{email_norm}"):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Слишком много попыток входа. Подождите минуту.",
                "email": email_norm,
            },
            status_code=429,
        )

    err = web_auth.validate_email(email_norm)
    if err:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": err, "email": email_norm},
            status_code=400,
        )

    user = db.find_user_by_email(email_norm)
    if not user or not user.get("password_hash") or not web_auth.verify_password(
        user["password_hash"], password
    ):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный email или пароль.", "email": email_norm},
        status_code=401,
    )

    if web_auth.needs_rehash(user["password_hash"]):
        try:
            db.set_password_hash(user["id"], web_auth.hash_password(password))
        except Exception:
            pass

    web_auth.login_user(request, int(user["id"]))
    return RedirectResponse("/", status_code=302)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "signup.html", {"error": None, "email": "", "name": ""}
    )


@app.post("/signup")
async def signup_post(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    name: str = Form(""),
):
    email_norm = (email or "").strip().lower()
    name_norm = (name or "").strip()

    ip = web_auth.client_ip(request)
    if web_auth.rate_limit_hit(f"signup:{ip}"):
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "Слишком много попыток. Попробуйте через минуту.",
                "email": email_norm,
                "name": name_norm,
            },
            status_code=429,
        )

    err = web_auth.validate_email(email_norm) or web_auth.validate_password(password)
    if not err and password != password2:
        err = "Пароли не совпадают."
    if err:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": err, "email": email_norm, "name": name_norm},
            status_code=400,
        )

    if db.find_user_by_email(email_norm):
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "Аккаунт с таким email уже существует. Войдите.",
                "email": email_norm,
                "name": name_norm,
            },
            status_code=409,
        )

    pwd_hash = web_auth.hash_password(password)
    try:
        user = db.create_user_with_email(email_norm, pwd_hash, name_norm)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": f"Не удалось создать аккаунт: {exc}",
                "email": email_norm,
                "name": name_norm,
            },
            status_code=500,
        )

    web_auth.login_user(request, int(user["id"]))
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    web_auth.logout_user(request)
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
        "/projects/archive",
        "/plan",
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


TASK_COLOR_CHOICES = (
    {"id": "", "label": "Без цвета", "swatch": "—"},
    {"id": "red", "label": "Красный", "swatch": "🔴"},
    {"id": "orange", "label": "Оранжевый", "swatch": "🟠"},
    {"id": "yellow", "label": "Жёлтый", "swatch": "🟡"},
    {"id": "green", "label": "Зелёный", "swatch": "🟢"},
    {"id": "blue", "label": "Синий", "swatch": "🔵"},
    {"id": "purple", "label": "Фиолетовый", "swatch": "🟣"},
    {"id": "gray", "label": "Серый", "swatch": "⚫"},
)


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
    if not _is_authenticated(request):
        return templates.TemplateResponse(
            request,
            "landing.html",
            _ctx(
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

    user_row = get_user_row(request)
    uid = user_row["id"]
    _maybe_transfer_overdue(uid)
    # Без _active_tasks_display_order: на главной нужны только числа (COUNT / len «сегодня»).
    today_tasks = db.get_today_tasks(uid)
    n_today = len(today_tasks)
    counts = db.home_counts(uid)
    n_tasks = counts["n_tasks"]
    n_routines = counts["n_routines"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _active_tasks_display_order, _format_time_human

    user_row = get_user_row(request)
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
            return (
                int(t.get("today_sort") or 0),
                0 if ti else 1,
                ti or "99:99",
                t.get("id", 0),
            )

        for bucket in _order:
            pairs = sorted(buckets[bucket], key=_sort_key)
            n_in_bucket = len(pairs)
            rows = []
            for bi, (_num, t) in enumerate(pairs):
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
                        "color": (t.get("color") or "").strip().lower(),
                        "estimate_min": int(t.get("estimate_min") or 0),
                        "repeat_day": rd,
                        "repeat_day_codes": _repeat_day_codes(rd),
                        "repeat_interval": _repeat_interval_for_row(rd),
                        "project_label": pl,
                        "kebab_remove_from_plan": bool(t.get("is_routine")),
                        "show_move_today": n_in_bucket >= 1,
                        "can_move_up_today": n_in_bucket > 1 and bi > 0,
                        "can_move_down_today": n_in_bucket > 1 and bi < n_in_bucket - 1,
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
            color_choices=TASK_COLOR_CHOICES,
            kebab_hide_schedule=True,
        ),
    )


@app.get("/tasks", response_class=HTMLResponse)
async def page_tasks(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _active_tasks_display_order, _format_date_human, _format_time_human

    uid = get_user_row(request)["id"]
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
            "color": (t.get("color") or "").strip().lower(),
            "estimate_min": int(t.get("estimate_min") or 0),
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
            color_choices=TASK_COLOR_CHOICES,
        ),
    )


@app.get("/reports/today", response_class=HTMLResponse)
async def page_report_today(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_done_report_today

    user_row = get_user_row(request)
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
    if not _is_authenticated(request):
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    archived_count = db.count_archived_projects(uid)
    return templates.TemplateResponse(
        request,
        "projects.html",
        _ctx(
            projects=project_rows,
            empty=len(project_rows) == 0,
            archived_count=archived_count,
        ),
    )


@app.get("/projects/archive", response_class=HTMLResponse)
async def page_projects_archive(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    raw = db.list_archived_projects(uid)
    rows: list[dict] = []
    from bot_v2 import _format_date_human

    for p in raw:
        pid = int(p["id"])
        n_done_all = db.count_done_tasks_in_project_all(uid, pid)
        archived_at = p.get("archived_at")
        a_label = ""
        if archived_at:
            raw_a = str(archived_at).replace("Z", "")
            ds = raw_a[:10]
            if len(ds) == 10 and ds[4] == "-":
                a_label = _format_date_human(ds)
            else:
                a_label = raw_a[:16]
        rows.append(
            {
                "id": pid,
                "title": (p.get("title") or "").strip(),
                "emoji": ((p.get("emoji") or "📁").strip() or "📁"),
                "n_done_all": n_done_all,
                "archived_label": a_label,
            }
        )
    return templates.TemplateResponse(
        request,
        "projects_archive.html",
        _ctx(projects=rows, empty=len(rows) == 0),
    )


@app.post("/projects/{project_id}/archive")
async def action_project_archive(request: Request, project_id: int):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = db.archive_project(uid, project_id, complete_active=True)
    return _flash_redirect(request, "/projects", result["message"], result["ok"])


@app.post("/projects/{project_id}/unarchive")
async def action_project_unarchive(request: Request, project_id: int):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = db.unarchive_project(uid, project_id)
    return _flash_redirect(request, "/projects/archive", result["message"], result["ok"])


# ── Планирование дня ─────────────────────────────────────────────────────

PLAN_DAY_START_MIN = 6 * 60   # 06:00
PLAN_DAY_END_MIN = 24 * 60    # 24:00 (показываем сетку до полуночи)
PLAN_SLOT_STEP_MIN = 30
PLAN_DURATION_PRESETS = (5, 15, 30, 45, 60, 90, 120, 180)


def _format_min_as_hhmm(m: int) -> str:
    m = max(0, min(int(m), 24 * 60 - 1))
    return f"{m // 60:02d}:{m % 60:02d}"


def _parse_hhmm_to_min(value: str) -> int | None:
    s = (value or "").strip()
    if not s or ":" not in s:
        return None
    try:
        h, m = s.split(":", 1)
        h_i, m_i = int(h), int(m)
    except (TypeError, ValueError):
        return None
    if not (0 <= h_i < 24 and 0 <= m_i < 60):
        return None
    return h_i * 60 + m_i


def _humanize_minutes(total: int) -> str:
    total = max(0, int(total))
    if total <= 0:
        return "0 мин"
    h, m = divmod(total, 60)
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин"


@app.get("/plan", response_class=HTMLResponse)
async def page_plan(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_date_human
    from datetime import date as _date, timedelta as _td

    uid = get_user_row(request)["id"]
    today_str = db.user_local_date_offset(uid, 0)
    raw_date = (request.query_params.get("date") or "").strip() or today_str
    try:
        d = _date.fromisoformat(raw_date)
    except (TypeError, ValueError):
        d = _date.fromisoformat(today_str)
    date_str = d.strftime("%Y-%m-%d")
    prev_date = (d - _td(days=1)).strftime("%Y-%m-%d")
    next_date = (d + _td(days=1)).strftime("%Y-%m-%d")

    slots = db.get_plan_slots(uid, date_str)
    planned_task_ids = {int(s["task_id"]) for s in slots}

    day_tasks = db.get_tasks_for_date(uid, date_str)
    db.attach_project_labels(uid, day_tasks)

    backlog: list[dict] = []
    for t in day_tasks:
        if int(t["id"]) in planned_task_ids:
            continue
        emoji = _task_row_emoji(t)
        backlog.append(
            {
                "task_id": int(t["id"]),
                "text": t["text"],
                "emoji": emoji,
                "color": (t.get("color") or "").strip().lower(),
                "is_routine": bool(t.get("is_routine")),
                "estimate_min": int(t.get("estimate_min") or 0),
                "project_label": (t.get("project_title") or "").strip(),
            }
        )

    other_tasks: list[dict] = []
    extras = db.get_active_tasks_ordered(uid)
    db.attach_project_labels(uid, extras)
    day_task_ids = {int(t["id"]) for t in day_tasks}
    for t in extras:
        tid = int(t["id"])
        if tid in day_task_ids or tid in planned_task_ids:
            continue
        if t.get("is_routine"):
            continue
        other_tasks.append(
            {
                "task_id": tid,
                "text": t["text"],
                "emoji": _task_row_emoji(t),
                "color": (t.get("color") or "").strip().lower(),
                "estimate_min": int(t.get("estimate_min") or 0),
                "project_label": (t.get("project_title") or "").strip(),
                "due_date": t.get("due_date") or "",
            }
        )

    slot_blocks: list[dict] = []
    total_planned = 0
    for s in slots:
        start = int(s["start_min"])
        dur = int(s["duration_min"])
        total_planned += dur
        slot_blocks.append(
            {
                "slot_id": int(s["slot_id"]),
                "task_id": int(s["task_id"]),
                "text": s["text"],
                "color": (s.get("color") or "").strip().lower(),
                "is_routine": bool(s.get("is_routine")),
                "start_min": start,
                "duration_min": dur,
                "start_label": _format_min_as_hhmm(start),
                "end_label": _format_min_as_hhmm(start + dur),
                "duration_label": _humanize_minutes(dur),
            }
        )

    grid_rows: list[dict] = []
    for m in range(PLAN_DAY_START_MIN, PLAN_DAY_END_MIN, PLAN_SLOT_STEP_MIN):
        grid_rows.append(
            {
                "start_min": m,
                "label": _format_min_as_hhmm(m),
                "is_hour": (m % 60 == 0),
            }
        )

    is_today = date_str == today_str
    is_past = d < _date.fromisoformat(today_str)

    return templates.TemplateResponse(
        request,
        "plan.html",
        _ctx(
            date_str=date_str,
            date_label=_format_date_human(date_str),
            prev_date=prev_date,
            next_date=next_date,
            today_str=today_str,
            is_today=is_today,
            is_past=is_past,
            backlog=backlog,
            other_tasks=other_tasks,
            slots=slot_blocks,
            grid_rows=grid_rows,
            slot_step_min=PLAN_SLOT_STEP_MIN,
            day_start_min=PLAN_DAY_START_MIN,
            day_end_min=PLAN_DAY_END_MIN,
            duration_presets=PLAN_DURATION_PRESETS,
            total_planned_min=total_planned,
            total_planned_label=_humanize_minutes(total_planned),
            backlog_count=len(backlog),
            color_choices=TASK_COLOR_CHOICES,
        ),
    )


@app.post("/plan/add_slot")
async def action_plan_add_slot(
    request: Request,
    task_id: int = Form(...),
    date: str = Form(...),
    start: str = Form(...),
    duration_min: int = Form(...),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    start_min = _parse_hhmm_to_min(start)
    if start_min is None:
        result = {"ok": False, "message": "Не понял время старта."}
    else:
        result = db.add_plan_slot(uid, date, task_id, start_min, int(duration_min))
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, f"/plan?date={date}", result["message"], result["ok"])


@app.post("/plan/update_slot")
async def action_plan_update_slot(
    request: Request,
    slot_id: int = Form(...),
    date: str = Form(...),
    start: str = Form(...),
    duration_min: int = Form(...),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    start_min = _parse_hhmm_to_min(start)
    if start_min is None:
        result = {"ok": False, "message": "Не понял время старта."}
    else:
        result = db.update_plan_slot(uid, slot_id, start_min, int(duration_min))
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, f"/plan?date={date}", result["message"], result["ok"])


@app.post("/plan/remove_slot")
async def action_plan_remove_slot(
    request: Request,
    slot_id: int = Form(...),
    date: str = Form(...),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = db.remove_plan_slot(uid, slot_id)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, f"/plan?date={date}", result["message"], result["ok"])


@app.post("/tasks/set_estimate")
async def action_set_estimate(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/plan"),
    minutes: int = Form(0),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    ok = db.set_task_estimate(uid, task_id, int(minutes))
    result = {"ok": ok, "message": "Оценка обновлена." if ok else "Не удалось обновить."}
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def page_project_detail(request: Request, project_id: int):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    proj = db.get_project(uid, project_id)
    if not proj:
        return RedirectResponse("/projects", status_code=302)
    from bot_v2 import _format_date_human, _format_time_human

    _maybe_transfer_overdue(uid)
    sort_q = (request.query_params.get("sort") or "").strip().lower()
    _raw_mode = str(proj.get("sort_mode") or "hybrid").strip().lower()
    proj_mode = _raw_mode if _raw_mode in ("hybrid", "manual") else "hybrid"
    if sort_q in ("manual", "date", "color", "text"):
        sort = sort_q
    elif sort_q == "hybrid":
        sort = "hybrid"
    else:
        sort = "manual" if proj_mode == "manual" else "hybrid"
    tasks = db.get_active_tasks_for_project(uid, project_id, sort=sort)
    rows: list[dict] = []
    _total = len(tasks)
    _show_move = sort in ("manual", "hybrid")
    for _idx, t in enumerate(tasks):
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
                "color": (t.get("color") or "").strip().lower(),
                "estimate_min": int(t.get("estimate_min") or 0),
                "repeat_day": "",
                "repeat_day_codes": [],
                "repeat_interval": "",
                "has_project": False,
                "project_label": "",
                "show_move": _show_move,
                "can_move_up": _show_move and _idx > 0,
                "can_move_down": _show_move and _idx < _total - 1,
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
    if sort_q:
        next_url = f"/projects/{project_id}?sort={sort_q}"
    w_start_utc, w_end_utc, w_mon, w_sun = db.user_calendar_week_bounds_utc(uid)
    n_done_week, n_done_all = db.count_done_tasks_in_project_week_and_all(
        uid, project_id, w_start_utc, w_end_utc
    )
    sort_display = {
        "hybrid": "По умолчанию",
        "manual": "По умолчанию",
        "date": "По сроку",
        "color": "По цвету",
        "text": "По названию",
    }.get(sort, "По умолчанию")
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
            sort=sort,
            sort_display=sort_display,
            color_choices=TASK_COLOR_CHOICES,
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _group_tasks_by_time_bucket

    uid = get_user_row(request)["id"]
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
                    "color": (t.get("color") or "").strip().lower(),
                    "estimate_min": int(t.get("estimate_min") or 0),
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    done = db.get_done_tasks_today(uid)
    done_items = [{"text": t.get("text", ""), "task_id": t["id"]} for t in done]
    return templates.TemplateResponse(
        request,
        "actions.html",
        _ctx(done_items=done_items),
    )


@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    try:
        n = int(max_tasks_per_day)
    except (TypeError, ValueError):
        n = 7
    n = max(1, min(50, n))
    db.update_settings(uid, max_tasks_per_day=n)
    return _flash_redirect(request, "/settings", "Настройки сохранены.", True)


@app.get("/reports/week", response_class=HTMLResponse)
async def page_report_week(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_done_report_week

    user_row = get_user_row(request)
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    from bot_v2 import _format_date_human

    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    user_row = get_user_row(request)
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    dest = f"/projects/{project_id}"
    updated = db.update_project(uid, project_id, title.strip(), (emoji or "").strip())
    if updated:
        return _flash_redirect(request, dest, "Проект обновлён.", True)
    return _flash_redirect(request, dest, "Укажи название проекта.", False)


@app.post("/projects/{project_id}/add_task")
async def action_project_add_task(
    request: Request, project_id: int, text: str = Form("")
):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    user_row = get_user_row(request)
    result = add_project_task_from_text(user_row, project_id, text)
    dest = f"/projects/{project_id}"
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/projects/{project_id}/delete")
async def action_project_delete(request: Request, project_id: int):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    ok = db.delete_project(uid, project_id)
    msg = (
        "Проект удалён. Задачи остались в списке, но без привязки к проекту."
        if ok
        else "Не удалось удалить проект."
    )
    return _flash_redirect(request, "/projects", msg, ok)


@app.post("/tasks/complete")
async def action_complete(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    raw = form.getlist("task_id")
    ids: list[int] = []
    for x in raw:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    is_routine = kind.strip().lower() == "routine"
    result = delete_task_by_number(uid, num, is_routine)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/tasks/edit")
async def action_edit(request: Request, phrase: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = apply_edit_phrase(uid, phrase)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/tasks/reschedule")
async def action_reschedule(request: Request, phrase: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = apply_reschedule_phrase(uid, phrase)
    dest = request.query_params.get("next", "/actions")
    return _flash_redirect(request, dest, result["message"], result["ok"])


@app.post("/tasks/uncomplete")
async def action_uncomplete(request: Request, task_id: int = Form(...)):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = set_task_category_by_id(uid, task_id, category_name)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/move_in_project")
async def action_move_in_project(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/projects"),
    direction: str = Form("up"),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "message": "Требуется вход."}, status_code=401
            )
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = db.move_task_in_project(uid, task_id, direction)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/move_in_today")
async def action_move_in_today(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/today"),
    direction: str = Form("up"),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = db.move_task_in_today_order(uid, task_id, direction)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/sync_today_order")
async def action_sync_today_order(
    request: Request,
    next: str = Form("/today"),
    order_utro: str = Form(""),
    order_den: str = Form(""),
    order_vecher: str = Form(""),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = db.sync_today_bucket_orders(uid, order_utro, order_den, order_vecher)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/reorder_project")
async def action_reorder_project(
    request: Request,
    project_id: int = Form(...),
    task_ids: str = Form(...),
    next: str = Form("/projects"),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    ids: list[int] = []
    for x in (task_ids or "").split(","):
        x = x.strip()
        if not x:
            continue
        try:
            ids.append(int(x))
        except ValueError:
            err = {"ok": False, "message": "Неверный список задач."}
            if _wants_json(request):
                return JSONResponse(err)
            return _flash_redirect(request, next, err["message"], False)
    result = db.reorder_project_tasks(uid, project_id, ids)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.post("/tasks/set_color")
async def action_set_color(
    request: Request,
    task_id: int = Form(...),
    next: str = Form("/tasks"),
    color: str = Form(""),
):
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "message": "Требуется вход."}, status_code=401
            )
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    result = set_task_color_by_id(uid, task_id, color)
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        if _wants_json(request):
            return JSONResponse({"ok": False, "message": "Требуется вход."}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
    flag = (make_routine or "").strip().lower() in ("1", "true", "yes", "on")
    result = set_task_routine_kind_by_id(uid, task_id, flag)
    if _wants_json(request):
        return JSONResponse(result)
    return _flash_redirect(request, next, result["message"], result["ok"])


@app.get("/categories", response_class=HTMLResponse)
async def page_categories(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    uid = get_user_row(request)["id"]
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
    if not _is_authenticated(request):
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
    max_voice_bytes = int(os.environ.get("WEB_MAX_VOICE_BYTES", str(5 * 1024 * 1024)))
    if len(body) > max_voice_bytes:
        msg = "Файл слишком большой. Максимум 5 МБ."
        if wants_json:
            return JSONResponse({"ok": False, "message": msg}, status_code=413)
        return _flash_redirect(request, dest, msg, False)
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
    user_row = get_user_row(request)
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
