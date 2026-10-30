"""Настройки бота.

Секреты (токен, ID мастера) берутся из файла .env или переменных окружения.
Расписание и бизнес-правила — ниже, в этом файле: меняйте их под своего мастера.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Расписание мастера
# --------------------------------------------------------------------------
# День недели (0 = понедельник ... 6 = воскресенье) -> (начало, конец) или None (выходной).
WORK_HOURS: dict[int, tuple[time, time] | None] = {
    0: (time(10, 0), time(19, 0)),
    1: (time(10, 0), time(19, 0)),
    2: (time(10, 0), time(19, 0)),
    3: (time(10, 0), time(19, 0)),
    4: (time(10, 0), time(19, 0)),
    5: (time(10, 0), time(16, 0)),
    6: None,
}

# Ежедневные перерывы (обед и т. п.). Чтобы убрать перерыв: BREAKS = ()
BREAKS: tuple[tuple[time, time], ...] = ((time(14, 0), time(15, 0)),)

# Услуги, которые добавятся при первом запуске: (название, минуты, цена).
# Потом их можно менять командами мастера /addservice и /delservice.
DEFAULT_SERVICES: list[tuple[str, int, int]] = [
    ("Маникюр классический", 60, 1500),
    ("Маникюр + покрытие гель-лак", 90, 2500),
    ("Снятие покрытия + маникюр", 90, 2000),
    ("Дизайн (1 ноготь)", 15, 150),
]


@dataclass(frozen=True)
class Settings:
    token: str
    admin_ids: frozenset[int]
    tz: ZoneInfo
    db_path: Path
    proxy: str | None = None

    # Что показываем клиентам в разделе «Контакты»
    salon_name: str = "Nail Studio"
    salon_address: str = "ул. Примерная, 1"
    salon_phone: str = "+7 900 000-00-00"
    currency: str = "₽"

    # Расписание
    work_hours: dict[int, tuple[time, time] | None] = field(default_factory=lambda: dict(WORK_HOURS))
    breaks: tuple[tuple[time, time], ...] = BREAKS

    # Правила записи
    slot_step_min: int = 30          # шаг сетки времени: 10:00, 10:30, ...
    min_lead_min: int = 60           # нельзя записаться ближе чем за 60 минут до начала
    horizon_days: int = 14           # на сколько дней вперёд открыта запись
    reminder_hours: int = 24         # за сколько часов напомнить о визите
    min_cancel_hours: int = 2        # клиент может отменить запись не позднее чем за 2 часа
    max_active_per_client: int = 3   # максимум одновременных предстоящих записей у клиента

    # Технические параметры
    conversation_timeout_sec: int = 1800   # через 30 минут молчания диалог записи сбрасывается
    request_timeout: float = 20.0          # таймаут запросов к Telegram (через прокси полезен побольше)


def _first_env(*names: str) -> str:
    """Первое непустое значение среди переменных окружения.

    Нужно из-за хостингов: Bothost подставляет токен под несколькими именами
    (BOT_TOKEN / TELEGRAM_BOT_TOKEN / API_TOKEN), и заранее неизвестно, какое сработает.
    """
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return frozenset(ids)


def load_settings() -> Settings:
    """Читает .env / переменные окружения и собирает Settings. Понятно ругается на ошибки."""
    load_dotenv(BASE_DIR / ".env")

    token = _first_env("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "API_TOKEN")
    if not token:
        raise SystemExit(
            "BOT_TOKEN is not set. Locally: copy .env.example to .env and paste the "
            "token from @BotFather. On hosting: add the BOT_TOKEN environment variable."
        )
    if ":" not in token or not token.split(":", 1)[0].isdigit():
        raise SystemExit(
            "BOT_TOKEN does not look like a Telegram bot token (expected 123456789:AA...)."
        )

    try:
        admin_ids = _parse_ids(os.getenv("ADMIN_IDS", ""))
    except ValueError:
        raise SystemExit("ADMIN_IDS должен содержать числовые Telegram ID через запятую.") from None
    if not admin_ids:
        logging.getLogger(__name__).warning(
            "ADMIN_IDS не задан: панель мастера и уведомления о записях будут недоступны."
        )

    tz_name = os.getenv("TIMEZONE", "Europe/Moscow").strip()
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SystemExit(
            f"Неизвестный часовой пояс {tz_name!r}. Пример: Europe/Moscow. "
            "На Windows может понадобиться пакет tzdata: pip install tzdata"
        ) from None

    db_path = Path(_first_env("DB_PATH", "DATABASE_PATH") or "data/bookings.db")
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    defaults = Settings(token=token, admin_ids=admin_ids, tz=tz, db_path=db_path)
    return Settings(
        token=token,
        admin_ids=admin_ids,
        tz=tz,
        db_path=db_path,
        proxy=_first_env("PROXY_URL") or None,
        salon_name=os.getenv("SALON_NAME", defaults.salon_name),
        salon_address=os.getenv("SALON_ADDRESS", defaults.salon_address),
        salon_phone=os.getenv("SALON_PHONE", defaults.salon_phone),
        currency=os.getenv("CURRENCY", defaults.currency),
    )
