import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    ChatMemberUpdated,
    Poll,
    PollAnswer,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from db import Database
from config import BOT_TOKEN, ORGY_COOLDOWN_HOURS, ORGY_POLL_DURATION_SECONDS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = Database()


class AnketaStates(StatesGroup):
    waiting_for_anketa = State()


ANKETA_TEXT = (
    "📋 *Анкета участника*\n\n"
    "Ответь на вопросы ниже одним сообщением (или несколькими — я сохраню всё как есть):\n\n"
    "1. Имя / как можно обращаться\n"
    "2. Возраст\n"
    "3. Город\n"
    "4. Работа / учёба\n"
    "5. Увлечения, хобби — всё что хочешь рассказать о себе"
)

RULES_TEXT = (
    "📜 *Правила беседы*\n\n"
    "ПРАВИЛА -- ТУТ БУДУТ ПРАВИЛА"
)

# Статусы, из которых считаем "вошёл в группу"
_LEFT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


# ─── Новый участник ───────────────────────────────────────────────────────────

@dp.chat_member()
async def on_new_member(event: ChatMemberUpdated):
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    joined = (
        old_status in _LEFT_STATUSES
        and new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
    )
    if not joined:
        return

    user = event.new_chat_member.user
    if user.is_bot:
        return

    chat_id = event.chat.id
    user_id = user.id
    username = user.username or ""
    full_name = user.full_name

    db.ensure_user(user_id, username, full_name)

    mention = f"@{username}" if username else full_name
    await bot.send_message(
        chat_id,
        f"👋 Привет, {mention}! Добро пожаловать в беседу!\n\n"
        f"{ANKETA_TEXT}\n\n"
        "_Напиши анкету прямо в этот чат, и я её сохраню._",
        parse_mode="Markdown",
    )
    db.set_awaiting_anketa(user_id, chat_id)


# ─── Приём анкеты ─────────────────────────────────────────────────────────────

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Проверяем ждёт ли пользователь заполнить анкету
    if db.is_awaiting_anketa(user_id, chat_id):
        text = message.text or message.caption or ""
        if text:
            db.save_anketa(user_id, text)
            db.clear_awaiting_anketa(user_id, chat_id)
            username = message.from_user.username or ""
            mention = f"@{username}" if username else message.from_user.full_name
            await message.reply(
                f"✅ Анкета сохранена, {mention}!\n\n{RULES_TEXT}",
                parse_mode="Markdown",
            )


# ─── /info ────────────────────────────────────────────────────────────────────

@dp.message(Command("info"))
async def cmd_info(message: Message, command: CommandObject):
    target_user_id = None
    target_name = None

    # Случай 1: /info в ответ на сообщение
    if message.reply_to_message:
        target_user_id = message.reply_to_message.from_user.id
        target_name = message.reply_to_message.from_user.full_name

    # Случай 2: /info @username
    elif command.args:
        raw = command.args.strip().lstrip("@")
        found = db.get_user_by_username(raw)
        if found:
            target_user_id, target_name = found["user_id"], found["full_name"]
        else:
            await message.reply(f"Пользователь @{raw} не найден в базе.")
            return
    else:
        await message.reply("Ответь на чьё-нибудь сообщение или укажи @username.")
        return

    anketa = db.get_anketa(target_user_id)
    if not anketa:
        await message.reply(f"У {target_name} анкеты пока нет. 🤷")
        return

    await message.reply(
        f"📋 *Анкета* {target_name}\n\n{anketa}",
        parse_mode="Markdown",
    )


# ─── /анкета (переоформить анкету) ────────────────────────────────────────────

