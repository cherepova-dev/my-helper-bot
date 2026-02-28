# -*- coding: utf-8 -*-
"""
Хранилище данных — PostgreSQL (Supabase) или SQLite (локально).
Если задан DATABASE_URL — PostgreSQL, иначе — SQLite.
Многопользовательская изоляция: все запросы фильтруются по user_id.
"""

import os
import logging
from datetime import datetime, timezone

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
) -> dict:
    score = _calc_score(priority_value, priority_urgency, priority_risk, priority_size)
    return _insert_returning(
        """INSERT INTO tasks
           (user_id, text, category_emoji, category_name,
            due_date, due_time, time_of_day,
            priority_value, priority_urgency, priority_risk, priority_size, priority_score)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (user_id, text, category_emoji, category_name,
         due_date, due_time, time_of_day,
         priority_value, priority_urgency, priority_risk, priority_size, score),
    )


def get_active_tasks(user_id: int) -> list[dict]:
    return _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' ORDER BY priority_score DESC",
        (user_id,),
    )


def get_today_tasks(user_id: int) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _fetchall(
        "SELECT * FROM tasks WHERE user_id = %s AND status = 'active' "
        "AND (due_date = %s OR due_date IS NULL) "
        "ORDER BY priority_score DESC",
        (user_id, today),
    )


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
