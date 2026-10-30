"""Клиентская часть: запись на маникюр (пошаговый диалог), «Мои записи», отмена записи.

Диалог записи — это ConversationHandler с шагами:
    услуга -> дата -> время -> (имя -> телефон) -> подтверждение

Все промежуточные данные лежат в context.user_data["booking"] и в базу попадают
только после нажатия «Подтвердить».
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from booking import available_days, slots_for_day
from config import Settings
from db import Appointment, Database, Service
from handlers.common import (
    client_link,
    get_db,
    get_settings,
    notify_admins,
    safe_edit,
    send_or_edit,
    show_contacts,
    show_price,
    start,
)
from keyboards import (
    BTN_BOOK,
    BTN_CANCEL,
    BTN_CONTACTS,
    BTN_MY,
    BTN_PRICE,
    MENU_BUTTONS,
    back_cancel_kb,
    confirm_kb,
    dates_kb,
    main_menu,
    phone_keyboard,
    services_kb,
    times_kb,
)
from utils import (
    clean_name,
    esc,
    fmt_date,
    fmt_date_full,
    fmt_duration,
    fmt_price,
    fmt_range,
    normalize_phone,
    now_local,
)

logger = logging.getLogger(__name__)

END = ConversationHandler.END

# Шаги (состояния) диалога записи
CHOOSING_SERVICE, CHOOSING_DATE, CHOOSING_TIME, ASKING_NAME, ASKING_PHONE, CONFIRMING = range(6)


# ====================================================================== помощники

def _drop_booking(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("booking", None)


def _load_booking(
    context: ContextTypes.DEFAULT_TYPE, *required: str
) -> tuple[dict, Service] | None:
    """Достаёт черновик записи и выбранную услугу. None — если черновик неполный или услуга исчезла."""
    booking = context.user_data.get("booking")
    if not booking or "service_id" not in booking or any(key not in booking for key in required):
        return None
    service = get_db(context).get_service(booking["service_id"])
    if service is None or not service.is_active:
        return None
    return booking, service


async def _session_expired(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _drop_booking(context)
    text = "Сессия записи устарела. Нажмите «💅 Записаться», чтобы начать заново."
    if update.callback_query:
        await safe_edit(update.callback_query, text)
    else:
        await update.effective_message.reply_text(text, reply_markup=main_menu())
    return END


def _start_of(booking: dict) -> datetime:
    return datetime.combine(
        date.fromisoformat(booking["date"]), time.fromisoformat(booking["time"])
    )


def _service_line(service: Service, settings: Settings) -> str:
    return (
        f"💅 <b>{esc(service.name)}</b> · {fmt_duration(service.duration_min)} · "
        f"{fmt_price(service.price, settings.currency)}"
    )


# ================================================================ шаги диалога записи

async def book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Точка входа: кнопка «Записаться» или команда /book."""
    db, settings = get_db(context), get_settings(context)
    _drop_booking(context)

    active = db.count_client_upcoming(update.effective_user.id, now_local(settings.tz))
    if active >= settings.max_active_per_client:
        await update.effective_message.reply_text(
            f"Достигнут лимит предстоящих записей: {settings.max_active_per_client}.\n"
            "Отмените одну из них в разделе «📋 Мои записи» "
            f"или позвоните мастеру: {esc(settings.salon_phone)}",
            reply_markup=main_menu(),
        )
        return END

    context.user_data["booking"] = {}
    return await _show_services(update, context)


async def _show_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db, settings = get_db(context), get_settings(context)
    services = db.list_services()
    if not services:
        _drop_booking(context)
        await send_or_edit(update, "Список услуг пока пуст. Загляните позже или свяжитесь с мастером.")
        return END
    await send_or_edit(update, "💅 <b>Выберите услугу</b>", services_kb(services, settings.currency))
    return CHOOSING_SERVICE


