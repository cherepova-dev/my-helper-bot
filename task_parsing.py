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
    "отметить задачу", "выполнить задачу", "отметить задачу номер", "отметить выполнение",
    "сделано", "готово",
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
    "выполнение ",
)
DONE_FILLER_LOWER = [p.lower() for p in sorted(DONE_FILLER_PHRASES, key=len, reverse=True)]

# Маркеры «изменить задачу» / «исправить» — изменение текста задачи
EDIT_PREFIXES = (
    "изменить задачу", "исправить задачу", "переименовать задачу",
    "изменить", "исправить", "переименовать",
)
EDIT_PREFIXES_LOWER = [p.lower() for p in sorted(EDIT_PREFIXES, key=len, reverse=True)]

# Маркеры «перенести задачу N на дату» — перенос по дате/времени
RESCHEDULE_PREFIXES = (
    "перенести задачу", "перенеси задачу", "переложи задачу",
    "перенести", "перенеси", "переложи",
)
RESCHEDULE_PREFIXES_LOWER = [p.lower() for p in sorted(RESCHEDULE_PREFIXES, key=len, reverse=True)]


def starts_with_edit_marker(text: str) -> bool:
    """True, если текст начинается с маркера изменения задачи."""
    lower = (text or "").strip().lower()
    return any(lower.startswith(p) for p in EDIT_PREFIXES_LOWER)


def extract_edit_target(text: str) -> tuple[int | None, str | None, str]:
    """
    После маркера «изменить задачу» извлекает идентификатор (номер или текст для поиска) и новый текст.
    Формат: «изменить задачу 2 на Купить хлеб» или «исправить купить молоко на купить хлеб».
    Возвращает (num_or_none, search_text_or_none, new_text). new_text не пустой только если есть « на ».
    """
    t = (text or "").strip()
    lower = t.lower()
    rest = ""
    for prefix in EDIT_PREFIXES_LOWER:
        if lower.startswith(prefix):
            rest = t[len(prefix):].strip()
            rest = re.sub(r"^(?:задач[уа]\.?\s*)?", "", rest, flags=re.IGNORECASE).strip()
            rest = re.sub(r"^[.,;:\s]+", "", rest).strip()
            break
    if not rest:
        return None, None, ""
    if " на " not in rest and " на " not in rest.lower():
        return None, None, ""
    parts = re.split(r"\s+на\s+", rest, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None, None, ""
    identifier, new_text = parts[0].strip(), parts[1].strip()
    if not identifier or not new_text:
        return None, None, ""
    m = re.match(r"^(\d+)\s*$", identifier)
    if m:
        return int(m.group(1)), None, new_text
    return None, identifier, new_text


def starts_with_reschedule_marker(text: str) -> bool:
    """True, если текст начинается с маркера переноса задачи."""
    lower = (text or "").strip().lower()
    return any(lower.startswith(p) for p in RESCHEDULE_PREFIXES_LOWER)


def extract_reschedule_target(text: str, today: datetime | None = None) -> tuple[int | None, str | None, str | None, str | None]:
    """
    После маркера «перенеси задачу» извлекает номер/название задачи и новую дату/время.
    Возвращает (num_or_none, search_text_or_none, due_date, due_time).
    """
    t = (text or "").strip()
    lower = t.lower()
    rest = ""
    for prefix in RESCHEDULE_PREFIXES_LOWER:
        if lower.startswith(prefix):
            rest = t[len(prefix):].strip()
            rest = re.sub(r"^(?:задач[уа]\.?\s*)?(?:номер\s*)?", "", rest, flags=re.IGNORECASE).strip()
            rest = re.sub(r"^[.,;:\s]+", "", rest).strip()
            break
    if not rest or " на " not in rest.replace("\n", " "):
        return None, None, None, None
    parts = re.split(r"\s+на\s+", rest, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None, None, None, None
    identifier, date_time_part = parts[0].strip(), parts[1].strip()
    if not identifier or not date_time_part:
        return None, None, None, None
    due_date = parse_due_date(date_time_part, today=today)
    due_time = parse_due_time(date_time_part)
    m = re.match(r"^(\d+)\s*$", identifier)
    if m:
        return int(m.group(1)), None, due_date, due_time
    return None, identifier, due_date, due_time


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


# Нормализация формулировки задачи к повелительному наклонению / инфинитиву для отображения в списке
# («Заправлять постели» -> «Заправить постель»)
VERB_TO_DISPLAY = {
    "заправлять": "заправить", "заправляю": "заправить", "заправляй": "заправить",
    "поливать": "полить", "поливаю": "полить", "поливай": "полить",
    "мыть": "помыть", "мой": "помыть", "помыть": "помыть",
    "убирать": "убрать", "убираю": "убрать", "убирай": "убрать",
    "готовить": "приготовить", "готовь": "приготовить", "приготовить": "приготовить",
    "звонить": "позвонить", "звоню": "позвонить", "позвонить": "позвонить",
    "писать": "написать", "пиши": "написать", "написать": "написать",
    "делать": "сделать", "делай": "сделать", "сделать": "сделать",
    "купить": "купить", "покупать": "купить", "покупай": "купить",
    "проветривать": "проветрить", "проветривай": "проветрить", "проветрить": "проветрить",
}
PLURAL_TO_SINGULAR = {
    "постели": "постель", "постелей": "постель",
    "цветы": "цветы", "цветов": "цветы",  # оставляем как есть или "цветы"
    "посуду": "посуда", "посуда": "посуда",
}


def normalize_task_display(text: str) -> str:
    """
    Приводит формулировку задачи к виду для отображения в списке:
    повелительное наклонение / инфинитив, единственное число где уместно.
    «Заправлять постели ежедневно» -> «Заправить постель».
    """
    if not text or not text.strip():
        return text
    s = text.strip()
    lower = s.lower()
    words = s.split()
    out = []
    for w in words:
        w_lower = w.lower()
        if w_lower in VERB_TO_DISPLAY:
            out.append(VERB_TO_DISPLAY[w_lower])
        elif w_lower in PLURAL_TO_SINGULAR:
            out.append(PLURAL_TO_SINGULAR[w_lower])
        else:
            out.append(w)
    result = " ".join(out)
    if result and result[0].isalpha():
        result = result[0].upper() + result[1:]
    return result.strip() if result.strip() else text.strip()
