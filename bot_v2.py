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
    infer_time_of_day,
    time_of_day_from_hour,
    classify_time_of_day_edit,
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
# После /done следующее сообщение = фрагмент названия задачи для выполнения.
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
    "• Несколько раз в неделю без дней: «массаж два раза в неделю», «йога 3 раза в неделю»\n"
    "• Время суток: «по утрам», «по вечерам», «утром», «вечером», «днём» — в «План на сегодня» и «Рутины» блоки *Утро / День / Вечер*; у обычных задач ещё помогает время «в 14:00».\n\n"
    "*Как выполнить задачу*\n"
    "• «Выполни купить молоко» — по части названия (как в списке задач)\n"
    "• После выполнения рутина исчезнет из плана на сегодня и снова появится в свой день.\n\n"
    "*Как удалить задачу или рутину*\n"
    "• «Удали задачу 3» — по номеру в формулировке команды (как в подсказках бота)\n"
    "• «Удали рутину 2» — по номеру в экране «Рутины» (/routines)\n\n"
    "*Как изменить задачу*\n"
    "• «Изменить задачу 2 на Купить хлеб» — новый текст (цифра — порядковый номер в команде, см. подсказки бота)\n"
    "• «Изменить рутину 3 на вечер» / «… на утро» / «… на день» / «… на ночь» — только время суток\n"
    "• «Изменить задачу 5 на без времени» — сбросить период суток\n"
    "• «Исправить купить молоко на Купить хлеб»\n\n"
    "*Как перенести задачу*\n"
    "• «Перенеси задачу 3 на завтра», «Перенести задачу 1 на пятницу в 10:00»\n"
    "• Для рутины — перенос на другой день недели\n\n"
    "*Планирование дня на сайте*\n"
    "• «Сегодня»: три блока *Утро / День / Вечер* — перетащи строку задачи (не чекбокс и не меню ⋯) в нужный блок.\n"
    "• «Все задачи»: перетащи строку на другую дату или в «Без срока» (рутины — только на дату).\n"
    "• «Проекты»: большие дела разбиваешь на шаги; шаги ведут себя как обычные задачи и помечаются в списках.\n\n"
    "*Отменить выполнение*\n"
    "• «Отменить выполнение [часть названия]» — вернуть задачу из «Сделано сегодня» в активные\n\n"
    "*Списки и отчёты*\n"
    "• «Список задач», «План на сегодня», «Рутины» — без номеров; для «выполни» используй слова из названия.\n"
    "• «Сделано сегодня», «Сделано за неделю» — по категориям с иконками; за неделю сводка, дата·время факта, повторы рутин и короткий разбор.\n\n"
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
    "отменить выполнение задачи номер",
    "отменить выполнение задачи",
    "отменить выполнение",
    "вернуть в список",
    "вернуть задачу",
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
    "*Список задач* — все активные. *План на сегодня* — на сегодня.\n\n"
    "*Выполнить:* «Выполни [часть названия]».\n\n"
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
    time_of_day: str | None = None,
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
    ]
    if (time_of_day or "").strip():
        lines.append(f"⏳ Время суток: *{time_of_day.strip()}*")
    lines.extend(["", "_Всё верно? Если нет — напиши, что исправить._"])
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


# Порядок блоков в «план на сегодня» и в рутинах (визуальное разделение списка)
_TIME_BUCKET_ORDER = ("утро", "день", "вечер", "ночь", "")
_TIME_BUCKET_HEADER = {
    "утро": "🌅 *Утро*",
    "день": "☀️ *День*",
    "вечер": "🌆 *Вечер*",
    "ночь": "🌙 *Ночь*",
}


def _task_time_bucket(t: dict) -> str:
    """Период суток для группировки: при указанном времени — по часу (как в сутках дня), иначе по time_of_day."""
    ti = (t.get("due_time") or "").strip()
    if ti:
        try:
            h = int(ti.split(":", 1)[0])
            inferred = time_of_day_from_hour(h)
            if inferred:
                return inferred
        except (ValueError, IndexError):
            pass
    tod = (t.get("time_of_day") or "").strip().lower()
    if tod in ("утро", "день", "вечер", "ночь"):
        return tod
    return ""


