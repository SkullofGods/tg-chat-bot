"""Браки, разводы, семьи."""

from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import members
from loader import bot, db
from utils import format_duration_since

router = Router(name="family")

RING = " 💍 "


def brak_keyboard(proposer_id: int, target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data=f"brak_yes:{proposer_id}:{target_id}:{chat_id}"),
        InlineKeyboardButton(text="Нет", callback_data=f"brak_no:{proposer_id}:{target_id}:{chat_id}"),
    ]])


@router.message(Command("brak", "брак"))
async def cmd_brak(message: Message, command: CommandObject):
    proposer = message.from_user
    target_id, raw = members.resolve_target(message, command.args)
    if target_id is None:
        await message.reply("Используй <code>/brak</code> в ответ на сообщение или <code>/brak @username</code>.")
        return
    if target_id == -1:
        await message.reply(f"Пользователь <code>@{escape(raw)}</code> не найден в базе — возможно, ещё не писал в чат. 🤷")
        return
    if proposer.id == target_id:
        await message.reply("На себе жениться нельзя! 😅")
        return
    target = db.get_user(target_id)
    if target and target["is_bot"]:
        await message.reply("С ботами браки не заключаются 🤖💔")
        return
    if db.are_married(proposer.id, target_id):
        await message.reply("Вы уже состоите в браке! 💑")
        return
    db.add_marriage_proposal(proposer.id, target_id, message.chat.id)
    await message.reply(
        f"💍 {members.mention(proposer.id)} делает предложение {members.mention(target_id)}!",
        reply_markup=brak_keyboard(proposer.id, target_id, message.chat.id),
    )


async def _proposal_from_callback(callback: CallbackQuery) -> tuple[int, int, int] | None:
    _, proposer_id, target_id, chat_id = callback.data.split(":")
    proposer_id, target_id, chat_id = int(proposer_id), int(target_id), int(chat_id)
    if callback.from_user.id != target_id:
        await callback.answer("Это предложение не тебе 😾", show_alert=True)
        return None
    if not db.get_marriage_proposal(proposer_id, target_id, chat_id):
        await callback.answer("Предложение уже неактуально.", show_alert=True)
        return None
    return proposer_id, target_id, chat_id


async def _show_result(callback: CallbackQuery, chat_id: int, text: str):
    if isinstance(callback.message, Message):
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(text)
            return
    await bot.send_message(chat_id, text)


@router.callback_query(F.data.startswith("brak_yes:"))
async def callback_brak_yes(callback: CallbackQuery):
    proposal = await _proposal_from_callback(callback)
    if not proposal:
        return
    proposer_id, target_id, chat_id = proposal
    if db.are_married(proposer_id, target_id):
        await callback.answer("Вы уже в браке.", show_alert=True)
        return
    db.add_marriage(proposer_id, target_id)
    db.delete_marriage_proposal(proposer_id, target_id, chat_id)
    await _show_result(
        callback, chat_id,
        f"💒 {members.mention(proposer_id)} и {members.mention(target_id)} теперь состоят в браке! Поздравляем! 🎉",
    )
    await callback.answer("Согласие принято 💞")


@router.callback_query(F.data.startswith("brak_no:"))
async def callback_brak_no(callback: CallbackQuery):
    proposal = await _proposal_from_callback(callback)
    if not proposal:
        return
    proposer_id, target_id, chat_id = proposal
    db.delete_marriage_proposal(proposer_id, target_id, chat_id)
    await _show_result(
        callback, chat_id,
        f"💔 {members.mention(target_id)} отклонил(а) предложение от {members.mention(proposer_id)}.",
    )
    await callback.answer("Ну и ладно")


@router.message(Command("razvod", "развод"))
async def cmd_razvod(message: Message, command: CommandObject):
    target_id, _ = members.resolve_target(message, command.args)
    if target_id is None:
        await message.reply("Ответь на сообщение того, с кем разводишься, или укажи @username.")
        return
    if target_id == -1:
        await message.reply("Пользователь не найден в базе. 🤷")
        return
    if not db.are_married(message.from_user.id, target_id):
        await message.reply("Вы не состоите в браке. 🤷")
        return
    db.remove_marriage(message.from_user.id, target_id)
    await message.reply(f"💔 {members.mention(message.from_user.id)} и {members.mention(target_id)} развелись.")


@router.message(Command("families", "семьи"))
async def cmd_families(message: Message):
    marriages = db.get_all_marriages()
    if not marriages:
        await message.reply("В беседе пока нет браков. 😢")
        return

    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    for item in marriages:
        parent[find(item["user1_id"])] = find(item["user2_id"])

    families: dict[int, list[int]] = {}
    started: dict[int, str] = {}
    for item in marriages:
        root = find(item["user1_id"])
        family = families.setdefault(root, [])
        for uid in (item["user1_id"], item["user2_id"]):
            if uid not in family:
                family.append(uid)
        started[root] = min(started.get(root, item["created_at"]), item["created_at"])

    # простые имена без ссылок: список семей не должен пинговать полбеседы
    names = members.display_names({uid for family in families.values() for uid in family})
    lines = ["💑 <b>Семьи в беседе:</b>\n"]
    for i, (root, family) in enumerate(families.items(), 1):
        lines.append(f"{i}. {RING.join(names[uid] for uid in family)} — в браке уже {format_duration_since(started[root])}")
    await message.reply("\n".join(lines))
