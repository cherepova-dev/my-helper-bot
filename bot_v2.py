# -*- coding: utf-8 -*-
"""
Бот v2: только добавление одной задачи и общий список.
Без LLM: классификация и извлечение задачи — по правилам и состоянию.
Голос — только Whisper (распознавание речи), дальше тот же алгоритм.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import db
import ai_module
import routines
from categories import assign_category
from task_parsing import (
    parse_due_date,
    parse_due_time,
    extract_task_text,
    starts_with_add_marker,
    starts_with_done_marker,
    extract_done_target,
    extract_done_targets,
    clean_task_text_from_datetime,
    normalize_task_display,
    starts_with_edit_marker,
    extract_edit_target,
    starts_with_reschedule_marker,
    extract_reschedule_target,
    starts_with_delete_marker,
    extract_delete_target,
)

# Алиасы для использования в _save_one_task_and_reply
_parse_due_date = parse_due_date
_parse_due_time = parse_due_time

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PROXY_URL = (
    os.environ.get("PROXY_URL", "").strip()
    or os.environ.get("HTTPS_PROXY", "").strip()
    or os.environ.get("HTTP_PROXY", "").strip()
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояние: после /add следующее сообщение = текст задачи. Ключ = telegram user id.
_awaiting_task: dict[int, bool] = {}
# После /done следующее сообщение = номер или название задачи для выполнения.
_awaiting_done: dict[int, bool] = {}

BOT_COMMANDS = [
    ("start", "Начать"),
    ("help", "Помощь"),
    ("add", "Добавить задачу"),
    ("tasks", "Список задач"),
    ("today", "План на сегодня"),
    ("routines", "Рутины"),
    ("done", "Отметить выполнение"),
    ("done_today", "Сделано сегодня"),
    ("done_week", "Сделано за неделю"),
]

# Краткая справка по голосовым и текстовым командам (команда «Помощь»)
HELP_TEXT = (
    "📖 *Помощь по командам*\n\n"
    "*Как добавить задачу*\n"
    "• Нажми «Добавить задачу» и отправь текст следующим сообщением\n"
    "• Или напиши/скажи: «Добавь [задача]», «Создай [задача]», «Запиши [задача]»\n"
    "• Рутины: «Ежедневная зарядка», «Поливать цветы каждый четверг», «Уборка раз в неделю»\n"
    "• Несколько раз в неделю без дней: «массаж два раза в неделю», «йога 3 раза в неделю»\n\n"
    "*Как выполнить задачу*\n"
    "• «Отметь 3» — по *глобальному* номеру в последнем списке «Список задач»\n"
    "• Несколько сразу: «Отметь 1, 2 и 3», «Выполни 1 4 5», «Отметь 1-3»\n"
    "• «Выполни купить молоко» — по названию\n"
    "• «Отметить задачу номер 1», «Отметить выполнение 2»\n\n"
    "*Как выполнить рутину*\n"
    "• Так же, как задачу: «Отметь N» по номеру в плане на сегодня. После выполнения рутина исчезнет из списка на сегодня и снова появится в свой день.\n\n"
    "*Как удалить задачу или рутину*\n"
    "• «Удали задачу 3» — по *глоб.* номеру из «Список задач»\n"
    "• «Удали рутину 2» — по номеру в экране «Рутины» (/routines)\n\n"
    "*Как изменить задачу*\n"
    "• «Изменить задачу 2 на Купить хлеб» — новый текст\n"
    "• «Исправить купить молоко на Купить хлеб»\n\n"
    "*Как перенести задачу*\n"
    "• «Перенеси задачу 3 на завтра», «Перенести задачу 1 на пятницу в 10:00»\n"
    "• Для рутины — перенос на другой день недели\n\n"
    "*Отменить выполнение*\n"
    "• «Отменить выполнение 1» — вернуть задачу из «Сделано сегодня» в активные\n\n"
    "*Списки и отчёты*\n"
    "• «Список задач», «План на сегодня», «Рутины»\n"
    "• В списке у каждого дня: локальный номер + _(глоб. N)_ — команды «отметь» используют *глоб.* N.\n"
    "• Номера в старых сообщениях чата не обновляются — ориентируйся на последний список.\n"
    "• «Сделано сегодня», «Сделано за неделю», «Отчёт за неделю»\n\n"
    "*Перенос даты*\n"
    "• «Перенеси задачу 6 на второе апреля», «на 2 апреля», «на 15.05»\n"
)

# Синонимы для текстовых/голосовых команд (без слэша)
SYN_LIST_TASKS = (
    "покажи список задач", "список задач", "все задачи", "мои задачи",
    "покажи список", "что в списке", "план", "задачи",
    "покажи задачи", "покажи все задачи", "покажи мои задачи",
)
SYN_TODAY = (
    "план на сегодня", "задачи на сегодня", "что на сегодня", "на сегодня",
    "покажи задачи на сегодня", "покажи план на сегодня", "что на сегодня сделать",
)
SYN_ADD_TASK = (
    "добавить задачу", "новая задача", "добавить новую задачу", "создать задачу",
)
SYN_DONE_TODAY = (
    "что сделала сегодня", "что сделал сегодня", "мои достижения сегодня",
    "сделано сегодня", "выполнено сегодня", "отчёт за день",
    "покажи отчёт за сегодня", "отчёт за сегодня", "покажи сделанное сегодня",
)
SYN_DONE_WEEK = (
    "отчёт за неделю", "сделано за неделю", "выполнено за неделю",
    "что сделала за неделю", "что сделал за неделю", "мои достижения за неделю",
    "покажи отчёт за неделю", "покажи сделанное за неделю",
)
SYN_ROUTINES = (
    "рутины", "мои рутины", "покажи рутины", "список рутин", "регулярные дела",
)
SYN_UNCOMPLETE = (
    "отменить выполнение", "вернуть задачу", "отменить выполнение задачи",
    "вернуть в список", "отменить выполнение задачи номер",
)
SYN_HELP = (
    "помощь", "справка", "как пользоваться", "что умеешь", "команды",
    "покажи помощь", "покажи справку",
)
# Изменение и перенос — маркеры в task_parsing (изменить задачу, перенеси на ...)
# SYN_EDIT / SYN_RESCHEDULE не нужны: проверяем starts_with_edit_marker, starts_with_reschedule_marker

ONBOARDING_V2 = (
    "Привет! Я помощник по задачам.\n\n"
    "*Добавить задачу:* «Добавить задачу» → текст задачи или: «Добавь [задача]», «Создай [задача]».\n\n"
    "*Список задач* — все активные (с номерами). *План на сегодня* — на сегодня.\n\n"
    "*Выполнить:* «Отметь 3» или «Выполни [название]» — по номеру из списка или по названию.\n\n"
    "*Отчёты:* «Сделано сегодня» / «Сделано за неделю»."
)


def _auto_schedule_date(user_id: int) -> tuple[str, str]:
    """Ближайший день с числом задач меньше лимита. Возвращает (date_str, human_label)."""
    settings = db.get_settings(user_id)
    limit = settings.get("max_tasks_per_day", 7)
    today = datetime.now()
    for offset in range(7):
        day = today + timedelta(days=offset)
        date_str = day.strftime("%Y-%m-%d")
        count = db.count_tasks_for_date(user_id, date_str)
        if count < limit:
            if offset == 0:
                label = "сегодня"
            elif offset == 1:
                label = "завтра"
            else:
                wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][day.weekday()]
                label = f"{wd} ({day.strftime('%d.%m')})"
            return date_str, label
    date_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    return date_str, "завтра"


def _build_confirmation(
    task_text: str,
    due_date: str | None,
    date_label: str,
    due_time: str | None,
    category_emoji: str = "📝",
    category_name: str = "Другое",
    is_routine: bool = False,
    repeat_day: str | None = None,
) -> str:
    """Текст подтверждения одной задачи."""
    if is_routine and repeat_day:
        date_display = f"🔁 {db.format_repeat_day_display(repeat_day)}"
    elif due_date and date_label:
        date_display = f"{date_label} ({due_date})"
    elif due_date:
        date_display = due_date
    else:
        date_display = "без срока"
    if due_time and not is_routine:
        date_display += f" в {due_time}"
    lines = [
        "✅ *Задача принята*",
        "",
        f"📝 «{task_text}»",
        f"📂 Категория: {category_emoji} {category_name}",
        f"📅 Срок: {date_display}",
        "🔥 Приоритет: 5/10",
        "",
        "_Всё верно? Если нет — напиши, что исправить._",
    ]
    return "\n".join(lines)


# Названия месяцев для человекочитаемой даты (родительный падеж)
_MONTH_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _format_date_human(date_str: str) -> str:
    """Преобразует YYYY-MM-DD в «4 марта 2026»."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.day} {_MONTH_RU[dt.month]} {dt.year}"
    except (ValueError, KeyError):
        return date_str