def _group_tasks_by_time_bucket(
    items: list[tuple[int, dict]],
) -> list[tuple[str, list[tuple[int, dict]]]]:
    buckets: dict[str, list[tuple[int, dict]]] = {k: [] for k in _TIME_BUCKET_ORDER}
    for pair in items:
        b = _task_time_bucket(pair[1])
        buckets[b].append(pair)

    def sort_key(p: tuple[int, dict]) -> tuple:
        t = p[1]
        ti = (t.get("due_time") or "").strip()
        # Сначала с конкретным временем, по возрастанию часов; без времени — в конце блока
        return (0 if ti else 1, ti or "99:99", t.get("id", 0))

    out: list[tuple[str, list[tuple[int, dict]]]] = []
    for b in _TIME_BUCKET_ORDER:
        if buckets[b]:
            out.append((b, sorted(buckets[b], key=sort_key)))
    return out


def _task_line_emoji(t: dict) -> str:
    if t.get("is_routine"):
        return (t.get("category_emoji") or "🔁").strip() or "🔁"
    if t.get("project_id"):
        return ((t.get("project_emoji") or "📁").strip() or "📁")
    return (t.get("category_emoji") or "📝").strip() or "📝"


def _format_task_list(tasks: list[dict]) -> str:
    """Список активных задач: группировка по дате, блок «Рутины», без номеров."""
    if not tasks:
        return "_Пока нет активных задач. Добавь задачу через меню или напиши «Добавь [задача]»._"

    def sort_key(t: dict) -> tuple:
        d = t.get("due_date") or ""
        ti = t.get("due_time") or ""
        return (d, ti, t.get("id", 0))

    sorted_tasks = sorted(tasks, key=sort_key)
    numbered = list(enumerate(sorted_tasks, start=1))

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
        for _loc_i, (_num, t) in enumerate(group, start=1):
            emoji = _task_line_emoji(t)
            text = t["text"]
            time_part = ""
            if t.get("due_time"):
                time_part = f" в {_format_time_human(t['due_time'])}"
            proj = ""
            if t.get("project_title"):
                pe = t.get("project_emoji") or "📁"
                proj = f" _(проект: {pe} {t['project_title']})_"
            lines.append(f"{emoji} {text}{time_part}{proj}")
        lines.append("")

    if "" in by_date:
        lines.append("*📅 Без срока*")
        for _loc_i, (_num, t) in enumerate(by_date[""], start=1):
            emoji = _task_line_emoji(t)
            text = t["text"]
            time_part = ""
            if t.get("due_time"):
                time_part = f" в {_format_time_human(t['due_time'])}"
            proj = ""
            if t.get("project_title"):
                pe = t.get("project_emoji") or "📁"
                proj = f" _(проект: {pe} {t['project_title']})_"
            lines.append(f"{emoji} {text}{time_part}{proj}")
        lines.append("")

    if routines_list:
        lines.append("*🔁 Рутины*")
        lines.append("")
        for bucket, pairs in _group_tasks_by_time_bucket(routines_list):
            if bucket:
                lines.append(_TIME_BUCKET_HEADER[bucket])
                lines.append("")
            for _loc_i, (_num, t) in enumerate(pairs, start=1):
                emoji = _task_line_emoji(t)
                text = t["text"]
                repeat_label = db.format_repeat_day_display(t.get("repeat_day"))
                tod_part = ""
                td = (t.get("time_of_day") or "").strip()
                if td and not bucket:
                    tod_part = f" · _{td}_"
                proj = ""
                if t.get("project_title"):
                    pe = t.get("project_emoji") or "📁"
                    proj = f" · _{pe} {t['project_title']}_"
                lines.append(f"{emoji} {text}{tod_part} — _{repeat_label}_{proj}")
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
    time_of_day_val = infer_time_of_day(task_text)
    if not time_of_day_val and due_time and not is_routine:
        try:
            h = int(str(due_time).split(":", 1)[0])
            time_of_day_val = time_of_day_from_hour(h)
        except (ValueError, IndexError):
            time_of_day_val = None
    # Убираем дату, время и фразы рутины из названия задачи
    task_title = (clean_task_text_from_datetime(task_text) or task_text).strip()
    if is_routine:
        task_title = (routines.clean_task_title_from_routine_phrases(task_title) or task_title).strip()
    if task_title:
        task_title = normalize_task_display(task_title)
    date_label = ""

    # Определение категории по тексту задачи (до upper — по оригиналу для лучшего матчинга)
    _raw_for_cat = (clean_task_text_from_datetime(task_text) or task_text).strip()
    category_emoji, category_name = assign_category(_raw_for_cat, internal_user_id)

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
            time_of_day=time_of_day_val,
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
                time_of_day=(task_row.get("time_of_day") or time_of_day_val),
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
    db.attach_project_labels(user_id, tasks)

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
    lines = ["🔁 *Рутины*", ""]
    indexed = [(i, t) for i, t in enumerate(routine_tasks, start=1)]
    for bucket, group in _group_tasks_by_time_bucket(indexed):
        if bucket:
            lines.append(_TIME_BUCKET_HEADER[bucket])
            lines.append("")
        for _num, t in group:
            repeat_label = db.format_repeat_day_display(t.get("repeat_day"))
            emoji = _task_line_emoji(t)
            lines.append(f"• {emoji} {t['text']} — _{repeat_label}_")
        lines.append("")
    await _reply(update, "\n".join(lines).rstrip())


