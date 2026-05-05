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


def _promote_session_user_admin(email="user@example.com"):
    import db

    u = db.find_user_by_email(email)
    assert u
    db.set_user_role(int(u["id"]), "admin")


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


def test_plan_evening_time_of_day_starts_in_evening_band(client):
    """Задача с блоком «вечер» получает слот не раньше полосы «Вечер» (17:00+) на сетке."""
    _signup(client)
    _promote_session_user_admin()
    import db

    u = db.find_user_by_email("user@example.com")
    assert u
    row = db.add_task(
        user_id=u["id"],
        text="walk",
        category_emoji="x",
        category_name="y",
        due_date="2026-08-10",
        time_of_day="вечер",
    )
    assert row
    db.set_task_estimate(u["id"], int(row["id"]), 30)

    r = client.get("/plan?date=2026-08-10")
    assert r.status_code == 200
    slots = db.get_plan_slots(u["id"], "2026-08-10")
    assert len(slots) == 1
    assert int(slots[0]["start_min"]) >= 17 * 60


def test_plan_forbidden_for_non_admin(client):
    _signup(client)
    r = client.get("/plan", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/today"


def test_plan_autoplaces_routine_with_time_bucket_only(client):
    """Рутина без due_time, только блок «утро» — попадает в план при открытии /plan."""
    _signup(client)
    _promote_session_user_admin()
    import db

    u = db.find_user_by_email("user@example.com")
    assert u
    row = db.add_task(
        user_id=u["id"],
        text="разминка",
        category_emoji="🔁",
        category_name="Спорт",
        is_routine=True,
        repeat_day="ежедневно",
        time_of_day="утро",
    )
    assert row
    db.set_task_estimate(u["id"], int(row["id"]), 10)
    tid = str(int(row["id"]))

    r = client.get("/plan?date=2026-05-05")
    assert r.status_code == 200
    assert tid in r.text


def test_plan_autoplaces_routine_estimate_only(client):
    """Рутина ежедневно только с оценкой 5 мин — автослот от начала сетки (9:00)."""
    _signup(client)
    _promote_session_user_admin()
    import db

    u = db.find_user_by_email("user@example.com")
    assert u
    row = db.add_task(
        user_id=u["id"],
        text="заправить постель",
        category_emoji="🔁",
        category_name="Быт",
        is_routine=True,
        repeat_day="ежедневно",
        time_of_day=None,
    )
    assert row
    db.set_task_estimate(u["id"], int(row["id"]), 5)
    tid = str(int(row["id"]))

    r = client.get("/plan?date=2026-05-06")
    assert r.status_code == 200
    assert tid in r.text


def test_plan_routine_with_stale_due_date_still_autoplaces(client):
    """Рутина с «чужой» due_date в БД всё равно попадает в план по repeat_day (ежедневно)."""
    _signup(client)
    _promote_session_user_admin()
    import db

    u = db.find_user_by_email("user@example.com")
    assert u
    row = db.add_task(
        user_id=u["id"],
        text="зарядка тест",
        category_emoji="🔁",
        category_name="Спорт",
        is_routine=True,
        repeat_day="ежедневно",
        time_of_day="утро",
        due_date="2026-04-01",
    )
    assert row
    db.set_task_estimate(u["id"], int(row["id"]), 15)
    tid = int(row["id"])

    assert any(int(t["id"]) == tid for t in db.get_tasks_for_date(u["id"], "2026-05-06"))

    r = client.get("/plan?date=2026-05-06")
    assert r.status_code == 200
    assert str(tid) in r.text


def test_ensure_plan_keeps_slot_when_task_done_that_day(monkeypatch, tmp_path):
    """Слот не снимается при prune: задача выполнена в дату плана и выпала из активных."""
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "plan_done.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("db", None)
    import db

    u = db.create_user_with_email("plan-done@example.com", "h", "")
    uid = int(u["id"])
    plan_date = "2026-09-01"
    row = db.add_task(
        user_id=uid,
        text="сделать отчёт",
        category_emoji="📝",
        category_name="Работа",
        due_date=plan_date,
        time_of_day="день",
    )
    assert row
    tid = int(row["id"])
    db.set_task_estimate(uid, tid, 25)

    db.ensure_plan_slots_from_due_time(uid, plan_date, 9 * 60)
    slots1 = db.get_plan_slots(uid, plan_date)
    assert len(slots1) == 1

    db._execute(
        "UPDATE tasks SET status = 'done', completed_at = %s WHERE id = %s AND user_id = %s",
        ("2026-09-01T14:00:00+00:00", tid, uid),
    )

    db.ensure_plan_slots_from_due_time(uid, plan_date, 9 * 60)
    slots2 = db.get_plan_slots(uid, plan_date)
    assert len(slots2) == 1
    assert int(slots2[0]["task_id"]) == tid


def test_ensure_plan_keeps_routine_slot_when_completed(monkeypatch, tmp_path):
    """Рутина после отметки «сделано» остаётся в слотах плана (зелёная метка)."""
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "routine_plan.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("db", None)
    import db

    u = db.create_user_with_email("routine-plan@example.com", "h", "")
    uid = int(u["id"])
    plan_date = "2026-10-03"
    row = db.add_task(
        user_id=uid,
        text="зарядка план",
        category_emoji="🔁",
        category_name="Спорт",
        is_routine=True,
        repeat_day="ежедневно",
        time_of_day="утро",
    )
    tid = int(row["id"])
    db.set_task_estimate(uid, tid, 10)

    db.ensure_plan_slots_from_due_time(uid, plan_date, 9 * 60)
    assert len(db.get_plan_slots(uid, plan_date)) == 1

    # Отметка «выполнено» в календарный день плана (не «сейчас», иначе другой день).
    done_iso = "2026-10-03T14:00:00+00:00"
    db._execute(
        "UPDATE tasks SET last_completed_at = %s WHERE id = %s AND user_id = %s",
        (done_iso, tid, uid),
    )
    db.log_routine_completion(uid, tid, done_iso)

    db.ensure_plan_slots_from_due_time(uid, plan_date, 9 * 60)
    slots = db.get_plan_slots(uid, plan_date)
    assert len(slots) == 1
    assert int(slots[0]["task_id"]) == tid
    assert db.is_task_done_on_local_date(
        uid,
        plan_date,
        {
            "task_id": tid,
            "is_routine": slots[0].get("is_routine"),
            "status": slots[0].get("status"),
            "completed_at": slots[0].get("completed_at"),
            "last_completed_at": slots[0].get("last_completed_at"),
        },
    )
