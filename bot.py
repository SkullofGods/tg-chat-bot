import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PollAnswer,
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
shutdown_sent = False


def mention_by_user(user) -> str:
    return f"@{user.username}" if user.username else user.full_name


def mention_by_db(user_id: int) -> str:
    u = db.get_user(user_id)
    if u and u["username"]:
        return f"@{u['username']}"
    if u:
        return f"[{u['full_name']}](tg://user?id={user_id})"
    return f"[user](tg://user?id={user_id})"


def format_duration_since(iso_dt: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_dt)
    except Exception:
        return "неизвестно сколько"
    delta = datetime.utcnow() - dt
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    if days > 0:
        return f"{days} дн. {hours} ч."
    if hours > 0:
        return f"{hours} ч. {minutes} мин."
    return f"{minutes} мин."


def resolve_target_from_command_or_reply(message: Message, command: CommandObject):
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        db.ensure_user(target.id, target.username or "", target.full_name)
        return target.id, target.full_name, target.username or ""
    if command.args:
        raw = command.args.strip().lstrip("@")
        found = db.get_user_by_username(raw)
        if found:
            return found["user_id"], found["full_name"], found["username"]
    return None, None, None


def brak_keyboard(proposer_id: int, target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Да", callback_data=f"brak_yes:{proposer_id}:{target_id}:{chat_id}"),
            InlineKeyboardButton(text="Нет", callback_data=f"brak_no:{proposer_id}:{target_id}:{chat_id}"),
        ]]
    )


async def announce_startup():
    for chat_id in db.get_known_chats():
        with suppress(Exception):
            await bot.send_message(chat_id, "бля, я ожил и обновился")


async def announce_shutdown():
    global shutdown_sent
    if shutdown_sent:
        return
    shutdown_sent = True
    for chat_id in db.get_known_chats():
        with suppress(Exception):
            await bot.send_message(chat_id, "бля, умираю")


@dp.chat_member()
async def on_new_member(event: ChatMemberUpdated):
    db.remember_chat(event.chat.id)
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    joined = old_status in _LEFT_STATUSES and new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
    if not joined:
        return
    user = event.new_chat_member.user
    if user.is_bot:
        return
    db.ensure_user(user.id, user.username or "", user.full_name)
    await bot.send_message(
        event.chat.id,
        f"👋 Привет, {mention_by_user(user)}! Добро пожаловать!\n\n{ANKETA_TEXT}\n\n"
        "_Напиши анкету прямо в этот чат, и я её сохраню._",
        parse_mode="Markdown",
    )
    db.set_awaiting_anketa(user.id, event.chat.id)


