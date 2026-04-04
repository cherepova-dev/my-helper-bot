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

# Маркеры «удали задачу N» / «удали рутину N»
DELETE_PREFIXES = (
    "удали рутину", "удалить рутину",
    "удали задачу", "удалить задачу",
    "удали", "удалить",
)
DELETE_PREFIXES_LOWER = [p.lower() for p in sorted(DELETE_PREFIXES, key=len, reverse=True)]


def starts_with_delete_marker(text: str) -> bool:
    """True, если текст начинается с маркера удаления задачи/рутины."""
    lower = (text or "").strip().lower()
    return any(lower.startswith(p) for p in DELETE_PREFIXES_LOWER)


def extract_delete_target(text: str) -> tuple[int | None, bool]:
    """
    После маркера «удали задачу» / «удали рутину» извлекает номер (1-based).
    Возвращает (num_or_none, is_routine). is_routine=True если в фразе «рутину».
    """
    t = (text or "").strip()
    lower = t.lower()
    rest = ""
    is_routine = False
    for prefix in DELETE_PREFIXES_LOWER:
        if lower.startswith(prefix):
            rest = t[len(prefix):].strip()
            is_routine = "рутину" in prefix
            rest = re.sub(r"^(?:задач[уа]\.?\s*|рутину\s*|номер\s*)?", "", rest, flags=re.IGNORECASE).strip()
            break
    if not rest:
        return None, is_routine
    m = re.match(r"^(\d+)\s*", rest)
    if m:
        return int(m.group(1)), is_routine
    return None, is_routine


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
    rest = re.sub(r"^(?:задач[уа]\.?|рутин[уа]\.?)\s*", "", rest, flags=re.IGNORECASE).strip()
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

    ru = _parse_russian_day_month_phrase(lower, today)
    if ru:
        return ru
    return None


# Порядковые (дата) + родительный падеж месяца: «второе апреля», «на 2 апреля 2026»
_ORDINAL_DAY_WORDS = {
    "первое": 1, "второе": 2, "третье": 3, "четвёртое": 4, "четвертое": 4,
    "пятое": 5, "шестое": 6, "седьмое": 7, "восьмое": 8, "девятое": 9, "десятое": 10,
    "одиннадцатое": 11, "двенадцатое": 12, "тринадцатое": 13, "четырнадцатое": 14,
    "пятнадцатое": 15, "шестнадцатое": 16, "семнадцатое": 17, "восемнадцатое": 18,
    "девятнадцатое": 19, "двадцатое": 20, "двадцать первое": 21, "двадцать второе": 22,
    "двадцать третье": 23, "двадцать четвёртое": 24, "двадцать четвертое": 24,
    "двадцать пятое": 25, "двадцать шестое": 26, "двадцать седьмое": 27,
    "двадцать восьмое": 28, "двадцать девятое": 29, "тридцатое": 30, "тридцать первое": 31,
}

_MONTH_NAME_TO_NUM = {
    "января": 1, "январь": 1, "февраля": 2, "февраль": 2, "марта": 3, "март": 3,
    "апреля": 4, "апрель": 4, "мая": 5, "май": 5, "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7, "августа": 8, "август": 8, "сентября": 9, "сентябрь": 9,
    "октября": 10, "октябрь": 10, "ноября": 11, "ноябрь": 11, "декабря": 12, "декабрь": 12,
}


def _finalize_calendar_date(day: int, month: int, year: int | None, today: datetime) -> str | None:
    """Год по умолчанию — текущий; если дата уже прошла, берём следующий год."""
    y = year if year is not None else today.year
    try:
        dt = datetime(y, month, day)
    except ValueError:
        return None
    if year is None and dt.date() < today.date():
        try:
            dt = datetime(y + 1, month, day)
        except ValueError:
            return None
    return dt.strftime("%Y-%m-%d")


def _parse_russian_day_month_phrase(lower: str, today: datetime) -> str | None:
    """«2 апреля», «второе апреля», «на 2 апреля 2026», порядковые + месяц."""
    # Явный год в конце фразы (опционально)
    year_m = re.search(r"\b(20\d{2})\b", lower)
    year = int(year_m.group(1)) if year_m else None

    for month_name, month_num in sorted(_MONTH_NAME_TO_NUM.items(), key=lambda x: -len(x[0])):
        idx = lower.find(month_name)
        if idx < 0:
            continue
        before = lower[:idx].strip()
        # Цифра + месяц: «2 апреля», «02 апреля»
        m_num = re.search(r"(\d{1,2})\s*$", before)
        if m_num:
            day = int(m_num.group(1))
            if 1 <= day <= 31:
                return _finalize_calendar_date(day, month_num, year, today)
        # Порядковое слово + месяц
        for ord_word, day_num in sorted(_ORDINAL_DAY_WORDS.items(), key=lambda x: -len(x[0])):
            if before.endswith(ord_word):
                if 1 <= day_num <= 31:
                    return _finalize_calendar_date(day_num, month_num, year, today)
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


def infer_time_of_day(text: str) -> str | None:
    """
    «Утро / день / вечер / ночь» из свободного текста (без точного времени).
    Для рутин: «по утрам», «по вечерам»; для задач: «вечером позвонить», «днём съездить».
    Не срабатывает на «в 10 утра» — там только parse_due_time.
    """
    if not text or not text.strip():
        return None
    lower = re.sub(r"\s+", " ", text.strip().lower())
    if "по утрам" in lower:
        return "утро"
    if "по вечерам" in lower:
        return "вечер"
    if re.search(r"\bутром\b", lower):
        return "утро"
    if re.search(r"\bвечером\b", lower):
        return "вечер"
    if re.search(r"\bднём\b", lower) or re.search(r"\bднем\b", lower):
        return "день"
    if re.search(r"\bночью\b", lower):
        return "ночь"
    return None