async def on_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    service = get_db(context).get_service(int(query.data.split(":", 1)[1]))
    if service is None or not service.is_active:
        _drop_booking(context)
        await safe_edit(query, "Эта услуга больше недоступна. Нажмите «💅 Записаться», чтобы выбрать другую.")
        return END
    context.user_data["booking"] = {"service_id": service.id}
    return await _show_dates(update, context)


async def _show_dates(update: Update, context: ContextTypes.DEFAULT_TYPE, notice: str = "") -> int:
    state = _load_booking(context)
    if state is None:
        return await _session_expired(update, context)
    booking, service = state
    db, settings = get_db(context), get_settings(context)

    days = available_days(db, settings, service.duration_min, now_local(settings.tz))
    if not days:
        _drop_booking(context)
        await send_or_edit(
            update,
            f"😔 На ближайшие {settings.horizon_days} дней свободного времени нет.\n"
            f"Свяжитесь с мастером: {esc(settings.salon_phone)}",
        )
        return END

    booking.pop("date", None)
    booking.pop("time", None)
    await send_or_edit(
        update,
        f"{notice}{_service_line(service, settings)}\n\n📅 <b>Выберите дату</b>",
        dates_kb(list(days)),
    )
    return CHOOSING_DATE


async def on_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    state = _load_booking(context)
    if state is None:
        return await _session_expired(update, context)
    state[0]["date"] = query.data.split(":", 1)[1]
    return await _show_times(update, context)


async def _show_times(update: Update, context: ContextTypes.DEFAULT_TYPE, notice: str = "") -> int:
    state = _load_booking(context, "date")
    if state is None:
        return await _session_expired(update, context)
    booking, service = state
    db, settings = get_db(context), get_settings(context)

    day = date.fromisoformat(booking["date"])
    slots = slots_for_day(db, settings, service.duration_min, day, now_local(settings.tz))
    if not slots:
        # Пока клиент думал, окна на эту дату закончились — возвращаем к выбору даты.
        return await _show_dates(
            update, context, notice="⚠️ На эту дату свободного времени уже не осталось. Выберите другую.\n\n"
        )

    booking.pop("time", None)
    await send_or_edit(
        update,
        f"{notice}{_service_line(service, settings)}\n"
        f"📅 {fmt_date_full(day)}\n\n🕒 <b>Выберите время</b>",
        times_kb([slot.time() for slot in slots]),
    )
    return CHOOSING_TIME


async def on_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    state = _load_booking(context, "date")
    if state is None:
        return await _session_expired(update, context)
    booking, service = state
    db, settings = get_db(context), get_settings(context)

    chosen = datetime.combine(
        date.fromisoformat(booking["date"]), time.fromisoformat(query.data.split(":", 1)[1])
    )
    # Проверяем ещё раз: кто-то мог занять это время, пока клиент смотрел на кнопки.
    free = slots_for_day(db, settings, service.duration_min, chosen.date(), now_local(settings.tz))
    if chosen not in free:
        return await _show_times(update, context, notice="⚠️ Это время уже занято. Выберите другое.\n\n")
    booking["time"] = chosen.strftime("%H:%M")

    if "name" in booking and "phone" in booking:  # данные уже введены раньше в этой сессии
        return await _show_summary(update, context)

    client = db.get_client(update.effective_user.id)
    if client:  # постоянный клиент: не заставляем вводить имя и телефон заново
        booking["name"], booking["phone"] = client.name, client.phone
        return await _show_summary(update, context)
    return await _ask_name(update, context)


async def _ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_or_edit(
        update,
        "👤 <b>Как вас зовут?</b>\nНапишите имя сообщением.",
        back_cancel_kb("back:time"),
    )
    return ASKING_NAME


async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    name = clean_name(message.text)
    if name is None:
        await message.reply_text("Имя должно быть длиной от 2 до 50 символов. Попробуйте ещё раз.")
        return ASKING_NAME

    state = _load_booking(context, "date", "time")
    if state is None:
        return await _session_expired(update, context)
    state[0]["name"] = name

    await message.reply_text(
        "📱 <b>Номер телефона для связи</b>\nНажмите кнопку ниже или напишите номер сообщением.",
        reply_markup=phone_keyboard(),
    )
    return ASKING_PHONE


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _accept_phone(update, context, update.effective_message.contact.phone_number)