@dp.message(Command("info"))
async def cmd_info(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    target_id, target_name, _ = resolve_target_from_command_or_reply(message, command)
    if not target_id:
        await message.reply("Ответь на сообщение пользователя или укажи @username.")
        return
    anketa = db.get_anketa(target_id)
    if not anketa:
        await message.reply(f"У {target_name} анкеты пока нет. 🤷")
        return
    await message.reply(f"📋 *Анкета* {target_name}\n\n{anketa}", parse_mode="Markdown")


@dp.message(Command("анкета", "anketa"))
async def cmd_anketa(message: Message):
    db.remember_chat(message.chat.id)
    db.ensure_user(message.from_user.id, message.from_user.username or "", message.from_user.full_name)
    db.set_awaiting_anketa(message.from_user.id, message.chat.id)
    await message.reply(f"{ANKETA_TEXT}\n\n_Напиши ответ в чат, я обновлю анкету._", parse_mode="Markdown")


@dp.message(Command("brak"))
async def cmd_brak(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    proposer = message.from_user
    db.ensure_user(proposer.id, proposer.username or "", proposer.full_name)
    target_id, target_name, _ = resolve_target_from_command_or_reply(message, command)
    if not target_id:
        await message.reply("Используй `/brak` в ответ на сообщение или `/brak @username`.", parse_mode="Markdown")
        return
    if proposer.id == target_id:
        await message.reply("На себе жениться нельзя! 😅")
        return
    if db.are_married(proposer.id, target_id):
        await message.reply("Вы уже состоите в браке! 💑")
        return
    db.add_marriage_proposal(proposer.id, target_id, message.chat.id)
    await message.reply(
        f"💍 {mention_by_user(proposer)} делает предложение {mention_by_db(target_id)}!",
        parse_mode="Markdown",
        reply_markup=brak_keyboard(proposer.id, target_id, message.chat.id),
    )


@dp.callback_query(F.data.startswith("brak_yes:"))
async def callback_brak_yes(callback: CallbackQuery):
    db.remember_chat(callback.message.chat.id)
    _, proposer_id, target_id, chat_id = callback.data.split(":")
    proposer_id, target_id, chat_id = int(proposer_id), int(target_id), int(chat_id)
    if callback.from_user.id != target_id:
        await callback.answer("Это предложение не тебе 😾", show_alert=True)
        return
    if not db.get_marriage_proposal(proposer_id, target_id, chat_id):
        await callback.answer("Предложение уже неактуально.", show_alert=True)
        return
    if db.are_married(proposer_id, target_id):
        await callback.answer("Вы уже в браке.", show_alert=True)
        return
    db.add_marriage(proposer_id, target_id)
    db.delete_marriage_proposal(proposer_id, target_id, chat_id)
    await callback.message.edit_text(
        f"💒 {mention_by_db(proposer_id)} и {mention_by_db(target_id)} теперь состоят в браке! Поздравляем! 🎉",
        parse_mode="Markdown",
    )
    await callback.answer("Согласие принято 💞")


@dp.callback_query(F.data.startswith("brak_no:"))
async def callback_brak_no(callback: CallbackQuery):
    db.remember_chat(callback.message.chat.id)
    _, proposer_id, target_id, chat_id = callback.data.split(":")
    proposer_id, target_id, chat_id = int(proposer_id), int(target_id), int(chat_id)
    if callback.from_user.id != target_id:
        await callback.answer("Это предложение не тебе 😾", show_alert=True)
        return
    if not db.get_marriage_proposal(proposer_id, target_id, chat_id):
        await callback.answer("Предложение уже неактуально.", show_alert=True)
        return
    db.delete_marriage_proposal(proposer_id, target_id, chat_id)
    await callback.message.edit_text(
        f"💔 {mention_by_db(target_id)} отклонил(а) предложение от {mention_by_db(proposer_id)}.",
        parse_mode="Markdown",
    )
    await callback.answer("Ну и ладно")


@dp.message(Command("razvod", "развод"))
async def cmd_razvod(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    target_id, _, _ = resolve_target_from_command_or_reply(message, command)
    if not target_id:
        await message.reply("Ответь на сообщение того, с кем разводишься, или укажи @username.")
        return
    if not db.are_married(message.from_user.id, target_id):
        await message.reply("Вы не состоите в браке. 🤷")
        return
    db.remove_marriage(message.from_user.id, target_id)
    await message.reply(f"💔 {mention_by_user(message.from_user)} и {mention_by_db(target_id)} развелись.", parse_mode="Markdown")


@dp.message(Command("families"))
async def cmd_families(message: Message):
    db.remember_chat(message.chat.id)
    marriages = db.get_all_marriages()
    if not marriages:
        await message.reply("В беседе пока нет браков. 😢")
        return
    parent = {}
    family_dates = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        parent[find(a)] = find(b)

    for item in marriages:
        uid1, uid2 = item["user1_id"], item["user2_id"]
        union(uid1, uid2)

    families: dict[int, list[int]] = {}
    for item in marriages:
        uid1, uid2 = item["user1_id"], item["user2_id"]
        root = find(uid1)
        families.setdefault(root, [])
        if uid1 not in families[root]:
            families[root].append(uid1)
        if uid2 not in families[root]:
            families[root].append(uid2)
        family_dates.setdefault(root, []).append(item["created_at"])

    lines = ["💑 *Семьи в беседе:*\n"]
    for i, (root, members) in enumerate(families.items(), 1):
        names = [mention_by_db(uid) for uid in members]
        oldest = min(family_dates[root]) if family_dates[root] else None
        duration = format_duration_since(oldest) if oldest else "неизвестно сколько"
        lines.append(f"{i}. {' 💍 '.join(names)} — в браке уже {duration}")

    await message.reply("\n".join(lines), parse_mode="Markdown")


@dp.message(Command("оргия", "orgy"))
async def cmd_orgy(message: Message):
    db.remember_chat(message.chat.id)
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
        return " ".join(mention_by_db(uid) for uid in ids) if ids else "никто"

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


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: Message):
    db.remember_chat(message.chat.id)
    user = message.from_user
    db.ensure_user(user.id, user.username or "", user.full_name)
    if db.is_awaiting_anketa(user.id, message.chat.id):
        text = message.text or message.caption or ""
        if text and not text.startswith("/"):
            db.save_anketa(user.id, text)
            db.clear_awaiting_anketa(user.id, message.chat.id)
            await message.reply(
                f"✅ Анкета сохранена, {mention_by_user(user)}!\n\n{RULES_TEXT}",
                parse_mode="Markdown",
            )


async def main():
    db.init()
    # BotCommandScopeAllGroupChats — команды видны только в группах,
    # поэтому Telegram не добавляет @botname суффикс в подсказках
    group_commands = [
        BotCommand(command="info",     description="Анкета (reply или @username)"),
        BotCommand(command="anketa",   description="Заполнить / обновить анкету (или /анкета)"),
        BotCommand(command="brak",     description="Сделать предложение брака"),
        BotCommand(command="razvod",   description="Развод (reply или @username)"),
        BotCommand(command="families", description="Все браки в беседе"),
        BotCommand(command="orgy",     description="Оргия-опрос (или /оргия, 1 раз в сутки)"),
    ]
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    await announce_startup()
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await announce_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
