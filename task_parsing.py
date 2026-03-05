# -*- coding: utf-8 -*-
"""
Парсинг текста задач для бота v2 (без LLM).
Используется в bot_v2 и покрыт юнит-тестами.
"""
import re
from datetime import datetime, timedelta

# Маркеры «добавь / создай / запиши ...» — всё после них считаем текстом задачи.
ADD_PREFIXES = (
    "запиши", "добавь", "добавить", "создай", "создать",
    "поставь задачу", "запланируй", "закажи", "нужно", "надо",
)
ADD_PREFIXES_LOWER = [p.lower() for p in ADD_PREFIXES]


def parse_due_date(text: str, today: datetime | None = None) -> str | None:
    """
    Извлекает дату из русского текста. Возвращает YYYY-MM-DD или None.
    today — опциональная опорная дата (для тестов); иначе datetime.now().
    """
    lower = text.lower()
    if today is None:
        today = datetime.now()

    if "сегодня" in lower:
        return today.strftime("%Y-%m-%d")
    if "послезавтра" in lower:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    if "завтра" in lower:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    m = re.search(r"через\s+(\d+)\s+(?:день|дня|дней)", lower)
    if m:
        return (today + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")

    weekdays = {
        "понедельник": 0, "вторник": 1, "среду": 2, "среда": 2,
        "четверг": 3, "пятницу": 4, "пятница": 4,
        "субботу": 5, "суббота": 5, "воскресенье": 6,
    }
    for name, wd in weekdays.items():
        if name in lower:
            days_ahead = wd - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", lower)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_due_time(text: str) -> str | None:
    """Извлекает время из текста. Возвращает HH:MM или None."""
    lower = text.lower()
    m = re.search(r"в\s+(\d{1,2})[\s:.](\d{2})", lower)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m = re.search(r"в\s+(\d{1,2})\s+(?:утра|часов|часа|час|дня|вечера|ночи)", lower)
    if m:
        hour = int(m.group(1))
        if "вечера" in lower and hour < 12:
            hour += 12
        if "ночи" in lower and hour < 12:
            hour += 12 if hour != 12 else 0
        return f"{hour:02d}:00"
    m = re.search(r"(\d{1,2}):(\d{2})", lower)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    return None


def extract_task_text(user_text: str) -> str:
    """Если сообщение начинается с маркера «добавь/создай/запиши» — убирает его и возвращает остаток."""
    text = user_text.strip()
    lower = text.lower()
    for prefix in sorted(ADD_PREFIXES_LOWER, key=len, reverse=True):
        if lower.startswith(prefix):
            text = text[len(prefix):].strip()
            text = re.sub(r"^(?:новую\s+)?задач[уа]\.?\s*", "", text, flags=re.IGNORECASE).strip()
            break
    return text if text else user_text.strip()


def starts_with_add_marker(text: str) -> bool:
    """True, если текст начинается с одного из маркеров добавления задачи."""
    lower = text.strip().lower()
    return any(lower.startswith(p) for p in ADD_PREFIXES_LOWER)
