# -*- coding: utf-8 -*-
"""
Smoke-тесты для нового потока регистрации/входа на FastAPI.

Запуск:
    pytest tests/test_web_auth.py -q

Тесты используют временную SQLite-БД (через DATABASE_URL пуст → SQLite).
Telegram-токен не нужен — мы напрямую дёргаем web.app:app.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "smoke.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BOT_DB_PATH", str(db_path))
    monkeypatch.setenv("WEB_DB_PATH", str(db_path))
    monkeypatch.setenv("WEB_SESSION_SECRET", "test-secret-12345")
    monkeypatch.setenv("RENDER", "")
    monkeypatch.setenv("WEB_HTTPS_ONLY", "")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")

    for mod in ("db", "web", "web.app", "web.auth"):
        sys.modules.pop(mod, None)

    from web.app import app

    with TestClient(app, base_url="http://testserver") as c:
        yield c


def test_landing_for_anonymous(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Создать аккаунт" in r.text


def test_login_page_loads(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Войти" in r.text
    assert 'name="csrf"' in r.text


def test_signup_then_login_flow(client):
    r = client.post(
        "/signup",
        data={
            "email": "alice@example.com",
            "password": "very-secret-1",
            "password2": "very-secret-1",
            "name": "Alice",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    assert r.headers["location"] == "/"

    r = client.get("/")
    assert r.status_code == 200
    # После входа главная не показывает «Создать аккаунт» как CTA лендинга.
    assert "Сегодня" in r.text or "today" in r.text.lower()

    client.cookies.clear()
    r = client.post(
        "/login",
        data={"email": "alice@example.com", "password": "very-secret-1"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/"

    r = client.post(
        "/login",
        data={"email": "alice@example.com", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert r.status_code == 401


def test_signup_rejects_short_password(client):
    r = client.post(
        "/signup",
        data={
            "email": "bob@example.com",
            "password": "abc",
            "password2": "abc",
            "name": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_signup_rejects_duplicate_email(client):
    payload = {
        "email": "carol@example.com",
        "password": "very-secret-1",
        "password2": "very-secret-1",
        "name": "",
    }
    r = client.post("/signup", data=payload, follow_redirects=False)
    assert r.status_code in (302, 303)
    client.cookies.clear()
    r = client.post("/signup", data=payload, follow_redirects=False)
    assert r.status_code == 409


def test_logout_clears_session(client):
    client.post(
        "/signup",
        data={
            "email": "dan@example.com",
            "password": "very-secret-1",
            "password2": "very-secret-1",
            "name": "",
        },
    )
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code in (302, 303)
    r = client.get("/today", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"].startswith("/login")
