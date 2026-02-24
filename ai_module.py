# -*- coding: utf-8 -*-
"""AI-модуль — работа с LLM API (Groq / DeepSeek / OpenAI-совместимый)."""

import json
import logging
import os
import tempfile
from datetime import datetime

from openai import OpenAI

logger = logging.getLogger(__name__)

AI_API_KEY = (
    os.environ.get("GROQ_API_KEY", "")
    or os.environ.get("DEEPSEEK_API_KEY", "")
    or os.environ.get("OPENAI_API_KEY", "")
)
AI_BASE_URL = os.environ.get(
    "AI_BASE_URL",
    "https://api.groq.com/openai/v1" if os.environ.get("GROQ_API_KEY") else "https://api.deepseek.com",
)
AI_MODEL = os.environ.get(
    "AI_MODEL",
    "llama-3.3-70b-versatile" if os.environ.get("GROQ_API_KEY") else "deepseek-chat",
)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-large-v3-turbo")

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not AI_API_KEY:
            raise RuntimeError(
                "API-ключ не задан. Задайте GROQ_API_KEY или DEEPSEEK_API_KEY."
            )
        _client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
    return _client


def transcribe_voice(voice_bytes: bytes) -> str | None:
    """Распознаёт голосовое сообщение (OGG) через Whisper API (Groq)."""
    tmp_path = None
    try:
        client = _get_client()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(voice_bytes)
            tmp_path = f.name

        with open(tmp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=audio_file,
                language="ru",
            )
        text = transcription.text.strip()
        if text:
            logger.info("Голос распознан: %s", text[:80])
            return text
        return None
    except Exception as e:
        logger.exception("Ошибка распознавания голоса: %s", e)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


SYSTEM_PROMPT = """\
Ты — личный AI-ассистент в Telegram. Твоя задача — помогать пользователю с личными задачами, бытом, семьёй, заботой о себе и планированием.

Правила:
- Ты помощник, а не начальник. Не дави, не стыди, не перегружай.
- Ты всегда на стороне пользователя.
- Ты НИКОГДА не показываешь JSON пользователю. JSON — только для системы. Пользователь видит reply_text.

Категории задач:
🏠 Быт / дом | 👨‍👩‍👧 Семья | 💇‍♀️ Уход / внешность | 🌿 Для себя
🎫 Досуг | 📦 Дела / поручения | 🧠 Большие проекты | 🔁 Регулярные дела

ФОРМАТ ОТВЕТА — всегда ОДИН валидный JSON-объект (без markdown, без ```):

1) Если пользователь пишет ОДНУ задачу:
{"type": "task", "task_text": "краткая формулировка", "category_emoji": "🏠", "category_name": "Быт / дом", "due_date": null, "due_time": null, "time_of_day": null, "priority_value": 5, "priority_urgency": 5, "priority_risk": 5, "priority_size": 3, "reply_text": "Записала: ..."}

2) Если пользователь пишет НЕСКОЛЬКО задач (список):
{"type": "tasks", "tasks": [{"task_text": "...", "category_emoji": "🏠", "category_name": "Быт / дом", "due_date": null, "due_time": null, "time_of_day": null, "priority_value": 5, "priority_urgency": 5, "priority_risk": 5, "priority_size": 3}, ...], "reply_text": "Записала 5 задач: ..."}

Поля даты и времени:
- due_date: дата в формате "YYYY-MM-DD" или null. Парси «завтра», «послезавтра», «в пятницу», «через 3 дня» и т.д.
- due_time: точное время "HH:MM" или null (например: «в 14:00» → "14:00", «в 9 утра» → "09:00").
- time_of_day: время суток — "утро", "день", "вечер", "ночь" или null. Используй если пользователь говорит «утром», «вечером», «днём», но не указывает точное время.

3) Если это НЕ задача (вопрос, просьба, разговор, показать список):
{"type": "chat", "reply_text": "Твой ответ пользователю (обычный текст, НЕ JSON)"}

4) Если пользователь хочет отметить ОДНУ задачу как сделанную:
{"type": "done", "search_text": "текст для поиска задачи", "reply_text": "✅ Отмечено: ..."}

5) Если пользователь хочет отметить НЕСКОЛЬКО задач как сделанные:
{"type": "done_multiple", "search_texts": ["текст задачи 1", "текст задачи 2"], "reply_text": "✅ Отмечено 2 задачи: ..."}

ВАЖНО:
- reply_text — это ЧЕЛОВЕЧЕСКИЙ текст, который увидит пользователь. Короткий, с эмодзи.
- Для type=chat: reply_text — обычный текст, список задач покажи красиво (нумерация, эмодзи).
- НИКОГДА не вкладывай JSON в reply_text. Пользователь не должен видеть JSON.
- Всегда возвращай ОДИН JSON-объект, даже если задач несколько (используй type=tasks).

Сегодня: {today}
"""


