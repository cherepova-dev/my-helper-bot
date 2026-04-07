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
    extract_edit_target,
    extract_reschedule_target,
    classify_time_of_day_edit,
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


def delete_task_by_number(user_id: int, num: int, is_routine: bool) -> dict[str, Any]:
    """Удаление по номеру: задачи — глобальный номер из списка; рутины — номер из экрана рутин."""
    from bot_v2 import _active_tasks_display_order

    if is_routine:
        tasks = db.get_routine_tasks(user_id)
        list_name = "рутин"
    else:
        tasks = _active_tasks_display_order(user_id)
        list_name = "задач"
    if not tasks:
        return {"ok": False, "message": f"Нет {list_name} для удаления."}
    if not (1 <= num <= len(tasks)):
        return {
            "ok": False,
            "message": f"Нет {list_name} с номером {num}. В списке от 1 до {len(tasks)}.",
        }
    task = tasks[num - 1]
    if db.delete_task(task["id"], user_id):
        return {"ok": True, "message": f"Удалено: «{task.get('text', '')}»"}
    return {"ok": False, "message": "Не удалось удалить."}


def apply_edit_phrase(user_id: int, phrase: str) -> dict[str, Any]:
    """Текст как в боте: «Изменить задачу 2 на …», «Исправить … на …»."""
    from bot_v2 import _resolve_task_by_num_or_search

    num, search_text, new_text = extract_edit_target(phrase or "")
    if not new_text or not new_text.strip():
        return {
            "ok": False,
            "message": "Укажи новый текст после « на », например: Изменить задачу 2 на Купить хлеб.",
        }
    task = _resolve_task_by_num_or_search(user_id, num, search_text)
    if task is None:
        if num is not None:
            return {"ok": False, "message": f"Нет задачи с номером {num}. Открой «Все задачи» и проверь номер."}
        return {
            "ok": False,
            "message": f"Задача не найдена или найдено несколько совпадений — укажи номер из списка.",
        }
    new_raw = new_text.strip()
    tod_action = classify_time_of_day_edit(new_raw)
    if tod_action != "not":
        new_tod = None if tod_action == "clear" else tod_action
        updated = db.update_task(task["id"], user_id, time_of_day=new_tod)
        if updated:
            title = updated.get("text") or task.get("text", "")
            if tod_action == "clear":
                return {"ok": True, "message": f"Время суток сброшено: «{title}»"}
            return {"ok": True, "message": f"Время суток: {new_tod} — «{title}»"}
        return {"ok": False, "message": "Не удалось обновить время суток."}
    new_title = normalize_task_display(new_raw)
    if new_title and new_title[0].isalpha():
        new_title = new_title[0].upper() + new_title[1:]
    updated = db.update_task(task["id"], user_id, text=new_title)
    if updated:
        return {"ok": True, "message": f"Задача обновлена: «{updated.get('text', new_title)}»"}
    return {"ok": False, "message": "Не удалось изменить задачу."}


def apply_reschedule_phrase(user_id: int, phrase: str) -> dict[str, Any]:
    """Текст как в боте: «Перенеси задачу 3 на завтра»."""
    from datetime import datetime

    from bot_v2 import _resolve_task_by_num_or_search

    num, search_text, due_date, due_time = extract_reschedule_target(phrase or "", today=datetime.now())
    task = _resolve_task_by_num_or_search(user_id, num, search_text)
    if task is None:
        if num is not None:
            return {"ok": False, "message": f"Нет задачи с номером {num}. Проверь номер в списке задач."}
        return {"ok": False, "message": "Задача не найдена или несколько совпадений — укажи номер."}
    if not due_date and not due_time:
        return {
            "ok": False,
            "message": "Укажи новую дату или время после « на », например: на завтра, на пятницу в 10:00.",
        }
    updates = {}
    if task.get("is_routine") and due_date:
        try:
            from datetime import datetime as _dt

            wd = _dt.strptime(due_date, "%Y-%m-%d").weekday()
            day_codes = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
            updates["repeat_day"] = day_codes[wd]
        except Exception:
            pass
    if not task.get("is_routine"):
        if due_date:
            updates["due_date"] = due_date
        if due_time:
            updates["due_time"] = due_time
    if not updates and due_date and task.get("is_routine"):
        try:
            from datetime import datetime as _dt

            wd = _dt.strptime(due_date, "%Y-%m-%d").weekday()
            day_codes = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
            updates["repeat_day"] = day_codes[wd]
        except Exception:
            pass
    if not updates:
        return {"ok": False, "message": "Не удалось определить новую дату. Попробуй: на завтра, на пятницу."}
    updated = db.update_task(task["id"], user_id, **updates)
    if updated:
        if "repeat_day" in updates:
            label = db.format_repeat_day_display(updates["repeat_day"])
            return {"ok": True, "message": f"Рутина перенесена: «{task.get('text', '')}» — {label}"}
        parts = [f"«{updated.get('text', task.get('text', ''))}»"]
        if updates.get("due_date"):
            parts.append(f"на {updates['due_date']}")
        if updates.get("due_time"):
            parts.append(f"в {updates['due_time']}")
        return {"ok": True, "message": "Задача перенесена: " + " ".join(parts)}
    return {"ok": False, "message": "Не удалось перенести задачу."}


def uncomplete_done_today(user_id: int, num: int) -> dict[str, Any]:
    """num — порядковый номер в списке «сделано сегодня» (как в боте), с 1."""
    done_tasks = db.get_done_tasks_today(user_id)
    if not done_tasks:
        return {"ok": False, "message": "Сегодня нет выполненных задач."}
    if not (1 <= num <= len(done_tasks)):
        return {
            "ok": False,
            "message": f"Нет задачи с номером {num}. В списке выполненных сегодня от 1 до {len(done_tasks)}.",
        }
    task = done_tasks[num - 1]
    if db.uncomplete_task(task["id"], user_id):
        return {"ok": True, "message": f"Задача «{task.get('text', '')}» снова в активных."}
    return {"ok": False, "message": "Не удалось отменить выполнение."}


def parse_number_list(s: str) -> list[int]:
    """Строка вида «1, 2, 3» или «1-3» → список номеров."""
    import re

    s = (s or "").strip()
    if not s:
        return []
    out: list[int] = []
    for part in re.split(r"[\s,;]+", s):
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b:
                out.extend(range(a, b + 1))
            continue
        if part.isdigit():
            out.append(int(part))
    return sorted(set(out))
