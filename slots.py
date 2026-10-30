"""Расчёт свободных окон.

Здесь только чистые функции: никакой базы данных и Telegram. Поэтому их легко
тестировать (см. tests/test_slots.py) — это «сердце» бота.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

Interval = tuple[datetime, datetime]
WorkHours = tuple[time, time]


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Пересекаются ли промежутки [a_start, a_end) и [b_start, b_end).

    Если один заканчивается ровно в момент начала другого — это не пересечение.
    """
    return a_start < b_end and a_end > b_start


def free_slots(
    day: date,
    duration_min: int,
    work_hours: WorkHours | None,
    busy: Iterable[Interval] = (),
    breaks: Iterable[WorkHours] = (),
    now: datetime | None = None,
    step_min: int = 30,
    min_lead_min: int = 60,
) -> list[datetime]:
    """Возвращает времена начала, на которые можно записаться в указанный день.

    day           — день, для которого считаем окна;
    duration_min  — длительность выбранной услуги;
    work_hours    — (начало, конец) рабочего дня или None, если день выходной;
    busy          — занятые промежутки (уже существующие записи);
    breaks        — ежедневные перерывы, например обед;
    now           — текущее время салона (без tzinfo); нужно, чтобы нельзя было
                    записаться «в прошлое» или слишком впритык;
    step_min      — шаг сетки (каждые 30 минут: 10:00, 10:30, ...);
    min_lead_min  — за сколько минут до начала запись ещё возможна.
    """
    if work_hours is None or duration_min <= 0:
        return []

    opens = datetime.combine(day, work_hours[0])
    closes = datetime.combine(day, work_hours[1])
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=step_min)
    earliest = now + timedelta(minutes=min_lead_min) if now is not None else None

    blocked: list[Interval] = list(busy)
    blocked += [
        (datetime.combine(day, brk_start), datetime.combine(day, brk_end))
        for brk_start, brk_end in breaks
    ]

    slots: list[datetime] = []
    start = opens
    while start + duration <= closes:
        end = start + duration
        too_early = earliest is not None and start < earliest
        if not too_early and not any(overlaps(start, end, b0, b1) for b0, b1 in blocked):
            slots.append(start)
        start += step
    return slots
