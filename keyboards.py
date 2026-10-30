"""Клавиатуры: меню внизу экрана (reply) и кнопки под сообщениями (inline)."""
from __future__ import annotations

from datetime import date, time
from typing import Sequence

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from db import Service
from utils import WEEKDAYS_SHORT, fmt_duration, fmt_price

# Тексты кнопок главного меню. Хендлеры ловят сообщения именно с этими текстами.
BTN_BOOK = "💅 Записаться"
BTN_MY = "📋 Мои записи"
BTN_PRICE = "💰 Прайс"
BTN_CONTACTS = "ℹ️ Контакты"
BTN_CANCEL = "❌ Отмена"
MENU_BUTTONS = [BTN_BOOK, BTN_MY, BTN_PRICE, BTN_CONTACTS]


def chunks(items: Sequence, size: int) -> list[list]:
    """Режет список на строки по size элементов (для сетки кнопок)."""
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


# ------------------------------------------------------------------ reply-клавиатуры

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_BOOK)],
            [KeyboardButton(BTN_MY), KeyboardButton(BTN_PRICE)],
            [KeyboardButton(BTN_CONTACTS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие…",
    )


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 Отправить мой номер", request_contact=True)],
            [KeyboardButton(BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Или напишите номер: +7 900 000-00-00",
    )


# ------------------------------------------------------------------ inline-клавиатуры

def cancel_button() -> Btn:
    return Btn("❌ Отмена", callback_data="cancel")


def services_kb(services: Sequence[Service], currency: str) -> InlineKeyboardMarkup:
    rows = [
        [Btn(
            f"{s.name} · {fmt_duration(s.duration_min)} · {fmt_price(s.price, currency)}",
            callback_data=f"svc:{s.id}",
        )]
        for s in services
    ]
    rows.append([cancel_button()])
    return InlineKeyboardMarkup(rows)


def dates_kb(days: Sequence[date]) -> InlineKeyboardMarkup:
    buttons = [
        Btn(f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}", callback_data=f"date:{d.isoformat()}")
        for d in days
    ]
    rows = chunks(buttons, 3)
    rows.append([Btn("◀️ Назад", callback_data="back:service"), cancel_button()])
    return InlineKeyboardMarkup(rows)


def times_kb(slots: Sequence[time]) -> InlineKeyboardMarkup:
    buttons = [Btn(f"{t:%H:%M}", callback_data=f"time:{t:%H:%M}") for t in slots]
    rows = chunks(buttons, 4)
    rows.append([Btn("◀️ Назад", callback_data="back:date"), cancel_button()])
    return InlineKeyboardMarkup(rows)


def back_cancel_kb(back_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Btn("◀️ Назад", callback_data=back_data), cancel_button()]])


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [Btn("✅ Подтвердить запись", callback_data="confirm")],
            [Btn("✏️ Изменить имя и телефон", callback_data="edit_contact")],
            [Btn("◀️ Назад", callback_data="back:time"), cancel_button()],
        ]
    )