def _format_today_list(ordered_today: list[tuple[int, dict]], title: str = "📅 *План на сегодня*") -> str:
    """Список задач на сегодня: блоки утро/день/вечер, без номеров."""
    lines = [f"{title}\n"]
    for bucket, pairs in _group_tasks_by_time_bucket(ordered_today):
        if bucket:
            lines.append(_TIME_BUCKET_HEADER[bucket])
            lines.append("")
        for _num, t in pairs:
            emoji = _task_line_emoji(t)
            time_part = f" в {_format_time_human(t['due_time'])}" if t.get("due_time") else ""
            tod_hint = ""
            td = (t.get("time_of_day") or "").strip()
            if td and not t.get("due_time") and not bucket:
                tod_hint = f" · _{td}_"
            proj = ""
            if t.get("project_title"):
                pe = t.get("project_emoji") or "📁"
                proj = f" · _{pe} {t['project_title']}_"
            lines.append(f"• {emoji} {t['text']}{time_part}{tod_hint}{proj}")
        if bucket:
            lines.append("")
    return "\n".join(lines).rstrip()


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
        "Напиши *часть названия* задачи, которую отметить выполненной "
        "(например: _выполни купить молоко_).\n"
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


def _format_completed_compact(dt) -> str:
    """Компактно: дата факта + время (для недельного отчёта)."""
    if not dt:
        return ""
    return f"{dt.day:02d}.{dt.month:02d} · {dt.strftime('%H:%M')}"


def _plural_tasks_word(n: int) -> str:
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        return "задача"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "задачи"
    return "задач"


def _plural_times_word(n: int) -> str:
    """Склонение для «N раз» (отметки рутин)."""
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        return "раз"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "раза"
    return "раз"


def _plural_days_word(n: int) -> str:
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "дня"
    return "дней"


def _routine_rollup_entries(tasks: list[dict]) -> list[tuple[str, int]]:
    """Рутины с 2+ отметками за период — для сводки «сколько раз»."""
    tallies: dict[str, tuple[str, int]] = {}
    for t in tasks:
        if not t.get("is_routine"):
            continue
        raw = (t.get("text") or "").strip()
        if not raw:
            continue
        k = raw.lower()
        if k not in tallies:
            tallies[k] = (raw, 0)
        disp, c = tallies[k]
        tallies[k] = (disp, c + 1)
    out = [(disp, c) for disp, c in tallies.values() if c >= 2]
    out.sort(key=lambda x: -x[1])
    return out