def _format_time_human(time_str: str) -> str:
    """Возвращает время в человекочитаемом виде (HH:MM)."""
    return (time_str or "").strip()


def _format_task_list(tasks: list[dict], with_numbers: bool = True) -> str:
    """Форматирует список активных задач: нумерация, группировка по дате, блок «Рутины» с регулярностью (RT-F7)."""
    if not tasks:
        return "_Пока нет активных задач. Добавь задачу через меню или напиши «Добавь [задача]»._"

    def sort_key(t: dict) -> tuple:
        d = t.get("due_date") or ""
        ti = t.get("due_time") or ""
        return (d, ti, t.get("id", 0))

    sorted_tasks = sorted(tasks, key=sort_key)
    numbered = list(enumerate(sorted_tasks, start=1))  # [(1, t), (2, t), ...]

    regular = [(num, t) for num, t in numbered if not t.get("is_routine")]
    routines_list = [(num, t) for num, t in numbered if t.get("is_routine")]

    by_date: dict[str, list[tuple[int, dict]]] = {}
    for num, t in regular:
        d = t.get("due_date") or ""
        if d not in by_date:
            by_date[d] = []
        by_date[d].append((num, t))

    lines = [f"📋 *Все задачи ({len(tasks)})*\n"]

    date_keys = [k for k in by_date if k]
    date_keys.sort()
    for date_str in date_keys:
        group = by_date[date_str]
        label = _format_date_human(date_str)
        lines.append(f"*📅 {label}*")
        for loc_i, (num, t) in enumerate(group, start=1):
            emoji = t.get("category_emoji") or "📝"
            text = t["text"]
            time_part = ""
            if t.get("due_time"):
                time_part = f" в {_format_time_human(t['due_time'])}"
            if with_numbers:
                prefix = f"{loc_i}. _(глоб. {num})_ "
            else:
                prefix = ""
            lines.append(f"{prefix}{emoji} {text}{time_part}")
        lines.append("")

    if "" in by_date:
        lines.append("*📅 Без срока*")
        for loc_i, (num, t) in enumerate(by_date[""], start=1):
            emoji = t.get("category_emoji") or "📝"
            text = t["text"]
            time_part = ""
            if t.get("due_time"):
                time_part = f" в {_format_time_human(t['due_time'])}"
            if with_numbers:
                prefix = f"{loc_i}. _(глоб. {num})_ "
            else:
                prefix = ""
            lines.append(f"{prefix}{emoji} {text}{time_part}")
        lines.append("")

    if routines_list:
        lines.append("*🔁 Рутины*")
        for loc_i, (num, t) in enumerate(routines_list, start=1):
            emoji = t.get("category_emoji") or "🔁"
            text = t["text"]
            repeat_label = db.format_repeat_day_display(t.get("repeat_day"))
            if with_numbers:
                prefix = f"{loc_i}. _(глоб. {num})_ "
            else:
                prefix = ""
            lines.append(f"{prefix}{emoji} {text} — _{repeat_label}_")
        lines.append("")

    return "\n".join(lines).strip()


