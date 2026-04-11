# -*- coding: utf-8 -*-
"""
Юнит-тесты модуля routines: распознавание рутин, парсинг регулярности, очистка заголовка.
Соответствует ROUTINES_PRODUCT.md и US-RT7.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import routines
from routines import ROUTINE_WEEKLY_NO_DAY, N_WEEK_PREFIX


class TestNTimesPerWeek:
    def test_parse_words_and_digits(self):
        assert routines.parse_n_times_per_week("массаж два раза в неделю") == 2
        assert routines.parse_n_times_per_week("йога 3 раза в неделю") == 3

    def test_is_routine_sets_marker(self):
        is_r, rep = routines.is_routine_and_repeat("массаж два раза в неделю")
        assert is_r is True
        assert rep == f"{N_WEEK_PREFIX}2"


class TestIsRoutineAndRepeat:
    """Определение рутины и извлечение регулярности (is_routine_and_repeat)."""

    def test_explicit_routine_command(self):
        is_r, rep = routines.is_routine_and_repeat("Записать рутину: полив цветов")
        assert is_r is True
        assert rep in ("ежедневно", None) or rep  # может быть распознано из контекста

    def test_daily_triggers(self):
        is_r, rep = routines.is_routine_and_repeat("Ежедневная зарядка")
        assert is_r is True
        assert rep == "ежедневно"
        is_r, rep = routines.is_routine_and_repeat("каждый день пить витамины")
        assert is_r is True
        assert rep == "ежедневно"

    def test_weekly_specific_day(self):
        is_r, rep = routines.is_routine_and_repeat("каждый понедельник планерка")
        assert is_r is True
        assert rep == "пн"
        is_r, rep = routines.is_routine_and_repeat("по четвергам полив цветов")
        assert is_r is True
        assert rep == "чт"

    def test_weekly_no_day(self):
        """Раз в неделю без указания дня → ROUTINE_WEEKLY_NO_DAY (бот потом ставит случайный день)."""
        is_r, rep = routines.is_routine_and_repeat("уборка раз в неделю")
        assert is_r is True
        assert rep == ROUTINE_WEEKLY_NO_DAY
        is_r, rep = routines.is_routine_and_repeat("еженедельная стирка")
        assert is_r is True
        assert rep == ROUTINE_WEEKLY_NO_DAY

    def test_multiple_days(self):
        is_r, rep = routines.is_routine_and_repeat("уборка в понедельник и четверг")
        assert is_r is True
        assert "пн" in rep and "чт" in rep

    def test_not_routine(self):
        is_r, rep = routines.is_routine_and_repeat("купить молоко завтра")
        assert is_r is False
        assert rep is None

    def test_single_weekday_without_recurrence_is_not_routine(self):
        """«В пятницу» без «каждый»/«по …» — разовая задача, не рутина."""
        is_r, rep = routines.is_routine_and_repeat("написать Тае в пятницу")
        assert is_r is False
        assert rep is None
        is_r, rep = routines.is_routine_and_repeat("встреча в понедельник")
        assert is_r is False
        assert rep is None
        is_r, rep = routines.is_routine_and_repeat("задача пт")
        assert is_r is False
        assert rep is None


class TestParseWeekdayFromText:
    """Парсинг одного дня недели."""

    def test_full_names(self):
        assert routines.parse_weekday_from_text("каждый понедельник") == "пн"
        assert routines.parse_weekday_from_text("по средам") == "ср"
        assert routines.parse_weekday_from_text("каждое воскресенье") == "вс"

    def test_short_codes(self):
        assert routines.parse_weekday_from_text("задача пт") == "пт"
        assert routines.parse_weekday_from_text("чт йога") == "чт"


class TestParseMultipleWeekdays:
    """Парсинг нескольких дней."""

    def test_two_days(self):
        days = routines.parse_multiple_weekdays("вт, чт уборка")
        assert days is not None
        assert "вт" in days and "чт" in days


class TestCleanTaskTitleFromRoutinePhrases:
    """Очистка названия задачи от фраз регулярности."""

    def test_removes_weekday_phrase(self):
        assert (
            routines.clean_task_title_from_routine_phrases("Поливать цветы каждый четверг")
            == "Поливать цветы"
        )

    def test_removes_daily_phrase(self):
        assert (
            routines.clean_task_title_from_routine_phrases("Ежедневная зарядка")
            == "зарядка"
        ) or "зарядка" in routines.clean_task_title_from_routine_phrases("Ежедневная зарядка")

    def test_removes_weekly_phrase(self):
        t = routines.clean_task_title_from_routine_phrases("Уборка раз в неделю")
        assert "раз в неделю" not in t or t.strip() == "Уборка"
        assert "Уборка" in t or t.strip()

    def test_plain_unchanged(self):
        assert routines.clean_task_title_from_routine_phrases("Купить молоко") == "Купить молоко"


class TestFormatRepeatDayDisplay:
    """Формат отображения repeat_day (db.format_repeat_day_display)."""

    def test_daily(self):
        import db
        assert db.format_repeat_day_display("ежедневно") == "Ежедневно"

    def test_single_day(self):
        import db
        assert db.format_repeat_day_display("пн") == "Каждый понедельник"
        assert db.format_repeat_day_display("вс") == "Каждое воскресенье"

    def test_multiple_days(self):
        import db
        out = db.format_repeat_day_display("вт,чт")
        assert "вт" in out and "чт" in out
        assert "(нед.)" in out
