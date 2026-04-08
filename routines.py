# -*- coding: utf-8 -*-
"""
Распознавание рутин и парсинг регулярности по тексту (без LLM).
Используется в bot_v2. Соответствует ROUTINES_PRODUCT.md.
"""
import re

# Коды дней: пн=0..вс=6 (как в Python weekday)
WEEKDAY_CODES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

# Полные названия и синонимы → короткий код
WEEKDAY_NAMES_TO_CODE = {
    "понедельник": "пн", "пн": "пн", "понедельникам": "пн",
    "вторник": "вт", "вт": "вт", "вторникам": "вт", "вторника": "вт",
    "среда": "ср", "ср": "ср", "среду": "ср", "средам": "ср", "среды": "ср",
    "четверг": "чт", "чт": "чт", "четвергу": "чт", "четвергам": "чт", "четверга": "чт",
    "пятница": "пт", "пт": "пт", "пятницу": "пт", "пятницам": "пт", "пятницы": "пт",
    "суббота": "сб", "сб": "сб", "субботу": "сб", "субботам": "сб", "субботы": "сб",
    "воскресенье": "вс", "вс": "вс", "воскресеньям": "вс", "воскресения": "вс",
}

# Значение-маркер: «раз в неделю» без указания дня — в db подставится случайный день
ROUTINE_WEEKLY_NO_DAY = "раз в неделю"
# Маркер для db.add_task: «N раз в неделю» без дней — подбор дней по загрузке (см. compute_n_week_repeat_days)
N_WEEK_PREFIX = "N_WEEK:"

# Число раз в неделю словами → число
_WORD_TO_N_TIMES = {
    "два": 2, "две": 2, "три": 3, "четыре": 4, "четырех": 4,
    "пять": 5, "пятью": 5, "шесть": 6, "семь": 7,
}

# Триггеры ежедневности
TRIGGERS_DAILY = (
    "ежедневн", "каждый день", "раз в день",
    "по утрам", "по вечерам",
)

# Триггеры еженедельности (без конкретного дня)
TRIGGERS_WEEKLY_GENERIC = (
    "еженедел", "раз в неделю", "каждую неделю",
)

# Явные команды рутины
TRIGGERS_ROUTINE_CMD = (
    "записать рутину", "добавить рутину", " рутина ", "рутина:",
)