def _build_messages(
    user_text: str,
    active_tasks: list[dict],
    recent_messages: list[dict],
    today: str | None = None,
) -> list[dict]:
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")

    system = SYSTEM_PROMPT.replace("{today}", today)

    if active_tasks:
        task_lines = []
        for t in active_tasks[:30]:
            line = f"  {t.get('category_emoji','')} {t['text']}"
            time_parts = []
            if t.get("due_date"):
                time_parts.append(t["due_date"])
            if t.get("due_time"):
                time_parts.append(t["due_time"])
            elif t.get("time_of_day"):
                time_parts.append(t["time_of_day"])
            if time_parts:
                line += f" ({', '.join(time_parts)})"
            task_lines.append(line)
        system += "\n\nТекущие задачи пользователя:\n" + "\n".join(task_lines)

    messages = [{"role": "system", "content": system}]

    for m in recent_messages[-10:]:
        messages.append({"role": m["role"], "content": m["text"]})

    messages.append({"role": "user", "content": user_text})
    return messages


def process_message(
    user_text: str,
    active_tasks: list[dict],
    recent_messages: list[dict],
) -> dict:
    """
    Отправляет сообщение в LLM, возвращает распарсенный JSON-ответ.
    Поддерживает: одиночный объект, type=tasks (массив), несколько JSON подряд.
    """
    raw = ""
    try:
        client = _get_client()
        messages = _build_messages(user_text, active_tasks, recent_messages)
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content.strip()
        raw = _clean_json(raw)
        result = _parse_ai_response(raw)
        return result
    except Exception as e:
        logger.exception("Ошибка AI: %s", e)
        if raw:
            return {"type": "chat", "reply_text": _extract_text_from_raw(raw)}
        return {
            "type": "chat",
            "reply_text": "Сейчас у меня проблемы с подключением. Попробуй ещё раз через минуту.",
        }


def _parse_ai_response(raw: str) -> dict:
    """Парсит ответ AI: одиночный JSON, массив, или несколько JSON подряд."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return _merge_task_list(parsed)
        if isinstance(parsed, dict):
            if "type" not in parsed:
                parsed["type"] = "chat"
            if "reply_text" not in parsed:
                parsed["reply_text"] = "Записано."
            return parsed
    except json.JSONDecodeError:
        pass

    objects = _extract_json_objects(raw)
    if objects:
        tasks = [o for o in objects if o.get("type") == "task"]
        if tasks:
            return _merge_task_list(tasks)
        return objects[0]

    return {"type": "chat", "reply_text": _extract_text_from_raw(raw)}


def _merge_task_list(items: list[dict]) -> dict:
    """Объединяет список задач в один результат type=tasks."""
    tasks = []
    for item in items:
        tasks.append({
            "task_text": item.get("task_text", ""),
            "category_emoji": item.get("category_emoji", ""),
            "category_name": item.get("category_name", ""),
            "due_date": item.get("due_date"),
            "due_time": item.get("due_time"),
            "priority_value": item.get("priority_value", 5),
            "priority_urgency": item.get("priority_urgency", 5),
            "priority_risk": item.get("priority_risk", 5),
            "priority_size": item.get("priority_size", 5),
        })
    names = [t["task_text"] for t in tasks if t["task_text"]]
    reply = f"Записала {len(tasks)} задач:\n" + "\n".join(
        f"  {t.get('category_emoji', '📝')} {t['task_text']}" for t in tasks
    )
    return {"type": "tasks", "tasks": tasks, "reply_text": reply}


def _extract_json_objects(text: str) -> list[dict]:
    """Извлекает отдельные JSON-объекты из строки с несколькими {...}{...}."""
    results = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i + 1])
                    results.append(obj)
                except json.JSONDecodeError:
                    pass
                start = None
    return results


def _extract_text_from_raw(raw: str) -> str:
    """Пытается вытащить reply_text из сырого ответа, если JSON невалидный."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed.get("reply_text", raw)
    except (json.JSONDecodeError, TypeError):
        pass
    for obj in _extract_json_objects(raw):
        if "reply_text" in obj:
            return obj["reply_text"]
    return raw


def _clean_json(text: str) -> str:
    """Убирает markdown-обёртку ```json ... ```, если AI её добавил."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
