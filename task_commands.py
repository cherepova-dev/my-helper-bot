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


def add_task_from_text(
    user_row: dict,
    task_text: str,
    project_id: int | None = None,
) -> dict[str, Any]:
    """
    Добавляет задачу/рутину. Возвращает
    {ok: bool, message: str} — message для показа пользователю (без Telegram Markdown).
    """
    if not task_text or not task_text.strip():
        return {"ok": False, "message": "Текст задачи пустой."}

    task_text = task_text.strip()
    internal_user_id = user_row["id"]
    if project_id is not None:
        if not db.get_project(internal_user_id, int(project_id)):
            return {"ok": False, "message": "Проект не найден."}
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
    category_emoji, category_name = assign_category(_raw_for_cat, user_row["id"])

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
            project_id=project_id,
        )
        if not task_row:
            return {"ok": False, "message": "Не удалось сохранить задачу."}
        tid = int(task_row["id"])
        if project_id is not None:
            db.append_color_sort_new_project_task(internal_user_id, int(project_id), tid)
        rd = task_row.get("repeat_day") or repeat_day
        msg = f"Задача принята: «{task_title}». Категория: {category_emoji} {category_name}. "
        if is_routine and rd:
            msg += f"Повтор: {db.format_repeat_day_display(rd)}."
        elif due_date:
            msg += f"Срок: {date_label}."
        if time_of_day_val:
            msg += f" Время суток: {time_of_day_val}."
        if project_id is not None:
            proj = db.get_project(internal_user_id, int(project_id))
            if proj:
                pem = (proj.get("emoji") or "📁").strip() or "📁"
                msg += f" Проект: {pem} {(proj.get('title') or '').strip()}."
        return {"ok": True, "message": msg.strip()}
    except Exception as e:
        logger.exception("add_task_from_text: %s", e)
        return {"ok": False, "message": "Ошибка при сохранении задачи."}


def _default_big_project_category() -> tuple[str, str]:
    from categories import CATEGORIES

    for cid, em, nm, _kw in CATEGORIES:
        if cid == "projects":
            return em, nm
    return "🧠", "Большие проекты"


def add_project_task_from_text(
    user_row: dict, project_id: int, task_text: str
) -> dict[str, Any]:
    """
    Добавляет обычную задачу внутри проекта. Рутины в проект не кладём.
    По умолчанию: категория «Большие проекты», без срока (дату задаёшь отдельно).
    Срок и время — только если явно указаны в тексте («завтра», «в 15:00»).
    """
    if not task_text or not task_text.strip():
        return {"ok": False, "message": "Текст задачи пустой."}

    task_text = task_text.strip()
    internal_user_id = user_row["id"]
    if not db.get_project(internal_user_id, project_id):
        return {"ok": False, "message": "Проект не найден."}

    is_routine, repeat_day = routines.is_routine_and_repeat(task_text)
    if is_routine:
        return {
            "ok": False,
            "message": "Рутины в проект не добавляются — создай рутину отдельно.",
        }

    due_date = _parse_due_date_with_today(task_text)
    due_time = parse_due_time(task_text)
    time_of_day_val = infer_time_of_day(task_text)
    if not time_of_day_val and due_time:
        try:
            h = int(str(due_time).split(":", 1)[0])
            time_of_day_val = time_of_day_from_hour(h)
        except (ValueError, IndexError):
            time_of_day_val = None

    task_title = (clean_task_text_from_datetime(task_text) or task_text).strip()
    if task_title:
        task_title = normalize_task_display(task_title)

    category_emoji, category_name = _default_big_project_category()

    date_label = "без срока"
    if due_date:
        lower = task_text.lower()
        if "сегодня" in lower:
            date_label = "сегодня"
        elif "завтра" in lower and "послезавтра" not in lower:
            date_label = "завтра"
        elif "послезавтра" in lower:
            date_label = "послезавтра"
        else:
            date_label = str(due_date)

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
            is_routine=False,
            repeat_day=None,
            project_id=project_id,
        )
        if not task_row:
            return {"ok": False, "message": "Не удалось сохранить задачу."}
        msg = (
            f"Задача в проекте: «{task_title}». Категория: {category_emoji} {category_name}. "
            f"Срок: {date_label}."
        )
        if time_of_day_val:
            msg += f" Время суток: {time_of_day_val}."
        return {"ok": True, "message": msg.strip()}
    except Exception as e:
        logger.exception("add_project_task_from_text: %s", e)
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


