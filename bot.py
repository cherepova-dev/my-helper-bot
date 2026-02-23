# -*- coding: utf-8 -*-
"""
Телеграм-бот: личный помощник.
Приветствие при /start, приём текста и голоса с ответом "Сообщение принято. Текст" и повтором.
"""

import logging
import tempfile
import os

from telegram import Update
from telegram.error import TimedOut
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    sr = None
    SR_AVAILABLE = False

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except Exception:
    PYDUB_AVAILABLE = False
    AudioSegment = None

# Токен бота — задайте в переменной окружения TELEGRAM_BOT_TOKEN или здесь
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8785603117:AAGWVVEWSVbIc_ZZDhd26OprknT0e6Ldh1Q")

# Прокси (если Telegram заблокирован). Примеры:
#   http://127.0.0.1:7890
#   socks5://127.0.0.1:1080
#   http://user:password@proxy.example.com:8080
# Или задайте переменную окружения HTTPS_PROXY / HTTP_PROXY — бот их подхватит.
PROXY_URL = os.environ.get("PROXY_URL", "").strip() or os.environ.get("HTTPS_PROXY", "").strip() or os.environ.get("HTTP_PROXY", "").strip()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

GREETING = (
    "Привет! Я твой личный помощник. Давай сделаем этот день лучшим."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start — приветствие."""
    await _reply_with_retry(update, GREETING)


async def _reply_with_retry(update: Update, text: str, max_retries: int = 2) -> None:
    """Отправка ответа с повторной попыткой при таймауте."""
    for attempt in range(max_retries + 1):
        try:
            await update.message.reply_text(text)
            return
        except TimedOut:
            if attempt < max_retries:
                logger.info("Retry %s/%s после таймаута...", attempt + 1, max_retries)
            else:
                logger.warning("Таймаут при отправке. Проверьте интернет или VPN.")


def transcribe_voice(voice_bytes: bytes) -> str | None:
    """
    Преобразует голосовое сообщение (ogg) в текст.
    Возвращает распознанный текст или None при ошибке.
    Для конвертации ogg нужен ffmpeg (и pydub).
    """
    if not (SR_AVAILABLE and PYDUB_AVAILABLE and AudioSegment):
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
            ogg_file.write(voice_bytes)
            ogg_path = ogg_file.name
        try:
            audio = AudioSegment.from_ogg(ogg_path)
            wav_path = ogg_path.replace(".ogg", ".wav")
            audio.export(wav_path, format="wav")
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                data = recognizer.record(source)
            text = recognizer.recognize_google(data, language="ru-RU")
            return text
        finally:
            for p in (ogg_path, ogg_path.replace(".ogg", ".wav")):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
    except Exception as e:
        logger.warning("Ошибка распознавания голоса: %s", e)
        return None


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка голосовых сообщений."""
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    voice_bytes = await file.download_as_bytearray()
    text = transcribe_voice(bytes(voice_bytes))
    if text:
        reply = f"Сообщение принято. Текст: {text}"
    else:
        reply = "Сообщение принято. Голосовое сообщение (текст не удалось распознать)."
    await _reply_with_retry(update, reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений."""
    text = update.message.text or ""
    reply = f"Сообщение принято. Текст: {text}"
    await _reply_with_retry(update, reply)


def main() -> None:
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Задайте токен бота: переменная окружения TELEGRAM_BOT_TOKEN или в bot.py (BOT_TOKEN).")
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
        logger.info("Используется прокси: %s", PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, TimedOut):
            logger.warning("Таймаут соединения с Telegram. Попробуйте ещё раз.")
        else:
            logger.exception("Ошибка: %s", context.error)

    app.add_error_handler(on_error)
    logger.info("Бот запущен.")
    if not PROXY_URL:
        logger.info("Таймауты? Задайте прокси: PROXY_URL или HTTPS_PROXY (см. README).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
