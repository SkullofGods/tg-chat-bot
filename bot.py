import asyncio
import logging

from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonWebApp,
    WebAppInfo,
)

import backup
import anniversaries
import casino_bank
import casino_crash
import casino_tables
import multiplayer
import members
import scheduler
import texts
import webserver
from config import BACKUP_CHAT_ID, DB_PATH, WEBAPP_URL
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
    BotCommand(command="casino", description="🎰 Казино «Золотой казан»"),
    BotCommand(command="d20", description="Бросок d20 — проверка на успех"),
    BotCommand(command="dnd", description="🐉 Случайный ивент из D&D"),
    BotCommand(command="who", description="🔮 Кто из нас...? Вопрос пиши после команды"),
    BotCommand(command="stats", description="📊 Статистика беседы или участника (reply / @username / я)"),
    BotCommand(command="forbes", description="🏦 Самые богатые"),
    BotCommand(command="casinostat", description="🎰 Статистика в казино (reply / @username)"),
    BotCommand(command="give", description="🤝 Перевести таджикоины (ответом на сообщение)"),
    BotCommand(command="duel", description="⚔️ Дуэль на таджикоины (ответом на сообщение)"),
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
    private = [start, BotCommand(command="casino", description="🎰 Открыть казино")]
    await bot.set_my_commands(private, scope=BotCommandScopeAllPrivateChats())
    if BACKUP_CHAT_ID is not None:
        await bot.set_my_commands(
            private + [
                BotCommand(command="backup", description="Сделать бэкап базы сейчас"),
                BotCommand(command="restore", description="Ответом на файл бэкапа: откатить базу"),
            ],
            scope=BotCommandScopeChat(chat_id=BACKUP_CHAT_ID),
        )
    if WEBAPP_URL:  # в личке с ботом кнопка меню сразу открывает казино
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Казино", web_app=WebAppInfo(url=f"{WEBAPP_URL}/"))
        )


async def on_startup():
    try:
        anniversaries.seed()   # когда кто пришёл в беседу — из экспорта истории; уже известное не трогает
    except Exception:
        logger.exception("Не смог записать даты прихода в беседу")
    try:
        await bot.me()  # кэшируем юзернейм бота: по нему сглыпа понимает, что к ней обращаются
        await setup_commands()
    except Exception as e:
        logger.warning("Не получилось обновить меню команд: %s", e)
    try:
        await webserver.start()
    except Exception:
        logger.exception("Мини-приложение казино не запустилось")
    await scheduler.announce(texts.STARTUP_TEXT)
    _background_tasks.append(asyncio.create_task(backup.backup_loop()))
    _background_tasks.append(asyncio.create_task(members.sync_members()))
    _background_tasks.append(asyncio.create_task(scheduler.spam_day_loop()))
    _background_tasks.append(asyncio.create_task(scheduler.arcade_prize_loop()))
    _background_tasks.append(asyncio.create_task(casino_tables.loop()))  # раунды рулетки и скачек со ставками
    _background_tasks.append(asyncio.create_task(casino_crash.loop()))   # рейсы Толян Эйр со ставками
    _background_tasks.append(asyncio.create_task(scheduler.anniversary_loop()))
    _background_tasks.append(asyncio.create_task(casino_bank.loop()))
    _background_tasks.append(asyncio.create_task(multiplayer.loop()))    # столы мультиплеера    # выплата вкладов, у которых вышел срок


async def on_shutdown():
    for task in _background_tasks:
        task.cancel()
    await webserver.stop()
    await backup.final_backup()
    await scheduler.announce(texts.SHUTDOWN_TEXT)
    db.close()


def grant_by_nickname_once(key: str, nickname: str, amount: int):
    """Разовое начисление одному человеку в главной беседе. Кого — определяем по нику в боте."""
    if db.get_meta(f"grant:{key}") is not None:
        return
    chat_id = db.get_main_chat()
    user_ids = db.find_users_by_nickname(nickname)
    if chat_id is None or len(user_ids) != 1:
        logger.warning("Разовое начисление %s не сделано: беседа %s, людей с ником «%s»: %s",
                       key, chat_id, nickname, len(user_ids))
        return
    balance = db.grant_user_once(key, chat_id, user_ids[0], amount)
    if balance is not None:
        logger.info("Начислил «%s» %s таджикоинов, баланс: %s", nickname, amount, balance)


async def main():
    logger.info("База: %s", DB_PATH)
    await backup.prepare_database()
    load_history_if_exists(db)
    granted = db.grant_once("welcome_1000", 1000)  # разовая раздача при запуске казино
    if granted:
        logger.info("Выдал по 1000 таджикоинов: %s участникам", granted)
    grant_by_nickname_once("tajik_1900", "таджик", 1900)  # по просьбе хозяина, 17.09.2026

    dp.message.outer_middleware(TrackingMiddleware())
    dp.callback_query.outer_middleware(TrackingMiddleware())
    dp.include_router(build_router())
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
