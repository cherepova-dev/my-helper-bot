# -*- coding: utf-8 -*-
"""
Бот v2: только добавление одной задачи и общий список.
Без LLM: классификация и извлечение задачи — по правилам и состоянию.
Голос — только Whisper (распознавание речи), дальше тот же алгоритм.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

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
from task_parsing import (
    parse_due_date,
    parse_due_time,
    extract_task_text,
    starts_with_add_marker,
    clean_task_text_from_datetime,
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

BOT_COMMANDS = [
    ("start", "Начать"),
    ("add", "Добавить задачу"),
    ("tasks", "Список задач"),
]

ONBOARDING_V2 = (
    "Привет! Я помощник по задачам.\n\n"
    "*Как добавить задачу:*\n"
    "• Нажми «Добавить задачу» и отправь следующим сообщением текст задачи\n"
    "• Или напиши: «Добавь [задача]», «Создай [задача]», «Запиши [задача]»\n\n"
    "«Список задач» — покажет все активные задачи."
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


def _build_confirmation(task_text: str, due_date: str | None, date_label: str, due_time: str | None) -> str:
    """Текст подтверждения одной задачи."""
    if due_date and date_label:
        date_display = f"{date_label} ({due_date})"
    elif due_date:
        date_display = due_date
    else:
        date_display = "без срока"
    if due_time:
        date_display += f" в {due_time}"
    lines = [
        "✅ *Задача принята*",
        "",
        f"📝 «{task_text}»",
        f"📂 Категория: 📝 Другое",
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


def _format_task_list(tasks: list[dict]) -> str:
    """Форматирует список активных задач: группировка по дате, сортировка по дате и времени, человекочитаемые дата/время."""
    if not tasks:
        return "_Пока нет активных задач. Добавь задачу через меню или напиши «Добавь [задача]»._"

    # Сортировка: сначала с датой (по дате, затем по времени), потом без даты
    def sort_key(t: dict) -> tuple:
        d = t.get("due_date") or ""
        ti = t.get("due_time") or ""
        return (d, ti)

    sorted_tasks = sorted(tasks, key=sort_key)

    # Группировка по дате
    by_date: dict[str, list[dict]] = {}
    for t in sorted_tasks:
        d = t.get("due_date") or ""
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(t)

    lines = [f"📋 *Все задачи ({len(tasks)})*\n"]

    # Сначала задачи с датой (пустая строка = без срока в конце)
    date_keys = [k for k in by_date if k]
    date_keys.sort()
    for date_str in date_keys:
        group = by_date[date_str]
        label = _format_date_human(date_str)
        lines.append(f"*📅 {label}*")
        for t in group:
            emoji = t.get("category_emoji") or "📝"
            text = t["text"]
            time_part = ""
            if t.get("due_time"):
                time_part = f" в {_format_time_human(t['due_time'])}"
            lines.append(f"☐ {emoji} {text}{time_part}")
        lines.append("")

    # Задачи без срока
    if "" in by_date:
        lines.append("*📅 Без срока*")
        for t in by_date[""]:
            emoji = t.get("category_emoji") or "📝"
            text = t["text"]
            time_part = ""
            if t.get("due_time"):
                time_part = f" в {_format_time_human(t['due_time'])}"
            lines.append(f"☐ {emoji} {text}{time_part}")
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

    due_date = _parse_due_date(task_text)
    due_time = _parse_due_time(task_text)
    # Убираем дату и время из названия задачи — они хранятся в полях due_date/due_time
    task_title = clean_task_text_from_datetime(task_text) or task_text
    date_label = ""

    if due_date:
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
            category_emoji="📝",
            category_name="Другое",
            due_date=due_date,
            due_time=due_time,
            priority_value=5,
            priority_urgency=5,
            priority_risk=5,
            priority_size=5,
        )
        if task_row:
            msg = _build_confirmation(task_title, due_date, date_label, due_time)
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
    await _reply(update, ONBOARDING_V2)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.get_or_create_user(user.id, user.first_name or "")
    _awaiting_task[user.id] = True
    await _reply(
        update,
        "Напиши или надиктуй задачу — *следующее* сообщение я сохраню как задачу.",
    )


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    tasks = db.get_active_tasks(user_row["id"])
    text = _format_task_list(tasks)
    await _reply(update, text)


# ─── Обработка текста и голоса ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")
    text = (update.message.text or "").strip()

    if not text:
        await _reply(update, "Напиши текст задачи или используй «Добавь [задача]».")
        return

    # Режим «ожидаю задачу» после /add
    if _awaiting_task.pop(user.id, False):
        await _save_one_task_and_reply(update, user_row, text)
        return

    # Фраза вида «Добавь ...» / «Создай ...» / «Запиши ...»
    if starts_with_add_marker(text):
        task_text = extract_task_text(text)
        await _save_one_task_and_reply(update, user_row, task_text)
        return

    # Иначе — подсказка
    await _reply(
        update,
        "Чтобы добавить задачу: нажми «Добавить задачу» и отправь текст задачи, "
        "или напиши: «Добавь [задача]», «Создай [задача]», «Запиши [задача]».",
    )


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

    await update.message.reply_text(f"🎤 Распознано: «{text[:200]}{'…' if len(text) > 200 else ''}»")

    # Тот же алгоритм, что и для текста
    if _awaiting_task.pop(user.id, False):
        await _save_one_task_and_reply(update, user_row, text.strip())
        return

    if starts_with_add_marker(text):
        task_text = extract_task_text(text)
        await _save_one_task_and_reply(update, user_row, task_text)
        return

    # Голос без маркера — считаем весь текст задачей (как «следующее сообщение» после add)
    await _save_one_task_and_reply(update, user_row, text.strip())


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
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, (TimedOut, NetworkError)):
            logger.warning("Сетевая ошибка: %s", context.error)
        else:
            logger.exception("Ошибка: %s", context.error)
            if isinstance(update, Update) and update.message:
                try:
                    await update.message.reply_text("⚠️ Произошла ошибка. Попробуй ещё раз через пару секунд.")
                except Exception:
                    pass

    app.add_error_handler(on_error)
    logger.info("Бот v2 запущен (без LLM, только Whisper для голоса).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
