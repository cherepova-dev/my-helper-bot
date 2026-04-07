# -*- coding: utf-8 -*-
"""Smoke-тесты веб-приложения (без реальной БД — только /health и /login GET)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_APP_PASSWORD", "test-secret")
    monkeypatch.setenv("WEB_SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "t.db"))
    os.environ.pop("DATABASE_URL", None)
    from web.app import app as fastapi_app

    return TestClient(fastapi_app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_login_page(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Пароль" in r.text or "password" in r.text.lower()


def test_root_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/login" in r.headers.get("location", "")
