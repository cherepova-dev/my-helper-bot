# -*- coding: utf-8 -*-
"""
Синхронные операции над задачами для веб-интерфейса (без Telegram).
Логика совпадает с bot_v2._save_one_task_and_reply и batch complete.
"""
from __future__ import annotations

import logging
from typing import Any

import db
import routines
from categories import assign_category
from task_parsing import (
    parse_due_date,
    parse_due_time,
    infer_time_of_day,
    time_of_day_from_hour,
    clean_task_text_from_datetime,
    normalize_task_display,
)

logger = logging.getLogger(__name__)


def _parse_due_date_with_today(task_text: str):
    from datetime import datetime

    return parse_due_date(task_text, today=datetime.now())


def add_task_from_text(user_row: dict, task_text: str) -> dict[str, Any]:
    """
    Добавляет задачу/рутину. Возвращает
    {ok: bool, message: str} — message для показа пользователю (без Telegram Markdown).
    """
    if not task_text or not task_text.strip():
        return {"ok": False, "message": "Текст задачи пустой."}

    task_text = task_text.strip()
    internal_user_id = user_row["id"]
    settings = db.get_settings(internal_user_id)

    is_routine, repeat_day = routines.is_routine_and_repeat(task_text)
    due_date = _parse_due_date_with_today(task_text) if not is_routine else None
    due_time = parse_due_time(task_text) if not is_routine else None
    time_of_day_val = infer_time_of_day(task_text)
    if not time_of_day_val and due_time and not is_routine:
        try:
            h = int(str(due_time).split(":", 1)[0])
            time_of_day_val = time_of_day_from_hour(h)
        except (ValueError, IndexError):
            time_of_day_val = None

    task_title = (clean_task_text_from_datetime(task_text) or task_text).strip()
    if is_routine:
        task_title = (routines.clean_task_title_from_routine_phrases(task_title) or task_title).strip()
    if task_title:
        task_title = normalize_task_display(task_title)

    _raw_for_cat = (clean_task_text_from_datetime(task_text) or task_text).strip()
    category_emoji, category_name = assign_category(_raw_for_cat)

    if is_routine:
        date_label = "рутина"
    elif due_date:
        lower = task_text.lower()
        if "сегодня" in lower:
            date_label = "сегодня"
        elif "завтра" in lower and "послезавтра" not in lower:
            date_label = "завтра"
        elif "послезавтра" in lower:
            date_label = "послезавтра"
        else:
            date_label = due_date
    elif settings.get("auto_schedule", True):
        from bot_v2 import _auto_schedule_date

        due_date, date_label = _auto_schedule_date(internal_user_id)
    else:
        date_label = "без срока"

    try:
        task_row = db.add_task(
            user_id=internal_user_id,
            text=task_title,
            category_emoji=category_emoji,
            category_name=category_name,
            due_date=due_date,
            due_time=due_time,
            time_of_day=time_of_day_val,
            priority_value=5,
            priority_urgency=5,
            priority_risk=5,
            priority_size=5,
            is_routine=is_routine,
            repeat_day=repeat_day,
        )
        if not task_row:
            return {"ok": False, "message": "Не удалось сохранить задачу."}
        rd = task_row.get("repeat_day") or repeat_day
        msg = f"Задача принята: «{task_title}». Категория: {category_emoji} {category_name}. "
        if is_routine and rd:
            msg += f"Повтор: {db.format_repeat_day_display(rd)}."
        elif due_date:
            msg += f"Срок: {date_label}."
        if time_of_day_val:
            msg += f" Время суток: {time_of_day_val}."
        return {"ok": True, "message": msg.strip()}
    except Exception as e:
        logger.exception("add_task_from_text: %s", e)
        return {"ok": False, "message": "Ошибка при сохранении задачи."}


def complete_task_numbers(user_id: int, nums: list[int]) -> tuple[list[str], list[int]]:
    """
    Отмечает выполненными задачи по глобальным номерам (как в списке задач).
    Возвращает (список заголовков успешных, список номеров с ошибкой).
    """
    from bot_v2 import _active_tasks_display_order

    ordered = _active_tasks_display_order(user_id)
    if not ordered:
        return [], list(range(1, max(nums or [0]) + 1)) if nums else ([], [])

    ok_titles: list[str] = []
    fail_nums: list[int] = []
    for n in sorted(set(nums)):
        if 1 <= n <= len(ordered):
            task = ordered[n - 1]
            if db.complete_task(task["id"], user_id, task=task):
                ok_titles.append(task["text"])
            else:
                fail_nums.append(n)
        else:
            fail_nums.append(n)
    return ok_titles, fail_nums
