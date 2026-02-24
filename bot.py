# -*- coding: utf-8 -*-
"""
Telegram-бот: личный AI-ассистент.
MVP: онбординг, приём задач через AI, список задач, отметка «сделано», подсказки.
"""

import logging
import os

from telegram import Update
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
    "Привет! Я твой личный помощник. "
    "Помогу с задачами, планами и рутиной — без давления и лишнего стресса.\n\n"
    "Вот что я умею:\n\n"
    "📝 Записывать задачи — просто напиши или надиктуй, я разберусь.\n"
    "   Например: «Купить продукты завтра» или «Записать дочку к врачу на пятницу»\n\n"
    "📋 Показывать список дел — «Что у меня на сегодня?» или «Покажи задачи»\n\n"
    "✅ Отмечать сделанное — «Готово: купила продукты»\n\n"
    "📊 Приоритизировать — я сам оценю важность и срочность, но ты всегда можешь поправить.\n\n"
    "⏰ Напоминать — мягко и вовремя, без давления.\n\n"
    "🗂 Категории: 🏠 быт · 👨‍👩‍👧 семья · 💇‍♀️ уход · 🌿 для себя · "
    "🎫 досуг · 📦 дела · 🧠 проекты · 🔁 рутины\n\n"
    "Для начала — просто напиши мне свою первую задачу!"
)

HELP_TEXT = (
    "Что я умею:\n\n"
    "📝 Задачи — просто напиши или надиктуй\n"
    "✅ Готово — «Готово: [задача]»\n"
    "📋 Список — «Покажи задачи» или /tasks\n"
    "📊 План — «Что на сегодня?» или /plan\n"
    "🗂 Категории — /categories\n"
    "⚙️ Настройки — /settings\n"
    "🔁 Регулярные — при создании скажи «каждый день» или «каждую неделю»\n\n"
    "Просто пиши как удобно — я пойму."
)

TIPS = [
    "💡 Ты можешь писать задачи голосом — просто отправь голосовое сообщение.",
    "💡 Напиши «Что на сегодня?» — я покажу план дня с приоритетами.",
    "💡 Большую задачу можно разбить на шаги. Напиши «Разбей [задачу] на шаги».",
    "💡 Чтобы отметить дело как сделанное, напиши «Готово: [задача]».",
    "💡 Я умею работать с датами: «завтра», «в пятницу в 14:00», «через неделю».",
    "💡 Напиши «Покажи категории» — можно добавить свои или переименовать.",
    "💡 Если дел накопилось много — попроси: «Выбери 3 самых важных на сегодня».",
]

# Показываем подсказку после каждой 3-й задачи (3, 6, 9, ... до 21)
TIP_INTERVAL = 3


# ── Утилиты ──────────────────────────────────────────────────────────────

async def _reply(update: Update, text: str, max_retries: int = 3) -> None:
    import asyncio
    for attempt in range(max_retries + 1):
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
    """Возвращает подсказку, если пора, иначе None."""
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
        return "У тебя пока нет активных задач. Напиши что-нибудь — я запишу!"
    lines = ["📋 Твои задачи:\n"]
    for t in tasks:
        emoji = t.get("category_emoji", "") or "📝"
        text = t["text"]
        extra = ""
        if t.get("due_date"):
            extra += f" 📅 {t['due_date']}"
        if t.get("due_time"):
            extra += f" ⏰ {t['due_time']}"
        elif t.get("time_of_day"):
            tod_icons = {"утро": "🌅", "день": "☀️", "вечер": "🌆", "ночь": "🌙"}
            tod = t["time_of_day"]
            extra += f" {tod_icons.get(tod, '🕐')} {tod}"
        score = t.get("priority_score", 0)
        if score:
            extra += f" (⚡ {score})"
        lines.append(f"☐ {emoji} {text}{extra}")
    return "\n".join(lines)


# ── Обработчики ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.get_or_create_user(user.id, user.first_name or "")
    await _reply(update, ONBOARDING)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    tasks = db.get_active_tasks(user_row["id"])
    await _reply(update, _format_task_list(tasks))


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    tasks = db.get_active_tasks(user_row["id"])
    if not tasks:
        await _reply(update, "Нет активных задач для завершения.")
        return
    text = _format_task_list(tasks)
    text += "\n\nНапиши номер задачи или «Готово: [текст задачи]», чтобы отметить."
    await _reply(update, text)


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = db.get_or_create_user(update.effective_user.id)
    cats = db.get_categories(user_row["id"])
    if not cats:
        await _reply(update, "Категории не найдены.")
        return
    lines = ["🗂 Твои категории:\n"]
    for c in cats:
        lines.append(f"  {c['emoji']} {c['name']}")
    await _reply(update, "\n".join(lines))


