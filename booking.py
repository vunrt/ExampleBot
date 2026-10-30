"""Связка базы данных и расчёта окон: какие дни и время сейчас доступны клиенту."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from config import Settings
from db import Database
from slots import free_slots


def slots_for_day(
    db: Database, settings: Settings, duration_min: int, day: date, now: datetime
) -> list[datetime]:
    """Свободное время на конкретную дату (пусто, если день прошёл, закрыт или выходной)."""
    if day < now.date() or db.is_day_off(day):
        return []
    return free_slots(
        day=day,
        duration_min=duration_min,
        work_hours=settings.work_hours.get(day.weekday()),
        busy=db.get_busy_intervals(day, day),
        breaks=settings.breaks,
        now=now,
        step_min=settings.slot_step_min,
        min_lead_min=settings.min_lead_min,
    )


def available_days(
    db: Database, settings: Settings, duration_min: int, now: datetime
) -> dict[date, list[datetime]]:
    """Дни ближайших horizon_days, где есть хотя бы одно окно, и сами окна.

    Запросов к БД всего два на весь период, а не по запросу на каждый день.
    """
    first = now.date()
    last = first + timedelta(days=settings.horizon_days - 1)
    days_off = db.get_days_off(first, last)
    busy = db.get_busy_intervals(first, last)  # free_slots сама отберёт пересечения нужного дня

    result: dict[date, list[datetime]] = {}
    for offset in range(settings.horizon_days):
        day = first + timedelta(days=offset)
        if day in days_off:
            continue
        slots = free_slots(
            day=day,
            duration_min=duration_min,
            work_hours=settings.work_hours.get(day.weekday()),
            busy=busy,
            breaks=settings.breaks,
            now=now,
            step_min=settings.slot_step_min,
            min_lead_min=settings.min_lead_min,
        )
        if slots:
            result[day] = slots
    return result