@dp.message(Command("анкета"))
async def cmd_anketa(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    db.set_awaiting_anketa(user_id, chat_id)
    await message.reply(
        f"{ANKETA_TEXT}\n\n_Напиши ответ в чат, я обновлю анкету._",
        parse_mode="Markdown",
    )


# ─── /жениться / /выйтизамуж ──────────────────────────────────────────────────

async def marry_handler(message: Message):
    if not message.reply_to_message:
        await message.reply("Ответь на сообщение того, на ком хочешь жениться/выйти замуж. 💍")
        return

    proposer = message.from_user
    target = message.reply_to_message.from_user

    if proposer.id == target.id:
        await message.reply("На себе жениться нельзя! 😅")
        return
    if target.is_bot:
        await message.reply("Боты — плохие партнёры для брака. 🤖")
        return

    db.ensure_user(target.id, target.username or "", target.full_name)

    if db.are_married(proposer.id, target.id):
        await message.reply("Вы уже состоите в браке! 💑")
        return

    db.add_marriage(proposer.id, target.id)

    p_mention = f"@{proposer.username}" if proposer.username else proposer.full_name
    t_mention = f"@{target.username}" if target.username else target.full_name
    await message.reply(
        f"💒 {p_mention} и {t_mention} теперь состоят в браке! Поздравляем! 🎉"
    )


@dp.message(Command("жениться"))
async def cmd_zhenit(message: Message):
    await marry_handler(message)


@dp.message(Command("выйтизамуж"))
async def cmd_zamuzh(message: Message):
    await marry_handler(message)


@dp.message(Command("развод"))
async def cmd_razvod(message: Message):
    if not message.reply_to_message:
        await message.reply("Ответь на сообщение того, с кем хочешь развестись.")
        return

    user1 = message.from_user.id
    user2 = message.reply_to_message.from_user.id

    if not db.are_married(user1, user2):
        await message.reply("Вы не состоите в браке. 🤷")
        return

    db.remove_marriage(user1, user2)
    p_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    t_mention = f"@{message.reply_to_message.from_user.username}" if message.reply_to_message.from_user.username else message.reply_to_message.from_user.full_name
    await message.reply(f"💔 {p_mention} и {t_mention} развелись.")


# ─── /families ────────────────────────────────────────────────────────────────

@dp.message(Command("families"))
async def cmd_families(message: Message):
    marriages = db.get_all_marriages()
    if not marriages:
        await message.reply("В беседе пока нет браков. 😢")
        return

    # Группируем в семьи через union-find
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    def union(a, b):
        parent[find(a)] = find(b)

    for uid1, uid2 in marriages:
        union(uid1, uid2)

    families: dict[int, list[int]] = {}
    for uid in parent:
        root = find(uid)
        families.setdefault(root, []).append(uid)

    lines = ["💑 *Семьи в беседе:*\n"]
    for i, (_, members) in enumerate(families.items(), 1):
        names = []
        for uid in members:
            u = db.get_user(uid)
            if u:
                names.append(f"@{u['username']}" if u['username'] else u['full_name'])
            else:
                names.append(str(uid))
        lines.append(f"{i}. " + " 💍 ".join(names))

    await message.reply("\n".join(lines), parse_mode="Markdown")


# ─── /оргия ───────────────────────────────────────────────────────────────────

orgy_state: dict[int, dict] = {}  # chat_id -> {last_time, poll_message_id, poll_id}


@dp.message(Command("оргия"))
async def cmd_orgy(message: Message):
    chat_id = message.chat.id
    now = datetime.utcnow()

    state = orgy_state.get(chat_id)
    if state:
        cooldown_end = state["last_time"] + timedelta(hours=ORGY_COOLDOWN_HOURS)
        if now < cooldown_end:
            remaining = cooldown_end - now
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            await message.reply(
                f"⏳ Следующая оргия возможна через {hours}ч {minutes}мин."
            )
            return

    poll_msg = await bot.send_poll(
        chat_id=chat_id,
        question="🔥 ОРГИЯ — ты участвуешь?",
        options=["Да, я в деле! 🍑", "Нет, не интересует 😇"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )

    orgy_state[chat_id] = {
        "last_time": now,
        "poll_message_id": poll_msg.message_id,
        "poll_id": poll_msg.poll.id,
        "yes_voters": [],
        "no_voters": [],
        "all_voter_ids": set(),
    }

    await asyncio.sleep(ORGY_POLL_DURATION_SECONDS)
    await finish_orgy(chat_id, poll_msg.message_id)


async def finish_orgy(chat_id: int, poll_message_id: int):
    try:
        await bot.stop_poll(chat_id, poll_message_id)
    except Exception:
        pass
    try:
        await bot.delete_message(chat_id, poll_message_id)
    except Exception:
        pass

    state = orgy_state.get(chat_id, {})
    yes_ids = state.get("yes_voters", [])
    no_ids = state.get("no_voters", [])

    def mentions(ids):
        parts = []
        for uid in ids:
            u = db.get_user(uid)
            if u and u["username"]:
                parts.append(f"@{u['username']}")
            elif u:
                parts.append(f"[{u['full_name']}](tg://user?id={uid})")
            else:
                parts.append(f"[user](tg://user?id={uid})")
        return " ".join(parts) if parts else "никто"

    yes_text = mentions(yes_ids)
    no_text = mentions(no_ids)

    await bot.send_message(
        chat_id,
        f"🔥 {yes_text} поимели жёсткий секс, а {no_text} с завистью смотрели.",
        parse_mode="Markdown",
    )


@dp.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer):
    user_id = poll_answer.user.id
    for chat_id, state in orgy_state.items():
        if poll_answer.poll_id == state.get("poll_id"):
            if user_id in state["all_voter_ids"]:
                # убираем старый голос
                state["yes_voters"] = [x for x in state["yes_voters"] if x != user_id]
                state["no_voters"] = [x for x in state["no_voters"] if x != user_id]
            state["all_voter_ids"].add(user_id)
            if 0 in poll_answer.option_ids:  # "Да"
                state["yes_voters"].append(user_id)
            else:
                state["no_voters"].append(user_id)
            db.ensure_user(
                user_id,
                poll_answer.user.username or "",
                poll_answer.user.full_name,
            )
            break


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    db.init()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
