# -*- coding: utf-8 -*-
"""
Аутентификация: argon2-хеши паролей, CSRF, rate-limit и helper-функции
для извлечения текущего пользователя из сессии.
"""
from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque
from typing import Deque

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import Form, HTTPException, Request

import db

_ph = PasswordHasher()

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 200


# ── Хеширование ─────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    if not stored_hash or not plain:
        return False
    try:
        _ph.verify(stored_hash, plain)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(stored_hash)
    except Exception:
        return False


# ── Валидация ──────────────────────────────────────────────────────────

def validate_email(email: str) -> str | None:
    e = (email or "").strip().lower()
    if not e:
        return "Введите email."
    if len(e) > 200:
        return "Слишком длинный email."
    if not EMAIL_RE.match(e):
        return "Похоже, email указан неверно."
    return None


def validate_password(pwd: str) -> str | None:
    if not pwd:
        return "Введите пароль."
    if len(pwd) < MIN_PASSWORD_LEN:
        return f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов."
    if len(pwd) > MAX_PASSWORD_LEN:
        return "Пароль слишком длинный."
    return None


# ── Rate-limit (in-memory, на один процесс) ─────────────────────────────

_rl_buckets: dict[str, Deque[float]] = defaultdict(deque)
_RL_WINDOW_SEC = 60.0
_RL_MAX_PER_WINDOW = 8


def rate_limit_hit(key: str) -> bool:
    """True, если лимит уже исчерпан и запрос надо отклонить."""
    now = time.monotonic()
    bucket = _rl_buckets[key]
    while bucket and now - bucket[0] > _RL_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _RL_MAX_PER_WINDOW:
        return True
    bucket.append(now)
    if len(_rl_buckets) > 5000:
        for k in list(_rl_buckets.keys())[:1000]:
            if not _rl_buckets[k]:
                _rl_buckets.pop(k, None)
    return False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


# ── CSRF ────────────────────────────────────────────────────────────────

def csrf_token(request: Request) -> str:
    """Возвращает CSRF-токен сессии (создаёт при первом обращении)."""
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


def verify_csrf(request: Request, csrf: str = Form("")) -> None:
    """Зависимость FastAPI: бросает 403, если csrf не совпадает с сессией."""
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(csrf, expected):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


# ── Текущий пользователь из сессии ──────────────────────────────────────

def current_user_id(request: Request) -> int | None:
    raw = request.session.get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def login_user(request: Request, user_id: int) -> None:
    request.session["user_id"] = int(user_id)
    request.session.pop("auth", None)
    request.session["csrf"] = secrets.token_urlsafe(32)


def logout_user(request: Request) -> None:
    request.session.clear()


def is_authenticated(request: Request) -> bool:
    return current_user_id(request) is not None


def get_current_user_row(request: Request) -> dict | None:
    uid = current_user_id(request)
    if uid is None:
        return None
    return db.get_user_by_id(uid)
