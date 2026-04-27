# -*- coding: utf-8 -*-
"""Smoke-тесты веб-приложения (in-memory SQLite, новый поток email/пароль)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("WEB_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RENDER", "")
    monkeypatch.setenv("WEB_HTTPS_ONLY", "")
    sys.modules.pop("web.app", None)
    sys.modules.pop("web.auth", None)
    sys.modules.pop("db", None)
    from web.app import app as fastapi_app

    c = TestClient(fastapi_app)
    return c


def _signup(client, email="user@example.com", password="very-secret-1"):
    return client.post(
        "/signup",
        data={
            "email": email,
            "password": password,
            "password2": password,
            "name": "",
        },
        follow_redirects=False,
    )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_login_page(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Email" in r.text and "Пароль" in r.text


def test_root_landing_for_anon(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Создать аккаунт" in r.text


def test_home_after_login(client):
    _signup(client)
    r = client.get("/")
    assert r.status_code == 200
    assert "Что делаем" in r.text or "Главная" in r.text or "Сегодня" in r.text


def test_projects_page_after_login(client):
    _signup(client)
    r = client.get("/projects")
    assert r.status_code == 200
    assert "Проекты" in r.text
    assert "Новый проект" in r.text


def test_reports_projects_after_login(client):
    _signup(client)
    r = client.get("/reports/projects")
    assert r.status_code == 200
    assert "проект" in r.text.lower()
