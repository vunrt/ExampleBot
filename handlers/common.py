"""Общие хендлеры и помощники для клиентской и админской частей бота."""
from __future__ import annotations

import logging

from telegram import CallbackQuery, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import ContextTypes

from config import Settings
from db import Database
from keyboards import main_menu
from utils import esc, fmt_duration, fmt_price, format_schedule

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- помощники

def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.bot_data["settings"]


def client_link(client_id: int, name: str, username: str | None = None) -> str:
    """Имя клиента как ссылка на его профиль в Telegram (+ @username, если он есть). Для мастера."""
    link = f'<a href="tg://user?id={client_id}">{esc(name)}</a>'
    return link + (f" (@{esc(username)})" if username else "")


async def safe_edit(
    query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """edit_message_text, который не падает, если текст не изменился (двойной тап по кнопке)."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def send_or_edit(
    update: Update, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Если пользователь нажал inline-кнопку — правим то же сообщение, иначе отправляем новое."""
    if update.callback_query:
        await safe_edit(update.callback_query, text, reply_markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def notify_admins(
    context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Отправляет сообщение мастеру (всем из ADMIN_IDS). Ошибки доставки только логируем."""
    for admin_id in get_settings(context).admin_ids:
        try:
            await context.bot.send_message(admin_id, text, reply_markup=reply_markup)
        except TelegramError:
            logger.warning(
                "Не удалось отправить уведомление мастеру %s (он нажимал /start у бота?)",
                admin_id, exc_info=True,
            )


# --------------------------------------------------------------------- хендлеры

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings(context)
    name = esc(update.effective_user.first_name) if update.effective_user else ""
    await update.effective_message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"Это бот онлайн-записи на маникюр — <b>{esc(settings.salon_name)}</b>.\n"
        "Выберите действие в меню ниже.",
        reply_markup=main_menu(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "ℹ️ <b>Как пользоваться ботом</b>\n\n"
        "💅 «Записаться» — выберите услугу, дату и время.\n"
        "📋 «Мои записи» — посмотреть и отменить свои записи.\n"
        "💰 «Прайс» — услуги и цены.\n\n"
        "Команды: /start — главное меню, /cancel — прервать запись.",
        reply_markup=main_menu(),
    )


async def show_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, settings = get_db(context), get_settings(context)
    services = db.list_services()
    if not services:
        await update.effective_message.reply_text("Прайс пока пуст. Загляните позже 🙂")
        return
    lines = ["💰 <b>Услуги и цены</b>\n"]
    for s in services:
        lines.append(
            f"• {esc(s.name)} — {fmt_price(s.price, settings.currency)} ({fmt_duration(s.duration_min)})"
        )
    await update.effective_message.reply_text("\n".join(lines), reply_markup=main_menu())


async def show_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings(context)
    await update.effective_message.reply_text(
        f"ℹ️ <b>{esc(settings.salon_name)}</b>\n\n"
        f"📍 {esc(settings.salon_address)}\n"
        f"📞 {esc(settings.salon_phone)}\n\n"
        f"🕒 <b>Часы работы</b>\n{esc(format_schedule(settings.work_hours))}",
        reply_markup=main_menu(),
    )


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Не понял вас 🤔 Воспользуйтесь кнопками меню или командой /start.",
        reply_markup=main_menu(),
    )


async def nothing_to_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Сейчас нечего отменять.", reply_markup=main_menu())


async def stale_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка, которую никто не обрабатывает (например, после перезапуска бота)."""
    query = update.callback_query
    await query.answer(
        "Эта кнопка устарела. Откройте меню (/start) и начните заново.", show_alert=True
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Единое место для необработанных ошибок: пишем в лог и вежливо сообщаем пользователю."""
    error = context.error
    if isinstance(error, NetworkError):
        # Таймауты и обрывы связи (в том числе через прокси) — не повод пугать пользователя.
        logger.warning("Сетевая ошибка Telegram: %s", error)
        return

    logger.error("Необработанная ошибка в хендлере", exc_info=error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "😔 Что-то пошло не так. Попробуйте ещё раз или нажмите /start.",
            )
        except TelegramError:
            pass