def time_of_day_from_hour(hour: int) -> str | None:
    """Час 0–23 → период суток для группировки (как у due_time)."""
    if hour < 0 or hour > 23:
        return None
    if hour < 12:
        return "утро"
    if hour < 17:
        return "день"
    return "вечер"


def classify_time_of_day_edit(new_text: str) -> str:
    """
    Правая часть «изменить задачу N на …» / «рутину N на …».
    Возвращает канон: «утро»|«день»|«вечер»|«ночь», «clear» — сбросить time_of_day,
    «not» — это новое название задачи (не трогаем логику заголовка).
    """
    if not new_text or not str(new_text).strip():
        return "not"
    s = re.sub(r"\s+", " ", str(new_text).strip()).lower().rstrip(".!")
    clear_phrases = (
        "без времени суток",
        "без времени",
        "сбрось время суток",
        "убери время суток",
        "сбросить время суток",
        "не указывай время",
        "убери период",
        "сбрось период",
    )
    if s in clear_phrases:
        return "clear"
    s2 = re.sub(r"^(на|в|поставь|сделать|сделай)\s+", "", s)
    s2 = re.sub(r"^(время суток|время|период)\s+", "", s2).strip()
    variants_to_canon: list[tuple[str, tuple[str, ...]]] = [
        ("утро", ("утро", "утром", "по утрам")),
        ("день", ("день", "днем", "днём")),
        ("вечер", ("вечер", "вечером", "по вечерам")),
        ("ночь", ("ночь", "ночью")),
    ]
    for canon, variants in variants_to_canon:
        if s in variants or s2 in variants:
            return canon
    return "not"


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


def _expand_done_number_token(token: str) -> list[int] | None:
    """Один токен: «3», «1-3», «1..3», «1—3» → список номеров."""
    t = re.sub(r"\s+", "", token.strip())
    if not t:
        return []
    m = re.fullmatch(r"(\d+)[-–—..]+(\d+)", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            a, b = b, a
        if b - a > 500:
            return None
        return list(range(a, b + 1))
    if t.isdigit():
        return [int(t)]
    return None


def _parse_done_multi_rest(rest: str) -> list[int] | None:
    """
    Если весь хвост — только несколько номеров (через запятую, и, или, пробелы, диапазоны),
    возвращает список. Иначе None.
    """
    r = rest.strip()
    if not r:
        return None

    # Текст задачи (русские буквы) — не мультиномера, кроме союзов «и», «или»
    def _has_forbidden_letters(s: str) -> bool:
        s = s.lower()
        tmp = s
        for w in (" и ", " или "):
            tmp = tmp.replace(w, " ")
        return bool(re.search(r"[а-яё]", tmp))

    if _has_forbidden_letters(r):
        return None

    normalized = re.sub(r"\s+", " ", r)
    if re.fullmatch(r"\d+(\s+\d+)+", normalized):
        chunks = normalized.split()
    else:
        chunks = re.split(r"\s*(?:,|;\s*|\s+и\s+|\s+или\s+)\s*", normalized, flags=re.IGNORECASE)
        chunks = [c.strip() for c in chunks if c.strip()]
        if len(chunks) < 2:
            if re.fullmatch(r"\d+[-–—..]+\d+", normalized.replace(" ", "")):
                chunks = [normalized.replace(" ", "")]
            else:
                return None

    out: list[int] = []
    seen: set[int] = set()
    for chunk in chunks:
        expanded = _expand_done_number_token(chunk)
        if expanded is None:
            return None
        for n in expanded:
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out if out else None


def extract_done_targets(text: str) -> tuple[list[int] | None, int | None, str]:
    """
    Несколько номеров (батч) или один номер / текст для поиска.
    Возвращает (list_nums, None, "") | (None, num, "") | (None, None, rest).
    """
    t = text.strip()
    lower = t.lower()
    rest = ""
    for prefix in DONE_PREFIXES_LOWER:
        if lower.startswith(prefix):
            rest = t[len(prefix):].strip()
            rest = re.sub(r"^(?:задач[уа]\.?\s*)?", "", rest, flags=re.IGNORECASE).strip()
            rest = re.sub(r"^[.,;:\s]+", "", rest).strip()
            rest = re.sub(r"\s+", " ", rest).strip()
            break
    if not rest:
        return None, None, ""

    rwork = rest
    while True:
        rwork = re.sub(r"^[.,;:\s]+", "", rwork).strip()
        rwork = re.sub(r"\s+", " ", rwork).strip()
        if not rwork:
            return None, None, ""
        rwork_lower = rwork.lower()
        stripped = False
        for filler in DONE_FILLER_LOWER:
            if rwork_lower.startswith(filler):
                rwork = rwork[len(filler):].strip()
                stripped = True
                break
        if not stripped:
            break

    multi = _parse_done_multi_rest(rwork)
    if multi is not None:
        return multi, None, ""

    s_num, s_rest = extract_done_target(text)
    if s_num is not None:
        return None, s_num, ""
    return None, None, s_rest


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
