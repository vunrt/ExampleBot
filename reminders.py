"""Напоминания клиентам о предстоящей записи.

Вместо отдельной задачи на каждую запись используется одна повторяющаяся задача:
раз в минуту она спрашивает базу, о каких визитах пора напомнить. Так проще и надёжнее:
  * ничего не нужно пересоздавать после перезапуска бота;
  * отменённые записи просто не попадают в выборку;
  * если бот был выключен, напоминание уйдёт, как только он запустится (пока визит не начался).
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from handlers.common import get_db, get_settings
from utils import esc, fmt_range, fmt_when, now_local

logger = logging.getLogger(__name__)


async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    db, settings = get_db(context), get_settings(context)
    now = now_local(settings.tz)

    for appointment in db.get_due_reminders(now, settings.reminder_hours):
        text = (
            "⏰ <b>Напоминание о записи</b>\n\n"
            f"Ждём вас {fmt_when(appointment.start, now)}, {fmt_range(appointment.start, appointment.end)}\n"
            f"💅 {esc(appointment.service_name)}\n"
            f"📍 {esc(settings.salon_address)}\n\n"
            "Если планы изменились — пожалуйста, отмените запись заранее."
        )
        markup = InlineKeyboardMarkup(
            [[Btn("❌ Отменить запись", callback_data=f"mycancel:{appointment.id}")]]
        )
        try:
            await context.bot.send_message(appointment.client_id, text, reply_markup=markup)
        except Forbidden:
            # Клиент заблокировал бота — повторять бессмысленно.
            logger.info("Клиент %s заблокировал бота, напоминание пропущено", appointment.client_id)
        except TelegramError:
            # Временная проблема (сеть, прокси): не помечаем отправленным — повторим через минуту.
            logger.warning(
                "Не удалось отправить напоминание по записи %s, повторю позже", appointment.id, exc_info=True
            )
            continue
        db.mark_reminded(appointment.id)