async def _reply(update: Update, text: str, max_retries: int = 3) -> None:
    for attempt in range(max_retries + 1):
        try:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return
        except (TimedOut, NetworkError) as e:
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.info("Retry %s/%s через %ss (%s)", attempt + 1, max_retries, wait, type(e).__name__)
                await asyncio.sleep(wait)
            else:
                logger.warning("Не удалось отправить после %s попыток: %s", max_retries + 1, e)
        except Exception:
            try:
                await update.message.reply_text(text)
                return
            except Exception as e2:
                logger.warning("Ошибка отправки: %s", e2)
                return


async def _save_one_task_and_reply(
    update: Update,
    user_row: dict,
    task_text: str,
) -> None:
    """Парсит дату/время из task_text, сохраняет задачу, отправляет подтверждение."""
    if not task_text or not task_text.strip():
        await _reply(update, "⚠️ Текст задачи пустой. Напиши, что нужно сделать.")
        return

    task_text = task_text.strip()
    internal_user_id = user_row["id"]
    settings = db.get_settings(internal_user_id)

    is_routine, repeat_day = routines.is_routine_and_repeat(task_text)
    due_date = _parse_due_date(task_text) if not is_routine else None
    due_time = _parse_due_time(task_text) if not is_routine else None
    # Убираем дату, время и фразы рутины из названия задачи
    task_title = (clean_task_text_from_datetime(task_text) or task_text).strip()
    if is_routine:
        task_title = (routines.clean_task_title_from_routine_phrases(task_title) or task_title).strip()
    if task_title:
        task_title = normalize_task_display(task_title)
    date_label = ""

    # Определение категории по тексту задачи (до upper — по оригиналу для лучшего матчинга)
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
            priority_value=5,
            priority_urgency=5,
            priority_risk=5,
            priority_size=5,
            is_routine=is_routine,
            repeat_day=repeat_day,
        )
        if task_row:
            msg = _build_confirmation(
                task_title, due_date, date_label, due_time,
                category_emoji=category_emoji,
                category_name=category_name,
                is_routine=is_routine,
                repeat_day=task_row.get("repeat_day") or repeat_day,
            )
            await _reply(update, msg)
            logger.info("v2: задача сохранена id=%s text='%s'", task_row.get("id"), task_title[:50])
        else:
            await _reply(update, "⚠️ Не удалось сохранить задачу. Попробуй ещё раз.")
    except Exception as e:
        logger.exception("v2: ошибка сохранения задачи: %s", e)
        await _reply(update, "⚠️ Произошла ошибка. Попробуй ещё раз.")


# ─── Обработчики команд ────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.get_or_create_user(user.id, user.first_name or "")
    _awaiting_task.pop(user.id, None)
    _awaiting_done.pop(user.id, None)
    await _reply(update, ONBOARDING_V2)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Краткая справка по командам (голос и текст)."""
    await _reply(update, HELP_TEXT)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.get_or_create_user(user.id, user.first_name or "")
    _awaiting_done.pop(user.id, None)
    _awaiting_task[user.id] = True
    await _reply(
        update,
        "Напиши или надиктуй задачу — *следующее* сообщение я сохраню как задачу.",
    )


def _active_tasks_display_order(user_id: int) -> list[dict]:
    """Активные задачи в порядке отображения в списке (для нумерации и «выполни N»)."""
    tasks = db.get_active_tasks_ordered(user_id)
    def key(t):
        return (t.get("due_date") or "", t.get("due_time") or "", t.get("id", 0))
    return sorted(tasks, key=key)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    uid = user_row["id"]
    db.transfer_overdue_tasks(uid)
    tasks = _active_tasks_display_order(uid)
    text = _format_task_list(tasks)
    await _reply(update, text)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    uid = user_row["id"]
    db.transfer_overdue_tasks(uid)
    ordered = _active_tasks_display_order(uid)
    today_tasks = db.get_today_tasks(uid)
    today_ids = {t["id"] for t in today_tasks}
    # Нумерация как в полном списке (чтобы «отметь 5» работало однозначно)
    ordered_today = [(i, t) for i, t in enumerate(ordered, start=1) if t["id"] in today_ids]
    text = _format_today_list(ordered_today) if ordered_today else "_На сегодня задач нет._"
    await _reply(update, text)


async def cmd_routines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отдельный экран со списком рутин и регулярностью (RT-F4, RT-F5)."""
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    uid = user_row["id"]
    routine_tasks = db.get_routine_tasks(uid)
    if not routine_tasks:
        await _reply(update, "🔁 *Рутины*\n\n_Пока нет рутин. Добавь, например: «Ежедневная зарядка», «Поливать цветы каждый четверг», «Уборка раз в неделю»._")
        return
    lines = ["🔁 *Рутины*\n"]
    for i, t in enumerate(routine_tasks, start=1):
        repeat_label = db.format_repeat_day_display(t.get("repeat_day"))
        emoji = t.get("category_emoji") or "🔁"
        lines.append(f"{i}. {emoji} {t['text']} — _{repeat_label}_")
    await _reply(update, "\n".join(lines))


def _format_today_list(ordered_today: list[tuple[int, dict]], title: str = "📅 *План на сегодня*") -> str:
    """Список задач на сегодня: список пар (номер в общем списке, задача). Рутины с 🔁 (RT-F8)."""
    lines = [f"{title}\n"]
    for num, t in ordered_today:
        emoji = "🔁" if t.get("is_routine") else (t.get("category_emoji") or "📝")
        time_part = f" в {_format_time_human(t['due_time'])}" if t.get("due_time") else ""
        lines.append(f"{num}. {emoji} {t['text']}{time_part}")
    return "\n".join(lines)