def _format_week_insight_lines(by_day: dict, n_tasks: int) -> list[str]:
    """Короткий «анализ» недели без сложной аналитики."""
    lines = ["*📊 Неделя в двух словах*", ""]
    active_keys = [k for k in by_day if k != "_no_date_"]
    if not active_keys:
        lines.append("_Пока мало отметок — зато есть куда расти._")
        return lines
    counts = [(k, len(by_day[k])) for k in active_keys]
    counts.sort(key=lambda x: -x[1])
    best_key, best_n = counts[0]
    best_label = _format_date_human(best_key)
    wd_part = ""
    try:
        dtp = datetime.strptime(best_key, "%Y-%m-%d")
        wd_short = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][dtp.weekday()]
        wd_part = f" ({wd_short})"
    except ValueError:
        pass
    lines.append(
        f"· Самый насыщенный день: *{best_label}*{wd_part} — *{best_n}* {_plural_tasks_word(best_n)}."
    )
    days_marked = len(counts)
    avg = n_tasks / max(days_marked, 1)
    lines.append(
        f"· В среднем *{avg:.1f}* задач на день с отметками "
        f"(*{days_marked}* {_plural_days_word(days_marked)} из 7)."
    )
    if n_tasks >= 14:
        lines.append("· _Очень плотная неделя — много закрытых дел._")
    elif n_tasks >= 7:
        lines.append("· _Хороший, устойчивый темп._")
    elif n_tasks >= 3:
        lines.append("· _Заметный прогресс, можно наращивать._")
    else:
        lines.append("· _Спокойный ритм; микрошаги тоже считаются._")
    return lines


def _format_done_report_today(tasks: list[dict], tz_name: str) -> str:
    """Отчёт за день: сводка, группировка по категориям, время выполнения, дружелюбный пустой."""
    if not tasks:
        return (
            "🔥 *Сделано сегодня*\n\n"
            "_Когда закроешь день, загляни сюда снова — увидишь, что уже сделано, и сможешь оценить баланс дел._\n\n"
            "_Сегодня пока ни одной задачи не отмечено. План на сегодня ещё можно выполнить!_"
        )
    lines = ["🔥 *Сделано сегодня*", ""]
    lines.append(
        "_Конец дня — момент и подбодрить себя, и честно посмотреть, куда ушло внимание._"
    )
    lines.append("")
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
    if n >= 8:
        lines.append(f"✨ *{n} задач за день* — сильный результат.")
    elif n >= 4:
        lines.append(f"👍 *{n} задач* — хороший, заметный прогресс.")
    else:
        lines.append(f"🌱 *{n} задач* — даже маленькие шаги складываются в день.")
    lines.append("")
    lines.extend(["*📌 Итог*", f"· задач: *{n}*", f"· категорий: *{n_cats}*", ""])
    if n >= 3 and cats:
        top_key = max(cats.keys(), key=lambda k: len(cats[k]))
        top_n = len(cats[top_key])
        if top_n >= 2:
            em, nm = top_key
            pct = round(100 * top_n / n)
            lines.append("*💡 Мягкий разбор*")
            lines.append(
                f"_Чаще всего сегодня — {em} *{nm}* ({top_n} из {n}, ~{pct}%)._"
            )
            lines.append(
                "_Если хочется «разгрузить» одну сферу, завтра можно заранее заложить блоки под другое._"
            )
            lines.append("")
    first_block = True
    for emoji, name in _REPORT_CATEGORY_ORDER:
        group = cats.get((emoji, name), [])
        if not group:
            continue
        if not first_block:
            lines.extend(["· · · · ·", ""])
        first_block = False
        lines.append(f"{emoji} *{name}*")
        for t in group:
            text = (t.get("text") or "").strip()
            ts = t.get("_use_completed_at") or t.get("completed_at") or t.get("last_completed_at")
            dt = _parse_completed_at(ts, tz_name)
            time_str = _format_completed_time(dt) if dt else ""
            routine_mark = " 🔁" if t.get("is_routine") else ""
            proj = ""
            if t.get("project_title"):
                pe = t.get("project_emoji") or "📁"
                proj = f" · _{pe} {t['project_title']}_"
            lines.append(f"  ▸ {emoji} {text}{time_str}{routine_mark}{proj}")
        lines.append("")
    lines.append("*🎯 Зачем смотреть отчёт*")
    lines.append("_Подкрепить ощущение «я молодец» и заметить, не уходит ли всё в один тип дел._")
    return "\n".join(lines).rstrip()


