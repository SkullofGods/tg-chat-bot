"""Объявления во все беседы и фоновая проверка спам-дня."""

import asyncio
import logging
import random
from datetime import datetime

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import limits
import texts
from config import LOCAL_TZ
from loader import bot, db

logger = logging.getLogger(__name__)


async def announce(text: str):
    for chat_id in db.get_known_chats():
        try:
            await bot.send_message(chat_id, text)
        except TelegramForbiddenError:
            db.forget_chat(chat_id)  # бота выгнали из беседы
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                db.forget_chat(chat_id)
        except Exception as e:
            logger.warning("Не смог написать в чат %s: %s", chat_id, e)


async def announce_spam_day_if_needed() -> bool:
    now = datetime.now(LOCAL_TZ)
    key = f"spam_day_announced:{now.date().isoformat()}"
    if not limits.spam_day_active(now) or db.get_meta(key):
        return False
    db.set_meta(key, "1")  # до отправки: если что-то упадёт на полпути, не объявим дважды
    await announce(random.choice(texts.SPAM_DAY_ANNOUNCEMENTS))
    return True


async def spam_day_loop():
    while True:
        try:
            await announce_spam_day_if_needed()
        except Exception:
            logger.exception("Проверка спам-дня не удалась")
        await asyncio.sleep(60)
