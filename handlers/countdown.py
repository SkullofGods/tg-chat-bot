"""Команды таймера хозяина (только личка): настройка отсчёта и финального поста.
В беседах из этих команд доступна только /cd_here — бот её мгновенно стирает и
запоминает, откуда была команда, чтобы таймер постить именно туда."""

import json
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import countdown
from config import LOCAL_TZ, OWNER_ID
from loader import bot, db

router = Router(name="countdown")
router.message.filter(F.from_user.id == OWNER_ID)


def _private(message: Message) -> bool:
    return message.chat.type == "private"


@router.message(Command("countdown"), F.chat.type == "private")
async def cmd_status(message: Message):
    await message.answer(countdown.status_text())


@router.message(Command("countdown_time"), F.chat.type == "private")
async def cmd_time(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    try:
        dt = datetime.fromisoformat(raw)
        dt.astimezone(LOCAL_TZ)
    except Exception:
        await message.answer("Формат: /countdown_time 2026-10-03T18:00 (время местное)")
        return
    c = countdown.cfg()
    c["target"] = raw
    countdown.save_cfg(c)
    db.delete_meta(countdown.DONE_KEY)
    db.delete_meta(countdown.LAST_KEY)
    await message.answer(f"🎯 Цель: {countdown.target_dt(c):%d.%m.%Y %H:%M}\n\n" + countdown.status_text())


@router.message(Command("countdown_chat"), F.chat.type == "private")
async def cmd_chat(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("Формат: /countdown_chat -1001234567890\nИли напиши /cd_here в самой беседе.")
        return
    c = countdown.cfg()
    c["chat_id"] = int(raw)
    countdown.save_cfg(c)
    await message.answer("💬 Беседа запомнена.\n\n" + countdown.status_text())


@router.message(Command("cd_here"), F.from_user.id == OWNER_ID)
async def cmd_here(message: Message):
    await bot.delete_message(message.chat.id, message.message_id)
    c = countdown.cfg()
    c["chat_id"] = message.chat.id
    countdown.save_cfg(c)
    await message.answer("💬 Беседа для таймера запомнена.\n\n" + countdown.status_text())


@router.message(Command("countdown_every"), F.chat.type == "private")
async def cmd_every(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("Формат: /countdown_every 180 (минуты между постами отсчёта)")
        return
    c = countdown.cfg()
    c["interval_min"] = int(raw)
    countdown.save_cfg(c)
    await message.answer("🔁 Готово.\n\n" + countdown.status_text())


@router.message(Command("countdown_text"), F.chat.type == "private")
async def cmd_text(message: Message, command: CommandObject):
    text = (command.args or "").strip()
    if not text:
        await message.answer("Формат: /countdown_text ⏳ Осталось: {left}")
        return
    c = countdown.cfg()
    c["text"] = text
    countdown.save_cfg(c)
    await message.answer("📜 Готово. Пример как будет: " + countdown.render(c))


@router.message(Command("countdown_final"), F.chat.type == "private")
async def cmd_final(message: Message, command: CommandObject):
    text = (command.args or "").strip()
    if not text:
        await message.answer("Формат: /countdown_final Текст финального поста")
        return
    c = countdown.cfg()
    c["final_text"] = text
    countdown.save_cfg(c)
    await message.answer("🏁 Готово.\n\n" + countdown.status_text())


@router.message(Command("countdown_media"), F.chat.type == "private")
async def cmd_media(message: Message):
    db.set_meta(countdown.WAITING_MEDIA_KEY, "1")
    await message.answer("🎬 Перешли мне следующим сообщением видео (или фото) — я его запомню для финального поста.")


@router.message(Command("countdown_off"), F.chat.type == "private")
async def cmd_off(message: Message):
    for key in (countdown.CFG_KEY, countdown.MEDIA_KEY, countdown.LAST_KEY,
                countdown.DONE_KEY, countdown.WAITING_MEDIA_KEY):
        db.set_meta(key, "")
    await message.answer("⏹ Таймер выключен и стёрт.")


@router.message(F.chat.type == "private", F.video | F.photo | F.animation)
async def catch_media(message: Message):
    if not db.get_meta(countdown.WAITING_MEDIA_KEY):
        return
    if message.video:
        kind, file_id = "video", message.video.file_id
    elif message.photo:
        kind, file_id = "photo", message.photo[-1].file_id
    else:
        kind, file_id = "animation", message.animation.file_id
    db.set_meta(countdown.MEDIA_KEY, f"{kind}:{file_id}", important=True)
    db.set_meta(countdown.WAITING_MEDIA_KEY, "")
    await message.answer("🎬 Медиа запомнено — улетит вместе с финальным постом.")
