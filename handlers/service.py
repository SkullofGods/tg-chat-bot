"""Служебное: /start в личке, id чата, ручные бэкапы и откат базы, фразы для «Кто это сказал?»."""

import json
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import backup
import texts
from config import BACKUP_CHAT_ID, LORE_GIF_KEY, OWNER_ID
from loader import bot, db
from textstats import count_with_word

logger = logging.getLogger(__name__)
router = Router(name="service")

QUOTES_MAX_BYTES = 2_000_000
QUOTE_MAX_LENGTH = 1000


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


def parse_quotes(raw: bytes, default_chat: int | None) -> tuple[int, list[tuple[int, str]]]:
    """Файл с фразами: {"chat_id": id беседы, "phrases": [[id автора, "фраза"], …]}. ValueError — не он."""
    data = json.loads(raw.decode("utf-8-sig"))
    chat_id = data.get("chat_id", default_chat) if isinstance(data, dict) else None
    phrases = data.get("phrases") if isinstance(data, dict) else None
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or not isinstance(phrases, list) or not phrases:
        raise ValueError("нет беседы или фраз")
    rows = []
    for item in phrases:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], int) and not isinstance(item[0], bool)
                and isinstance(item[1], str) and 0 < len(item[1].strip()) <= QUOTE_MAX_LENGTH):
            raise ValueError(f"кривая фраза: {str(item)[:60]}")
        rows.append((item[0], item[1].strip()))
    return chat_id, rows


@router.message(F.chat.type == "private", F.from_user.id == OWNER_ID, F.document.file_name.endswith(".json"))
async def load_quotes(message: Message):
    """Фразы для «Кто это сказал?». Хозяин проверяет их сам и присылает файлом: в публичный репозиторий
    переписка беседы не едет, а в базе они живут вместе с бэкапами."""
    document = message.document
    try:
        if document.file_size and document.file_size > QUOTES_MAX_BYTES:
            raise ValueError("слишком большой файл")
        chat_id, rows = parse_quotes((await bot.download(document)).read(), db.get_main_chat())
    except (ValueError, UnicodeDecodeError) as e:
        logger.info("Файл с фразами не принят: %s", e)
        await message.reply(texts.QUOTES_BAD)
        return
    me = await bot.me()
    db.upsert_user(me.id, me.username or "", me.full_name, True)   # фразы самого бота тоже в игре
    db.replace_quote_phrases(chat_id, rows)
    live, authors = db.quote_stats(chat_id)
    phrase_forms = ("фраза", "фразы", "фраз")
    await message.reply(texts.QUOTES_LOADED.format(
        total=count_with_word(len(rows), phrase_forms), live=count_with_word(live, phrase_forms),
        authors=count_with_word(authors, ("автора", "авторов", "авторов"))))


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
