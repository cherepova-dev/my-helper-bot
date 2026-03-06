# -*- coding: utf-8 -*-
"""
Юнит-тесты логики bot_v2: парсинг даты/времени, извлечение текста задачи,
маркеры добавления, форматирование списка и подтверждения.
Запуск: pytest tests/ -v
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from task_parsing import (
    parse_due_date,
    parse_due_time,
    extract_task_text,
    starts_with_add_marker,
)
from bot_v2 import (
    _format_task_list,
    _build_confirmation,
    _format_today_list,
    _format_done_report,
)


MONDAY = datetime(2026, 3, 2, 12, 0, 0)


class TestParseDueDate:
    """Парсинг даты из текста (_parse_due_date → task_parsing.parse_due_date)."""

    def test_segodnya(self):
        assert parse_due_date("сегодня купить молоко", today=MONDAY) == "2026-03-02"

    def test_zavtra(self):
        assert parse_due_date("завтра позвонить", today=MONDAY) == "2026-03-03"

    def test_v_pyatnitsu(self):
        assert parse_due_date("в пятницу встреча", today=MONDAY) == "2026-03-06"

    def test_cherez_dva_dnya(self):
        assert parse_due_date("через 2 дня отчёт", today=MONDAY) == "2026-03-04"

    def test_explicit_date(self):
        assert parse_due_date("дедлайн 25.03", today=MONDAY) == "2026-03-25"

    def test_no_date_returns_none(self):
        assert parse_due_date("просто задача без даты", today=MONDAY) is None
        assert parse_due_date("купить молоко", today=MONDAY) is None


class TestParseDueTime:
    """Парсинг времени из текста (_parse_due_time → task_parsing.parse_due_time)."""

    def test_v_10_30(self):
        assert parse_due_time("в 10:30 встреча") == "10:30"

    def test_v_9_utra(self):
        assert parse_due_time("в 9 утра прийти") == "09:00"

    def test_no_time_returns_none(self):
        assert parse_due_time("задача без времени") is None
        assert parse_due_time("просто текст") is None


class TestExtractTaskText:
    """Извлечение текста задачи после маркера (_extract_task_text)."""

    def test_dobav_kupi_moloko(self):
        assert extract_task_text("Добавь купить молоко") == "купить молоко"

    def test_sozday_zadachu_x(self):
        assert extract_task_text("Создай задачу записаться к врачу") == "записаться к врачу"
        assert extract_task_text("Создай задачу X") == "X"

    def test_no_prefix_unchanged(self):
        assert extract_task_text("Купить молоко") == "Купить молоко"
        assert extract_task_text("просто текст") == "просто текст"


class TestStartsWithAddMarker:
    """Проверка маркера добавления задачи (_starts_with_add_marker)."""

    def test_true_dobav_sozday_zapishi(self):
        assert starts_with_add_marker("Добавь купить молоко") is True
        assert starts_with_add_marker("Создай задачу") is True
        assert starts_with_add_marker("Запиши позвонить") is True

    def test_false_plain_text(self):
        assert starts_with_add_marker("Купить молоко") is False


class TestFormatTaskList:
    """Форматирование списка задач (_format_task_list)."""

    def test_empty_list_no_tasks_message(self):
        result = _format_task_list([])
        assert "нет активных задач" in result.lower() or "пока нет" in result.lower()

    def test_one_task_contains_text(self):
        tasks = [{"text": "купить молоко", "category_emoji": "📝", "due_date": None, "due_time": None}]
        result = _format_task_list(tasks)
        assert "купить молоко" in result
        assert "1" in result or "задач" in result.lower()

    def test_human_date_and_grouping(self):
        """Список: человекочитаемая дата (например «3 марта 2026»), группировка по дате, сортировка по дате и времени."""
        tasks = [
            {"text": "позвонить", "category_emoji": "📝", "due_date": "2026-03-05", "due_time": "10:00"},
            {"text": "встреча", "category_emoji": "📝", "due_date": "2026-03-05", "due_time": "14:00"},
            {"text": "без срока", "category_emoji": "📝", "due_date": None, "due_time": None},
        ]
        result = _format_task_list(tasks)
        assert "марта" in result or "март" in result
        assert "2026" in result
        assert "позвонить" in result and "встреча" in result
        assert result.index("позвонить") < result.index("встреча")
        assert "Без срока" in result or "без срока" in result.lower()


class TestBuildConfirmation:
    """Текст подтверждения задачи (_build_confirmation)."""

    def test_contains_task_text_and_acceptance(self):
        result = _build_confirmation("купить молоко", "2026-03-03", "завтра", None)
        assert "купить молоко" in result
        assert "Задача принята" in result


class TestFormatTodayList:
    """Список на сегодня с нумерацией (_format_today_list). Принимает список пар (номер, задача)."""

    def test_empty(self):
        result = _format_today_list([])
        assert "План на сегодня" in result

    def test_with_tasks(self):
        ordered_today = [
            (2, {"text": "позвонить", "category_emoji": "📝", "due_time": "10:00", "id": 1}),
            (5, {"text": "встреча", "category_emoji": "📝", "due_time": None, "id": 2}),
        ]
        result = _format_today_list(ordered_today)
        assert "позвонить" in result and "встреча" in result
        assert "2." in result and "5." in result


class TestFormatDoneReport:
    """Отчёт по выполненным (_format_done_report)."""

    def test_empty(self):
        result = _format_done_report([], "Сделано сегодня")
        assert "Сделано сегодня" in result
        assert "Нет выполненных" in result

    def test_with_tasks(self):
        tasks = [{"text": "Купить молоко"}, {"text": "Позвонить"}]
        result = _format_done_report(tasks, "Сделано за неделю")
        assert "Купить молоко" in result and "Позвонить" in result