async def _send_remaining_today(update: Update, user_id: int) -> None:
    """После выполнения задачи — отправить список «ОСТАЛОСЬ СЕГОДНЯ СДЕЛАТЬ» (как план на сегодня)."""
    ordered = _active_tasks_display_order(user_id)
    today_tasks = db.get_today_tasks(user_id)
    today_ids = {t["id"] for t in today_tasks}
    ordered_today = [(i, t) for i, t in enumerate(ordered, start=1) if t["id"] in today_ids]
    if ordered_today:
        text = _format_today_list(ordered_today, title="🔥 *ОСТАЛОСЬ СЕГОДНЯ СДЕЛАТЬ*")
    else:
        text = "🔥 *ОСТАЛОСЬ СЕГОДНЯ СДЕЛАТЬ*\n\n_Всё сделано на сегодня._"
    await _reply(update, text)


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    _awaiting_task.pop(user.id, None)
    _awaiting_done[user.id] = True
    await _reply(
        update,
        "Напиши *номер* задачи из списка (например: _1_ или _3_) или часть названия.\n"
        "Следующее сообщение я восприму как указание, какую задачу отметить выполненной.",
    )


# Порядок категорий для отображения в отчётах (эмодзи, название)
_REPORT_CATEGORY_ORDER = [
    ("🏠", "Быт / дом"),
    ("👨‍👩‍👧", "Семья"),
    ("💇‍♀️", "Уход / внешность"),
    ("🌿", "Для себя"),
    ("🎫", "Досуг"),
    ("📦", "Дела / поручения"),
    ("🧠", "Большие проекты"),
    ("📝", "Другое"),
]


def _parse_completed_at(completed_at_str, tz_name: str = "Europe/Moscow"):
    """Парсит completed_at (ISO, дата-время с пробелом или только YYYY-MM-DD) и возвращает datetime в TZ пользователя."""
    if not completed_at_str:
        return None
    try:
        if not isinstance(completed_at_str, str):
            return None
        s = completed_at_str.strip().replace("Z", "+00:00")
        if "T" not in s and " " in s:
            s = s.replace(" ", "T", 1)
        if "T" in s:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if ZoneInfo:
                tz = ZoneInfo(tz_name)
                return dt.astimezone(tz)
            return dt
        if len(s) >= 10 and s[:10].replace("-", "").isdigit():
            from datetime import date as _date
            d = _date.fromisoformat(s[:10])
            if ZoneInfo:
                try:
                    tz = ZoneInfo(tz_name)
                    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
                except Exception:
                    return datetime.combine(d, datetime.min.time())
            return datetime.combine(d, datetime.min.time())
    except Exception:
        pass
    return None


def _format_completed_time(dt) -> str:
    """Форматирует время: «в 10:30»."""
    if not dt:
        return ""
    return f" в {dt.strftime('%H:%M')}"


def _format_completed_datetime(dt) -> str:
    """Форматирует дату и время: «24 февраля в 14:00»."""
    if not dt:
        return ""
    return f"{dt.day} {_MONTH_RU[dt.month]} в {dt.strftime('%H:%M')}"


