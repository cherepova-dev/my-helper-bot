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

# Маркеры «отметь / выполни ...» — выполнение задачи.
DONE_PREFIXES = (
    "отметь", "отметить", "выполни", "выполнить", "сделай", "сделать",
    "заверши", "завершить", "отметь задачу", "выполни задачу",
    "отметить задачу", "выполнить задачу", "сделано", "готово",
)
DONE_PREFIXES_LOWER = [p.lower() for p in sorted(DONE_PREFIXES, key=len, reverse=True)]

# Фразы-«мусор» между действием и названием задачи (убираем с начала остатка).
# Длинные — первыми, чтобы «выполненную задачу» убрать до «выполненную».
DONE_FILLER_PHRASES = (
    "как выполненную задачу", "как выполненной задачу",
    "выполненную задачу", "выполненной задачу",
    "как выполненную", "как выполненной",
    "выполненную", "выполненной",
    "задачу номер", "задачу #", "номер",
)
DONE_FILLER_LOWER = [p.lower() for p in sorted(DONE_FILLER_PHRASES, key=len, reverse=True)]


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
    """Извлекает время из текста. Возвращает HH:MM или None.
    Поддерживает: «в 12:00», «к 12:00», «к 12 завтра», «в 10 утра», «к 10 утра», «12:00».
    """
    lower = text.lower()
    # «в 12:00» или «к 12:00», «в 10.30», «к 10,00» (запятая как разделитель)
    m = re.search(r"[вк]\s+(\d{1,2})[\s:.,](\d{2})", lower)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m = re.search(r"[вк]\s+(\d{1,2})\s+(?:утра|часов|часа|час|дня|вечера|ночи)", lower)
    if m:
        hour = int(m.group(1))
        if "вечера" in lower and hour < 12:
            hour += 12
        if "ночи" in lower and hour < 12:
            hour += 12 if hour != 12 else 0
        return f"{hour:02d}:00"
    # «к 12 завтра», «в 14 сегодня» — только час, минуты 00
    m = re.search(r"[вк]\s+(\d{1,2})\s+(?:завтра|сегодня|послезавтра)\b", lower)
    if m:
        hour = int(m.group(1))
        if 0 <= hour <= 23:
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


def starts_with_done_marker(text: str) -> bool:
    """True, если текст начинается с маркера выполнения задачи (отметь, выполни и т.д.)."""
    lower = text.strip().lower()
    return any(lower.startswith(p) for p in DONE_PREFIXES_LOWER)


def extract_done_target(text: str) -> tuple[int | None, str]:
    """
    После маркера «отметь/выполни» извлекает номер задачи (1-based) или текст для поиска.
    Возвращает (number, rest_text): если после маркера число — (N, ""), иначе (None, rest).
    Убирает filler-фразы: «выполненную задачу», «выполненной», «задачу номер» и т.п.
    """
    t = text.strip()
    lower = t.lower()
    rest = ""
    for prefix in DONE_PREFIXES_LOWER:
        if lower.startswith(prefix):
            rest = t[len(prefix):].strip()
            rest = re.sub(r"^(?:задач[уа]\.?\s*)?", "", rest, flags=re.IGNORECASE).strip()
            # Голос может дать «отметь задачу. Отогнать машину» — убираем ведущие точки/запятые
            rest = re.sub(r"^[.,;:\s]+", "", rest).strip()
            rest = re.sub(r"\s+", " ", rest).strip()
            break
    if not rest:
        return None, ""
    # Убираем filler-фразы с начала (отметь выполненную задачу X → X)
    while True:
        rest = re.sub(r"^[.,;:\s]+", "", rest).strip()
        rest = re.sub(r"\s+", " ", rest).strip()
        if not rest:
            return None, ""
        rest_lower = rest.lower()
        stripped = False
        for filler in DONE_FILLER_LOWER:
            if rest_lower.startswith(filler):
                rest = rest[len(filler):].strip()
                stripped = True
                break
        if not stripped:
            break
    # Один номер?
    m = re.match(r"^(\d+)\s*$", rest)
    if m:
        return int(m.group(1)), ""
    # Несколько слов — первое число как номер?
    m = re.match(r"^(\d+)\s*[,.]?\s*", rest)
    if m:
        return int(m.group(1)), rest[m.end():].strip() or ""
    return None, rest


def clean_task_text_from_datetime(text: str) -> str:
    """
    Убирает из текста задачи фразы с датой и временем, чтобы в названии
    осталось только суть (дата и время сохраняются в отдельных полях).
    """
    if not text or not text.strip():
        return text
    s = text.strip()
    lower = s.lower()

    # Время: "в 12:00", "к 12:00", "в 10.30", "к 10,00"
    s = re.sub(r"\s*[вк]\s+\d{1,2}[\s:.,]\d{2}\s*", " ", s, flags=re.IGNORECASE)
    # Время без минут: "к 12 завтра", "в 14 сегодня"
    s = re.sub(r"\s*[вк]\s+\d{1,2}\s+(?=завтра|сегодня|послезавтра)", " ", s, flags=re.IGNORECASE)
    # Время: "в 10 утра", "к 10 утра", "в 7 вечера"
    s = re.sub(
        r"\s*[вк]\s+\d{1,2}\s+(?:утра|часов|часа|час|дня|вечера|ночи)\s*",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    # Отдельно время без "в": "12:00" в конце или с запятой
    s = re.sub(r",?\s*\d{1,2}:\d{2}\s*$", "", s)
    s = re.sub(r"^[\s,]*\d{1,2}:\d{2}\s*", "", s)

    # Относительные даты
    for phrase in ("послезавтра", "завтра", "сегодня"):
        s = re.sub(rf"\s*{re.escape(phrase)}\s*", " ", s, flags=re.IGNORECASE)
    # "через N дней/дня/дней"
    s = re.sub(r"\s*через\s+\d+\s+(?:день|дня|дней)\s*", " ", s, flags=re.IGNORECASE)
    # Дни недели: "в пятницу", "в среду", "в понедельник"
    weekdays = (
        "понедельник", "вторник", "среду", "среда", "четверг",
        "пятницу", "пятница", "субботу", "суббота", "воскресенье",
    )
    for w in weekdays:
        s = re.sub(rf"\s*в\s+{re.escape(w)}\s*", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"\s+{re.escape(w)}\s*$", "", s, flags=re.IGNORECASE)
        s = re.sub(rf"^\s*{re.escape(w)}\s+", "", s, flags=re.IGNORECASE)
    # Дата ДД.ММ или ДД/ММ
    s = re.sub(r"\s*\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\s*", " ", s)

    s = re.sub(r"\s+", " ", s).strip().strip(".,;:")
    return s if s else text.strip()
