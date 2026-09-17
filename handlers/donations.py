"""Донаты: скрытая /donate в личке и подтверждение переводов хозяином."""

from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import donations
import members
import texts
from casino_engine import GameError, signed
from config import OWNER_ID
from loader import db

router = Router(name="donations")


@router.message(Command("donate", "донат"))
async def cmd_donate(message: Message, command: CommandObject):
    info = donations.payment_info()
    if info is None:
        return  # донаты выключены — команды как будто нет
    if message.chat.type != "private":
        await message.reply(texts.DONATION_GO_PRIVATE)
        return
    chat_id, user_id = db.get_main_chat(), message.from_user.id
    if chat_id is None:
        return
    # номер для перевода показываем только своим — участникам беседы
    if db.member_status(chat_id, user_id) is not True and await members.check_member(chat_id, user_id) is not True:
        return
    args = (command.args or "").split(maxsplit=1)
    if not args or not args[0].isdigit():
        bank = f" ({escape(info['bank'], quote=False)})" if info["bank"] else ""
        await message.answer(texts.DONATION_INFO.format(phone=info["pretty"], bank=bank, rate=donations.COINS_PER_RUBLE))
        return
    amount = int(args[0])
    try:
        await donations.request(chat_id, message.from_user.id, amount, args[1] if len(args) > 1 else "")
    except GameError as e:
        await message.answer(str(e))
        return
    await message.answer(texts.DONATION_THANKS.format(coins=signed(donations.reward(amount))))


@router.callback_query(F.data.startswith("don:"), F.from_user.id == OWNER_ID)
async def on_owner_decision(callback: CallbackQuery):
    _, action, donation_id = callback.data.split(":")
    result = await donations.decide(int(donation_id), approve=action == "ok")
    await callback.answer(result)
    if isinstance(callback.message, Message):
        with suppress(TelegramAPIError):
            await callback.message.edit_text(f"{callback.message.html_text}\n\n<b>{result}</b>")


@router.message(Command("donated"), F.from_user.id == OWNER_ID, F.chat.type == "private")
async def cmd_donated(message: Message, command: CommandObject):
    """/donated @username 500 сообщение — отметить донат, который пришёл мимо формы."""
    args = (command.args or "").split(maxsplit=2)
    if len(args) < 2 or not args[1].isdigit():
        await message.answer(
            "💸 <code>/donated @username 500 сообщение</code> — объявить донат в беседе "
            f"и начислить таджикоины (×{donations.COINS_PER_RUBLE})"
        )
        return
    target_id, raw = members.resolve_target(message, args[0])
    chat_id = db.get_main_chat()
    if target_id in (None, -1) or chat_id is None:
        await message.answer(f"Не знаю такого человека: <code>{escape(raw or args[0])}</code>")
        return
    amount = int(args[1])
    await donations.add_confirmed(chat_id, target_id, amount, args[2] if len(args) > 2 else "")
    await message.answer(f"Объявил в беседе ❤️ Начислил {signed(donations.reward(amount))}")
