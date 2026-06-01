import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    ChatMemberUpdated,
    PollAnswer,
    BotCommand,
)
from aiogram.enums import ChatMemberStatus
from aiogram.fsm.storage.memory import MemoryStorage

from db import Database
from config import BOT_TOKEN, ORGY_COOLDOWN_HOURS, ORGY_POLL_DURATION_SECONDS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = Database()

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

_LEFT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}

orgy_state: dict[int, dict] = {}


# ─── Новый участник ──────────────────────────────────────

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
    db.ensure_user(user.id, user.username or "", user.full_name)
    mention = f"@{user.username}" if user.username else user.full_name
    await bot.send_message(
        event.chat.id,
        f"👋 Привет, {mention}! Добро пожаловать!\n\n{ANKETA_TEXT}\n\n"
        "_Напиши анкету прямо в этот чат, и я её сохраню._",
        parse_mode="Markdown",
    )
    db.set_awaiting_anketa(user.id, event.chat.id)


# ─── /info ─────────────────────────────────────────────

@dp.message(Command("info"))
async def cmd_info(message: Message, command: CommandObject):
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        target_name = message.reply_to_message.from_user.full_name
        db.ensure_user(target_id, message.reply_to_message.from_user.username or "", target_name)
    elif command.args:
        raw = command.args.strip().lstrip("@")
        found = db.get_user_by_username(raw)
        if not found:
            await message.reply(f"Пользователь @{raw} не найден. Он должен хоть раз написать в чат.")
            return
        target_id, target_name = found["user_id"], found["full_name"]
    else:
        await message.reply("Ответь на чьё-нибудь сообщение или укажи @username.")
        return
    anketa = db.get_anketa(target_id)
    if not anketa:
        await message.reply(f"У {target_name} анкеты пока нет. 🤷")
        return
    await message.reply(f"📋 *Анкета* {target_name}\n\n{anketa}", parse_mode="Markdown")


# ─── /anketa ──────────────────────────────────────────

@dp.message(Command("анкета", "anketa"))
async def cmd_anketa(message: Message):
    db.ensure_user(message.from_user.id, message.from_user.username or "", message.from_user.full_name)
    db.set_awaiting_anketa(message.from_user.id, message.chat.id)
    await message.reply(
        f"{ANKETA_TEXT}\n\n_Напиши ответ в чат, я обновлю анкету._",
        parse_mode="Markdown",
    )


# ─── Браки ─────────────────────────────────────────────

async def marry_handler(message: Message):
    if not message.reply_to_message:
        await message.reply("Ответь на сообщение того, на ком хочешь жениться. 💍")
        return
    proposer = message.from_user
    target = message.reply_to_message.from_user
    if proposer.id == target.id:
        await message.reply("На себе жениться нельзя! 😅")
        return
    if target.is_bot:
        await message.reply("Боты — плохие партнёры. 🤖")
        return
    db.ensure_user(target.id, target.username or "", target.full_name)
    if db.are_married(proposer.id, target.id):
        await message.reply("Вы уже в браке! 💑")
        return
    db.add_marriage(proposer.id, target.id)
    p = f"@{proposer.username}" if proposer.username else proposer.full_name
    t = f"@{target.username}" if target.username else target.full_name
    await message.reply(f"💒 {p} и {t} теперь в браке! 🎉")


@dp.message(Command("жениться", "zhenit"))
async def cmd_zhenit(message: Message):
    await marry_handler(message)


@dp.message(Command("выйтизамуж", "zamuzh"))
async def cmd_zamuzh(message: Message):
    await marry_handler(message)


@dp.message(Command("развод", "razvod"))
async def cmd_razvod(message: Message):
    if not message.reply_to_message:
        await message.reply("Ответь на сообщение того, с кем разводишься.")
        return
    u1, u2 = message.from_user.id, message.reply_to_message.from_user.id
    if not db.are_married(u1, u2):
        await message.reply("Вы не состоите в браке. 🤷")
        return
    db.remove_marriage(u1, u2)
    p = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    t = f"@{message.reply_to_message.from_user.username}" if message.reply_to_message.from_user.username else message.reply_to_message.from_user.full_name
    await message.reply(f"💔 {p} и {t} развелись.")


