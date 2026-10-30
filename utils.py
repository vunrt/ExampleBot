"""Мелкие вспомогательные функции: время, форматирование, проверка ввода."""
from __future__ import annotations

import html
import re
from datetime import date, datetime, time, tzinfo
from typing import Mapping

WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
WEEKDAYS_FULL = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)
MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

# Формат хранения времени в БД: локальное время салона, без часового пояса.
DB_DT_FORMAT = "%Y-%m-%d %H:%M"

# Код страны по умолчанию для номеров, введённых без него (8 912... или 912...).
DEFAULT_COUNTRY_CODE = "7"


# --------------------------------------------------------------------------- время

def now_local(tz: tzinfo) -> datetime:
    """Текущее время салона без tzinfo (все расчёты идут в «настенном» времени салона)."""
    return datetime.now(tz).replace(tzinfo=None, second=0, microsecond=0)


def to_db(dt: datetime) -> str:
    return dt.strftime(DB_DT_FORMAT)


def from_db(value: str) -> datetime:
    return datetime.strptime(value, DB_DT_FORMAT)


# ----------------------------------------------------------------- форматирование

def esc(text: object) -> str:
    """Экранирует текст для parse_mode=HTML (имена клиентов, названия услуг и т. д.)."""
    return html.escape(str(text), quote=False)


def fmt_date(d: date) -> str:
    """Пн, 26 октября"""
    return f"{WEEKDAYS_SHORT[d.weekday()]}, {d.day} {MONTHS_GEN[d.month - 1]}"


def fmt_date_full(d: date) -> str:
    """Понедельник, 26 октября"""
    return f"{WEEKDAYS_FULL[d.weekday()].capitalize()}, {d.day} {MONTHS_GEN[d.month - 1]}"


def fmt_range(start: datetime, end: datetime) -> str:
    """10:00–11:30"""
    return f"{start:%H:%M}–{end:%H:%M}"


def fmt_when(start: datetime, now: datetime) -> str:
    """«сегодня», «завтра» или дата — для текстов напоминаний."""
    delta = (start.date() - now.date()).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    return fmt_date(start.date())


def fmt_price(value: int, currency: str = "₽") -> str:
    """1 500 ₽ (с неразрывными пробелами, чтобы цена не переносилась)."""
    return f"{value:,}".replace(",", "\u00a0") + f"\u00a0{currency}"


def fmt_duration(minutes: int) -> str:
    """90 -> «1 ч 30 мин»"""
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if mins:
        parts.append(f"{mins} мин")
    return " ".join(parts) or "0 мин"


def format_schedule(work_hours: Mapping[int, tuple[time, time] | None]) -> str:
    """Склеивает одинаковые соседние дни: «Пн–Пт: 10:00–19:00 / Сб: 10:00–16:00 / Вс: выходной»."""
    groups: list[tuple[int, int, tuple[time, time] | None]] = []
    for weekday in range(7):
        hours = work_hours.get(weekday)
        if groups and groups[-1][2] == hours:
            groups[-1] = (groups[-1][0], weekday, hours)
        else:
            groups.append((weekday, weekday, hours))

    lines = []
    for first, last, hours in groups:
        if first == last:
            days = WEEKDAYS_SHORT[first]
        else:
            days = f"{WEEKDAYS_SHORT[first]}–{WEEKDAYS_SHORT[last]}"
        value = f"{hours[0]:%H:%M}–{hours[1]:%H:%M}" if hours else "выходной"
        lines.append(f"{days}: {value}")
    return "\n".join(lines)


# ------------------------------------------------------------------ проверка ввода

_PHONE_ALLOWED = re.compile(r"[\d\s+\-().]+")


def normalize_phone(raw: str) -> str | None:
    """Приводит номер к виду +79123456789. Возвращает None, если это не похоже на номер."""
    raw = (raw or "").strip()
    if not _PHONE_ALLOWED.fullmatch(raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = DEFAULT_COUNTRY_CODE + digits[1:]
    elif len(digits) == 10:
        digits = DEFAULT_COUNTRY_CODE + digits
    if not 11 <= len(digits) <= 15:
        return None
    return "+" + digits


def clean_name(raw: str) -> str | None:
    """Убирает лишние пробелы; имя должно быть длиной от 2 до 50 символов."""
    name = " ".join((raw or "").split())
    if not 2 <= len(name) <= 50:
        return None
    return name
