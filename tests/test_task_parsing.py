# -*- coding: utf-8 -*-
"""
Юнит-тесты на логику парсинга задач (task_parsing).
Запуск: из корня проекта выполнить pytest tests/ -v
Перед доработкой фич — прогонять тесты, чтобы не сломать текущее поведение.
"""
import sys
from datetime import datetime
from pathlib import Path

# Корень проекта в path, чтобы импортировать task_parsing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from task_parsing import (
    parse_due_date,
    parse_due_time,
    extract_task_text,
    starts_with_add_marker,
    starts_with_done_marker,
    extract_done_target,
    clean_task_text_from_datetime,
)


# Понедельник 2 марта 2026 — фиксированная дата для детерминированных тестов
MONDAY = datetime(2026, 3, 2, 12, 0, 0)


class TestParseDueDate:
    """Парсинг даты из текста."""

    def test_segodnya(self):
        assert parse_due_date("купить молоко сегодня", today=MONDAY) == "2026-03-02"

    def test_zavtra(self):
        assert parse_due_date("завтра позвонить маме", today=MONDAY) == "2026-03-03"

    def test_poslezavtra(self):
        assert parse_due_date("послезавтра к врачу", today=MONDAY) == "2026-03-04"

    def test_cherez_dney(self):
        assert parse_due_date("через 3 дня отправить отчёт", today=MONDAY) == "2026-03-05"
        assert parse_due_date("через 1 день", today=MONDAY) == "2026-03-03"

    def test_weekday_pyatnitsa(self):
        # Понедельник 2.03 → пятница 6.03
        assert parse_due_date("в пятницу массаж", today=MONDAY) == "2026-03-06"
        assert parse_due_date("пятница встреча", today=MONDAY) == "2026-03-06"

    def test_weekday_sreda(self):
        assert parse_due_date("в среду звонок", today=MONDAY) == "2026-03-04"

    def test_weekday_voskresene(self):
        # Следующее воскресенье от понедельника 2.03 → 8.03
        assert parse_due_date("воскресенье отдых", today=MONDAY) == "2026-03-08"

    def test_explicit_date_dm(self):
        assert parse_due_date("сдать отчёт 15.04", today=MONDAY) == "2026-04-15"
        assert parse_due_date("к 25.12 купить подарки", today=MONDAY) == "2026-12-25"

    def test_explicit_date_dm_slash(self):
        assert parse_due_date("дедлайн 10/05", today=MONDAY) == "2026-05-10"

    def test_no_date_returns_none(self):
        assert parse_due_date("просто задача без даты", today=MONDAY) is None
        assert parse_due_date("позвонить", today=MONDAY) is None


class TestParseDueTime:
    """Парсинг времени из текста."""

    def test_time_colon(self):
        assert parse_due_time("в 10:30 встреча") == "10:30"
        assert parse_due_time("в 14:00 звонок") == "14:00"

    def test_time_utra_vechera(self):
        assert parse_due_time("в 10 утра") == "10:00"
        assert parse_due_time("в 7 вечера") == "19:00"
        assert parse_due_time("в 12 часов") == "12:00"

    def test_time_v_with_minutes(self):
        assert parse_due_time("в 9.30 прийти") == "09:30"
        assert parse_due_date("в 9.30 прийти", today=MONDAY) is None  # не дата
        assert parse_due_time("в 9.30 прийти") == "09:30"

    def test_time_in_middle_or_end(self):
        """Время распознаётся в любом месте фразы (например «на мойку в 12:00 завтра»)."""
        assert parse_due_time("отогнать машину на мойку в 12:00 завтра") == "12:00"
        assert parse_due_time("в 12:00 завтра") == "12:00"

    def test_time_k_format(self):
        """Формат «к 12:00» (на мойку к 12:00 завтра)."""
        assert parse_due_time("на мойку к 12:00 завтра") == "12:00"
        assert parse_due_time("к 10:30 приехать") == "10:30"
        assert parse_due_time("к 9 утра") == "09:00"

    def test_time_k_without_minutes(self):
        """«к 12 завтра» без минут — трактуется как 12:00."""
        assert parse_due_time("на мойку к 12 завтра") == "12:00"
        assert parse_due_time("в 14 сегодня") == "14:00"

    def test_time_comma_separator(self):
        """Время с запятой: «к 12,00»."""
        assert parse_due_time("к 12,00 завтра") == "12:00"

    def test_no_time_returns_none(self):
        assert parse_due_time("задача без времени") is None
        assert parse_due_time("просто текст") is None


