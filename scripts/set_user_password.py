# -*- coding: utf-8 -*-
"""
Установить email/пароль на существующего пользователя в БД.

Зачем: до этого вход был по общему WEB_APP_PASSWORD, у пользователя не было
ни email, ни password_hash. После миграции на email+пароль нужно «прописать»
учётку, чтобы можно было войти и не потерять задачи/проекты.

Использование (локально, SQLite):
    set BOT_DB_PATH=C:\\Project\\bot\\bot_data.db
    python scripts/set_user_password.py --email me@example.com

Использование (Render, PostgreSQL):
    # в Shell задеплоенного сервиса:
    DATABASE_URL=$DATABASE_URL python scripts/set_user_password.py --email me@example.com

Скрипт интерактивно спросит пароль (без эха), посчитает argon2-хеш и обновит
поля users.email, users.password_hash, users.password_algo. Если в БД больше
одного пользователя — нужно указать --user-id, иначе скрипт остановится.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
from web import auth as web_auth  # noqa: E402


def _resolve_user_id(arg_user_id: int | None) -> int:
    if arg_user_id is not None:
        u = db.get_user_by_id(arg_user_id)
        if not u:
            raise SystemExit(f"Пользователь id={arg_user_id} не найден.")
        return int(u["id"])

    only = db.get_single_user_if_exactly_one()
    if only:
        return int(only["id"])

    n = db.count_users()
    if n == 0:
        raise SystemExit(
            "В таблице users нет ни одной строки. "
            "Зарегистрируйся через /signup и потом, если надо, перепрошьюй пароль этим скриптом."
        )
    ids = db.list_user_ids()
    raise SystemExit(
        f"В БД несколько пользователей ({n}). Укажи явно --user-id <id>. "
        f"Доступные id: {ids}"
    )


def _set_email_and_password(user_id: int, email: str, plain_password: str) -> None:
    """Обновляет email/password_hash напрямую в users — не используем db helper'ов
    `create_user_with_email`, потому что user уже существует."""
    pwd_hash = web_auth.hash_password(plain_password)

    # Проверим, не занят ли email другим аккаунтом.
    other = db.find_user_by_email(email)
    if other and int(other["id"]) != int(user_id):
        raise SystemExit(
            f"Email {email!r} уже привязан к другому пользователю "
            f"(id={other['id']}). Либо выбери другой email, либо сначала "
            f"освободи его в БД."
        )

    n = db._execute(
        "UPDATE users SET email = %s, password_hash = %s, password_algo = %s "
        "WHERE id = %s",
        (email, pwd_hash, "argon2", user_id),
    )
    if n == 0:
        raise SystemExit(f"UPDATE не затронул ни одной строки (id={user_id}).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Email для входа в веб.")
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="ID пользователя в БД. Если не указан — берём единственного.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Пароль (не рекомендуется передавать в командной строке — лучше "
        "интерактивно).",
    )
    args = parser.parse_args()

    email = (args.email or "").strip().lower()
    err = web_auth.validate_email(email)
    if err:
        raise SystemExit(f"Email невалиден: {err}")

    if args.password is not None:
        password = args.password
    else:
        password = getpass.getpass("Новый пароль (минимум 8 символов): ")
        confirm = getpass.getpass("Повторите пароль: ")
        if password != confirm:
            raise SystemExit("Пароли не совпадают.")

    err = web_auth.validate_password(password)
    if err:
        raise SystemExit(f"Пароль невалиден: {err}")

    uid = _resolve_user_id(args.user_id)
    user = db.get_user_by_id(uid)
    print(
        f"Будет обновлён пользователь id={uid} "
        f"(name={user.get('name') or '—'!r}, "
        f"telegram_id={user.get('telegram_id') or '—'}, "
        f"current_email={user.get('email') or '—'!r})."
    )
    if not args.password:
        ans = input("Подтвердить? [y/N]: ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            raise SystemExit("Отменено.")

    _set_email_and_password(uid, email, password)
    print(f"Готово. Можно войти на /login с email={email}.")


if __name__ == "__main__":
    main()
