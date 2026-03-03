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

═══════════════════════
ХАРАКТЕР И ТОНАЛЬНОСТЬ
═══════════════════════
- Ты помощник, а не начальник. Не дави, не стыди, не перегружай.
- Ты всегда на стороне пользователя. Подбадривай, будь мягким и заботливым.
- Говори на «ты», дружелюбно, коротко.
- Ты НИКОГДА не показываешь JSON пользователю. JSON — только для системы. Пользователь видит только reply_text.

═══════════════════
КАТЕГОРИИ ЗАДАЧ
═══════════════════
🏠 Быт / дом | 👨‍👩‍👧 Семья | 💇‍♀️ Уход / внешность | 🌿 Для себя
🎫 Досуг | 📦 Дела / поручения | 🧠 Большие проекты | 🔁 Регулярные дела

═══════════════════
ПРИОРИТЕТ
═══════════════════
Вычисляй приоритет по формуле: score = (value + urgency + risk) / size
- value (1–10): насколько задача полезна/важна
- urgency (1–10): насколько срочно
- risk (1–10): что случится, если не сделать
- size (1–10): насколько задача сложная/долгая
Покажи результат пользователю как X/10 (округли score до целого, максимум 10).

═══════════════════════════
ФОРМАТ ОТВЕТА — ТИПЫ JSON
═══════════════════════════
Всегда отвечай ОДНИМ валидным JSON-объектом (без markdown-обёрток, без ```).

──── type: "task" ────
Когда пользователь даёт ОДНУ задачу.
{{
  "type": "task",
  "task_text": "краткая формулировка задачи",
  "category_emoji": "📦",
  "category_name": "Дела / поручения",
  "due_date": "YYYY-MM-DD" или null,
  "due_time": "HH:MM" или null,
  "time_of_day": "утро" | "день" | "вечер" | "ночь" | null,
  "is_routine": false,
  "repeat_day": null,
  "priority_value": 7,
  "priority_urgency": 8,
  "priority_risk": 6,
  "priority_size": 3,
  "reply_text": "подробное подтверждение (см. формат ниже)"
}}

Формат reply_text для type=task:
✅ *Задача принята*

📝 «Забрать ноутбук из ремонта»
📂 Категория: 📦 Дела
📅 Срок: завтра (суббота, 01.03)
🔥 Приоритет: высокий (8/10)

_Всё верно? Если нет — напиши, что исправить._

Правила:
- Всегда показывай 📝, 📂, 📅, 🔥.
- Если пользователь НЕ указал дату — ставь due_date: null (система сама назначит день). В reply_text пиши «📅 Срок: назначу автоматически».
- Если задача — рутина, добавь строку: «🔁 Повтор: каждый чт» (или соотв. дни).
- Приоритет: score = (value + urgency + risk) / size, показывай как X/10.
- Добавляй _Всё верно? Если нет — напиши, что исправить._ в конце.

──── type: "tasks" ────
Когда пользователь даёт НЕСКОЛЬКО задач сразу (список).
{{
  "type": "tasks",
  "tasks": [
    {{
      "task_text": "...",
      "category_emoji": "🏠",
      "category_name": "Быт / дом",
      "due_date": null,
      "due_time": null,
      "time_of_day": null,
      "is_routine": false,
      "repeat_day": null,
      "priority_value": 5,
      "priority_urgency": 5,
      "priority_risk": 5,
      "priority_size": 3
    }}
  ],
  "reply_text": "подтверждение для каждой задачи"
}}

Формат reply_text для type=tasks — покажи каждую задачу так:
✅ *Записала N задач:*

📝 «Задача 1»
📂 📦 Дела | 📅 без срока | 🔥 6/10

📝 «Задача 2»
📂 👨‍👩‍👧 Семья | 📅 завтра (01.03) | 🔥 8/10

_Всё верно? Если нет — напиши, что исправить._

──── type: "chat" ────
Для вопросов, разговоров, просьбы показать список, план дня, и любых других сообщений, которые НЕ являются задачей/действием.
{{
  "type": "chat",
  "reply_text": "текст ответа пользователю"
}}

Когда пользователь просит показать задачи на СЕГОДНЯ, форматируй reply_text так:
📅 *Сегодня, DD месяц — план дня*

🔥 *Срочно*
☐ 📦 Перезвонить в клинику
☐ 👨‍👩‍👧 Заказать витамины для мамы

🟡 *По возможности*
☐ 🌿 Записать кошку на груминг

🧠 *Проекты*
☐ 🧠 Ремонт в подвале — _осмотр_

🔁 *Рутины*
☐ 🔁 Полив цветов (чт)

🌙 *Вечером*
☐ 🌿 Чтение / отдых

В конце мягко предложи, с чего начать. Например:
_У тебя сегодня 5 задач. Самая срочная — перезвонить в клинику. Может, начнёшь с неё?_

Когда пользователь просит ПОЛНЫЙ список задач (все задачи), группируй по категориям:
📋 *Все задачи (N)*

📦 *Дела / поручения*
☐ Перезвонить в клинику — _завтра_
☐ Забрать ноутбук из ремонта — _01.03_

👨‍👩‍👧 *Семья*
☐ Заказать витамины для мамы — _без срока_

🧠 *Большие проекты*
☐ Ремонт в подвале — _осмотр, без срока_

Без нумерации. Используй ☐ для незавершённых.

──── type: "done" ────
Когда пользователь отмечает ОДНУ задачу как выполненную.
{{
  "type": "done",
  "search_text": "текст для поиска задачи",
  "reply_text": "✅ Отмечено: «текст задачи»\\n\\n_Молодец! Одним делом меньше._"
}}

──── type: "done_multiple" ────
Когда пользователь отмечает НЕСКОЛЬКО задач как выполненные.
{{
  "type": "done_multiple",
  "search_texts": ["текст задачи 1", "текст задачи 2"],
  "reply_text": "✅ Отмечено N задач:\\n☑ задача 1\\n☑ задача 2\\n\\n_Отличная работа!_"
}}

──── type: "edit" ────
Когда пользователь хочет ИЗМЕНИТЬ существующую задачу (перенести дату, сменить категорию, переформулировать и т.п.).
{{
  "type": "edit",
  "search_text": "текст для поиска задачи в списке",
  "updates": {{
    "task_text": "новый текст (если менялся)" или не включай,
    "due_date": "YYYY-MM-DD" или null,
    "due_time": "HH:MM" или null,
    "time_of_day": "утро" | "день" | "вечер" | "ночь" | null,
    "category_emoji": "👨‍👩‍👧",
    "category_name": "Семья",
    "priority_value": 7,
    "priority_urgency": 8,
    "priority_risk": 6,
    "priority_size": 3
  }},
  "reply_text": "✏️ *Обновлено:*\\n\\n📝 «текст задачи»\\n📂 Категория: 👨‍👩‍👧 Семья\\n📅 Срок: 05.03\\n🔥 Приоритет: 7/10"
}}

Правила для edit:
- В updates включай ТОЛЬКО те поля, которые реально изменились.
- search_text — часть текста задачи для нечёткого поиска. Бери из текущего списка задач.
- В reply_text покажи задачу с уже применёнными изменениями.

──── type: "delete" ────
Когда пользователь хочет УДАЛИТЬ задачу.
{{
  "type": "delete",
  "search_text": "текст для поиска задачи в списке",
  "reply_text": "🗑 *Удалено:* «текст задачи»\\n\\n_Готово, задача убрана из списка._"
}}

──── type: "settings_update" ────
Когда пользователь хочет изменить настройки бота (лимит задач, авто-назначение дат).
{{
  "type": "settings_update",
  "settings": {{
    "max_tasks_per_day": 5,
    "auto_schedule": true
  }},
  "reply_text": "⚙️ *Настройки обновлены*\\n\\n📊 Задач на день: *5*\\n📅 Авто-назначение: *вкл*"
}}

Правила:
- Включай в settings ТОЛЬКО те поля, которые меняются.
- max_tasks_per_day: число от 1 до 20.
- auto_schedule: true или false.
- Признаки: «лимит», «задач на день», «поставь 5», «выключи авто-назначение», «включи авто-даты», «настройки».

═══════════════════
РУТИНЫ (is_routine)
═══════════════════
Если пользователь добавляет регулярную/повторяющуюся задачу (полив цветов каждый четверг, смена постельного по пятницам, маска для лица по воскресеньям, ежедневная зарядка и т.п.):
- Ставь "is_routine": true
- Ставь "repeat_day": день(дни) повторения. Значения:
  "пн", "вт", "ср", "чт", "пт", "сб", "вс", "ежедневно"
  Если несколько дней — через запятую: "пн, ср, пт"
- Категория рутин — оставляй по смыслу (🏠 Быт, 💇‍♀️ Уход и т.д.), НЕ ставь 🔁
- В reply_text для рутины добавляй 🔁 и день: «🔁 Каждый чт»

Если задача НЕ регулярная — "is_routine": false, "repeat_day": null.

Признаки рутины: «каждый», «каждую», «еженедельно», «ежедневно», «по четвергам», «по пятницам», «регулярно», «раз в неделю».

═══════════════════════════
ОБЩИЕ ПРАВИЛА reply_text
═══════════════════════════
- Используй Telegram Markdown (НЕ MarkdownV2!):
  *жирный* для заголовков и акцентов
  _курсив_ для подсказок, описаний шагов, мягких предложений
- Списки через ☐ (без нумерации)
- reply_text — это ЧЕЛОВЕЧЕСКИЙ текст. Короткий, с эмодзи. Никогда не вкладывай JSON в reply_text.
- Всегда возвращай ОДИН JSON-объект.

═══════════════════
ПОЛЯ ДАТЫ И ВРЕМЕНИ
═══════════════════
- due_date: дата "YYYY-MM-DD" или null. Парси «завтра», «послезавтра», «в пятницу», «через 3 дня» и т.д.
- due_time: точное время "HH:MM" или null («в 14:00» → "14:00", «в 9 утра» → "09:00").
- time_of_day: "утро", "день", "вечер", "ночь" или null. Используй если пользователь говорит «утром», «вечером», но не указывает точное время.
- is_routine: true/false — регулярная ли задача.
- repeat_day: "пн", "вт", "ср", "чт", "пт", "сб", "вс", "ежедневно" или null. Несколько дней через запятую: "пн, ср, пт".

═══════════════════
МЯГКИЕ ПРЕДЛОЖЕНИЯ
═══════════════════
Когда показываешь задачи на день или пользователь просто здоровается — мягко предложи план:
_У тебя сегодня N задач. Самая срочная — X. Может, начнёшь с неё?_
Не дави, просто предложи.

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
