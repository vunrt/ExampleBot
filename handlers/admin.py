"""Панель мастера: расписание на день, закрытие дней, отмена записей, управление услугами.

Доступ только у пользователей из ADMIN_IDS (см. декоратор admin_only).
Все callback_data панели начинаются с «adm:», например «adm:day:2026-10-30».
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import wraps

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import Settings
from db import Database
from handlers.common import client_link, get_db, get_settings, safe_edit
from keyboards import chunks
from utils import (
    WEEKDAYS_SHORT,
    esc,
    fmt_date,
    fmt_date_full,
    fmt_duration,
    fmt_price,
    fmt_range,
    now_local,
)

logger = logging.getLogger(__name__)

View = tuple[str, InlineKeyboardMarkup]


def admin_only(handler):
    """Пропускает к хендлеру только мастера; остальным вежливо отказывает."""

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or user.id not in get_settings(context).admin_ids:
            if update.callback_query:
                await update.callback_query.answer("Недостаточно прав", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("Эта команда доступна только мастеру.")
            return None
        return await handler(update, context, *args, **kwargs)

    return wrapper


# ================================================================= экраны (views)

def _back_to_menu_row() -> list[Btn]:
    return [Btn("◀️ В меню", callback_data="adm:menu")]


def _menu_view() -> View:
    markup = InlineKeyboardMarkup(
        [
            [Btn("📅 Сегодня", callback_data="adm:today"), Btn("📅 Завтра", callback_data="adm:tomorrow")],
            [Btn("🗓 Другая дата", callback_data="adm:dates")],
            [Btn("🚫 Закрыть / открыть день", callback_data="adm:closelist")],
            [Btn("💅 Услуги", callback_data="adm:services")],
        ]
    )
    return "🛠 <b>Панель мастера</b>\n\nВыберите действие:", markup


def _day_buttons(settings: Settings, now: datetime, closed: set[date], prefix: str) -> list[list[Btn]]:
    """Сетка кнопок-дат на ближайшие horizon_days. Закрытые дни помечены значком 🚫."""
    buttons = []
    for offset in range(settings.horizon_days):
        day = now.date() + timedelta(days=offset)
        mark = "🚫 " if day in closed else ""
        label = f"{mark}{WEEKDAYS_SHORT[day.weekday()]} {day:%d.%m}"
        buttons.append(Btn(label, callback_data=f"{prefix}:{day.isoformat()}"))
    return chunks(buttons, 3)


def _dates_view(db: Database, settings: Settings, now: datetime) -> View:
    closed = db.get_days_off(now.date(), now.date() + timedelta(days=settings.horizon_days))
    rows = _day_buttons(settings, now, closed, "adm:day")
    rows.append(_back_to_menu_row())
    return "🗓 <b>Выберите дату</b>", InlineKeyboardMarkup(rows)


def _day_view(db: Database, day: date, notice: str = "") -> View:
    appointments = db.get_day_appointments(day)
    lines = [f"{notice}\n" if notice else "", f"📅 <b>{fmt_date_full(day)}</b>"]
    if db.is_day_off(day):
        lines.append("🚫 День закрыт для записи")

    if not appointments:
        lines.append("\nЗаписей нет.")
    else:
        lines.append(f"Записей: {len(appointments)}\n")
        for a in appointments:
            lines.append(
                f"<b>{fmt_range(a.start, a.end)}</b> · №{a.id}\n"
                f"💅 {esc(a.service_name)}\n"
                f"👤 {client_link(a.client_id, a.client_name, a.client_username)} · {esc(a.client_phone)}\n"
            )

    rows = [
        [Btn(f"❌ №{a.id} · {a.start:%H:%M} · {a.client_name[:16]}", callback_data=f"adm:cancel:{a.id}")]
        for a in appointments
    ]
    rows.append(_back_to_menu_row())
    return "\n".join(line for line in lines if line != ""), InlineKeyboardMarkup(rows)


def _closelist_view(db: Database, settings: Settings, now: datetime, notice: str = "") -> View:
    closed = db.get_days_off(now.date(), now.date() + timedelta(days=settings.horizon_days))
    rows = _day_buttons(settings, now, closed, "adm:toggle")
    rows.append(_back_to_menu_row())
    text = (
        f"{notice}\n\n" if notice else ""
    ) + (
        "🚫 <b>Закрыть / открыть день</b>\n\n"
        "Нажмите на дату, чтобы закрыть её для записи (🚫 — день уже закрыт) или открыть снова."
    )
    return text, InlineKeyboardMarkup(rows)


def _services_view(db: Database, settings: Settings) -> View:
    services = db.list_services()
    lines = ["💅 <b>Услуги</b>\n"]
    if not services:
        lines.append("Список пуст.")
    for s in services:
        lines.append(
            f"{s.id}. {esc(s.name)} — {fmt_duration(s.duration_min)} — {fmt_price(s.price, settings.currency)}"
        )
    lines.append(
        "\nДобавить: <code>/addservice Название | минуты | цена</code>\n"
        "Скрыть: <code>/delservice номер</code>"
    )
    return "\n".join(lines), InlineKeyboardMarkup([_back_to_menu_row()])


def _cancel_confirm_view(db: Database, appointment_id: int, now: datetime) -> View:
    appointment = db.get_appointment(appointment_id)
    if appointment is None or appointment.status != "booked":
        text, markup = _menu_view()
        return f"Эта запись уже отменена или не найдена.\n\n{text}", markup
    markup = InlineKeyboardMarkup(
        [[
            Btn("✅ Да, отменить", callback_data=f"adm:cancelyes:{appointment.id}"),
            Btn("↩️ Нет", callback_data=f"adm:day:{appointment.start.date().isoformat()}"),
        ]]
    )
    return (
        f"Отменить запись №{appointment.id}?\n\n"
        f"📅 {fmt_date(appointment.start.date())}, {fmt_range(appointment.start, appointment.end)}\n"
        f"💅 {esc(appointment.service_name)}\n"
        f"👤 {esc(appointment.client_name)}\n\n"
        "Клиент получит уведомление об отмене."
    ), markup


# ================================================================= хендлеры

@admin_only
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = _menu_view()
    await update.effective_message.reply_text(text, reply_markup=markup)


@admin_only
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Один обработчик на все кнопки панели: смотрим на action в callback_data."""
    query = update.callback_query
    await query.answer()
    db, settings = get_db(context), get_settings(context)
    now = now_local(settings.tz)

    _, action, *rest = query.data.split(":")
    arg = rest[0] if rest else ""

    if action == "menu":
        view = _menu_view()
    elif action == "today":
        view = _day_view(db, now.date())
    elif action == "tomorrow":
        view = _day_view(db, now.date() + timedelta(days=1))
    elif action == "dates":
        view = _dates_view(db, settings, now)
    elif action == "day":
        view = _day_view(db, date.fromisoformat(arg))
    elif action == "closelist":
        view = _closelist_view(db, settings, now)
    elif action == "toggle":
        view = _toggle_day(db, settings, now, date.fromisoformat(arg))
    elif action == "services":
        view = _services_view(db, settings)
    elif action == "cancel":
        view = _cancel_confirm_view(db, int(arg), now)
    elif action == "cancelyes":
        view = await _cancel_by_master(context, db, settings, now, int(arg))
    else:
        return
    await safe_edit(query, *view)