# ─── /families ───────────────────────────────────────────

@dp.message(Command("families"))
async def cmd_families(message: Message):
    marriages = db.get_all_marriages()
    if not marriages:
        await message.reply("В беседе пока нет браков. 😢")
        return
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
        families.setdefault(find(uid), []).append(uid)
    lines = ["💑 *Семьи в беседе:*\n"]
    for i, members in enumerate(families.values(), 1):
        names = []
        for uid in members:
            u = db.get_user(uid)
            names.append(f"@{u['username']}" if u and u['username'] else (u['full_name'] if u else str(uid)))
        lines.append(f"{i}. " + " 💍 ".join(names))
    await message.reply("\n".join(lines), parse_mode="Markdown")


# ─── /orgy ─────────────────────────────────────────────

@dp.message(Command("оргия", "orgy"))
async def cmd_orgy(message: Message):
    chat_id = message.chat.id
    now = datetime.utcnow()
    state = orgy_state.get(chat_id)
    if state:
        cooldown_end = state["last_time"] + timedelta(hours=ORGY_COOLDOWN_HOURS)
        if now < cooldown_end:
            remaining = cooldown_end - now
            h, rem = divmod(int(remaining.total_seconds()), 3600)
            m = rem // 60
            await message.reply(f"⏳ Следующая оргия возможна через {h}ч {m}мин.")
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
    asyncio.create_task(finish_orgy_after(chat_id, poll_msg.message_id))


async def finish_orgy_after(chat_id: int, poll_message_id: int):
    await asyncio.sleep(ORGY_POLL_DURATION_SECONDS)
    await finish_orgy(chat_id, poll_message_id)


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
    await bot.send_message(
        chat_id,
        f"🔥 {mentions(yes_ids)} поимели жёсткий секс, а {mentions(no_ids)} с завистью смотрели.",
        parse_mode="Markdown",
    )


@dp.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer):
    user_id = poll_answer.user.id
    for chat_id, state in orgy_state.items():
        if poll_answer.poll_id == state.get("poll_id"):
            if user_id in state["all_voter_ids"]:
                state["yes_voters"] = [x for x in state["yes_voters"] if x != user_id]
                state["no_voters"] = [x for x in state["no_voters"] if x != user_id]
            state["all_voter_ids"].add(user_id)
            if 0 in poll_answer.option_ids:
                state["yes_voters"].append(user_id)
            else:
                state["no_voters"].append(user_id)
            db.ensure_user(user_id, poll_answer.user.username or "", poll_answer.user.full_name)
            break


# ─── Общий хэндлер (регистрируем ПОСЛЕДНИМ, чтобы не блокировать команды) ───

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: Message):
    user = message.from_user
    db.ensure_user(user.id, user.username or "", user.full_name)
    if db.is_awaiting_anketa(user.id, message.chat.id):
        text = message.text or message.caption or ""
        if text and not text.startswith("/"):
            db.save_anketa(user.id, text)
            db.clear_awaiting_anketa(user.id, message.chat.id)
            mention = f"@{user.username}" if user.username else user.full_name
            await message.reply(
                f"✅ Анкета сохранена, {mention}!\n\n{RULES_TEXT}",
                parse_mode="Markdown",
            )


# ─── Main ─────────────────────────────────────────────

async def main():
    db.init()
    await bot.set_my_commands([
        BotCommand(command="info",     description="Анкета (reply или @username)"),
        BotCommand(command="anketa",   description="Заполнить / обновить анкету (или /анкета)"),
        BotCommand(command="zhenit",   description="Жениться (reply) (или /жениться)"),
        BotCommand(command="zamuzh",   description="Выйти замуж (reply) (или /выйтизамуж)"),
        BotCommand(command="razvod",   description="Развод (reply) (или /развод)"),
        BotCommand(command="families", description="Все браки в беседе"),
        BotCommand(command="orgy",     description="Оргия-опрос (или /оргия, 1 раз в сутки)"),
    ])
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
