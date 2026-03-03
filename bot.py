# -*- coding: utf-8 -*-
"""
Telegram-бот: личный AI-ассистент.
Приём задач, приоритизация, планирование, рутины, отчёты.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from telegram import Bot, BotCommand, Update
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

BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", "8785603117:AAGWVVEWSVbIc_ZZDhd26OprknT0e6Ldh1Q"
)
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

# ── Тексты ───────────────────────────────────────────────────────────────

ONBOARDING = (
    "Привет! Я твой помощник по задачам и планам. "
    "Помогаю с делами и рутиной — спокойно и без лишнего стресса.\n\n"
    "*Вот что я умею:*\n\n"
    "📝 Записывать задачи — просто напиши или надиктуй\n"
    "   _Например: «Купить продукты завтра» или «Записать дочку к врачу на пятницу»_\n\n"
    "📋 Показывать список дел — «Что у меня на сегодня?»\n\n"
    "✅ Отмечать сделанное — «Готово: купила продукты»\n\n"
    "✏️ Редактировать — «Перенеси врача на пятницу»\n\n"
    "📊 Приоритизировать — оценю важность и срочность\n\n"
    "📈 Отчёты — итоги за неделю\n\n"
    "🗂 Категории: 🏠 быт · 👨‍👩‍👧 семья · 💇‍♀️ уход · 🌿 для себя · "
    "🎫 досуг · 📦 дела · 🧠 проекты · 🔁 рутины\n\n"
    "_Для начала — просто напиши мне свою первую задачу!_"
)

HELP_TEXT = (
    "*Что я умею:*\n\n"
    "📝 /add — Добавить задачу\n"
    "📋 /tasks — Все активные задачи\n"
    "📅 /today — План на сегодня\n"
    "🔁 /routines — Рутины по дням\n"
    "✅ /done — Отметить выполненной\n"
    "📈 /report — Отчёт за неделю\n"
    "📜 /history — Выполненные задачи\n"
    "🗂 /categories — Категории\n"
    "⚙️ /settings — Настройки\n"
    "🔄 /start — Начать сначала\n\n"
    "_Или просто пиши / диктуй как удобно — я пойму._"
)

TIPS = [
    "💡 Ты можешь писать задачи голосом — просто отправь голосовое сообщение.",
    "💡 Напиши «Что на сегодня?» — я покажу план дня с приоритетами.",
    "💡 Большую задачу можно разбить на шаги. Напиши «Разбей [задачу] на шаги».",
    "💡 Чтобы отметить дело как сделанное, напиши «Готово: [задача]».",
    "💡 Я умею работать с датами: «завтра», «в пятницу в 14:00», «через неделю».",
    "💡 Чтобы перенести задачу, напиши: «Перенеси [задачу] на пятницу».",
    "💡 Если дел накопилось много — попроси: «Выбери 3 самых важных на сегодня».",
]

TIP_INTERVAL = 3

BOT_COMMANDS = [
    ("start", "Начать"),
    ("tasks", "Все задачи"),
    ("today", "План на сегодня"),
    ("routines", "Рутины"),
    ("done", "Отметить выполненной"),
    ("add", "Добавить задачу"),
    ("report", "Отчёт за неделю"),
    ("history", "Выполненные задачи"),
    ("categories", "Категории"),
    ("settings", "Настройки"),
    ("help", "Помощь"),
]


# ── Утилиты ──────────────────────────────────────────────────────────────

async def _reply(update: Update, text: str, max_retries: int = 3) -> None:
    for attempt in range(max_retries + 1):
        try:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return
        except Exception:
            try:
                await update.message.reply_text(text)
                return
            except (TimedOut, NetworkError) as e:
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.info("Retry %s/%s через %sс (%s)", attempt + 1, max_retries, wait, type(e).__name__)
                    await asyncio.sleep(wait)
                else:
                    logger.warning("Не удалось отправить после %s попыток: %s", max_retries + 1, e)


def _get_tip(tips_shown: int) -> str | None:
    if tips_shown <= 0:
        return None
    if tips_shown % TIP_INTERVAL != 0:
        return None
    tip_index = (tips_shown // TIP_INTERVAL) - 1
    if 0 <= tip_index < len(TIPS):
        return TIPS[tip_index]
    return None


def _format_task_list(tasks: list[dict]) -> str:
    if not tasks:
        return "_У тебя пока нет активных задач. Напиши что-нибудь — я запишу!_"

    regular = [t for t in tasks if not t.get("is_routine")]
    routines = [t for t in tasks if t.get("is_routine")]

    lines = [f"📋 *Все задачи ({len(tasks)})*\n"]

    if regular:
        groups: dict[str, list[str]] = {}
        for t in regular:
            emoji = t.get("category_emoji", "") or "📝"
            cat = t.get("category_name", "") or "Другое"
            text = t["text"]
            extra_parts = []
            if t.get("due_date"):
                extra_parts.append(t["due_date"])
            if t.get("due_time"):
                extra_parts.append(t["due_time"])
            elif t.get("time_of_day"):
                extra_parts.append(t["time_of_day"])
            extra = f" — _{', '.join(extra_parts)}_" if extra_parts else ""
            line = f"☐ {emoji} {text}{extra}"
            key = f"{emoji} *{cat}*"
            groups.setdefault(key, []).append(line)

        for header, items in groups.items():
            lines.append(header)
            lines.extend(items)
            lines.append("")

    if routines:
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("🔁 *Рутины*")
        for t in routines:
            emoji = t.get("category_emoji", "") or "🔁"
            text = t["text"]
            day = t.get("repeat_day") or ""
            day_str = f" — _каждый {day}_" if day else ""
            lines.append(f"☐ {emoji} {text}{day_str}")
        lines.append("")

    return "\n".join(lines)


def _format_routines(tasks: list[dict]) -> str:
    if not tasks:
        return "_У тебя пока нет рутин. Напиши, например: «Поливать цветы каждый четверг»_"

    DAY_ORDER = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6, "ежедневно": 7}
    DAY_FULL = {
        "пн": "Понедельник", "вт": "Вторник", "ср": "Среда",
        "чт": "Четверг", "пт": "Пятница", "сб": "Суббота",
        "вс": "Воскресенье", "ежедневно": "Ежедневно",
    }

    by_day: dict[str, list[str]] = {}
    no_day: list[str] = []

    for t in tasks:
        emoji = t.get("category_emoji", "") or "🔁"
        text = t["text"]
        repeat = (t.get("repeat_day") or "").strip()

        if not repeat:
            no_day.append(f"☐ {emoji} {text}")
            continue

        days = [d.strip() for d in repeat.split(",")]
        for day in days:
            day_lower = day.lower()
            key = day_lower if day_lower in DAY_ORDER else "другое"
            line = f"☐ {emoji} {text}"
            by_day.setdefault(key, []).append(line)

    lines = ["🔁 *Рутины*\n"]

    sorted_days = sorted(by_day.keys(), key=lambda d: DAY_ORDER.get(d, 99))
    for day in sorted_days:
        label = DAY_FULL.get(day, day.capitalize())
        lines.append(f"*{label}:*")
        lines.extend(by_day[day])
        lines.append("")

    if no_day:
        lines.append("*Без привязки к дню:*")
        lines.extend(no_day)
        lines.append("")

    return "\n".join(lines)


WEEKDAY_NAMES_RU = {
    0: "понедельник", 1: "вторник", 2: "среду",
    3: "четверг", 4: "пятницу", 5: "субботу", 6: "воскресенье",
}


def _auto_schedule_date(user_id: int, priority_score: float) -> tuple[str, str]:
    """Находит ближайший день, где задач меньше лимита. Возвращает (date_str, human_label)."""
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
                wd = WEEKDAY_NAMES_RU.get(day.weekday(), day.strftime("%d.%m"))
                label = f"в {wd} ({day.strftime('%d.%m')})"
            return date_str, label

    date_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    return date_str, "завтра"


def _build_overload_hint(user_id: int, assigned_date: str, date_label: str) -> str:
    """Если день уже загружен, возвращает мягкую подсказку. Иначе пустую строку."""
    settings = db.get_settings(user_id)
    limit = settings.get("max_tasks_per_day", 7)
    count = db.count_tasks_for_date(user_id, assigned_date)

    if count < limit - 1:
        return ""

    least = db.get_least_priority_task_for_date(user_id, assigned_date)
    hint = f"\n\n⚠️ _На {date_label} уже {count} задач (лимит: {limit})._"
    if least:
        hint += f"\n_Наименее срочная: «{least['text']}». Может, перенести её?_"
    return hint


def _format_today_tasks(tasks: list[dict]) -> str:
    if not tasks:
        return "📅 _На сегодня задач нет. Свободный день или напиши новую задачу!_"

    now = datetime.now()
    date_str = now.strftime("%d.%m")
    months_ru = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
    }
    month_name = months_ru.get(now.month, "")

    urgent, normal, evening, routine, projects = [], [], [], [], []
    for t in tasks:
        emoji = t.get("category_emoji", "") or "📝"
        text = t["text"]
        time_str = ""
        if t.get("due_time"):
            time_str = f" ⏰ {t['due_time']}"
        line = f"☐ {emoji} {text}{time_str}"

        is_routine = t.get("is_routine", False)
        tod = (t.get("time_of_day") or "").lower()
        cat_emoji = t.get("category_emoji", "")
        score = t.get("priority_score", 0) or 0

        if is_routine:
            routine.append(line)
        elif cat_emoji == "🧠":
            projects.append(line)
        elif tod in ("вечер", "ночь"):
            evening.append(line)
        elif score >= 5:
            urgent.append(line)
        else:
            normal.append(line)

    lines = [f"📅 *Сегодня, {now.day} {month_name} — план дня*\n"]

    if urgent:
        lines.append("🔥 *Срочно*")
        lines.extend(urgent)
        lines.append("")
    if normal:
        lines.append("🟡 *По возможности*")
        lines.extend(normal)
        lines.append("")
    if projects:
        lines.append("🧠 *Проекты*")
        lines.extend(projects)
        lines.append("")
    if routine:
        lines.append("🔁 *Рутины*")
        lines.extend(routine)
        lines.append("")
    if evening:
        lines.append("🌙 *Вечером*")
        lines.extend(evening)
        lines.append("")

    total = len(tasks)
    if total > 0 and urgent:
        first_urgent = urgent[0].replace("☐ ", "").split(" ", 1)[-1].split(" ⏰")[0]
        lines.append(f"_У тебя сегодня {total} задач. Самая срочная — {first_urgent}. Может, начнёшь с неё?_")

    return "\n".join(lines)


def _format_report(stats: dict) -> str:
    lines = ["📈 *Итоги за неделю*\n"]
    lines.append(f"✅ Выполнено: *{stats['total_done']}*")
    lines.append(f"📋 Активных: *{stats['total_active']}*\n")

    cats = stats.get("categories_done", {})
    if cats:
        lines.append("*По категориям:*")
        for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
            cat_display = cat if cat else "Без категории"
            lines.append(f"  ☑ {cat_display}: {count}")
        lines.append("")

    postponed = stats.get("most_postponed_category")
    if postponed:
        lines.append(f"⚠️ _Чаще всего откладываются задачи из: {postponed}_\n")

    if stats["total_done"] > 0:
        lines.append("_Отлично поработала на этой неделе!_ 🎉")
    else:
        lines.append("_На этой неделе пока ничего не отмечено — ничего страшного, начни с малого!_")

    return "\n".join(lines)


def _format_history(tasks: list[dict]) -> str:
    if not tasks:
        return "_За последнюю неделю нет выполненных задач._"

    lines = ["📜 *Выполненные задачи (за 7 дней)*\n"]
    for t in tasks:
        emoji = t.get("category_emoji", "") or "✅"
        text = t["text"]
        completed = t.get("completed_at", "")
        date_part = ""
        if completed:
            try:
                dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                date_part = f" — _{dt.strftime('%d.%m')}_"
            except (ValueError, AttributeError):
                pass
        lines.append(f"☑ {emoji} {text}{date_part}")
    return "\n".join(lines)


# ── Обработчики ──────────────────────────────────────────────────────────

_menu_commands_sent = False


async def _ensure_menu_commands_set(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _menu_commands_sent
    logger.info("MENU: _ensure_menu_commands_set вызван, _menu_commands_sent=%s", _menu_commands_sent)
    if _menu_commands_sent:
        return
    try:
        bot = context.application.bot
        await bot.delete_my_commands()
        commands = [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
        logger.info("MENU: вызываю set_my_commands (%d команд)", len(commands))
        await bot.set_my_commands(commands)
        _menu_commands_sent = True
        logger.info("MENU: set_my_commands успешно, меню установлено")
    except Exception as e:
        logger.exception("MENU: set_my_commands ошибка: %s", e)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ensure_menu_commands_set(context)
    user = update.effective_user
    db.get_or_create_user(user.id, user.first_name or "")
    await _reply(update, ONBOARDING)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    tasks = db.get_active_tasks(user_row["id"])
    await _reply(update, _format_task_list(tasks))


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    tasks = db.get_today_tasks(user_row["id"])
    await _reply(update, _format_today_tasks(tasks))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(
        update,
        "📝 Напиши или надиктуй задачу — я её запишу.\n\n"
        "_Например:_\n"
        "• Купить продукты завтра\n"
        "• Записать дочку к врачу в пятницу в 10:00\n"
        "• Позвонить маме вечером",
    )


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    tasks = db.get_active_tasks(user_row["id"])
    if not tasks:
        await _reply(update, "_Нет активных задач для завершения._")
        return
    text = _format_task_list(tasks)
    text += "\n\n_Напиши «Готово: [текст задачи]», чтобы отметить._"
    await _reply(update, text)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    stats = db.get_weekly_stats(user_row["id"])
    await _reply(update, _format_report(stats))


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    tasks = db.get_done_tasks(user_row["id"], days=7)
    await _reply(update, _format_history(tasks))


async def cmd_routines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    tasks = db.get_routine_tasks(user_row["id"])
    await _reply(update, _format_routines(tasks))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    settings = db.get_settings(user_row["id"])
    limit = settings.get("max_tasks_per_day", 7)
    auto = settings.get("auto_schedule", True)
    auto_str = "вкл" if auto else "выкл"
    text = (
        "⚙️ *Настройки*\n\n"
        f"📊 Задач на день: *{limit}*\n"
        f"📅 Авто-назначение дат: *{auto_str}*\n\n"
        "_Чтобы изменить, напиши:_\n"
        "_«Поставь лимит 5 задач на день»_\n"
        "_«Выключи авто-назначение дат»_"
    )
    await _reply(update, text)


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    cats = db.get_categories(user_row["id"])
    if not cats:
        await _reply(update, "_Категории не найдены._")
        return
    lines = ["🗂 *Твои категории:*\n"]
    for c in cats:
        lines.append(f"  {c['emoji']} {c['name']}")
    await _reply(update, "\n".join(lines))


async def _process_user_text(update: Update, user_text: str) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")

    db.save_message(user_row["id"], "user", user_text)

    active_tasks = db.get_active_tasks(user_row["id"])
    recent = db.get_recent_messages(user_row["id"], limit=20)

    ai_result = ai_module.process_message(user_text, active_tasks, recent)

    reply_text = ai_result.get("reply_text", "Записано.")
    msg_type = ai_result.get("type", "chat")

    if msg_type == "task":
        due_date = ai_result.get("due_date")
        is_routine = bool(ai_result.get("is_routine", False))
        settings = db.get_settings(user_row["id"])
        schedule_note = ""

        if not due_date and not is_routine and settings.get("auto_schedule", True):
            pv = ai_result.get("priority_value", 5)
            pu = ai_result.get("priority_urgency", 5)
            pr = ai_result.get("priority_risk", 5)
            ps = ai_result.get("priority_size", 5)
            score = (pv + pu + pr) / max(ps, 1)
            due_date, label = _auto_schedule_date(user_row["id"], score)
            schedule_note = f"\n📅 _Поставила на {label}_"
            hint = _build_overload_hint(user_row["id"], due_date, label)
            schedule_note += hint

        db.add_task(
            user_id=user_row["id"],
            text=ai_result.get("task_text", user_text),
            category_emoji=ai_result.get("category_emoji", ""),
            category_name=ai_result.get("category_name", ""),
            due_date=due_date,
            due_time=ai_result.get("due_time"),
            time_of_day=ai_result.get("time_of_day"),
            priority_value=ai_result.get("priority_value", 5),
            priority_urgency=ai_result.get("priority_urgency", 5),
            priority_risk=ai_result.get("priority_risk", 5),
            priority_size=ai_result.get("priority_size", 5),
            is_routine=is_routine,
            repeat_day=ai_result.get("repeat_day"),
        )

        if schedule_note:
            reply_text += schedule_note

        tips_count = db.increment_tips(user.id)
        tip = _get_tip(tips_count)
        if tip:
            reply_text += f"\n\n{tip}"

    elif msg_type == "tasks":
        task_items = ai_result.get("tasks", [])
        settings = db.get_settings(user_row["id"])
        auto = settings.get("auto_schedule", True)
        scheduled_notes = []

        for t in task_items:
            due_date = t.get("due_date")
            is_routine = bool(t.get("is_routine", False))

            if not due_date and not is_routine and auto:
                pv = t.get("priority_value", 5)
                pu = t.get("priority_urgency", 5)
                pr = t.get("priority_risk", 5)
                ps = t.get("priority_size", 5)
                score = (pv + pu + pr) / max(ps, 1)
                due_date, label = _auto_schedule_date(user_row["id"], score)
                task_name = t.get("task_text", "")
                scheduled_notes.append(f"📅 «{task_name}» → _{label}_")

            db.add_task(
                user_id=user_row["id"],
                text=t.get("task_text", ""),
                category_emoji=t.get("category_emoji", ""),
                category_name=t.get("category_name", ""),
                due_date=due_date,
                due_time=t.get("due_time"),
                time_of_day=t.get("time_of_day"),
                priority_value=t.get("priority_value", 5),
                priority_urgency=t.get("priority_urgency", 5),
                priority_risk=t.get("priority_risk", 5),
                priority_size=t.get("priority_size", 5),
                is_routine=is_routine,
                repeat_day=t.get("repeat_day"),
            )
            db.increment_tips(user.id)

        if scheduled_notes:
            reply_text += "\n\n" + "\n".join(scheduled_notes)

        tips_count = db.get_tips_shown(user.id)
        tip = _get_tip(tips_count)
        if tip:
            reply_text += f"\n\n{tip}"

    elif msg_type == "done":
        search = ai_result.get("search_text", "")
        found = db.find_task_by_text(user_row["id"], search)
        if found:
            db.complete_task(found["id"], user_id=user_row["id"])
            reply_text = f"✅ Отмечено: «{found['text']}»\n\n_Молодец! Одним делом меньше._"
        else:
            reply_text = "_Не нашла такую задачу. Покажи список_ (/tasks) _и уточни._"

    elif msg_type == "done_multiple":
        searches = ai_result.get("search_texts", [])
        found_tasks = db.find_tasks_by_texts(user_row["id"], searches)
        if found_tasks:
            done_names = []
            for t in found_tasks:
                db.complete_task(t["id"], user_id=user_row["id"])
                done_names.append(t["text"])
            reply_text = f"✅ *Отмечено {len(done_names)} задач:*\n" + "\n".join(
                f"  ☑ {name}" for name in done_names
            )
            not_found = [
                s for s in searches
                if s.lower().strip() not in [n.lower() for n in done_names]
                and not db.find_task_by_text(user_row["id"], s)
            ]
            if not_found:
                reply_text += "\n\n⚠️ _Не нашла:_ " + ", ".join(not_found)
            reply_text += "\n\n_Отличная работа!_"
        else:
            reply_text = "_Не нашла эти задачи. Покажи список_ (/tasks) _и уточни._"

    elif msg_type == "edit":
        search = ai_result.get("search_text", "")
        updates = ai_result.get("updates", {})
        found = db.find_task_by_text(user_row["id"], search)
        if found:
            update_kwargs = {}
            field_map = {
                "task_text": "text",
                "due_date": "due_date",
                "due_time": "due_time",
                "time_of_day": "time_of_day",
                "category_emoji": "category_emoji",
                "category_name": "category_name",
                "priority_value": "priority_value",
                "priority_urgency": "priority_urgency",
                "priority_risk": "priority_risk",
                "priority_size": "priority_size",
            }
            for ai_key, db_key in field_map.items():
                if ai_key in updates:
                    update_kwargs[db_key] = updates[ai_key]
            db.update_task(found["id"], user_row["id"], **update_kwargs)
        else:
            reply_text = "_Не нашла задачу для редактирования. Покажи список_ (/tasks) _и уточни._"

    elif msg_type == "delete":
        search = ai_result.get("search_text", "")
        found = db.find_task_by_text(user_row["id"], search)
        if found:
            db.delete_task(found["id"], user_row["id"])
            reply_text = f"🗑 *Удалено:* «{found['text']}»\n\n_Готово, задача убрана из списка._"
        else:
            reply_text = "_Не нашла задачу для удаления. Покажи список_ (/tasks) _и уточни._"

    elif msg_type == "settings_update":
        changes = ai_result.get("settings", {})
        if changes:
            db.update_settings(user_row["id"], **changes)
            settings = db.get_settings(user_row["id"])
            limit = settings.get("max_tasks_per_day", 7)
            auto = "вкл" if settings.get("auto_schedule", True) else "выкл"
            reply_text = (
                "⚙️ *Настройки обновлены*\n\n"
                f"📊 Задач на день: *{limit}*\n"
                f"📅 Авто-назначение дат: *{auto}*"
            )

    db.save_message(user_row["id"], "assistant", reply_text)
    await _reply(update, reply_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)
    voice_bytes = bytes(await tg_file.download_as_bytearray())

    text = ai_module.transcribe_voice(voice_bytes)
    if not text:
        await _reply(update, "🎤 _Не удалось распознать голосовое. Попробуй ещё раз или напиши текстом._")
        return

    await _reply(update, f"🎤 Распознано: «{text}»")
    await _process_user_text(update, text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = (update.message.text or "").strip()
    if not user_text:
        return
    await _process_user_text(update, user_text)


# ── Запуск ───────────────────────────────────────────────────────────────

def _set_menu_commands_sync() -> None:
    async def _do():
        bot = Bot(token=BOT_TOKEN)
        try:
            await bot.delete_my_commands()
            commands = [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
            await bot.set_my_commands(commands)
            logger.info("MENU: команды установлены при старте (%d пунктов)", len(commands))
        except Exception as e:
            logger.exception("MENU: ошибка установки команд: %s", e)

    try:
        asyncio.run(_do())
    except Exception as e:
        logger.exception("MENU: asyncio.run ошибка: %s", e)


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Задайте токен: переменная TELEGRAM_BOT_TOKEN.")
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
        logger.info("MENU: post_init вызван, устанавливаю команды...")
        try:
            await application.bot.delete_my_commands()
            commands = [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
            await application.bot.set_my_commands(commands)
            logger.info("MENU: post_init set_my_commands OK (%d пунктов)", len(commands))
        except Exception as e:
            logger.exception("MENU: post_init set_my_commands ошибка: %s", e)

    app = builder.post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("routines", cmd_routines))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, (TimedOut, NetworkError)):
            logger.warning("Сетевая ошибка Telegram: %s", context.error)
        else:
            logger.exception("Ошибка: %s", context.error)

    app.add_error_handler(on_error)
    logger.info("Бот запущен (v2.1-routines). [меню при старте]")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