def _calendar_week_range_pretty(week_mon: str | None, week_sun: str | None) -> str:
    if not week_mon or not week_sun:
        return ""
    try:
        a = datetime.strptime(week_mon, "%Y-%m-%d")
        b = datetime.strptime(week_sun, "%Y-%m-%d")
        if a.year == b.year:
            return f"{a.day} {_MONTH_RU[a.month]} – {b.day} {_MONTH_RU[b.month]} {b.year}"
        return f"{a.day} {_MONTH_RU[a.month]} {a.year} – {b.day} {_MONTH_RU[b.month]} {b.year}"
    except (ValueError, KeyError):
        return f"{week_mon} – {week_sun}"


def _format_done_report_week(
    tasks: list[dict],
    tz_name: str,
    *,
    week_mon: str | None = None,
    week_sun: str | None = None,
    habit_counts: list[tuple[str, int]] | None = None,
) -> str:
    """Отчёт за календарную неделю (пн–вс): сводка, по дням, привычки по журналу отметок."""
    wk = _calendar_week_range_pretty(week_mon, week_sun)
    wk_line = f" ({wk})" if wk else ""
    if not tasks:
        return (
            f"🔥 *Сделано за неделю*{wk_line}\n\n"
            "_Календарная неделя: с понедельника по воскресенье (в твоём часовом поясе)._"
            "\n\n"
            "_За эту неделю не отмечено ни одной задачи. "
            "Добавляй дела и отмечай выполненные — прогресс накапливается!_"
        )
    lines = [f"🔥 *Сделано за неделю*{wk_line}", ""]
    lines.append(
        "_Календарная неделя (пн–вс). Срез помогает почувствовать темп и спланировать следующую неделю._"
    )
    lines.append("")
    n = len(tasks)
    if n >= 25:
        lines.append(f"🏆 *{n} задач за неделю* — очень плотная неделя.")
    elif n >= 14:
        lines.append(f"💪 *{n} задач* — устойчивый, сильный ритм.")
    elif n >= 7:
        lines.append(f"👍 *{n} задач* — хороший недельный объём.")
    else:
        lines.append(f"🌿 *{n} задач* — спокойный ритм; микрошаги тоже считаются.")
    lines.append("")
    lines.append(f"Всего за неделю: *{n}* отметок в отчёте")
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
    lines.append("*📂 Категории*")
    lines.append("")
    for emoji, name in _REPORT_CATEGORY_ORDER:
        c = cat_counts.get((emoji, name), 0)
        if c:
            lines.append(f"  {emoji} *{name}:* {c}")
    lines.append("")
    if n >= 5 and cat_counts:
        top_key = max(cat_counts.keys(), key=lambda k: cat_counts[k])
        top_c = cat_counts[top_key]
        em, nm = top_key
        pct = round(100 * top_c / n)
        lines.append("*📊 Фокус недели*")
        lines.append(
            f"_Больше всего закрыто в категории {em} *{nm}* — *{top_c}* задач (~{pct}%)._"
        )
        lines.append(
            "_Если хочется больше баланса, можно на следующей неделе сознательно заложить «якорные» дела в других сферах._"
        )
        lines.append("")
    roll = _routine_rollup_entries(tasks)
    if roll:
        lines.append("*🔁 Повторы в списке отчёта*")
        lines.append("_Несколько строк по одной рутине (разные отметки в списке)._")
        lines.append("")
        for disp, c in roll:
            lines.append(f"  · *{disp}* ×{c}")
        lines.append("")
    if habit_counts:
        lines.append("*🌿 Привычки (рутины)*")
        lines.append("_Сколько раз нажала «выполнено» по каждой рутине — по журналу отметок._")
        lines.append("")
        for name, c in habit_counts:
            nm = (name or "").strip() or "—"
            lines.append(f"  · *{nm}* — *{c}* {_plural_times_word(c)}")
        lines.append("")
    day_keys = sorted([k for k in by_day.keys() if k != "_no_date_"], reverse=True)
    if "_no_date_" in by_day:
        day_keys.append("_no_date_")
    lines.append("*🗓 По дням*")
    lines.append("")
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
            cname = t.get("category_name") or "Другое"
            key = (emoji, cname)
            if key not in by_cat:
                by_cat[key] = []
            by_cat[key].append(t)
        for emoji, name in _REPORT_CATEGORY_ORDER:
            sub = by_cat.get((emoji, name), [])
            if not sub:
                continue
            lines.append(f"  {emoji} *{name}*")
            for t in sub:
                text = (t.get("text") or "").strip()
                ts = t.get("_use_completed_at") or t.get("completed_at") or t.get("last_completed_at")
                dt = _parse_completed_at(ts, tz_name)
                routine_part = ""
                if t.get("is_routine") and t.get("repeat_day"):
                    routine_part = f" 🔁 {db.format_repeat_day_display(t.get('repeat_day'))}"
                elif t.get("is_routine"):
                    routine_part = " 🔁"
                proj = ""
                if t.get("project_title"):
                    pe = t.get("project_emoji") or "📁"
                    proj = f" · _{pe} {t['project_title']}_"
                if dt:
                    fact = _format_completed_compact(dt)
                    lines.append(f"    ▸ {emoji} {text} · _{fact}_{routine_part}{proj}")
                else:
                    lines.append(f"    ▸ {emoji} {text}{routine_part}{proj}")
        lines.append("")
    lines.extend(_format_week_insight_lines(by_day, n))
    lines.append("")
    lines.append("*🎯 Итог*")
    lines.append("_Ты уже проделала работу — отчёт лишь делает её видимой. Используй его, как подсказку, а не как оценку «хорошо/плохо»._")
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
    db.attach_project_labels(user_row["id"], tasks)
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    text = _format_done_report_today(tasks, tz_name)
    await _reply(update, text)