def complete_task_ids(user_id: int, task_ids: list[int]) -> tuple[list[str], list[int]]:
    """Отмечает выполненными задачи по id (активный список). Возвращает (заголовки, id с ошибкой).

    Использует батч-операцию db.complete_tasks_bulk: вместо 1 + N round-trip'ов делает 2-4.
    """
    if not task_ids:
        return [], []
    completed, missing = db.complete_tasks_bulk(user_id, task_ids)
    ok_titles = [str(r.get("text") or "") for r in completed]
    return ok_titles, missing


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


def uncomplete_done_today_by_id(user_id: int, task_id: int) -> dict[str, Any]:
    """Вернуть в активные выполненную сегодня задачу по id."""
    for t in db.get_done_tasks_today(user_id):
        if t["id"] == task_id:
            if db.uncomplete_task(task_id, user_id):
                return {"ok": True, "message": f"Задача «{t.get('text', '')}» снова в активных."}
            return {"ok": False, "message": "Не удалось отменить выполнение."}
    return {"ok": False, "message": "Эта задача не найдена среди выполненных сегодня."}


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


def _find_task_in_active(user_id: int, task_id: int) -> dict | None:
    """Одна строка из БД вместо загрузки всех активных задач (ускоряет веб-действия)."""
    task = db.get_active_task_by_id(user_id, task_id)
    if not task:
        return None
    db.attach_project_labels(user_id, [task])
    return task


def routine_snooze_from_today_plan(user_id: int, task_id: int) -> dict[str, Any]:
    """
    Рутина остаётся в списке рутин, но исчезает с «Сегодня»: ставим due_date на завтра (в TZ пользователя).
    """
    from datetime import datetime, timedelta

    task = _find_task_in_active(user_id, task_id)
    if not task:
        return {"ok": False, "message": "Задача не найдена."}
    if not task.get("is_routine"):
        return {"ok": False, "message": "Только для рутин."}
    today_str, _ = db._get_today_in_user_tz(user_id)
    try:
        y, m, d = map(int, today_str.split("-"))
        nxt = datetime(y, m, d) + timedelta(days=1)
        tomorrow = nxt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return {"ok": False, "message": "Не удалось вычислить дату."}
    updated = db.update_task(task_id, user_id, due_date=tomorrow)
    if updated:
        return {
            "ok": True,
            "message": f"Рутина перенесена на {tomorrow} — не будет в плане на сегодня.",
        }
    return {"ok": False, "message": "Не удалось обновить."}


def delete_task_by_id(user_id: int, task_id: int) -> dict[str, Any]:
    if not _find_task_in_active(user_id, task_id):
        return {"ok": False, "message": "Задача не найдена в активном списке."}
    if db.delete_task(task_id, user_id):
        return {"ok": True, "message": "Задача удалена."}
    return {"ok": False, "message": "Не удалось удалить."}


def update_task_text_by_id(user_id: int, task_id: int, new_text: str) -> dict[str, Any]:
    new_text = (new_text or "").strip()
    if not new_text:
        return {"ok": False, "message": "Текст не может быть пустым."}
    if not _find_task_in_active(user_id, task_id):
        return {"ok": False, "message": "Задача не найдена."}
    new_title = normalize_task_display(new_text)
    if new_title and new_title[0].isalpha():
        new_title = new_title[0].upper() + new_title[1:]
    updated = db.update_task(task_id, user_id, text=new_title)
    if updated:
        return {"ok": True, "message": f"Сохранено: «{updated.get('text', new_title)}»"}
    return {"ok": False, "message": "Не удалось сохранить текст."}


