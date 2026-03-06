# -*- coding: utf-8 -*-
"""
Хранилище данных — PostgreSQL (Supabase) или SQLite (локально).
Если задан DATABASE_URL — PostgreSQL, иначе — SQLite.
Многопользовательская изоляция: все запросы фильтруются по user_id.
"""

import os
import logging
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Python < 3.9

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

_conn = None
_SQLITE_PATH = os.environ.get("BOT_DB_PATH", "bot_data.db")


# ── Подключение ─────────────────────────────────────────────────────────

def _get_conn():
    global _conn
    if USE_PG:
        need_new = _conn is None
        if not need_new:
            try:
                need_new = _conn.closed != 0
                if not need_new:
                    _conn.cursor().execute("SELECT 1")
            except Exception:
                need_new = True
                try:
                    _conn.close()
                except Exception:
                    pass
        if need_new:
            _conn = psycopg2.connect(DATABASE_URL, sslmode="require")
            _conn.autocommit = True
            _init_tables_pg()
            logger.info("PostgreSQL connected")
    else:
        if _conn is None:
            _conn = sqlite3.connect(_SQLITE_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _init_tables_sqlite()
    return _conn


def _init_tables_pg() -> None:
    cur = _conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id              SERIAL PRIMARY KEY,
        telegram_id     BIGINT UNIQUE NOT NULL,
        name            TEXT DEFAULT '',
        timezone        TEXT DEFAULT 'Europe/Moscow',
        tips_shown      INTEGER DEFAULT 0,
        settings_json   TEXT DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS categories (
        id          SERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        emoji       TEXT NOT NULL,
        name        TEXT NOT NULL,
        sort_order  INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text            TEXT NOT NULL,
        category_emoji  TEXT DEFAULT '',
        category_name   TEXT DEFAULT '',
        status          TEXT DEFAULT 'active' CHECK(status IN ('active','done','cancelled')),
        priority_value   REAL DEFAULT 5,
        priority_urgency REAL DEFAULT 5,
        priority_risk    REAL DEFAULT 5,
        priority_size    REAL DEFAULT 5,
        priority_score   REAL DEFAULT 0,
        due_date        TEXT,
        due_time        TEXT,
        time_of_day     TEXT,
        repeat_rule     TEXT,
        parent_task_id  INTEGER REFERENCES tasks(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        completed_at    TIMESTAMPTZ
    );
    CREATE TABLE IF NOT EXISTS messages (
        id          SERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role        TEXT NOT NULL CHECK(role IN ('user','assistant')),
        text        TEXT NOT NULL,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
    CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
    CREATE INDEX IF NOT EXISTS idx_categories_user ON categories(user_id);
    """)
    for col_sql in (
        "ALTER TABLE tasks ADD COLUMN is_routine BOOLEAN DEFAULT FALSE",
        "ALTER TABLE tasks ADD COLUMN repeat_day TEXT",
    ):
        try:
            cur.execute(col_sql)
        except Exception:
            if _conn and not _conn.closed:
                _conn.rollback()
    cur.close()


def _init_tables_sqlite() -> None:
    _conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id     INTEGER UNIQUE NOT NULL,
        name            TEXT DEFAULT '',
        timezone        TEXT DEFAULT 'Europe/Moscow',
        tips_shown      INTEGER DEFAULT 0,
        settings_json   TEXT DEFAULT '{}',
        created_at      TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS categories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id),
        emoji       TEXT NOT NULL,
        name        TEXT NOT NULL,
        sort_order  INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES users(id),
        text            TEXT NOT NULL,
        category_emoji  TEXT DEFAULT '',
        category_name   TEXT DEFAULT '',
        status          TEXT DEFAULT 'active' CHECK(status IN ('active','done','cancelled')),
        priority_value   REAL DEFAULT 5,
        priority_urgency REAL DEFAULT 5,
        priority_risk    REAL DEFAULT 5,
        priority_size    REAL DEFAULT 5,
        priority_score   REAL DEFAULT 0,
        due_date        TEXT,
        due_time        TEXT,
        time_of_day     TEXT,
        repeat_rule     TEXT,
        parent_task_id  INTEGER REFERENCES tasks(id),
        created_at      TEXT DEFAULT (datetime('now')),
        completed_at    TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id),
        role        TEXT NOT NULL CHECK(role IN ('user','assistant')),
        text        TEXT NOT NULL,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    """)
    _conn.commit()
    for col_sql in (
        "ALTER TABLE tasks ADD COLUMN is_routine BOOLEAN DEFAULT FALSE",
        "ALTER TABLE tasks ADD COLUMN repeat_day TEXT",
    ):
        try:
            _conn.execute(col_sql)
            _conn.commit()
        except Exception:
            pass


# ── Универсальные хелперы ────────────────────────────────────────────────

def _ph(index: int = 0) -> str:
    """Плейсхолдер: %s для PG, ? для SQLite."""
    return "%s" if USE_PG else "?"


def _query(sql: str) -> str:
    """Адаптирует SQL: заменяет %s на ? для SQLite."""
    if not USE_PG:
        sql = sql.replace("%s", "?")
    return sql


def _fetchone(sql: str, params: tuple = ()) -> dict | None:
    conn = _get_conn()
    sql = _query(sql)
    if USE_PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    else:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def _fetchall(sql: str, params: tuple = ()) -> list[dict]:
    conn = _get_conn()
    sql = _query(sql)
    if USE_PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    else:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def _execute(sql: str, params: tuple = ()) -> int:
    conn = _get_conn()
    sql = _query(sql)
    if USE_PG:
        cur = conn.cursor()
        cur.execute(sql, params)
        n = cur.rowcount
        cur.close()
        return n
    else:
        n = conn.execute(sql, params).rowcount
        conn.commit()
        return n


def _insert_returning(sql: str, params: tuple = ()) -> dict | None:
    """INSERT ... RETURNING * для PG, двухшаговый для SQLite."""
    conn = _get_conn()
    if USE_PG:
        sql = _query(sql)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    else:
        sql_clean = sql.replace("RETURNING *", "")
        sql_clean = _query(sql_clean)
        cur = conn.execute(sql_clean, params)
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row) if row else None


# ── Users ────────────────────────────────────────────────────────────────

DEFAULT_CATEGORIES = [
    ("🏠", "Быт / дом"),
    ("👨‍👩‍👧", "Семья"),
    ("💇‍♀️", "Уход / внешность"),
    ("🌿", "Для себя"),
    ("🎫", "Досуг"),
    ("📦", "Дела / поручения"),
    ("🧠", "Большие проекты"),
    ("🔁", "Регулярные дела"),
]


def get_or_create_user(telegram_id: int, name: str = "") -> dict:
    row = _fetchone("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
    if row:
        return row

    if USE_PG:
        _execute(
            "INSERT INTO users (telegram_id, name) VALUES (%s, %s) ON CONFLICT (telegram_id) DO NOTHING",
            (telegram_id, name),
        )
    else:
        _execute(
            "INSERT OR IGNORE INTO users (telegram_id, name) VALUES (%s, %s)",
            (telegram_id, name),
        )

    user = _fetchone("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
    user_id = user["id"]

    existing = _fetchone("SELECT COUNT(*) AS cnt FROM categories WHERE user_id = %s", (user_id,))
    if existing and existing["cnt"] == 0:
        for i, (emoji, cat_name) in enumerate(DEFAULT_CATEGORIES):
            _execute(
                "INSERT INTO categories (user_id, emoji, name, sort_order) VALUES (%s, %s, %s, %s)",
                (user_id, emoji, cat_name, i),
            )
    return user


def get_user_by_tg(telegram_id: int) -> dict | None:
    return _fetchone("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))


def increment_tips(telegram_id: int) -> int:
    _execute("UPDATE users SET tips_shown = tips_shown + 1 WHERE telegram_id = %s", (telegram_id,))
    row = _fetchone("SELECT tips_shown FROM users WHERE telegram_id = %s", (telegram_id,))
    return row["tips_shown"] if row else 0


def get_tips_shown(telegram_id: int) -> int:
    row = _fetchone("SELECT tips_shown FROM users WHERE telegram_id = %s", (telegram_id,))
    return row["tips_shown"] if row else 0


def get_user_timezone(user_id: int) -> str:
    """Часовой пояс пользователя для отображения дат (например Europe/Moscow)."""
    row = _fetchone("SELECT timezone FROM users WHERE id = %s", (user_id,))
    if row and row.get("timezone"):
        return (row["timezone"] or "").strip() or "Europe/Moscow"
    return "Europe/Moscow"


# ── Settings ──────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "max_tasks_per_day": 7,
    "auto_schedule": True,
}


def get_settings(user_id: int) -> dict:
    import json as _json
    row = _fetchone("SELECT settings_json FROM users WHERE id = %s", (user_id,))
    if not row or not row.get("settings_json"):
        return dict(DEFAULT_SETTINGS)
    try:
        saved = _json.loads(row["settings_json"])
    except (ValueError, TypeError):
        saved = {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(saved)
    return merged


def update_settings(user_id: int, **kwargs) -> dict:
    import json as _json
    current = get_settings(user_id)
    current.update(kwargs)
    _execute(
        "UPDATE users SET settings_json = %s WHERE id = %s",
        (_json.dumps(current, ensure_ascii=False), user_id),
    )
    return current


def count_tasks_for_date(user_id: int, date_str: str) -> int:
    row = _fetchone(
        "SELECT COUNT(*) AS cnt FROM tasks "
        "WHERE user_id = %s AND status = 'active' AND due_date = %s",
        (user_id, date_str),
    )
    return row["cnt"] if row else 0


def get_least_priority_task_for_date(user_id: int, date_str: str) -> dict | None:
    return _fetchone(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' "
        "AND due_date = %s ORDER BY priority_score ASC LIMIT 1",
        (user_id, date_str),
    )


# ── Categories ───────────────────────────────────────────────────────────

def get_categories(user_id: int) -> list[dict]:
    return _fetchall("SELECT * FROM categories WHERE user_id = %s ORDER BY sort_order", (user_id,))


# ── Tasks ────────────────────────────────────────────────────────────────

def add_task(
    user_id: int,
    text: str,
    category_emoji: str = "",
    category_name: str = "",
    due_date: str | None = None,
    due_time: str | None = None,
    time_of_day: str | None = None,
    priority_value: float = 5,
    priority_urgency: float = 5,
    priority_risk: float = 5,
    priority_size: float = 5,
    is_routine: bool = False,
    repeat_day: str | None = None,
) -> dict:
    logger.info("add_task: text='%s' is_routine=%s repeat_day=%s due_date=%s",
                text[:40], is_routine, repeat_day, due_date)
    score = _calc_score(priority_value, priority_urgency, priority_risk, priority_size)
    result = _insert_returning(
        """INSERT INTO tasks
           (user_id, text, category_emoji, category_name,
            due_date, due_time, time_of_day,
            priority_value, priority_urgency, priority_risk, priority_size, priority_score,
            is_routine, repeat_day)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (user_id, text, category_emoji, category_name,
         due_date, due_time, time_of_day,
         priority_value, priority_urgency, priority_risk, priority_size, score,
         is_routine, repeat_day),
    )
    if result:
        logger.info("add_task OK: id=%s is_routine=%s", result.get("id"), result.get("is_routine"))
    return result


def get_active_tasks(user_id: int) -> list[dict]:
    return _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' ORDER BY priority_score DESC",
        (user_id,),
    )


def get_active_tasks_ordered(user_id: int) -> list[dict]:
    """Активные задачи в порядке для списка: по дате, времени, id (стабильная нумерация)."""
    rows = _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' ORDER BY COALESCE(due_date, '9999-99-99'), COALESCE(due_time, ''), id",
        (user_id,),
    )
    return rows


def transfer_overdue_tasks(user_id: int) -> int:
    """Переносит просроченные активные задачи (due_date < сегодня) на сегодня. Возвращает число перенесённых."""
    today_str, _ = _get_today_in_user_tz(user_id)
    n = _execute(
        "UPDATE tasks SET due_date = %s WHERE user_id = %s AND status = 'active' AND due_date IS NOT NULL AND due_date < %s",
        (today_str, user_id, today_str),
    )
    if n and n > 0:
        logger.info("transfer_overdue_tasks: user_id=%s moved %s tasks to %s", user_id, n, today_str)
    return n or 0


def _normalize_search(s: str) -> str:
    """Нормализация для поиска: нижний регистр, схлопывание пробелов (голос может дать лишние)."""
    if not s:
        return ""
    import re
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def find_tasks_matching_text(user_id: int, search: str) -> list[dict]:
    """Все активные задачи, в тексте которых встречается search (подстрока или все слова)."""
    search_norm = _normalize_search(search)
    if not search_norm:
        return []
    tasks = get_active_tasks_ordered(user_id)
    out = []
    for t in tasks:
        task_text = _normalize_search(t.get("text") or "")
        if not task_text:
            continue
        if search_norm in task_text:
            out.append(t)
            continue
        words = [w for w in search_norm.split() if w]
        if words and all(w in task_text for w in words):
            out.append(t)
    return out


def _get_today_in_user_tz(user_id: int) -> tuple[str, int]:
    """Возвращает (дата YYYY-MM-DD в часовом поясе пользователя, weekday 0=пн..6=вс)."""
    row = _fetchone("SELECT timezone FROM users WHERE id = %s", (user_id,))
    tz_name = "Europe/Moscow"
    if row and row.get("timezone"):
        tz_name = (row["timezone"] or "").strip() or tz_name
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now(timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.weekday()  # weekday: 0=Monday, 6=Sunday


# День недели для рутин: пн=0, вт=1, ср=2, чт=3, пт=4, сб=5, вс=6 (как в Python weekday)
_ROUTINE_DAY_MAP = {
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
    "ежедневно": -1,  # показывать каждый день
}


def _routine_matches_today(task: dict, today_weekday: int) -> bool:
    """Проверяет, должна ли рутина показываться сегодня (по repeat_day)."""
    repeat = (task.get("repeat_day") or "").strip()
    if not repeat:
        return True
    if repeat.lower() == "ежедневно":
        return True
    days = [d.strip().lower() for d in repeat.split(",")]
    for d in days:
        wd = _ROUTINE_DAY_MAP.get(d)
        if wd == today_weekday or wd == -1:
            return True
    return False


def get_today_tasks(user_id: int) -> list[dict]:
    """Задачи на сегодня: дата считается в часовом поясе пользователя; рутины — только по repeat_day."""
    today_str, today_weekday = _get_today_in_user_tz(user_id)
    rows = _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' "
        "AND (due_date = %s OR due_date IS NULL) "
        "ORDER BY priority_score DESC",
        (user_id, today_str),
    )
    result = []
    for t in rows:
        if t.get("is_routine"):
            if _routine_matches_today(t, today_weekday):
                result.append(t)
        else:
            result.append(t)
    return result


def complete_task(task_id: int, user_id: int | None = None) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    if user_id is not None:
        n = _execute(
            "UPDATE tasks SET status = 'done', completed_at = %s WHERE id = %s AND user_id = %s AND status = 'active'",
            (now, task_id, user_id),
        )
    else:
        n = _execute(
            "UPDATE tasks SET status = 'done', completed_at = %s WHERE id = %s AND status = 'active'",
            (now, task_id),
        )
    logger.info("complete_task: task_id=%s user_id=%s rows_updated=%s", task_id, user_id, n)
    return n > 0


def find_task_by_text(user_id: int, search: str) -> dict | None:
    search_lower = search.lower().strip()
    if not search_lower:
        return None
    tasks = get_active_tasks(user_id)
    for t in tasks:
        if search_lower in t["text"].lower():
            return t
    for t in tasks:
        words = search_lower.split()
        if all(w in t["text"].lower() for w in words):
            return t
    return None


def find_tasks_by_texts(user_id: int, searches: list[str]) -> list[dict]:
    found = []
    seen_ids = set()
    for search in searches:
        task = find_task_by_text(user_id, search)
        if task and task["id"] not in seen_ids:
            found.append(task)
            seen_ids.add(task["id"])
    return found


def get_done_tasks(user_id: int, days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'done' "
        "AND completed_at >= %s ORDER BY completed_at DESC",
        (user_id, cutoff),
    )


def get_done_tasks_today(user_id: int) -> list[dict]:
    """Выполненные задачи за сегодня (по календарному дню в часовом поясе пользователя)."""
    today_str, _ = _get_today_in_user_tz(user_id)
    row = _fetchone("SELECT timezone FROM users WHERE id = %s", (user_id,))
    tz_name = (row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
            y, m, d = map(int, today_str.split("-"))
            start = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
            end = start + timedelta(days=1)
            start_utc = start.astimezone(timezone.utc).isoformat()
            end_utc = end.astimezone(timezone.utc).isoformat()
        except Exception:
            start_utc = today_str + "T00:00:00+00:00"
            end_dt = datetime.strptime(today_str, "%Y-%m-%d") + timedelta(days=1)
            end_utc = end_dt.strftime("%Y-%m-%d") + "T00:00:00+00:00"
    else:
        start_utc = today_str + "T00:00:00+00:00"
        end_dt = datetime.strptime(today_str, "%Y-%m-%d") + timedelta(days=1)
        end_utc = end_dt.strftime("%Y-%m-%d") + "T00:00:00+00:00"
    return _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'done' "
        "AND completed_at >= %s AND completed_at < %s ORDER BY completed_at DESC",
        (user_id, start_utc, end_utc),
    )


def get_tasks_by_category(user_id: int, category_name: str) -> list[dict]:
    return _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' "
        "AND LOWER(category_name) = LOWER(%s) ORDER BY priority_score DESC",
        (user_id, category_name),
    )


def update_task(task_id: int, user_id: int, **kwargs) -> dict | None:
    allowed = {
        "text", "due_date", "due_time", "time_of_day",
        "category_emoji", "category_name",
        "priority_value", "priority_urgency", "priority_risk", "priority_size",
        "is_routine", "repeat_day",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return _fetchone(
            "SELECT * FROM tasks WHERE id = %s AND user_id = %s",
            (task_id, user_id),
        )

    priority_keys = {"priority_value", "priority_urgency", "priority_risk", "priority_size"}
    if fields.keys() & priority_keys:
        current = _fetchone(
            "SELECT priority_value, priority_urgency, priority_risk, priority_size "
            "FROM tasks WHERE id = %s AND user_id = %s",
            (task_id, user_id),
        )
        if not current:
            return None
        pv = fields.get("priority_value", current["priority_value"])
        pu = fields.get("priority_urgency", current["priority_urgency"])
        pr = fields.get("priority_risk", current["priority_risk"])
        ps = fields.get("priority_size", current["priority_size"])
        fields["priority_score"] = _calc_score(pv, pu, pr, ps)

    set_parts = []
    params = []
    for col, val in fields.items():
        set_parts.append(f"{col} = %s")
        params.append(val)
    params.extend([task_id, user_id])

    _execute(
        f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = %s AND user_id = %s",
        tuple(params),
    )
    return _fetchone(
        "SELECT * FROM tasks WHERE id = %s AND user_id = %s",
        (task_id, user_id),
    )


def delete_task(task_id: int, user_id: int) -> bool:
    n = _execute(
        "UPDATE tasks SET status = 'cancelled' WHERE id = %s AND user_id = %s AND status = 'active'",
        (task_id, user_id),
    )
    return n > 0


def get_weekly_stats(user_id: int) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    done_tasks = _fetchall(
        "SELECT category_name FROM tasks WHERE user_id = %s AND status = 'done' "
        "AND completed_at >= %s",
        (user_id, cutoff),
    )
    total_done = len(done_tasks)

    categories_done: dict[str, int] = {}
    for t in done_tasks:
        cat = t["category_name"] or ""
        categories_done[cat] = categories_done.get(cat, 0) + 1

    row = _fetchone(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE user_id = %s AND status = 'active'",
        (user_id,),
    )
    total_active = row["cnt"] if row else 0

    overdue_rows = _fetchall(
        "SELECT category_name, COUNT(*) AS cnt FROM tasks "
        "WHERE user_id = %s AND status = 'active' "
        "AND (due_date < %s OR due_date IS NULL) "
        "GROUP BY category_name ORDER BY cnt DESC",
        (user_id, datetime.now(timezone.utc).strftime("%Y-%m-%d")),
    )
    most_postponed_category = overdue_rows[0]["category_name"] if overdue_rows else None

    return {
        "total_done": total_done,
        "total_active": total_active,
        "categories_done": categories_done,
        "most_postponed_category": most_postponed_category,
    }


def get_routine_tasks(user_id: int) -> list[dict]:
    result = _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' "
        "AND is_routine = TRUE ORDER BY priority_score DESC",
        (user_id,),
    )
    logger.info("get_routine_tasks: user=%s found=%d", user_id, len(result))
    if not result:
        all_tasks = _fetchall(
            "SELECT id, text, is_routine, repeat_day, status FROM tasks WHERE user_id = %s ORDER BY id DESC LIMIT 10",
            (user_id,),
        )
        logger.info("get_routine_tasks debug: last 10 tasks=%s", all_tasks)
    return result


def _calc_score(value: float, urgency: float, risk: float, size: float) -> float:
    if size <= 0:
        size = 1
    return round((value + urgency + risk) / size, 2)


# ── Messages ─────────────────────────────────────────────────────────────

def save_message(user_id: int, role: str, text: str) -> None:
    _execute("INSERT INTO messages (user_id, role, text) VALUES (%s, %s, %s)", (user_id, role, text))


def get_recent_messages(user_id: int, limit: int = 20) -> list[dict]:
    rows = _fetchall(
        "SELECT role, text FROM messages WHERE user_id = %s ORDER BY id DESC LIMIT %s",
        (user_id, limit),
    )
    return list(reversed(rows))