async def cmd_done_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    uid = user_row["id"]
    tasks, mon, sun, start_utc, end_utc = db.get_done_tasks_calendar_week(uid)
    db.attach_project_labels(uid, tasks)
    tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
    habits = db.routine_completion_counts_between(uid, start_utc, end_utc)
    habit_lines = [(str(r.get("text") or ""), int(r["c"])) for r in habits if r.get("c")]
    text = _format_done_report_week(
        tasks,
        tz_name,
        week_mon=mon,
        week_sun=sun,
        habit_counts=habit_lines,
    )
    await _reply(update, text)


async def _handle_complete(
    update: Update, user_row: dict, text: str
) -> None:
    """Обработка «выполни [название]»: по тексту; при нескольких совпадениях — уточнение."""
    uid = user_row["id"]
    nums, num, rest = extract_done_targets(text)
    ordered = _active_tasks_display_order(uid)
    if not ordered:
        await _reply(update, "Нет активных задач для выполнения.")
        return

    if nums or num is not None:
        await _reply(
            update,
            "Отметка по номеру отключена. Напиши часть названия задачи, например: "
            "_«Выполни купить молоко»_.",
        )
        return
    # По названию
    if not rest:
        await _reply(
            update,
            "Напиши часть названия задачи, например: _«Выполни купить молоко»_.",
        )
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
            await _reply(update, f"🔥 Выполнено: «{task['text']}»")
            await _send_remaining_today(update, uid)
        else:
            await _reply(update, "Не удалось отметить задачу.")
        return
    parts = []
    for t in matches:
        em = _task_line_emoji(t)
        parts.append(f"• {em} {t['text']}")
    msg = (
        f"Найдено задач: {len(matches)}.\n\n"
        + "\n".join(parts)
        + "\n\n_Уточни формулировку, чтобы совпала одна задача (добавь слова из названия)._"
    )
    await _reply(update, msg)


