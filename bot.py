"""Точка входа: python bot.py

Здесь собирается приложение: настройки, база данных, хендлеры и фоновая задача напоминаний.
"""
from __future__ import annotations

import logging
import sys
import warnings

from telegram import BotCommand, BotCommandScopeChat
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest
from telegram.warnings import PTBUserWarning

from config import DEFAULT_SERVICES, Settings, load_settings
from db import Database
from handlers import admin, client, common
from keyboards import BTN_CONTACTS, BTN_MY, BTN_PRICE
from reminders import send_reminders

logger = logging.getLogger(__name__)

CLIENT_COMMANDS = [
    BotCommand("start", "Главное меню"),
    BotCommand("book", "Записаться на маникюр"),
    BotCommand("my", "Мои записи"),
    BotCommand("price", "Услуги и цены"),
    BotCommand("contacts", "Контакты и часы работы"),
    BotCommand("cancel", "Прервать запись"),
]
ADMIN_COMMANDS = [
    BotCommand("admin", "Панель мастера"),
    BotCommand("addservice", "Добавить услугу"),
    BotCommand("delservice", "Скрыть услугу"),
]


def register_handlers(app: Application, settings: Settings) -> None:
    """Порядок важен: Telegram-библиотека отдаёт апдейт первому подходящему хендлеру."""

    # 1. Диалог записи — первым: пока клиент внутри диалога, он забирает его сообщения.
    app.add_handler(client.build_booking_conversation(settings))

    # 2. Остальные возможности клиента.
    app.add_handler(CommandHandler("start", common.start))
    app.add_handler(CommandHandler("help", common.help_command))
    app.add_handler(CommandHandler("price", common.show_price))
    app.add_handler(CommandHandler("contacts", common.show_contacts))
    app.add_handler(CommandHandler("my", client.my_appointments))
    app.add_handler(CommandHandler("cancel", common.nothing_to_cancel))
    app.add_handler(MessageHandler(filters.Text([BTN_MY]), client.my_appointments))
    app.add_handler(MessageHandler(filters.Text([BTN_PRICE]), common.show_price))
    app.add_handler(MessageHandler(filters.Text([BTN_CONTACTS]), common.show_contacts))
    app.add_handler(CallbackQueryHandler(client.ask_cancel, pattern=r"^mycancel:\d+$"))
    app.add_handler(CallbackQueryHandler(client.do_cancel, pattern=r"^mycancel_yes:\d+$"))
    app.add_handler(CallbackQueryHandler(client.cancel_declined, pattern=r"^mycancel_no$"))

    # 3. Панель мастера (внутри хендлеров проверяется, что пользователь — мастер).
    app.add_handler(CommandHandler("admin", admin.admin_menu))
    app.add_handler(CommandHandler("addservice", admin.add_service_cmd))
    app.add_handler(CommandHandler("delservice", admin.del_service_cmd))
    app.add_handler(CallbackQueryHandler(admin.admin_callback, pattern=r"^adm:"))

    # 4. Всё, что не подошло выше. Эти хендлеры обязаны стоять последними.
    app.add_handler(MessageHandler(filters.TEXT, common.unknown_message))
    app.add_handler(CallbackQueryHandler(common.stale_callback))

    app.add_error_handler(common.on_error)


async def post_init(app: Application) -> None:
    """Выполняется один раз при старте: публикуем список команд в меню Telegram."""
    settings: Settings = app.bot_data["settings"]
    try:
        await app.bot.set_my_commands(CLIENT_COMMANDS)
        for admin_id in settings.admin_ids:
            await app.bot.set_my_commands(
                CLIENT_COMMANDS + ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id)
            )
    except TelegramError:
        # Не критично: бот работает и без списка команд (например, мастер ещё не нажимал /start).
        logger.warning("Не удалось установить список команд бота", exc_info=True)


def _configure_logging() -> None:
    """Логи в stdout с мгновенным сбросом.

    На хостинге (Docker) stdout буферизуется блоками: без flush записи висят
    в буфере и в панели управления ботом не видно, что происходит.
    """
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.flush = handler.stream.flush  # type: ignore[method-assign]


def build_request(settings: Settings) -> tuple[HTTPXRequest, HTTPXRequest]:
    """Создаёт HTTP-клиенты для обычных вызовов и для getUpdates.

    trust_env=False обязателен: иначе httpx сам подхватывает системный прокси
    из окружения или реестра Windows (например, socks4://127.0.0.1:10808 от
    VPN-клиента) и падает с "Unknown scheme for proxy URL". Соединение всегда
    выбираем явно: либо PROXY_URL, либо напрямую.
    """
    kwargs = {
        "connect_timeout": settings.request_timeout,
        "read_timeout": settings.request_timeout,
        "write_timeout": settings.request_timeout,
        "pool_timeout": settings.request_timeout,
        "httpx_kwargs": {"trust_env": False},
    }
    request = HTTPXRequest(proxy=settings.proxy, **kwargs)
    # getUpdates (long polling) держит соединение открытым дольше обычных вызовов.
    get_updates_request = HTTPXRequest(
        proxy=settings.proxy, connection_pool_size=1, **kwargs
    )
    return request, get_updates_request


def main() -> None:
    _configure_logging()
    # Библиотека httpx пишет в лог полные адреса запросов, а в них — токен бота. Приглушаем.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    settings = load_settings()

    db = Database(settings.db_path)
    db.init_schema()
    db.seed_services(DEFAULT_SERVICES)

    # Информационное предупреждение библиотеки про inline-кнопки внутри диалога
    # (per_message=False). Для нашего сценария всё работает как задумано.
    warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

    request, get_updates_request = build_request(settings)

    builder = (
        Application.builder()
        .token(settings.token)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(post_init)
    )
    if settings.proxy:
        logger.info("Bot uses proxy %s", settings.proxy)
    else:
        logger.info("Bot connects to Telegram directly (no proxy, environment ignored)")
    app = builder.build()

    if app.job_queue is None:
        raise SystemExit(
            'JobQueue is not installed (required for reminders): '
            'pip install "python-telegram-bot[job-queue]"'
        )

    # Общие объекты, доступные во всех хендлерах через context.bot_data.
    app.bot_data["settings"] = settings
    app.bot_data["db"] = db

    register_handlers(app, settings)
    app.job_queue.run_repeating(send_reminders, interval=60, first=10, name="reminders")

    logger.info("Salon: %s | timezone: %s", settings.salon_name, settings.tz)
    logger.info("Database: %s", settings.db_path)
    logger.info("Admins: %s", sorted(settings.admin_ids) or "not configured")
    logger.info("Bot is starting (long polling). Stop: Ctrl+C")
    # Принимаем только сообщения и нажатия кнопок. Заодно бот не получает
    # отредактированные сообщения (у них update.message пустой).
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