async def on_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _accept_phone(update, context, update.effective_message.text)


async def _accept_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str) -> int:
    phone = normalize_phone(raw)
    if phone is None:
        await update.effective_message.reply_text(
            "Не похоже на номер телефона 🤔 Пример: +7 912 345-67-89",
            reply_markup=phone_keyboard(),
        )
        return ASKING_PHONE

    state = _load_booking(context, "date", "time", "name")
    if state is None:
        return await _session_expired(update, context)
    state[0]["phone"] = phone

    # Возвращаем главное меню на место клавиатуры с кнопкой «Отправить номер».
    await update.effective_message.reply_text("Спасибо! 👍", reply_markup=main_menu())
    return await _show_summary(update, context)


async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = _load_booking(context, "date", "time", "name", "phone")
    if state is None:
        return await _session_expired(update, context)
    booking, service = state
    settings = get_settings(context)

    start_dt = _start_of(booking)
    end_dt = start_dt + timedelta(minutes=service.duration_min)
    await send_or_edit(
        update,
        "📝 <b>Проверьте запись</b>\n\n"
        f"{_service_line(service, settings)}\n"
        f"📅 {fmt_date_full(start_dt.date())}\n"
        f"🕒 {fmt_range(start_dt, end_dt)}\n"
        f"👤 {esc(booking['name'])}\n"
        f"📱 {esc(booking['phone'])}",
        confirm_kb(),
    )
    return CONFIRMING


async def on_edit_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    state = _load_booking(context, "date", "time")
    if state is None:
        return await _session_expired(update, context)
    state[0].pop("name", None)
    state[0].pop("phone", None)
    return await _ask_name(update, context)


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    state = _load_booking(context, "date", "time", "name", "phone")
    if state is None:
        return await _session_expired(update, context)
    booking, service = state
    db, settings = get_db(context), get_settings(context)
    user = update.effective_user
    now = now_local(settings.tz)

    if db.count_client_upcoming(user.id, now) >= settings.max_active_per_client:
        _drop_booking(context)
        await safe_edit(
            query,
            f"Достигнут лимит предстоящих записей: {settings.max_active_per_client}. "
            "Отмените одну из них в разделе «📋 Мои записи».",
        )
        return END

    start_dt = _start_of(booking)
    appointment_id = None
    # Двойная защита от «гонки»: сначала сверяем с актуальным расписанием,
    # а create_appointment() ещё раз проверяет занятость внутри транзакции.
    if start_dt in slots_for_day(db, settings, service.duration_min, start_dt.date(), now):
        appointment_id = db.create_appointment(
            client_id=user.id,
            name=booking["name"],
            phone=booking["phone"],
            username=user.username,
            service=service,
            start=start_dt,
            now=now,
            reminder_hours=settings.reminder_hours,
        )
    if appointment_id is None:
        return await _show_times(
            update, context, notice="⚠️ Это время только что заняли. Выберите другое.\n\n"
        )

    appointment = db.get_appointment(appointment_id)
    _drop_booking(context)

    await safe_edit(
        query,
        "✅ <b>Вы записаны!</b>\n\n"
        f"💅 {esc(service.name)}\n"
        f"📅 {fmt_date_full(appointment.start.date())}\n"
        f"🕒 {fmt_range(appointment.start, appointment.end)}\n"
        f"📍 {esc(settings.salon_address)}\n\n"
        f"Номер записи: №{appointment.id}\n"
        f"За {settings.reminder_hours} ч до визита я пришлю напоминание.\n"
        "Посмотреть или отменить запись можно в разделе «📋 Мои записи».",
    )
    await notify_admins(
        context,
        f"🆕 <b>Новая запись №{appointment.id}</b>\n\n"
        f"📅 {fmt_date(appointment.start.date())}, {fmt_range(appointment.start, appointment.end)}\n"
        f"💅 {esc(service.name)} — {fmt_price(service.price, settings.currency)}\n"
        f"👤 {client_link(user.id, appointment.client_name, user.username)}\n"
        f"📱 {esc(appointment.client_phone)}",
    )
    return END


