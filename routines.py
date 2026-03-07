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

    # 3) Конкретный день или несколько дней (проверяем до «раз в неделю», чтобы «каждый четверг» дал чт)
    days = parse_multiple_weekdays(task_text)
    if days:
        return True, ",".join(days)
    day = parse_weekday_from_text(task_text)
    if day:
        return True, day

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

    # «записать рутину», «добавить рутину», «рутина:»
    s = re.sub(r"\s*записать\s+рутину\s*:?\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*добавить\s+рутину\s*\-?\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*рутина\s*:?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+рутина\s*$", "", s, flags=re.IGNORECASE)

    s = re.sub(r"\s+", " ", s).strip().strip(".,;:-")
    return s if s else text.strip()