def _toggle_day(db: Database, settings: Settings, now: datetime, day: date) -> View:
    closed = db.toggle_day_off(day)
    if not closed:
        notice = f"✅ {fmt_date(day)} снова открыт для записи."
    else:
        notice = f"🚫 {fmt_date(day)} закрыт для записи."
        booked = len(db.get_day_appointments(day))
        if booked:
            notice += (
                f"\n⚠️ На этот день уже есть записей: {booked}. "
                "Они не отменяются автоматически — отмените их вручную в расписании дня."
            )
    return _closelist_view(db, settings, now, notice)


async def _cancel_by_master(
    context: ContextTypes.DEFAULT_TYPE, db: Database, settings: Settings, now: datetime, appointment_id: int
) -> View:
    appointment = db.get_appointment(appointment_id)
    if appointment is None:
        return _menu_view()
    if not db.cancel_appointment(appointment_id, "master", now):
        return _day_view(db, appointment.start.date(), notice="Эта запись уже была отменена.")

    notice = f"✅ Запись №{appointment.id} отменена, клиент уведомлён."
    try:
        await context.bot.send_message(
            appointment.client_id,
            f"😔 К сожалению, мастер вынужден отменить вашу запись №{appointment.id}:\n\n"
            f"📅 {fmt_date(appointment.start.date())}, {fmt_range(appointment.start, appointment.end)}\n"
            f"💅 {esc(appointment.service_name)}\n\n"
            "Приносим извинения! Выберите другое время: нажмите «💅 Записаться».",
        )
    except TelegramError:
        logger.warning("Не удалось уведомить клиента %s об отмене", appointment.client_id, exc_info=True)
        notice = (
            f"✅ Запись №{appointment.id} отменена, но клиента уведомить не удалось. "
            f"Свяжитесь с ним: {esc(appointment.client_phone)}"
        )
    return _day_view(db, appointment.start.date(), notice=notice)


@admin_only
async def add_service_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addservice Название | минуты | цена"""
    message = update.effective_message
    usage = (
        "Формат: <code>/addservice Название | минуты | цена</code>\n"
        "Пример: <code>/addservice Педикюр | 90 | 3000</code>"
    )
    raw = (message.text or "").partition(" ")[2]
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) != 3:
        await message.reply_text(usage)
        return

    name, duration_raw, price_raw = parts
    if not 2 <= len(name) <= 60 or not duration_raw.isdigit() or not price_raw.isdigit():
        await message.reply_text(usage)
        return
    duration, price = int(duration_raw), int(price_raw)
    if not 5 <= duration <= 480:
        await message.reply_text("Длительность должна быть от 5 до 480 минут.")
        return

    settings = get_settings(context)
    service_id = get_db(context).add_service(name, duration, price)
    await message.reply_text(
        f"✅ Услуга №{service_id} добавлена: {esc(name)} — {fmt_duration(duration)} — "
        f"{fmt_price(price, settings.currency)}"
    )


@admin_only
async def del_service_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delservice номер — услуга скрывается из записи, но старые записи остаются."""
    message = update.effective_message
    if not context.args or not context.args[0].isdigit():
        await message.reply_text(
            "Формат: <code>/delservice номер</code>\nНомера услуг — в «Панель мастера → Услуги»."
        )
        return
    if get_db(context).deactivate_service(int(context.args[0])):
        await message.reply_text("✅ Услуга скрыта: новые записи на неё невозможны.")
    else:
        await message.reply_text("Услуга с таким номером не найдена (или уже скрыта).")