def _normalize(text: str) -> str:
    """Нижний регистр, схлопывание пробелов."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


def parse_weekday_from_text(text: str) -> str | None:
    """
    Извлекает один день недели из фразы.
    «каждый понедельник», «по понедельникам», «в четверг», «пт» → «пн»/«чт»/«пт» и т.д.
    Возвращает короткий код (пн..вс) или None.
    """
    lower = _normalize(text)
    # Короткие коды в тексте (отдельно или в списке пн,вт)
    for code in WEEKDAY_CODES:
        # Отдельное слово «пн» или « пн » или «пн,»
        if re.search(rf"(^|[\s,]){re.escape(code)}([\s,]|$)", lower):
            return code
    # Паттерны: каждый понедельник, по понедельникам, каждую среду, по средам
    for name, code in WEEKDAY_NAMES_TO_CODE.items():
        if len(name) <= 2:
            continue
        if name in lower:
            return code
    return None


def parse_multiple_weekdays(text: str) -> list[str] | None:
    """
    Несколько дней: «пн и чт», «вт, чт», «понедельник и четверг», «в понедельник и четверг».
    Возвращает список кодов ["пн", "чт"] или None.
    """
    lower = _normalize(text)
    found: list[str] = []
    # Уже разобранный один день
    one = parse_weekday_from_text(text)
    if one:
        found.append(one)
    # Ищем остальные: «и чт», «, чт», «вт, чт»
    for code in WEEKDAY_CODES:
        if code in found:
            continue
        if re.search(rf"(^|[\s,]|и)\s*{re.escape(code)}([\s,]|$)", lower):
            found.append(code)
    for name, code in WEEKDAY_NAMES_TO_CODE.items():
        if len(name) <= 2 or code in found:
            continue
        if name in lower:
            found.append(code)
    # Уникальный порядок пн..вс
    order = {c: i for i, c in enumerate(WEEKDAY_CODES)}
    seen = set()
    unique = []
    for c in found:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    unique.sort(key=lambda c: order.get(c, 99))
    return unique if unique else None


def _explicit_recurrence_context(text: str) -> bool:
    """
    Явные маркеры повторяемости (не просто «в пятницу» как срок разовой задачи).
    Один день недели без этого контекста — не рутина.
    """
    lower = _normalize(text)
    if any(kw in lower for kw in TRIGGERS_DAILY):
        return True
    if any(kw in lower for kw in TRIGGERS_WEEKLY_GENERIC):
        return True
    if re.search(r"\bкажд(ый|ую|ое|ые)\b", lower):
        return True
    # «по понедельникам», «по четвергам», «по средам»; не цепляем «средства»
    if re.search(
        r"по\s+(?:"
        r"пн|вт|ср|чт|пт|сб|вс"
        r"|понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям|воскресениям"
        r")(?:[\s,.;:!?]|$)",
        lower,
    ):
        return True
    if parse_n_times_per_week(text) is not None:
        return True
    return False


def parse_n_times_per_week(text: str) -> int | None:
    """
    «два раза в неделю», «3 раза в неделю», «2 раза в неделю» → 2..7.
    """
    lower = _normalize(text)
    m = re.search(r"(\d+)\s*раза?\s*в\s*недел", lower)
    if m:
        v = int(m.group(1))
        return max(1, min(7, v))
    for word, n in sorted(_WORD_TO_N_TIMES.items(), key=len, reverse=True):
        if re.search(rf"{re.escape(word)}\s+раза?\s*в\s*недел", lower):
            return n
    return None


def parse_repeat_from_text(text: str) -> str | None:
    """
    Парсит регулярность из текста рутины (после определения, что это рутина).
    Возвращает: «ежедневно», «пн», «вт,чт», ROUTINE_WEEKLY_NO_DAY или None.
    """
    lower = _normalize(text)
    if any(kw in lower for kw in TRIGGERS_DAILY):
        return "ежедневно"
    days = parse_multiple_weekdays(text)
    if days:
        return ",".join(days)
    day = parse_weekday_from_text(text)
    if day:
        return day
    if any(kw in lower for kw in TRIGGERS_WEEKLY_GENERIC):
        return ROUTINE_WEEKLY_NO_DAY
    return None


def is_routine_and_repeat(task_text: str) -> tuple[bool, str | None]:
    """
    Определяет, является ли задача рутиной, и извлекает регулярность.
    Возвращает (is_routine, repeat_day).
    repeat_day: «ежедневно», «пн», «вт,чт», ROUTINE_WEEKLY_NO_DAY или None.
    """
    if not task_text or not task_text.strip():
        return False, None
    text = _normalize(task_text)

    # 1) Явные команды рутины
    if any(tr in text for tr in TRIGGERS_ROUTINE_CMD):
        repeat = parse_repeat_from_text(task_text)
        return True, repeat or "ежедневно"

    # 2) Ежедневность
    if any(kw in text for kw in TRIGGERS_DAILY):
        return True, "ежедневно"

    # 3) Несколько дней — всегда рутина; один день — только при явной повторяемости
    days = parse_multiple_weekdays(task_text)
    if days:
        if len(days) >= 2:
            return True, ",".join(days)
        if _explicit_recurrence_context(task_text):
            return True, days[0]
        return False, None
    day = parse_weekday_from_text(task_text)
    if day:
        if _explicit_recurrence_context(task_text):
            return True, day
        return False, None

    # 3b) «N раз в неделю» без конкретных дней — дни подберёт БД по равномерности и загрузке
    n_times = parse_n_times_per_week(task_text)
    if n_times is not None:
        return True, f"{N_WEEK_PREFIX}{n_times}"

    # 4) Еженедельно без дня
    if any(kw in text for kw in TRIGGERS_WEEKLY_GENERIC):
        return True, ROUTINE_WEEKLY_NO_DAY

    return False, None


def clean_task_title_from_routine_phrases(text: str) -> str:
    """
    Убирает из названия задачи фразы регулярности, чтобы осталась только суть.
    «Поливать цветы каждый четверг» → «Поливать цветы»
    """
    if not text or not text.strip():
        return text
    s = text.strip()
    lower = s.lower()

    # Убираем «каждый понедельник», «по понедельникам», «каждую среду» и т.д.
    for name in sorted(WEEKDAY_NAMES_TO_CODE.keys(), key=len, reverse=True):
        if len(name) <= 2:
            continue
        s = re.sub(rf"\s*каждый\s+{re.escape(name)}\s*", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"\s*каждую\s+{re.escape(name)}\s*", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"\s*каждое\s+{re.escape(name)}\s*", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"\s*по\s+{re.escape(name)}\s*", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"\s*в\s+{re.escape(name)}\s*", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"\s+{re.escape(name)}\s*$", "", s, flags=re.IGNORECASE)
        s = re.sub(rf"^\s*{re.escape(name)}\s+", "", s, flags=re.IGNORECASE)

    # Ежедневно, раз в день, по утрам, по вечерам
    for phrase in ("ежедневно", "ежедневная", "ежедневную", "каждый день", "раз в день", "по утрам", "по вечерам"):
        s = re.sub(rf"\s*{re.escape(phrase)}\s*", " ", s, flags=re.IGNORECASE)
    for phrase in ("еженедельно", "еженедельная", "раз в неделю", "каждую неделю"):
        s = re.sub(rf"\s*{re.escape(phrase)}\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(
        r"\s*\d+\s*раза?\s*в\s*неделю\s*",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    for w in ("два раза в неделю", "две раза в неделю", "три раза в неделю", "четыре раза в неделю",
              "пять раз в неделю", "шесть раз в неделю", "семь раз в неделю"):
        s = re.sub(rf"\s*{re.escape(w)}\s*", " ", s, flags=re.IGNORECASE)

    # «записать рутину», «добавить рутину», «рутина:»
    s = re.sub(r"\s*записать\s+рутину\s*:?\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*добавить\s+рутину\s*\-?\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*рутина\s*:?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+рутина\s*$", "", s, flags=re.IGNORECASE)

    s = re.sub(r"\s+", " ", s).strip().strip(".,;:-")
    return s if s else text.strip()
