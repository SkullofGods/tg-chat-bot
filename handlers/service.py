"""Служебное: /start в личке, id чата, ручные бэкапы и откат базы."""

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import backup
import texts
from config import BACKUP_CHAT_ID, LORE_GIF_KEY, OWNER_ID
from loader import db

router = Router(name="service")


def _in_backup_chat(message: Message) -> bool:
    return BACKUP_CHAT_ID is not None and message.chat.id == BACKUP_CHAT_ID


@router.message(CommandStart(), F.chat.type == "private")
@router.message(Command("help"), F.chat.type == "private")
async def cmd_start(message: Message):
    user_id = message.from_user.id
    text = texts.PRIVATE_START.format(user_id=user_id)
    if _in_backup_chat(message):
        text += texts.BACKUP_HERE_HELP
    elif BACKUP_CHAT_ID is None:
        text += texts.BACKUP_SETUP_HELP.format(user_id=user_id)
    if user_id == OWNER_ID:
        text += texts.OWNER_HELP
    await message.answer(text)


@router.message(F.chat.type == "private", F.from_user.id == OWNER_ID, F.animation)
async def save_lore_gif(message: Message):
    """Та самая гифка. Присылаем её боту в личку один раз — дальше он шлёт её сам."""
    db.set_meta(LORE_GIF_KEY, message.animation.file_id)
    await message.reply("🥛 Запомнил. Теперь она иногда прилетает в беседу — там, где по лору положено.")


@router.message(Command("chatid"))
async def cmd_chatid(message: Message):
    await message.reply(f"🆔 ID этого чата: <code>{message.chat.id}</code>")


@router.message(Command("backup"), _in_backup_chat)
async def cmd_backup(message: Message):
    status = await message.answer("🗄 Делаю бэкап...")
    try:
        result = await backup.make_backup("вручную")
    except Exception as e:
        await status.edit_text(f"❌ Не получилось: <code>{escape(str(e))}</code>")
        return
    await status.edit_text(f"✅ Бэкап готов и закреплён: {result}")


@router.message(Command("restore"), _in_backup_chat)
async def cmd_restore(message: Message):
    reply = message.reply_to_message
    if not reply or not reply.document:
        await message.answer("Ответь этой командой на сообщение с файлом бэкапа.")
        return
    status = await message.answer("♻️ Восстанавливаю базу...")
    try:
        result = await backup.restore_from_document(reply.document)
    except Exception as e:
        await status.edit_text(f"❌ Не получилось: <code>{escape(str(e))}</code>\nБаза осталась как была.")
        return
    await status.edit_text(
        f"✅ База восстановлена ({result}). Прошлая версия сохранена рядом с базой как <code>bot.db.before-restore</code>.\n"
        "Минут через пять закреплю свежий бэкап."
    )
