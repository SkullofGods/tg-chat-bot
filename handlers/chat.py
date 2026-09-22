"""Все обычные сообщения в беседе: статистика, анкеты, сглыпа."""

from aiogram import F, Router
from aiogram.types import Message

import members
import multiplayer
import sglypa
import texts
from config import LOCAL_TZ
from loader import db
from textstats import extract_words, is_corpus_worthy

router = Router(name="chat")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(F.new_chat_members)
async def on_members_joined(message: Message):
    for user in message.new_chat_members:
        members.remember_user(user)
        if not user.is_bot:
            members.remember_member(message.chat.id, user.id)


@router.message(F.left_chat_member)
async def on_member_left(message: Message):
    user = message.left_chat_member
    members.remember_user(user)
    members.forget_member(message.chat.id, user.id)


def _has_content(message: Message) -> bool:
    return any((
        message.text, message.caption, message.sticker, message.voice, message.video_note, message.photo,
        message.video, message.animation, message.audio, message.document, message.poll, message.dice,
    ))


def _record(message: Message, text: str, allow_phrase: bool):
    letters = [c for c in text if c.isalpha()]
    phrase = None
    if allow_phrase and message.text and not message.forward_origin and not message.via_bot \
            and is_corpus_worthy(message.text):
        phrase = message.text
    sticker_ids = (message.sticker.file_unique_id, message.sticker.file_id) if message.sticker else None
    db.record_message(
        message.chat.id,
        message.from_user.id,
        words=extract_words(text),
        hour=message.date.astimezone(LOCAL_TZ).hour,
        is_text=bool(text),
        sticker=bool(message.sticker),
        voice=bool(message.voice or message.video_note),
        media=bool(message.photo or message.video or message.animation),
        question="?" in text,
        caps=len(letters) >= 5 and all(c.isupper() for c in letters),
        phrase=phrase,
        sticker_ids=sticker_ids,
    )
    sglypa.note_saved(message.chat.id, phrase=bool(phrase), sticker=bool(sticker_ids))


@router.message()
async def on_message(message: Message):
    user = message.from_user
    if not user or user.is_bot or message.sender_chat or not _has_content(message):
        return
    text = message.text or message.caption or ""
    if text.startswith("/"):
        return

    awaiting_anketa = bool(text) and db.is_awaiting_anketa(user.id, message.chat.id)
    _record(message, text, allow_phrase=not awaiting_anketa)  # анкеты в генератор фраз не попадают

    if awaiting_anketa:
        db.save_anketa(user.id, text)
        db.clear_awaiting_anketa(user.id, message.chat.id)
        await message.reply(f"✅ Анкета сохранена, {members.mention(user.id)}!\n\n{texts.RULES_TEXT}")
        return

    guessed = multiplayer.chat_guess(message.chat.id, user.id, text)
    if guessed:                       # крокодил: слово угадали прямо в беседе
        await message.reply(guessed["text"])
        return

    await sglypa.on_group_message(message)
