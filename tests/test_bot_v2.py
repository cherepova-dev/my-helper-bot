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
    _format_done_report_today,
    _format_done_report_week,
    _group_tasks_by_time_bucket,
    _task_time_bucket,
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
        assert "задач" in result.lower()

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

    def test_routines_block_and_regularity(self):
        """Список с рутинами: отдельный блок «Рутины» с подписью регулярности (RT-F7)."""
        tasks = [
            {"text": "разовая задача", "category_emoji": "📝", "due_date": "2026-03-05", "due_time": None, "is_routine": False},
            {"text": "полив цветов", "category_emoji": "🔁", "due_date": None, "due_time": None, "is_routine": True, "repeat_day": "чт"},
        ]
        result = _format_task_list(tasks)
        assert "Рутины" in result
        assert "полив цветов" in result
        assert "четверг" in result.lower() or "Четверг" in result
        assert "глоб." not in result

    def test_project_emoji_for_project_task(self):
        tasks = [
            {
                "text": "шаг",
                "category_emoji": "📝",
                "project_id": 1,
                "project_emoji": "🏗",
                "project_title": "Ремонт",
                "due_date": "2026-03-05",
                "due_time": None,
                "is_routine": False,
            },
        ]
        result = _format_task_list(tasks)
        assert "🏗" in result
        assert "проект" in result.lower()


class TestBuildConfirmation:
    """Текст подтверждения задачи (_build_confirmation)."""

    def test_contains_task_text_and_acceptance(self):
        result = _build_confirmation("купить молоко", "2026-03-03", "завтра", None)
        assert "купить молоко" in result
        assert "Задача принята" in result

    def test_contains_category(self):
        result = _build_confirmation(
            "уборка", "2026-03-03", "сегодня", None,
            category_emoji="🏠",
            category_name="Быт / дом",
        )
        assert "Быт / дом" in result
        assert "🏠" in result

    def test_routine_confirmation_shows_repeat(self):
        """Подтверждение рутины показывает регулярность вместо даты (🔁)."""
        result = _build_confirmation(
            "Зарядка", None, "рутина", None,
            category_emoji="🌿",
            category_name="Для себя",
            is_routine=True,
            repeat_day="ежедневно",
        )
        assert "Зарядка" in result
        assert "Ежедневно" in result
        assert "🔁" in result

    def test_time_of_day_in_confirmation(self):
        result = _build_confirmation(
            "Зарядка", None, "рутина", None,
            is_routine=True,
            repeat_day="ежедневно",
            time_of_day="утро",
        )
        assert "Время суток" in result
        assert "утро" in result


class TestFormatTodayList:
    """Список на сегодня (_format_today_list). Пары (номер в общем порядке, задача) — номер не выводится."""

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
        assert "•" in result
        assert "Утро" in result


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

    def test_with_category_emoji(self):
        tasks = [{"text": "Помыть посуду", "category_emoji": "🏠"}]
        result = _format_done_report(tasks, "Сделано сегодня")
        assert "🏠" in result
        assert "Помыть посуду" in result


class TestFormatDoneReportToday:
    """Отчёт за день с группировкой по категориям."""

    def test_empty_friendly(self):
        result = _format_done_report_today([], "Europe/Moscow")
        assert "Сделано сегодня" in result
        assert "ни одной задачи" in result or "план" in result.lower()

    def test_with_tasks_grouped(self):
        tasks = [
            {"text": "Купить молоко", "category_emoji": "🏠", "category_name": "Быт / дом"},
            {"text": "Помыть посуду", "category_emoji": "🏠", "category_name": "Быт / дом"},
        ]
        result = _format_done_report_today(tasks, "Europe/Moscow")
        assert "2" in result
        assert "Быт" in result or "быт" in result.lower()
        assert "Купить молоко" in result and "Помыть посуду" in result


class TestFormatDoneReportWeek:
    """Отчёт за неделю с группировкой по дням."""

    def test_empty_friendly(self):
        result = _format_done_report_week([], "Europe/Moscow")
        assert "Сделано за неделю" in result
        assert "7 дней" in result or "ни одной" in result


class TestTaskTimeBucket:
    """Блок суток: при наличии due_time ориентируемся на час, а не на устаревшее time_of_day."""

    def test_due_time_overrides_time_of_day(self):
        t = {"time_of_day": "утро", "due_time": "14:30", "id": 1}
        assert _task_time_bucket(t) == "день"

    def test_twelve_is_day_not_morning(self):
        assert _task_time_bucket({"due_time": "12:00", "id": 1}) == "день"

    def test_without_due_time_uses_time_of_day(self):
        assert _task_time_bucket({"time_of_day": "вечер", "due_time": None, "id": 1}) == "вечер"

    def test_group_sorts_timed_before_untimed(self):
        pairs = [
            (1, {"id": 10, "text": "без времени", "due_time": None, "time_of_day": "утро"}),
            (2, {"id": 11, "text": "в 09:00", "due_time": "09:00"}),
            (3, {"id": 12, "text": "в 11:00", "due_time": "11:00"}),
        ]
        by_bucket = {b: pr for b, pr in _group_tasks_by_time_bucket(pairs)}
        nums = [p[0] for p in by_bucket["утро"]]
        assert nums == [2, 3, 1]


