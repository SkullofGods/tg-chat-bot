import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

import members

logger = logging.getLogger(__name__)


class TrackingMiddleware(BaseMiddleware):
    """До любых обработчиков запоминает чат, автора и то, что автор сейчас в чате."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, Message):
                _track_message(event)
            elif isinstance(event, CallbackQuery):
                members.remember_user(event.from_user)
        except Exception:
            logger.exception("Не получилось запомнить отправителя")
        return await handler(event, data)


def _track_message(message: Message):
    user = message.from_user
    if not user or message.sender_chat:  # анонимные админы и посты каналов
        return
    members.remember_user(user)
    if members.is_group(message.chat):
        members.remember_chat(message.chat.id)
        if not user.is_bot:
            members.remember_member(message.chat.id, user.id)
