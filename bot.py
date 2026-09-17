import asyncio
import logging

from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

import backup
import members
import scheduler
import texts
from config import BACKUP_CHAT_ID, DB_PATH
from handlers import build_router
from handlers.tracking import TrackingMiddleware
from import_history import load_history_if_exists
from loader import bot, db, dp
from utils import recent_logs_handler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger().addHandler(recent_logs_handler)
logger = logging.getLogger(__name__)

_background_tasks: list[asyncio.Task] = []

GROUP_COMMANDS = [
    BotCommand(command="tajik", description="🇹🇯 Таджик дня"),
    BotCommand(command="tadjikistan", description="Случайный таджикистан-ивент"),
    BotCommand(command="d20", description="Бросок d20 — проверка на успех"),
    BotCommand(command="dnd", description="🐉 Случайный ивент из D&D"),
    BotCommand(command="who", description="🔮 Кто из нас...? Вопрос пиши после команды"),
    BotCommand(command="casino", description="🎰 Казино «Золотой казан»: все игры и правила"),
    BotCommand(command="roulette", description="🎡 Рулетка: /roulette 100 красное"),
    BotCommand(command="rr", description="🔫 Русская рулетка"),
    BotCommand(command="slots", description="🎰 Однорукий бандит: /slots 100"),
    BotCommand(command="coin", description="🪙 Монетка: /coin 100 орёл"),
    BotCommand(command="duel", description="⚔️ Дуэль на таджикоины (ответом на сообщение)"),
    BotCommand(command="balance", description="💰 Мои таджикоины"),
    BotCommand(command="bonus", description="🎁 Бонус таджикоинов раз в день"),
    BotCommand(command="forbes", description="🏦 Самые богатые"),
    BotCommand(command="give", description="🤝 Перевести таджикоины (ответом на сообщение)"),
    BotCommand(command="stats", description="📊 Статистика беседы или участника (reply / @username / я)"),
    BotCommand(command="tajiktop", description="Зал славы таджиков дня"),
    BotCommand(command="info", description="Анкета (reply или @username)"),
    BotCommand(command="anketa", description="Заполнить / обновить анкету (или /анкета)"),
    BotCommand(command="nick", description="Установить никнейм (или /ник)"),
    BotCommand(command="brak", description="Сделать предложение брака"),
    BotCommand(command="razvod", description="Развод (reply или @username)"),
    BotCommand(command="families", description="Все семьи беседы"),
    BotCommand(command="orgy", description="Оргия-опрос (или /оргия)"),
    BotCommand(command="rules", description="Правила беседы (или /правила)"),
]


async def setup_commands():
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
    start = BotCommand(command="start", description="Кто я такой")
    await bot.set_my_commands([start], scope=BotCommandScopeAllPrivateChats())
    if BACKUP_CHAT_ID is not None:
        await bot.set_my_commands(
            [
                start,
                BotCommand(command="backup", description="Сделать бэкап базы сейчас"),
                BotCommand(command="restore", description="Ответом на файл бэкапа: откатить базу"),
            ],
            scope=BotCommandScopeChat(chat_id=BACKUP_CHAT_ID),
        )


async def on_startup():
    try:
        await bot.me()  # кэшируем юзернейм бота: по нему сглыпа понимает, что к ней обращаются
        await setup_commands()
    except Exception as e:
        logger.warning("Не получилось обновить меню команд: %s", e)
    await scheduler.announce(texts.STARTUP_TEXT)
    _background_tasks.append(asyncio.create_task(backup.backup_loop()))
    _background_tasks.append(asyncio.create_task(members.sync_members()))
    _background_tasks.append(asyncio.create_task(scheduler.spam_day_loop()))


async def on_shutdown():
    for task in _background_tasks:
        task.cancel()
    await backup.final_backup()
    await scheduler.announce(texts.SHUTDOWN_TEXT)
    db.close()


async def main():
    logger.info("База: %s", DB_PATH)
    await backup.prepare_database()
    load_history_if_exists(db)
    granted = db.grant_once("welcome_1000", 1000)  # разовая раздача при запуске казино
    if granted:
        logger.info("Выдал по 1000 таджикоинов: %s участникам", granted)

    dp.message.outer_middleware(TrackingMiddleware())
    dp.callback_query.outer_middleware(TrackingMiddleware())
    dp.include_router(build_router())
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