# ---------------------------------------------------------- «назад», отмена, выход

async def back_to_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _show_services(update, context)


async def back_to_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _show_dates(update, context)


async def back_to_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _show_times(update, context)


async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена записи на любом шаге: inline-кнопка, /cancel или кнопка «❌ Отмена»."""
    _drop_booking(context)
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit(
            update.callback_query,
            "Запись отменена. Когда захотите — нажмите «💅 Записаться».",
        )
    else:
        await update.effective_message.reply_text("Запись отменена.", reply_markup=main_menu())
    return END


async def start_in_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/start посреди записи: сбрасываем черновик и показываем главное меню."""
    _drop_booking(context)
    await start(update, context)
    return END


async def menu_during_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Нажата другая кнопка меню посреди записи — прерываем запись и открываем нужный раздел."""
    _drop_booking(context)
    text = update.effective_message.text
    if text == BTN_MY:
        await my_appointments(update, context)
    elif text == BTN_PRICE:
        await show_price(update, context)
    elif text == BTN_CONTACTS:
        await show_contacts(update, context)
    return END


def build_booking_conversation(settings: Settings) -> ConversationHandler:
    # Обычный текст, который НЕ является командой и НЕ кнопкой меню (имя / телефон).
    text_input = (
        filters.TEXT & ~filters.COMMAND & ~filters.Text(MENU_BUTTONS + [BTN_CANCEL])
    )
    return ConversationHandler(
        entry_points=[
            CommandHandler("book", book_start),
            MessageHandler(filters.Text([BTN_BOOK]), book_start),
        ],
        states={
            CHOOSING_SERVICE: [
                CallbackQueryHandler(on_service, pattern=r"^svc:\d+$"),
            ],
            CHOOSING_DATE: [
                CallbackQueryHandler(on_date, pattern=r"^date:\d{4}-\d{2}-\d{2}$"),
                CallbackQueryHandler(back_to_service, pattern=r"^back:service$"),
            ],
            CHOOSING_TIME: [
                CallbackQueryHandler(on_time, pattern=r"^time:\d{2}:\d{2}$"),
                CallbackQueryHandler(back_to_date, pattern=r"^back:date$"),
            ],
            ASKING_NAME: [
                CallbackQueryHandler(back_to_time, pattern=r"^back:time$"),
                MessageHandler(text_input, on_name),
            ],
            ASKING_PHONE: [
                MessageHandler(filters.CONTACT, on_contact),
                MessageHandler(text_input, on_phone_text),
            ],
            CONFIRMING: [
                CallbackQueryHandler(on_confirm, pattern=r"^confirm$"),
                CallbackQueryHandler(on_edit_contact, pattern=r"^edit_contact$"),
                CallbackQueryHandler(back_to_time, pattern=r"^back:time$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_booking, pattern=r"^cancel$"),
            CommandHandler("cancel", cancel_booking),
            MessageHandler(filters.Text([BTN_CANCEL]), cancel_booking),
            CommandHandler("start", start_in_conversation),
            MessageHandler(filters.Text([BTN_MY, BTN_PRICE, BTN_CONTACTS]), menu_during_booking),
        ],
        allow_reentry=True,  # кнопка «Записаться» перезапускает диалог в любой момент
        conversation_timeout=settings.conversation_timeout_sec,
    )


# ============================================================ «Мои записи» и отмена

def _my_view(
    db: Database, user_id: int, now: datetime
) -> tuple[str, InlineKeyboardMarkup | None]:
    appointments = db.get_client_upcoming(user_id, now)
    if not appointments:
        return (
            "У вас нет предстоящих записей.\nНажмите «💅 Записаться», чтобы выбрать время.",
            None,
        )
    lines = ["📋 <b>Ваши записи</b>\n"]
    rows = []
    for a in appointments:
        lines.append(
            f"<b>№{a.id}</b> · {fmt_date(a.start.date())}, {fmt_range(a.start, a.end)}\n"
            f"💅 {esc(a.service_name)}\n"
        )
        rows.append(
            [Btn(f"❌ Отменить №{a.id} ({a.start:%d.%m %H:%M})", callback_data=f"mycancel:{a.id}")]
        )
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def my_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, settings = get_db(context), get_settings(context)
    text, markup = _my_view(db, update.effective_user.id, now_local(settings.tz))
    await update.effective_message.reply_text(text, reply_markup=markup or main_menu())


def _check_cancellable(
    db: Database, settings: Settings, appointment_id: int, user_id: int, now: datetime
) -> tuple[Appointment | None, str | None]:
    """Можно ли клиенту отменить эту запись? Возвращает (запись, None) или (None, текст_ошибки)."""
    appointment = db.get_appointment(appointment_id)
    if (
        appointment is None
        or appointment.client_id != user_id  # чужие записи отменять нельзя
        or appointment.status != "booked"
        or appointment.end <= now
    ):
        return None, "Запись не найдена или уже отменена."
    if appointment.start - now < timedelta(hours=settings.min_cancel_hours):
        return None, (
            f"Отменить запись можно не позднее чем за {settings.min_cancel_hours} ч до визита. "
            f"Пожалуйста, позвоните мастеру: {settings.salon_phone}"
        )
    return appointment, None


async def ask_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db, settings = get_db(context), get_settings(context)
    appointment_id = int(query.data.split(":", 1)[1])

    appointment, error = _check_cancellable(
        db, settings, appointment_id, update.effective_user.id, now_local(settings.tz)
    )
    if appointment is None:
        await query.answer(error, show_alert=True)
        return

    await query.answer()
    await safe_edit(
        query,
        f"Отменить запись №{appointment.id}?\n\n"
        f"📅 {fmt_date(appointment.start.date())}, {fmt_range(appointment.start, appointment.end)}\n"
        f"💅 {esc(appointment.service_name)}",
        InlineKeyboardMarkup(
            [[
                Btn("✅ Да, отменить", callback_data=f"mycancel_yes:{appointment.id}"),
                Btn("↩️ Нет", callback_data="mycancel_no"),
            ]]
        ),
    )


async def do_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db, settings = get_db(context), get_settings(context)
    user = update.effective_user
    now = now_local(settings.tz)
    appointment_id = int(query.data.split(":", 1)[1])

    appointment, error = _check_cancellable(db, settings, appointment_id, user.id, now)
    if appointment is None or not db.cancel_appointment(appointment.id, "client", now):
        await query.answer(error or "Запись уже отменена.", show_alert=True)
        return

    await query.answer("Запись отменена")
    await safe_edit(
        query,
        f"✅ Запись №{appointment.id} отменена.\nБудем рады видеть вас снова — нажмите «💅 Записаться».",
    )
    await notify_admins(
        context,
        f"❌ <b>Клиент отменил запись №{appointment.id}</b>\n\n"
        f"📅 {fmt_date(appointment.start.date())}, {fmt_range(appointment.start, appointment.end)}\n"
        f"💅 {esc(appointment.service_name)}\n"
        f"👤 {client_link(user.id, appointment.client_name, user.username)}\n"
        f"📱 {esc(appointment.client_phone)}",
    )


async def cancel_declined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«Нет» в подтверждении отмены — показываем список записей заново."""
    query = update.callback_query
    await query.answer()
    db, settings = get_db(context), get_settings(context)
    text, markup = _my_view(db, update.effective_user.id, now_local(settings.tz))
    await safe_edit(query, text, markup)
