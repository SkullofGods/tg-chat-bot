"""Приветствие, анкеты, правила, ники."""

from html import escape

from aiogram import Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMember, ChatMemberUpdated, Message

import members
import texts
from loader import bot, db

router = Router(name="profile")

_PRESENT = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}


def _is_present(member: ChatMember) -> bool:
    return member.status in _PRESENT or (
        member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False)
    )


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated):
    user = event.new_chat_member.user
    members.remember_user(user)
    if user.is_bot:
        return
    chat_id = event.chat.id
    members.remember_chat(chat_id)
    was_present, is_present = _is_present(event.old_chat_member), _is_present(event.new_chat_member)
    if not is_present:
        members.forget_member(chat_id, user.id)
        return
    members.remember_member(chat_id, user.id)
    if not was_present:
        await bot.send_message(
            chat_id, f"👋 Привет, {members.mention(user.id)}! Добро пожаловать!\n\n{texts.ANKETA_TEXT}"
        )
        db.set_awaiting_anketa(user.id, chat_id)


@router.message(Command("info", "инфо"))
async def cmd_info(message: Message, command: CommandObject):
    target_id, raw = members.resolve_target(message, command.args)
    if target_id is None:
        await message.reply("Ответь на сообщение пользователя или укажи @username.")
        return
    if target_id == -1:
        await message.reply(f"Пользователь <code>@{escape(raw)}</code> не найден в базе — возможно, ещё не писал в чат. 🤷")
        return
    name = members.display_name(target_id)
    anketa = db.get_anketa(target_id)
    if not anketa:
        await message.reply(f"У {name} анкеты пока нет. 🤷")
        return
    if len(anketa) > 3800:
        anketa = anketa[:3800] + "…"
    await message.reply(f"📋 <b>Анкета</b> {name}\n\n{escape(anketa, quote=False)}")


@router.message(Command("anketa", "анкета"))
async def cmd_anketa(message: Message):
    if not members.is_group(message.chat):
        await message.reply("Анкету заполняют в беседе 🙂")
        return
    db.set_awaiting_anketa(message.from_user.id, message.chat.id)
    await message.reply(texts.ANKETA_TEXT)


@router.message(Command("rules", "правила"))
async def cmd_rules(message: Message):
    await message.reply(texts.RULES_TEXT)


@router.message(Command("nick", "ник"))
async def cmd_nick(message: Message, command: CommandObject):
    user = message.from_user
    args = (command.args or "").strip()
    if not args:
        current = db.get_nickname(user.id)
        if current:
            await message.reply(
                f"Твой текущий никнейм: <b>{escape(current)}</b>\nЧтобы убрать его, напиши <code>/ник -</code>"
            )
        else:
            await message.reply("У тебя пока нет никнейма.\nУстанови: <code>/ник ИмяКотороеХочешь</code>")
        return
    if args == "-":
        db.delete_nickname(user.id)
        await message.reply("Никнейм удалён. Бот будет обращаться по имени. ✅")
        return
    new_nick = " ".join(args.split())
    if len(new_nick) > 32:
        await message.reply("Никнейм слишком длинный (макс. 32 символа). 😕")
        return
    db.set_nickname(user.id, new_nick)
    await message.reply(
        f"Никнейм установлен: {members.mention(user.id)} ✅\nТеперь бот будет обращаться к тебе именно так."
    )