async def _handle_uncomplete(update: Update, user_row: dict, text: str) -> None:
    """Отмена выполнения: вернуть задачу в активные (из списка «Сделано сегодня») по фрагменту названия."""
    import re

    uid = user_row["id"]
    done_tasks = db.get_done_tasks_today(uid)
    if not done_tasks:
        await _reply(update, "Сегодня пока нет выполненных задач. Нечего отменять.")
        return

    lower = text.strip().lower()
    rest = ""
    for syn in SYN_UNCOMPLETE:
        if syn in lower:
            rest = text[lower.index(syn) + len(syn) :].strip()
            rest = re.sub(r"^(?:задач[уа]\.?\s*)?(?:номер\s*)?", "", rest, flags=re.IGNORECASE).strip()
            break

    if not rest:
        lines = [
            "Выполнено сегодня. Чтобы вернуть задачу в активные, напиши, например:\n"
            "_«Отменить выполнение [часть названия]»_\n"
        ]
        for t in done_tasks:
            lines.append(f"• {t.get('text', '')}")
        await _reply(update, "\n".join(lines))
        return

    q = rest.lower()
    matches = [t for t in done_tasks if q in (t.get("text") or "").lower()]
    if len(matches) > 1:
        await _reply(update, "Найдено несколько совпадений — уточни фразу из названия.")
        return
    if len(matches) == 0:
        await _reply(update, "Не нашла такую задачу среди выполненных сегодня.")
        return
    task = matches[0]
    if db.uncomplete_task(task["id"], uid):
        await _reply(update, f"↩️ Задача «{task.get('text', '')}» снова в списке активных.")
    else:
        await _reply(update, "Не удалось отменить выполнение.")


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
    """Изменить текст задачи/рутины или только время суток (глобальный номер из списка задач)."""
    uid = user_row["id"]
    num, search_text, new_text = extract_edit_target(text)
    if not new_text or not new_text.strip():
        await _reply(
            update,
            "Напиши, например: _Изменить задачу 2 на Купить хлеб_, "
            "_Изменить рутину 3 на вечер_, "
            "_Исправить купить молоко на Купить хлеб_.",
        )
        return
    task = _resolve_task_by_num_or_search(uid, num, search_text)
    if task is None:
        if num is not None:
            await _reply(update, f"Нет задачи с номером {num}. Посмотри список задач и укажи верный номер.")
        else:
            await _reply(update, f"Задача по запросу «{search_text}» не найдена или найдено несколько — укажи номер.")
        return
    new_raw = new_text.strip()
    tod_action = classify_time_of_day_edit(new_raw)
    if tod_action != "not":
        new_tod = None if tod_action == "clear" else tod_action
        updated = db.update_task(task["id"], uid, time_of_day=new_tod)
        if updated:
            title = updated.get("text") or task.get("text", "")
            if tod_action == "clear":
                await _reply(update, f"✏️ Время суток сброшено: «{title}»")
            else:
                await _reply(
                    update,
                    f"✏️ Время суток: *{new_tod}* — «{title}»",
                )
        else:
            await _reply(update, "Не удалось обновить время суток.")
        return
    new_title = normalize_task_display(new_raw)
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
        db.attach_project_labels(uid, tasks)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        await _reply(update, _format_done_report_today(tasks, tz_name))
        return

    # Синонимы: сделано за неделю
    if _match_synonym(text, SYN_DONE_WEEK):
        uid = user_row["id"]
        tasks, mon, sun, start_utc, end_utc = db.get_done_tasks_calendar_week(uid)
        db.attach_project_labels(uid, tasks)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        habits = db.routine_completion_counts_between(uid, start_utc, end_utc)
        habit_lines = [(str(r.get("text") or ""), int(r["c"])) for r in habits if r.get("c")]
        await _reply(
            update,
            _format_done_report_week(
                tasks,
                tz_name,
                week_mon=mon,
                week_sun=sun,
                habit_counts=habit_lines,
            ),
        )
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
        db.attach_project_labels(uid, tasks)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        await _reply(update, _format_done_report_today(tasks, tz_name))
        return

    if _match_synonym(text, SYN_DONE_WEEK):
        uid = user_row["id"]
        tasks, mon, sun, start_utc, end_utc = db.get_done_tasks_calendar_week(uid)
        db.attach_project_labels(uid, tasks)
        tz_name = (user_row.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow"
        habits = db.routine_completion_counts_between(uid, start_utc, end_utc)
        habit_lines = [(str(r.get("text") or ""), int(r["c"])) for r in habits if r.get("c")]
        await _reply(
            update,
            _format_done_report_week(
                tasks,
                tz_name,
                week_mon=mon,
                week_sun=sun,
                habit_counts=habit_lines,
            ),
        )
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