def _format_done_report_today(tasks: list[dict], tz_name: str) -> str:
    """Отчёт за день: сводка, группировка по категориям, время выполнения, дружелюбный пустой."""
    if not tasks:
        return (
            "🔥 *Сделано сегодня*\n\n"
            "_Сегодня пока ни одной задачи не отмечено. "
            "План на сегодня ещё можно выполнить!_"
        )
    lines = ["🔥 *Сделано сегодня*", ""]
    n = len(tasks)
    cats = {}
    for t in tasks:
        emoji = t.get("category_emoji") or "📝"
        name = t.get("category_name") or "Другое"
        key = (emoji, name)
        if key not in cats:
            cats[key] = []
        cats[key].append(t)
    n_cats = len(cats)
    lines.append(f"Выполнено: *{n}* задач · *{n_cats}* категорий")
    lines.append("")
    for emoji, name in _REPORT_CATEGORY_ORDER:
        group = cats.get((emoji, name), [])
        if not group:
            continue
        lines.append(f"*{emoji} {name}*")
        for t in group:
            text = (t.get("text") or "").strip()
            ts = t.get("_use_completed_at") or t.get("completed_at") or t.get("last_completed_at")
            dt = _parse_completed_at(ts, tz_name)
            time_str = _format_completed_time(dt) if dt else ""
            routine_mark = " 🔁" if t.get("is_routine") else ""
            lines.append(f"  🔹 {text}{time_str}{routine_mark}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_done_report_week(tasks: list[dict], tz_name: str) -> str:
    """Отчёт за неделю: сводка, группировка по дням и категориям, статистика по категориям."""
    if not tasks:
        return (
            "🔥 *Сделано за неделю*\n\n"
            "_За последние 7 дней не отмечено ни одной задачи. "
            "Добавляй дела и отмечай выполненные — прогресс накапливается!_"
        )
    lines = ["🔥 *Сделано за неделю*", ""]
    n = len(tasks)
    lines.append(f"За 7 дней: *{n}* задач")
    cat_counts = {}
    by_day: dict[str, list[dict]] = {}
    for t in tasks:
        ts = t.get("_use_completed_at") or t.get("completed_at") or t.get("last_completed_at")
        dt = _parse_completed_at(ts, tz_name)
        day_key = dt.strftime("%Y-%m-%d") if dt else "_no_date_"
        if day_key not in by_day:
            by_day[day_key] = []
        by_day[day_key].append(t)
        emoji = t.get("category_emoji") or "📝"
        name = t.get("category_name") or "Другое"
        key = (emoji, name)
        cat_counts[key] = cat_counts.get(key, 0) + 1
    cat_line_parts = [
        f"{e} {n}: {c}" for (e, n), c in sorted(cat_counts.items(), key=lambda x: -x[1])
    ]
    if cat_line_parts:
        lines.append("По категориям: " + " · ".join(cat_line_parts))
    lines.append("")
    day_keys = sorted([k for k in by_day.keys() if k != "_no_date_"], reverse=True)
    if "_no_date_" in by_day:
        day_keys.append("_no_date_")
    for day_key in day_keys:
        group = by_day[day_key]
        ts_first = (group[0].get("_use_completed_at") or group[0].get("completed_at") or group[0].get("last_completed_at")) if group else None
        dt_first = _parse_completed_at(ts_first, tz_name) if group else None
        day_label = _format_date_human(day_key) if day_key and day_key != "_no_date_" else "Без даты"
        wd_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        wd = wd_names[dt_first.weekday()] if dt_first else ""
        lines.append(f"*📅 {day_label}* ({wd})")
        by_cat = {}
        for t in group:
            emoji = t.get("category_emoji") or "📝"
            name = t.get("category_name") or "Другое"
            key = (emoji, name)
            if key not in by_cat:
                by_cat[key] = []
            by_cat[key].append(t)
        for emoji, name in _REPORT_CATEGORY_ORDER:
            sub = by_cat.get((emoji, name), [])
            for t in sub:
                text = (t.get("text") or "").strip()
                ts = t.get("_use_completed_at") or t.get("completed_at") or t.get("last_completed_at")
                dt = _parse_completed_at(ts, tz_name)
                time_str = _format_completed_time(dt) if dt else ""
                routine_part = ""
                if t.get("is_routine") and t.get("repeat_day"):
                    routine_part = f" 🔁 {db.format_repeat_day_display(t.get('repeat_day'))}"
                elif t.get("is_routine"):
                    routine_part = " 🔁"
                lines.append(f"  🔹 {emoji} {text}{time_str}{routine_part}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_done_report(tasks: list[dict], title: str) -> str:
    """Простой формат (для обратной совместимости тестов)."""
    if not tasks:
        return f"{title}\n\n_Нет выполненных задач._"
    lines = [f"✅ *{title}*\n"]
    for t in tasks:
        emoji = t.get("category_emoji") or "📝"
        text = (t.get("text") or "").strip()
        lines.append(f"🔹 {emoji} {text}")
    return "\n".join(lines)


async def cmd_done_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    tasks = db.get_done_tasks_today(user_row["id"])
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    text = _format_done_report_today(tasks, tz_name)
    await _reply(update, text)


async def cmd_done_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    tasks = db.get_done_tasks(user_row["id"], days=7)
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    text = _format_done_report_week(tasks, tz_name)
    await _reply(update, text)


async def _handle_complete(
    update: Update, user_row: dict, text: str
) -> None:
    """Обработка «отметь N» / «выполни название»: по номеру или по тексту, при нескольких — уточнение."""
    uid = user_row["id"]
    nums, num, rest = extract_done_targets(text)
    ordered = _active_tasks_display_order(uid)
    if not ordered:
        await _reply(update, "Нет активных задач для выполнения.")
        return

    if nums:
        ok_titles: list[str] = []
        fail_nums: list[int] = []
        for n in sorted(set(nums)):
            if 1 <= n <= len(ordered):
                task = ordered[n - 1]
                if db.complete_task(task["id"], uid, task=task):
                    ok_titles.append(task["text"])
                else:
                    fail_nums.append(n)
            else:
                fail_nums.append(n)
        parts: list[str] = []
        if ok_titles:
            shown = ok_titles[:12]
            tail = " …" if len(ok_titles) > 12 else ""
            parts.append(
                f"🔥 Отмечено ({len(ok_titles)}): "
                + ", ".join(f"«{t}»" for t in shown)
                + tail
            )
        if fail_nums:
            parts.append(
                f"Не получилось для номеров: {', '.join(str(x) for x in sorted(set(fail_nums)))} "
                f"(в списке 1–{len(ordered)})."
            )
        hint = (
            "\n\n_Команды «отметь» используют глобальный номер _(глоб. N)_ из «Список задач»; "
            "в чате смотри последний список._"
        )
        await _reply(update, "\n".join(parts) + hint)
        if ok_titles:
            await _send_remaining_today(update, uid)
        return

    # По номеру
    if num is not None:
        if 1 <= num <= len(ordered):
            task = ordered[num - 1]
            if db.complete_task(task["id"], uid, task=task):
                await _reply(
                    update,
                    f"🔥 Выполнено: «{task['text']}»\n\n"
                    "_Номера — по глоб. номеру в последнем «Список задач»._",
                )
                await _send_remaining_today(update, uid)
            else:
                await _reply(update, "Не удалось отметить задачу.")
        else:
            await _reply(update, f"Нет задачи с номером {num}. В списке задач от 1 до {len(ordered)}.")
        return
    # По названию
    if not rest:
        await _reply(update, "Напиши номер задачи или часть названия. Например: «Отметь 3» или «Выполни купить молоко».")
        return
    # Голос может дать «зарегистрировать домен или отогнать машину» — пробуем по частям до первого однозначного
    search_phrases = [s.strip() for s in rest.split(" или ") if s.strip()]
    if not search_phrases:
        search_phrases = [rest]
    matches = None
    used_query = rest
    for phrase in search_phrases:
        m = db.find_tasks_matching_text(uid, phrase)
        if len(m) == 1:
            matches = m
            used_query = phrase
            break
        if len(m) > 1:
            matches = m
            used_query = phrase
    if matches is None and len(search_phrases) > 1:
        matches = db.find_tasks_matching_text(uid, rest)
        used_query = rest
    if not matches:
        await _reply(update, f"Задача по запросу «{used_query}» не найдена.")
        return
    if len(matches) == 1:
        task = matches[0]
        if db.complete_task(task["id"], uid, task=task):
            await _reply(
                update,
                f"🔥 Выполнено: «{task['text']}»\n\n"
                "_Номера — по глоб. номеру в последнем «Список задач»._",
            )
            await _send_remaining_today(update, uid)
        else:
            await _reply(update, "Не удалось отметить задачу.")
        return
    # Несколько совпадений — показать номера и попросить уточнить
    ordered_ids = {t["id"]: i for i, t in enumerate(ordered, start=1)}
    parts = []
    for t in matches:
        n = ordered_ids.get(t["id"], "?")
        parts.append(f"{n}. {t['text']}")
    msg = (
        f"Найдено задач: {len(matches)}.\n\n"
        + "\n".join(parts)
        + "\n\n_Уточните, какую отметить: напишите номер (например, 1 или 4)._"
    )
    await _reply(update, msg)


def _extract_uncomplete_number(text: str) -> int | None:
    """Из «отменить выполнение 1», «вернуть задачу номер 2» извлекает номер (1-based)."""
    import re
    lower = text.strip().lower()
    for syn in SYN_UNCOMPLETE:
        if syn in lower:
            rest = text[lower.index(syn) + len(syn):].strip()
            rest = re.sub(r"^(?:задач[уа]\.?\s*)?(?:номер\s*)?", "", rest, flags=re.IGNORECASE).strip()
            m = re.match(r"^(\d+)", rest)
            if m:
                return int(m.group(1))
            return None
    return None


async def _handle_uncomplete(update: Update, user_row: dict, text: str) -> None:
    """Отмена выполнения: вернуть задачу в активные (из списка «Сделано сегодня»)."""
    uid = user_row["id"]
    done_tasks = db.get_done_tasks_today(uid)
    if not done_tasks:
        await _reply(update, "Сегодня пока нет выполненных задач. Нечего отменять.")
        return
    num = _extract_uncomplete_number(text)
    if num is None:
        lines = ["Выполнено сегодня (укажи номер для отмены):\n"]
        for i, t in enumerate(done_tasks, start=1):
            lines.append(f"{i}. {t.get('text', '')}")
        lines.append("\n_Напиши, например: «Отменить выполнение 1»_")
        await _reply(update, "\n".join(lines))
        return
    if 1 <= num <= len(done_tasks):
        task = done_tasks[num - 1]
        if db.uncomplete_task(task["id"], uid):
            await _reply(update, f"↩️ Задача «{task.get('text', '')}» снова в списке активных.")
        else:
            await _reply(update, "Не удалось отменить выполнение.")
    else:
        await _reply(update, f"Нет задачи с номером {num}. В списке выполненных сегодня от 1 до {len(done_tasks)}.")


def _resolve_task_by_num_or_search(uid: int, num: int | None, search_text: str | None) -> dict | None:
    """По номеру (1-based) или по поиску возвращает задачу из активного списка или None."""
    ordered = _active_tasks_display_order(uid)
    if not ordered:
        return None
    if num is not None:
        if 1 <= num <= len(ordered):
            return ordered[num - 1]
        return None
    if search_text and search_text.strip():
        matches = db.find_tasks_matching_text(uid, search_text.strip())
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


async def _handle_edit(update: Update, user_row: dict, text: str) -> None:
    """Изменить описание задачи: «Изменить задачу 2 на Купить хлеб»."""
    uid = user_row["id"]
    num, search_text, new_text = extract_edit_target(text)
    if not new_text or not new_text.strip():
        await _reply(
            update,
            "Напиши, например: _Изменить задачу 2 на Купить хлеб_ или _Исправить купить молоко на Купить хлеб_.",
        )
        return
    task = _resolve_task_by_num_or_search(uid, num, search_text)
    if task is None:
        if num is not None:
            await _reply(update, f"Нет задачи с номером {num}. Посмотри список задач и укажи верный номер.")
        else:
            await _reply(update, f"Задача по запросу «{search_text}» не найдена или найдено несколько — укажи номер.")
        return
    new_title = normalize_task_display(new_text.strip())
    if new_title and new_title[0].isalpha():
        new_title = new_title[0].upper() + new_title[1:]
    updated = db.update_task(task["id"], uid, text=new_title)
    if updated:
        await _reply(update, f"✏️ Задача обновлена: «{updated.get('text', new_title)}»")
    else:
        await _reply(update, "Не удалось изменить задачу.")


async def _handle_reschedule(update: Update, user_row: dict, text: str) -> None:
    """Перенести задачу по дате/времени: «Перенеси задачу 3 на завтра», для рутины — смена дня недели."""
    uid = user_row["id"]
    num, search_text, due_date, due_time = extract_reschedule_target(text)
    task = _resolve_task_by_num_or_search(uid, num, search_text)
    if task is None:
        if num is not None:
            await _reply(update, f"Нет задачи с номером {num}. Посмотри список задач и укажи верный номер.")
        else:
            await _reply(update, f"Задача по запросу «{search_text}» не найдена или найдено несколько — укажи номер.")
        return
    if not due_date and not due_time:
        await _reply(
            update,
            "Укажи новую дату или время, например: _Перенеси задачу 2 на завтра_, _на пятницу в 10:00_.",
        )
        return
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
        await _reply(update, "Не удалось определить новую дату или день. Попробуй: _на завтра_, _на пятницу_.")
        return
    updated = db.update_task(task["id"], uid, **updates)
    if updated:
        if "repeat_day" in updates:
            label = db.format_repeat_day_display(updates["repeat_day"])
            await _reply(update, f"📅 Рутина перенесена: «{task.get('text', '')}» — _{label}_")
        else:
            parts = [f"«{updated.get('text', task.get('text', ''))}»"]
            if updates.get("due_date"):
                parts.append(f"на {updates['due_date']}")
            if updates.get("due_time"):
                parts.append(f"в {updates['due_time']}")
            await _reply(update, "📅 Задача перенесена: " + " ".join(parts))
    else:
        await _reply(update, "Не удалось перенести задачу.")


async def _handle_delete(update: Update, user_row: dict, text: str) -> None:
    """Удаление задачи или рутины по номеру: «Удали задачу 3», «Удали рутину 2»."""
    uid = user_row["id"]
    num, is_routine = extract_delete_target(text)
    if num is None:
        if is_routine:
            await _reply(update, "Напиши номер рутины для удаления, например: _Удали рутину 2_. Список: /routines")
        else:
            await _reply(update, "Напиши номер задачи для удаления, например: _Удали задачу 3_. Номера — в списке задач.")
        return
    if is_routine:
        tasks = db.get_routine_tasks(uid)
        list_name = "рутин"
    else:
        tasks = _active_tasks_display_order(uid)
        list_name = "задач"
    if not tasks:
        await _reply(update, f"Нет {list_name} для удаления.")
        return
    if 1 <= num <= len(tasks):
        task = tasks[num - 1]
        if db.delete_task(task["id"], uid):
            await _reply(update, f"🗑 Удалено: «{task.get('text', '')}»")
        else:
            await _reply(update, "Не удалось удалить.")
    else:
        await _reply(update, f"Нет {list_name} с номером {num}. В списке от 1 до {len(tasks)}.")


def _match_synonym(text: str, phrases: tuple[str, ...]) -> bool:
    """True, если текст (нижний регистр) совпадает с фразой или содержит её."""
    lower = text.strip().lower()
    return any(p in lower or lower == p for p in phrases)


# ─── Обработка текста и голоса ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    text = (update.message.text or "").strip()

    if not text:
        await _reply(update, "Напиши текст задачи или используй «Добавь [задача]».")
        return

    # Режим «ожидаю номер/название для выполнения» после /done
    if _awaiting_done.pop(user.id, False):
        msg = text.strip()
        if msg.isdigit():
            await _handle_complete(update, user_row, f"отметь {msg}")
        else:
            await _handle_complete(update, user_row, f"выполни {msg}")
        return

    # Режим «ожидаю задачу» после /add
    if _awaiting_task.pop(user.id, False):
        await _save_one_task_and_reply(update, user_row, text)
        return

    # Синонимы: помощь
    if _match_synonym(text, SYN_HELP):
        await cmd_help(update, context)
        return

    # Синонимы: список задач
    if _match_synonym(text, SYN_LIST_TASKS):
        uid = user_row["id"]
        db.transfer_overdue_tasks(uid)
        tasks = _active_tasks_display_order(uid)
        await _reply(update, _format_task_list(tasks))
        return

    # Синонимы: план на сегодня
    if _match_synonym(text, SYN_TODAY):
        uid = user_row["id"]
        db.transfer_overdue_tasks(uid)
        ordered = _active_tasks_display_order(uid)
        today_tasks = db.get_today_tasks(uid)
        today_ids = {t["id"] for t in today_tasks}
        ordered_today = [(i, t) for i, t in enumerate(ordered, start=1) if t["id"] in today_ids]
        msg = _format_today_list(ordered_today) if ordered_today else "_На сегодня задач нет._"
        await _reply(update, msg)
        return

    # Синонимы: рутины
    if _match_synonym(text, SYN_ROUTINES):
        await cmd_routines(update, context)
        return

    # Синонимы: сделано сегодня
    if _match_synonym(text, SYN_DONE_TODAY):
        uid = user_row["id"]
        tasks = db.get_done_tasks_today(uid)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        await _reply(update, _format_done_report_today(tasks, tz_name))
        return

    # Синонимы: сделано за неделю
    if _match_synonym(text, SYN_DONE_WEEK):
        uid = user_row["id"]
        tasks = db.get_done_tasks(uid, days=7)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        await _reply(update, _format_done_report_week(tasks, tz_name))
        return

    # Синонимы: добавить задачу (без текста задачи) — включить режим «следующее сообщение = задача»
    if _match_synonym(text, SYN_ADD_TASK):
        _awaiting_task[user.id] = True
        await _reply(update, "Напиши или надиктуй задачу — *следующее* сообщение я сохраню как задачу.")
        return

    # Удалить задачу/рутину по номеру: «Удали задачу 3», «Удали рутину 2»
    if starts_with_delete_marker(text):
        await _handle_delete(update, user_row, text)
        return

    # Изменить задачу: «Изменить задачу 2 на Купить хлеб»
    if starts_with_edit_marker(text):
        await _handle_edit(update, user_row, text)
        return

    # Перенести задачу: «Перенеси задачу 3 на завтра»
    if starts_with_reschedule_marker(text):
        await _handle_reschedule(update, user_row, text)
        return

    # Отменить выполнение: «отменить выполнение 1», «вернуть задачу 2»
    if _match_synonym(text, SYN_UNCOMPLETE):
        await _handle_uncomplete(update, user_row, text)
        return

    # Выполнение: «отметь 3», «выполни купить молоко»
    if starts_with_done_marker(text):
        await _handle_complete(update, user_row, text)
        return

    # Фраза вида «Добавь ...» / «Создай ...» / «Запиши ...»
    if starts_with_add_marker(text):
        task_text = extract_task_text(text)
        await _save_one_task_and_reply(update, user_row, task_text)
        return

    # По умолчанию: нет явной команды — записываем как задачу
    await _save_one_task_and_reply(update, user_row, text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")

    voice = update.message.voice
    if not voice:
        await _reply(update, "⚠️ Не удалось получить голосовое сообщение.")
        return

    try:
        file = await context.bot.get_file(voice.file_id)
        voice_bytes = await file.download_as_bytearray()
        text = ai_module.transcribe_voice(bytes(voice_bytes))
    except Exception as e:
        logger.exception("v2: ошибка распознавания голоса: %s", e)
        await _reply(update, "⚠️ Не удалось распознать голос. Попробуй ещё раз или напиши текстом.")
        return

    if not (text and text.strip()):
        await _reply(update, "⚠️ Речь не распознана. Напиши задачу текстом.")
        return

    text = text.strip()
    await update.message.reply_text(f"🎤 Распознано: «{text[:200]}{'…' if len(text) > 200 else ''}»")

    if _awaiting_done.pop(user.id, False):
        if text.isdigit():
            await _handle_complete(update, user_row, f"отметь {text}")
        else:
            await _handle_complete(update, user_row, f"выполни {text}")
        return

    if _awaiting_task.pop(user.id, False):
        await _save_one_task_and_reply(update, user_row, text)
        return

    if _match_synonym(text, SYN_HELP):
        await cmd_help(update, context)
        return

    if _match_synonym(text, SYN_LIST_TASKS):
        uid = user_row["id"]
        db.transfer_overdue_tasks(uid)
        tasks = _active_tasks_display_order(uid)
        await _reply(update, _format_task_list(tasks))
        return

    if _match_synonym(text, SYN_TODAY):
        uid = user_row["id"]
        db.transfer_overdue_tasks(uid)
        ordered = _active_tasks_display_order(uid)
        today_tasks = db.get_today_tasks(uid)
        today_ids = {t["id"] for t in today_tasks}
        ordered_today = [(i, t) for i, t in enumerate(ordered, start=1) if t["id"] in today_ids]
        msg = _format_today_list(ordered_today) if ordered_today else "_На сегодня задач нет._"
        await _reply(update, msg)
        return

    if _match_synonym(text, SYN_ROUTINES):
        await cmd_routines(update, context)
        return

    if _match_synonym(text, SYN_DONE_TODAY):
        uid = user_row["id"]
        tasks = db.get_done_tasks_today(uid)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        await _reply(update, _format_done_report_today(tasks, tz_name))
        return

    if _match_synonym(text, SYN_DONE_WEEK):
        uid = user_row["id"]
        tasks = db.get_done_tasks(uid, days=7)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        await _reply(update, _format_done_report_week(tasks, tz_name))
        return

    if _match_synonym(text, SYN_ADD_TASK):
        _awaiting_task[user.id] = True
        await _reply(update, "Напиши или надиктуй задачу — *следующее* сообщение я сохраню как задачу.")
        return

    if starts_with_delete_marker(text):
        await _handle_delete(update, user_row, text)
        return

    if starts_with_edit_marker(text):
        await _handle_edit(update, user_row, text)
        return

    if starts_with_reschedule_marker(text):
        await _handle_reschedule(update, user_row, text)
        return

    if _match_synonym(text, SYN_UNCOMPLETE):
        await _handle_uncomplete(update, user_row, text)
        return

    if starts_with_done_marker(text):
        await _handle_complete(update, user_row, text)
        return

    if starts_with_add_marker(text):
        task_text = extract_task_text(text)
        await _save_one_task_and_reply(update, user_row, task_text)
        return

    await _save_one_task_and_reply(update, user_row, text)


# ─── Меню и запуск ─────────────────────────────────────────────────────────

def _set_menu_commands_sync() -> None:
    try:
        import asyncio as _a
        app = Application.builder().token(BOT_TOKEN).build()
        async def _do():
            await app.bot.delete_my_commands()
            await app.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS])
        _a.run(_do())
    except Exception as e:
        logger.warning("Не удалось установить меню при старте: %s", e)


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        print("Задайте TELEGRAM_BOT_TOKEN.")
        return

    _set_menu_commands_sync()

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
    )
    if PROXY_URL:
        builder = builder.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)
        logger.info("Прокси: %s", PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL)

    async def post_init(application: Application) -> None:
        try:
            await application.bot.delete_my_commands()
            await application.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS])
            logger.info("v2: меню установлено (%d пунктов)", len(BOT_COMMANDS))
        except Exception as e:
            logger.exception("v2: ошибка установки меню: %s", e)

    app = builder.post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("routines", cmd_routines))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("done_today", cmd_done_today))
    app.add_handler(CommandHandler("done_week", cmd_done_week))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Логирует полный traceback и контекст апдейта для быстрой диагностики в проде."""
        update_type = type(update).__name__
        user_id = None
        chat_id = None
        message_text = None
        callback_data = None
        if isinstance(update, Update):
            if update.effective_user:
                user_id = update.effective_user.id
            if update.effective_chat:
                chat_id = update.effective_chat.id
            if update.message:
                message_text = (update.message.text or "").strip()
            if update.callback_query:
                callback_data = update.callback_query.data

        logger.exception(
            "Ошибка обработчика: %s | update_type=%s user_id=%s chat_id=%s text=%r callback=%r",
            context.error,
            update_type,
            user_id,
            chat_id,
            message_text,
            callback_data,
        )
        if isinstance(context.error, (TimedOut, NetworkError)):
            logger.warning("Сетевая ошибка (будет ретрай/повтор): %s", context.error)

        if isinstance(update, Update) and update.message:
            try:
                await update.message.reply_text("⚠️ Произошла ошибка. Попробуй ещё раз через пару секунд.")
            except Exception:
                pass

    app.add_error_handler(on_error)
    logger.info("Бот v2 запущен (без LLM, только Whisper для голоса).")

    # Python 3.12+: в main thread loop может отсутствовать.
    # run_polling внутри PTB обращается к текущему loop.
    try:
        import asyncio as _a
        try:
            _a.get_event_loop()
        except RuntimeError:
            _a.set_event_loop(_a.new_event_loop())
    except Exception:
        pass

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