async def _process_user_text(update: Update, user_text: str) -> None:
    """Общая логика: принимает текст, отправляет в AI, сохраняет задачи, отвечает."""
    user = update.effective_user
    user_row = db.get_or_create_user(user.id, user.first_name or "")

    db.save_message(user_row["id"], "user", user_text)

    active_tasks = db.get_active_tasks(user_row["id"])
    recent = db.get_recent_messages(user_row["id"], limit=20)

    ai_result = ai_module.process_message(user_text, active_tasks, recent)

    reply_text = ai_result.get("reply_text", "Записано.")
    msg_type = ai_result.get("type", "chat")

    if msg_type == "task":
        db.add_task(
            user_id=user_row["id"],
            text=ai_result.get("task_text", user_text),
            category_emoji=ai_result.get("category_emoji", ""),
            category_name=ai_result.get("category_name", ""),
            due_date=ai_result.get("due_date"),
            due_time=ai_result.get("due_time"),
            time_of_day=ai_result.get("time_of_day"),
            priority_value=ai_result.get("priority_value", 5),
            priority_urgency=ai_result.get("priority_urgency", 5),
            priority_risk=ai_result.get("priority_risk", 5),
            priority_size=ai_result.get("priority_size", 5),
        )

        tips_count = db.increment_tips(user.id)
        tip = _get_tip(tips_count)
        if tip:
            reply_text += f"\n\n{tip}"

    elif msg_type == "tasks":
        task_items = ai_result.get("tasks", [])
        for t in task_items:
            db.add_task(
                user_id=user_row["id"],
                text=t.get("task_text", ""),
                category_emoji=t.get("category_emoji", ""),
                category_name=t.get("category_name", ""),
                due_date=t.get("due_date"),
                due_time=t.get("due_time"),
                time_of_day=t.get("time_of_day"),
                priority_value=t.get("priority_value", 5),
                priority_urgency=t.get("priority_urgency", 5),
                priority_risk=t.get("priority_risk", 5),
                priority_size=t.get("priority_size", 5),
            )
            db.increment_tips(user.id)

        tips_count = db.get_tips_shown(user.id)
        tip = _get_tip(tips_count)
        if tip:
            reply_text += f"\n\n{tip}"

    elif msg_type == "done":
        search = ai_result.get("search_text", "")
        found = db.find_task_by_text(user_row["id"], search)
        if found:
            db.complete_task(found["id"], user_id=user_row["id"])
            reply_text = f"✅ Отмечено: {found['text']}"
        else:
            reply_text = "Не нашла такую задачу. Покажи список (/tasks) и уточни."

    elif msg_type == "done_multiple":
        searches = ai_result.get("search_texts", [])
        found_tasks = db.find_tasks_by_texts(user_row["id"], searches)
        if found_tasks:
            done_names = []
            for t in found_tasks:
                db.complete_task(t["id"], user_id=user_row["id"])
                done_names.append(t["text"])
            reply_text = f"✅ Отмечено {len(done_names)} задач:\n" + "\n".join(
                f"  ✅ {name}" for name in done_names
            )
            not_found = [s for s in searches if not db.find_task_by_text(user_row["id"], s) and s.lower().strip() not in [n.lower() for n in done_names]]
            if not_found:
                reply_text += "\n\n⚠️ Не нашла: " + ", ".join(not_found)
        else:
            reply_text = "Не нашла эти задачи. Покажи список (/tasks) и уточни."

    db.save_message(user_row["id"], "assistant", reply_text)
    await _reply(update, reply_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Голосовое сообщение → Whisper → обработка как текст."""
    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)
    voice_bytes = bytes(await tg_file.download_as_bytearray())

    text = ai_module.transcribe_voice(voice_bytes)
    if not text:
        await _reply(update, "🎤 Не удалось распознать голосовое. Попробуй ещё раз или напиши текстом.")
        return

    await _reply(update, f"🎤 Распознано: «{text}»")
    await _process_user_text(update, text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = (update.message.text or "").strip()
    if not user_text:
        return
    await _process_user_text(update, user_text)


# ── Запуск ───────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Задайте токен: переменная TELEGRAM_BOT_TOKEN.")
        return

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

    app = builder.build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, (TimedOut, NetworkError)):
            logger.warning("Сетевая ошибка Telegram: %s", context.error)
        else:
            logger.exception("Ошибка: %s", context.error)

    app.add_error_handler(on_error)
    logger.info("Бот запущен (MVP).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