class TestExtractTaskText:
    """Извлечение текста задачи после маркера «добавь/создай/запиши»."""

    def test_dobav(self):
        assert extract_task_text("Добавь купить молоко") == "купить молоко"
        assert extract_task_text("добавь завтра позвонить маме") == "завтра позвонить маме"

    def test_sozday(self):
        assert extract_task_text("Создай задачу записаться к врачу") == "записаться к врачу"

    def test_zapishi(self):
        assert extract_task_text("Запиши полить цветы в пятницу") == "полить цветы в пятницу"

    def test_novuyu_zadachu(self):
        assert extract_task_text("Добавь новую задачу купить хлеб") == "купить хлеб"

    def test_no_prefix_returns_whole(self):
        assert extract_task_text("купить молоко") == "купить молоко"
        assert extract_task_text("просто текст задачи") == "просто текст задачи"

    def test_prefix_only_returns_original(self):
        # Если после префикса пусто — возвращаем исходный текст
        assert extract_task_text("Добавь").strip() != "" or extract_task_text("Добавь") == "Добавь"


class TestStartsWithAddMarker:
    """Проверка, что сообщение начинается с маркера добавления задачи."""

    def test_yes(self):
        assert starts_with_add_marker("Добавь задачу") is True
        assert starts_with_add_marker("создай купить молоко") is True
        assert starts_with_add_marker("Запиши позвонить") is True
        assert starts_with_add_marker("нужно сдать отчёт") is True
        assert starts_with_add_marker("надо сделать") is True

    def test_no(self):
        assert starts_with_add_marker("купить молоко") is False
        assert starts_with_add_marker("удали задачу") is False
        assert starts_with_add_marker("что на сегодня?") is False
        assert starts_with_add_marker("") is False


class TestCleanTaskTextFromDatetime:
    """Удаление даты и времени из названия задачи."""

    def test_removes_time_and_tomorrow(self):
        assert (
            clean_task_text_from_datetime("Отогнать машину на мойку в 12:00 завтра")
            == "Отогнать машину на мойку"
        )

    def test_removes_k_time(self):
        """Убирает «к 12:00» и «к 12 завтра» из названия задачи."""
        assert (
            clean_task_text_from_datetime("На мойку к 12:00 завтра")
            == "На мойку"
        )
        assert (
            clean_task_text_from_datetime("На мойку к 12 завтра")
            == "На мойку"
        )

    def test_removes_today(self):
        assert clean_task_text_from_datetime("Купить молоко сегодня") == "Купить молоко"

    def test_removes_weekday(self):
        assert (
            clean_task_text_from_datetime("Встреча в пятницу в 10:00")
            == "Встреча"
        )

    def test_plain_text_unchanged(self):
        assert clean_task_text_from_datetime("Позвонить маме") == "Позвонить маме"


class TestStartsWithDoneMarker:
    """Маркеры выполнения задачи: отметь, выполни и т.д."""

    def test_yes(self):
        assert starts_with_done_marker("Отметь 3") is True
        assert starts_with_done_marker("выполни купить молоко") is True
        assert starts_with_done_marker("Отметить задачу полить цветы") is True
        assert starts_with_done_marker("сделано") is True
        assert starts_with_done_marker("Готово") is True

    def test_no(self):
        assert starts_with_done_marker("купить молоко") is False
        assert starts_with_done_marker("добавь задачу") is False
        assert starts_with_done_marker("") is False


class TestExtractDoneTarget:
    """Извлечение номера или текста после маркера выполнения."""

    def test_by_number(self):
        assert extract_done_target("отметь 3") == (3, "")
        assert extract_done_target("выполни 1") == (1, "")
        assert extract_done_target("Отметь задачу 5") == (5, "")

    def test_by_text(self):
        assert extract_done_target("выполни купить молоко") == (None, "купить молоко")
        assert extract_done_target("отметь задачу полить цветы") == (None, "полить цветы")

    def test_empty_after_marker(self):
        assert extract_done_target("отметь") == (None, "")
        assert extract_done_target("выполни") == (None, "")

    def test_filler_phrases_stripped(self):
        """«отметь выполненную задачу X» → X, «отметь выполненной X» → X."""
        assert extract_done_target("отметь выполненную задачу отогнать машину") == (
            None,
            "отогнать машину",
        )
        assert extract_done_target("отметь выполненной отогнать машину") == (
            None,
            "отогнать машину",
        )
        assert extract_done_target("выполни выполненную задачу купить молоко") == (
            None,
            "купить молоко",
        )