def reschedule_task_by_id(user_id: int, task_id: int, due_date: str) -> dict[str, Any]:
    """Перенос на дату YYYY-MM-DD (обычная задача или день недели для рутины)."""
    from datetime import datetime

    task = _find_task_in_active(user_id, task_id)
    if not task:
        return {"ok": False, "message": "Задача не найдена."}
    try:
        datetime.strptime(due_date, "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "message": "Неверный формат даты."}
    updates = {}
    if task.get("is_routine"):
        wd = datetime.strptime(due_date, "%Y-%m-%d").weekday()
        day_codes = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
        updates["repeat_day"] = day_codes[wd]
    else:
        updates["due_date"] = due_date
    updated = db.update_task(task_id, user_id, **updates)
    if updated:
        if task.get("is_routine"):
            label = db.format_repeat_day_display(updates["repeat_day"])
            return {"ok": True, "message": f"Рутина «{task.get('text', '')}» — {label}"}
        return {"ok": True, "message": f"На {due_date}: «{updated.get('text', task.get('text', ''))}»"}
    return {"ok": False, "message": "Не удалось перенести."}


def set_task_time_bucket_by_id(user_id: int, task_id: int, bucket: str) -> dict[str, Any]:
    """Веб: перетаскивание в блок утро/день/вечер; «none» — снять время суток (рутины)."""
    b = (bucket or "").strip().lower()
    if b in ("none", "clear", "__none__"):
        if not _find_task_in_active(user_id, task_id):
            return {"ok": False, "message": "Задача не найдена."}
        updated = db.update_task(task_id, user_id, time_of_day=None)
        if updated:
            db.ensure_today_sort_tail(user_id, task_id)
            db.refresh_today_plan_slots_after_bucket_change(user_id, task_id)
            return {"ok": True, "message": "Время суток снято."}
        return {"ok": False, "message": "Не удалось обновить."}
    if b not in ("утро", "день", "вечер", "ночь"):
        return {"ok": False, "message": "Неверный блок: утро, день, вечер или ночь."}
    if not _find_task_in_active(user_id, task_id):
        return {"ok": False, "message": "Задача не найдена."}
    # Иначе блок в UI не меняется: _task_time_bucket сначала смотрит на due_time.
    updated = db.update_task(task_id, user_id, time_of_day=b, due_time=None)
    if updated:
        db.ensure_today_sort_tail(user_id, task_id)
        db.refresh_today_plan_slots_after_bucket_change(user_id, task_id)
        return {"ok": True, "message": "Время суток обновлено."}
    return {"ok": False, "message": "Не удалось обновить."}


def move_task_tasks_page_by_id(
    user_id: int, task_id: int, target_kind: str, due_date: str | None
) -> dict[str, Any]:
    """
    Веб: перетаскивание на странице «Все задачи».
    target_kind: date | nodate | routine
    """
    task = _find_task_in_active(user_id, task_id)
    if not task:
        return {"ok": False, "message": "Задача не найдена."}
    kind = (target_kind or "").strip().lower()
    if kind == "routine":
        if not task.get("is_routine"):
            return {
                "ok": False,
                "message": "Обычную задачу нельзя перенести в «Рутины» перетаскиванием.",
            }
        return {"ok": True, "message": "Без изменений."}
    if kind == "nodate":
        if task.get("is_routine"):
            return {
                "ok": False,
                "message": "У рутины нужен день недели — перетащи на дату в календаре.",
            }
        updated = db.update_task(task_id, user_id, due_date=None)
        if updated:
            return {"ok": True, "message": "Срок снят."}
        return {"ok": False, "message": "Не удалось обновить."}
    if kind == "date":
        d = (due_date or "").strip()
        if not d:
            return {"ok": False, "message": "Не указана дата."}
        return reschedule_task_by_id(user_id, task_id, d)
    return {"ok": False, "message": "Неизвестный тип секции."}


_REPEAT_WEB_ALLOWED = frozenset({"ежедневно", "пн", "вт", "ср", "чт", "пт", "сб", "вс"})
_REPEAT_DAY_ORDER = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _normalize_web_repeat_day(raw: str) -> str | None:
    """Единичный день, ежедневно, «раз в неделю» или несколько дней через запятую (пн,ср,пт)."""
    import random

    from routines import ROUTINE_WEEKLY_NO_DAY, WEEKDAY_CODES

    s = (raw or "").strip()
    if not s:
        return None
    up = s.upper()
    if up.startswith("N_DAYS:"):
        try:
            n = int(s.split(":", 1)[1].strip())
        except (ValueError, IndexError):
            return None
        if 2 <= n <= 365:
            return f"N_DAYS:{n}"
        return None
    if up.startswith("BIWEEK:"):
        code = s.split(":", 1)[1].strip().lower() if ":" in s else ""
        if code in _REPEAT_WEB_ALLOWED and code != "ежедневно":
            return f"BIWEEK:{code}"
        return None
    if s == ROUTINE_WEEKLY_NO_DAY or s.lower() == "раз в неделю":
        return random.choice(tuple(WEEKDAY_CODES))
    low = s.lower()
    if low == "ежедневно":
        return "ежедневно"
    if "," in s:
        parts = [p.strip().lower() for p in s.split(",") if p.strip()]
        if not parts:
            return None
        for p in parts:
            if p not in _REPEAT_DAY_ORDER:
                return None
        return ",".join(c for c in _REPEAT_DAY_ORDER if c in parts)
    if low in _REPEAT_WEB_ALLOWED:
        return low
    return None


def set_task_project_by_id(user_id: int, task_id: int, raw_project_id: str) -> dict[str, Any]:
    """Привязать задачу к проекту или отвязать (пустая строка)."""
    task = _find_task_in_active(user_id, task_id)
    if not task:
        return {"ok": False, "message": "Задача не найдена."}
    raw = (raw_project_id or "").strip()
    if not raw:
        db.update_task(task_id, user_id, project_id=None)
        return {"ok": True, "message": "Проект отвязан."}
    if not raw.isdigit():
        return {"ok": False, "message": "Некорректный проект."}
    pid = int(raw)
    if not db.get_project(user_id, pid):
        return {"ok": False, "message": "Проект не найден."}
    db.update_task(task_id, user_id, project_id=pid)
    db.append_color_sort_new_project_task(user_id, pid, task_id)
    return {"ok": True, "message": "Проект обновлён."}


def set_task_category_by_id(user_id: int, task_id: int, category_name: str) -> dict[str, Any]:
    category_name = (category_name or "").strip()
    if not category_name:
        return {"ok": False, "message": "Выбери категорию."}
    task = _find_task_in_active(user_id, task_id)
    if not task:
        return {"ok": False, "message": "Задача не найдена."}
    emoji = "📝"
    found = False
    for r in db.get_categories(user_id):
        if r["name"].strip().lower() == category_name.strip().lower():
            emoji = (r.get("emoji") or "📝").strip() or "📝"
            category_name = r["name"]
            found = True
            break
    if not found:
        from categories import CATEGORIES

        for _cid, em, nm, _kw in CATEGORIES:
            if nm.strip().lower() == category_name.strip().lower():
                emoji = em
                category_name = nm
                found = True
                break
    if not found:
        return {"ok": False, "message": "Неизвестная категория."}
    updated = db.update_task(task_id, user_id, category_emoji=emoji, category_name=category_name)
    if updated:
        return {"ok": True, "message": f"Категория: {emoji} {category_name}"}
    return {"ok": False, "message": "Не удалось обновить."}


_COLOR_LABEL = {
    "": "без цвета",
    "red": "🔴 красный",
    "orange": "🟠 оранжевый",
    "yellow": "🟡 жёлтый",
    "green": "🟢 зелёный",
    "blue": "🔵 синий",
    "purple": "🟣 фиолетовый",
    "gray": "⚫ серый",
}


def set_task_color_by_id(user_id: int, task_id: int, color: str) -> dict[str, Any]:
    c = (color or "").strip().lower()
    if c not in db.VALID_TASK_COLORS:
        return {"ok": False, "message": "Недопустимый цвет."}
    task = _find_task_in_active(user_id, task_id)
    if not task:
        return {"ok": False, "message": "Задача не найдена."}
    if not db.set_task_color(user_id, task_id, c):
        return {"ok": False, "message": "Не удалось обновить."}
    return {"ok": True, "message": f"Цвет: {_COLOR_LABEL.get(c, c or 'без цвета')}"}


def set_task_repeat_day_by_id(user_id: int, task_id: int, repeat_day: str) -> dict[str, Any]:
    raw = (repeat_day or "").strip()
    if not raw:
        return {"ok": False, "message": "Выбери расписание."}
    new_rd = _normalize_web_repeat_day(raw)
    if not new_rd:
        return {"ok": False, "message": "Недопустимое расписание."}
    task = _find_task_in_active(user_id, task_id)
    if not task or not task.get("is_routine"):
        return {"ok": False, "message": "Это не рутина."}
    updated = db.update_task(task_id, user_id, repeat_day=new_rd)
    if updated:
        lbl = db.format_repeat_day_display(new_rd)
        return {"ok": True, "message": f"Расписание: {lbl}"}
    return {"ok": False, "message": "Не удалось обновить."}


def set_task_routine_kind_by_id(user_id: int, task_id: int, make_routine: bool) -> dict[str, Any]:
    task = _find_task_in_active(user_id, task_id)
    if not task:
        return {"ok": False, "message": "Задача не найдена."}
    today_str, today_wd = db._get_today_in_user_tz(user_id)
    codes = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    if make_routine:
        if task.get("is_routine"):
            return {"ok": True, "message": "Уже рутина."}
        rd = codes[today_wd]
        updated = db.update_task(
            task_id,
            user_id,
            is_routine=True,
            repeat_day=rd,
            due_date=None,
        )
        if updated:
            return {
                "ok": True,
                "message": f"Рутина: {db.format_repeat_day_display(rd)}. При необходимости смени день в меню ⋯.",
            }
        return {"ok": False, "message": "Не удалось обновить."}
    if not task.get("is_routine"):
        return {"ok": True, "message": "Уже обычная задача."}
    updated = db.update_task(
        task_id,
        user_id,
        is_routine=False,
        repeat_day=None,
        due_date=today_str,
    )
    if updated:
        return {"ok": True, "message": "Сделана обычной задачей на сегодня."}
    return {"ok": False, "message": "Не удалось обновить."}
