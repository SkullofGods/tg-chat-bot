import asyncio
import logging
import random
from contextlib import suppress
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeDefault,
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

RING = " \U0001f48d "  # separator for families list

ANKETA_TEXT = (
    "\U0001f4cb *\u0410\u043d\u043a\u0435\u0442\u0430 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430*\n\n"
    "\u041e\u0442\u0432\u0435\u0442\u044c \u043d\u0430 \u0432\u043e\u043f\u0440\u043e\u0441\u044b \u043d\u0438\u0436\u0435 \u043e\u0434\u043d\u0438\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c "
    "(\u0438\u043b\u0438 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u0438\u043c\u0438 \u2014 \u044f \u0441\u043e\u0445\u0440\u0430\u043d\u044e \u0432\u0441\u0451 \u043a\u0430\u043a \u0435\u0441\u0442\u044c):\n\n"
    "1. \u0418\u043c\u044f / \u043a\u0430\u043a \u043c\u043e\u0436\u043d\u043e \u043e\u0431\u0440\u0430\u0449\u0430\u0442\u044c\u0441\u044f\n"
    "2. \u0412\u043e\u0437\u0440\u0430\u0441\u0442\n"
    "3. \u0413\u043e\u0440\u043e\u0434\n"
    "4. \u0420\u0430\u0431\u043e\u0442\u0430 / \u0443\u0447\u0451\u0431\u0430\n"
    "5. \u0423\u0432\u043b\u0435\u0447\u0435\u043d\u0438\u044f, \u0445\u043e\u0431\u0431\u0438 \u2014 \u0432\u0441\u0451 \u0447\u0442\u043e \u0445\u043e\u0447\u0435\u0448\u044c \u0440\u0430\u0441\u0441\u043a\u0430\u0437\u0430\u0442\u044c \u043e \u0441\u0435\u0431\u0435"
)

RULES_TEXT = (
    "*=\u041f\u0420\u0410\u0412\u0418\u041b\u0410=*\n\n"
    "1\ufe0f\u20e3 \u0417\u0430\u043f\u0440\u0435\u0449\u0435\u043d\u044b \u043e\u0441\u043a\u043e\u0440\u0431\u043b\u0435\u043d\u0438\u044f, \u0441\u0440\u0430\u0447\u0438, \u0434\u043e\u0435\u0431\u043a\u0438 \u0438 \u043f\u0440\u043e\u0447\u0438\u0439 \u043f\u043e\u0434\u043e\u0431\u043d\u044b\u0439 \u043d\u0435\u0433\u0430\u0442\u0438\u0432\n\n"
    "2\ufe0f\u20e3 \u0417\u0430\u043f\u0440\u0435\u0449\u0435\u043d\u043e \u0430\u0433\u0440\u0435\u0441\u0441\u0438\u0432\u043d\u043e\u0435 \u043e\u0431\u0441\u0443\u0436\u0434\u0435\u043d\u0438\u0435 \u043a\u0430\u043a\u0438\u0445-\u043b\u0438\u0431\u043e \u0442\u0435\u043c (\u0442.\u0435. \u043e\u0431\u0441\u0443\u0436\u0434\u0435\u043d\u0438\u0435, \u043f\u0435\u0440\u0435\u0442\u0435\u043a\u0430\u044e\u0449\u0435\u0435 \u0432 \u043a\u043e\u043d\u0444\u043b\u0438\u043a\u0442 \u0438\u043b\u0438 \u0432 \u043f\u0435\u0440\u0435\u0445\u043e\u0434\u044b \u043d\u0430 \u043b\u0438\u0447\u043d\u043e\u0441\u0442\u0438)\n\n"
    "3\ufe0f\u20e3 \u041f\u043e\u0434\u043d\u0438\u043c\u0430\u0439\u0442\u0435 \u0441\u043f\u043e\u0440\u043d\u044b\u0435 \u0442\u0435\u043c\u044b (\u0432 \u0442.\u0447. \u043f\u043e\u043b\u0438\u0442\u0438\u043a\u0443) \u043e\u0441\u0442\u043e\u0440\u043e\u0436\u043d\u043e \u0432 \u0441\u0432\u044f\u0437\u0438 \u0441 \u043f\u0443\u043d\u043a\u0442\u043e\u043c \u043f\u0440\u0430\u0432\u0438\u043b \u2116\u00a02\n\n"
    "4\ufe0f\u20e3 *\u041d\u0415\u041b\u042c\u0417\u042f* \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0442\u044c \u043d\u043e\u0432\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432 \u0431\u0435\u0437 \u0441\u043e\u0433\u043b\u0430\u0441\u043e\u0432\u0430\u043d\u0438\u044f \u0441 \u0410\u0437\u0430\u0440\u0442\u043e\u043c \u0438\u043b\u0438 \u0410\u0440\u0438\u043d\u043e\u0439. \u041c\u044b \u043d\u0435 \u043f\u0440\u043e\u0442\u0438\u0432 \u043d\u043e\u0432\u044b\u0445 \u043b\u044e\u0434\u0435\u0439, \u043f\u0440\u043e\u0441\u0442\u043e \u0445\u043e\u0442\u0438\u043c \u0437\u043d\u0430\u0442\u044c, \u043a\u0442\u043e \u0438 \u043e\u0442\u043a\u0443\u0434\u0430 \u043f\u043e\u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f\n\n"
    "5\ufe0f\u20e3 \u041f\u043e \u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438 \u043f\u0440\u044f\u0447\u044c\u0442\u0435 \u043f\u043e\u0434 \u0441\u043f\u043e\u0439\u043b\u0435\u0440\u044b \u043e\u0441\u0442\u0440\u044b\u0435 \u0442\u0435\u043c\u044b, \u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u043c\u043e\u0433\u0443\u0442 \u0441\u0442\u0430\u0442\u044c \u0447\u044c\u0438\u043c-\u0442\u043e \u0442\u0440\u0438\u0433\u0433\u0435\u0440\u043e\u043c, \u0438 \u0441\u0442\u0430\u0432\u044c\u0442\u0435 *TW*\n\n"
    "\u2015\u2015\u2015\n"
    "\u0412 \u0431\u0435\u0441\u0435\u0434\u0435 \u043d\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0443\u0435\u0442 \u0441\u0438\u0441\u0442\u0435\u043c\u0430 \u0432\u0430\u0440\u043d\u043e\u0432/\u043c\u0443\u0442\u043e\u0432, \u043d\u0435\u0442 \u043a\u0430\u043a\u043e\u0433\u043e-\u0442\u043e \u0447\u0451\u0442\u043a\u043e\u0433\u043e \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u0430 \u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0439 \u0434\u043b\u044f \u043a\u0438\u043a\u0430\n"
    "\u0415\u0441\u043b\u0438 \u0447\u044c\u0438-\u0442\u043e \u0441\u043b\u043e\u0432\u0430 \u0432\u0430\u0441 \u0437\u0430\u0434\u0435\u043b\u0438/\u043e\u0431\u0438\u0434\u0435\u043b\u0438 \u0438\u043b\u0438 \u0447\u0442\u043e-\u0442\u043e \u0432\u0430\u0441 \u043d\u0435 \u0443\u0441\u0442\u0440\u0430\u0438\u0432\u0430\u0435\u0442 \u2014 \u043e\u0431\u0440\u0430\u0449\u0430\u0439\u0442\u0435\u0441\u044c \u0432 \u043b\u0441 \u043a \u0430\u0434\u043c\u0438\u043d\u0430\u043c, \u043d\u0435 \u043c\u043e\u043b\u0447\u0438\u0442\u0435, \u043f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430. \u0412\u0441\u0435 \u0440\u0430\u0437\u0440\u0443\u043b\u0438\u043c \u2764\ufe0f\n\n"
    "\u0411\u0443\u0434\u044c\u0442\u0435 \u0441\u043e\u043b\u043d\u044b\u0448\u043a\u0430\u043c\u0438 \u0438 \u0432\u0441\u0435 \u0431\u0443\u0434\u0435\u0442 \u0445\u043e\u0440\u043e\u0448\u043e \u003c\u0437"
)

_D20_CRIT_FAIL = [
    "\u041a\u0420\u0418\u0422\u0418\u0427\u0415\u0421\u041a\u0418\u0419 \u041f\u0420\u041e\u0412\u0410\u041b! \U0001f480 \u0412\u0441\u0435\u043b\u0435\u043d\u043d\u0430\u044f \u043b\u0438\u0447\u043d\u043e \u043f\u0440\u043e\u0442\u0438\u0432 \u0442\u0435\u0431\u044f.",
    "\u042d\u041f\u0418\u041a \u0424\u0415\u0419\u041b! \U0001f480 \u0414\u0430\u0436\u0435 \u0431\u043e\u0433\u0438 \u043e\u0442\u0432\u0435\u0440\u043d\u0443\u043b\u0438\u0441\u044c.",
    "\u041a\u0420\u0418\u0422-\u041f\u0420\u041e\u0412\u0410\u041b! \U0001f480 \u042d\u0442\u043e... \u044d\u0442\u043e \u0431\u044b\u043b\u043e \u043d\u0435\u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e \u043f\u0440\u043e\u0432\u0430\u043b\u0438\u0442\u044c. \u041d\u043e \u0442\u044b \u0441\u043c\u043e\u0433(\u043b\u0430).",
]
_D20_FAIL = [
    "\u041f\u0440\u043e\u0432\u0430\u043b. \U0001f61e \u041d\u0435 \u0441\u0435\u0433\u043e\u0434\u043d\u044f.",
    "\u041d\u0435 \u043f\u0440\u043e\u0448\u043b\u043e. \U0001f61e \u0421\u0443\u0434\u044c\u0431\u0430 \u0441\u043a\u0430\u0437\u0430\u043b\u0430 \u043d\u0435\u0442.",
    "\u041c\u0438\u043c\u043e. \U0001f61e \u0411\u044b\u0432\u0430\u0435\u0442.",
    "\u041d\u0435\u0443\u0434\u0430\u0447\u0430. \U0001f61e \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u0435\u0449\u0451 \u0440\u0430\u0437... \u0438\u043b\u0438 \u043d\u0435\u0442.",
]
_D20_SUCCESS = [
    "\u0423\u0441\u043f\u0435\u0445! \u2705 \u041f\u043e\u043b\u0443\u0447\u0438\u043b\u043e\u0441\u044c.",
    "\u041f\u0440\u043e\u0448\u043b\u043e! \u2705 \u0423\u0434\u0430\u0447\u0430 \u0443\u043b\u044b\u0431\u043d\u0443\u043b\u0430\u0441\u044c.",
    "\u0423\u0441\u043f\u0435\u0445. \u2705 \u0412 \u044d\u0442\u043e\u0442 \u0440\u0430\u0437 \u043f\u043e\u0432\u0435\u0437\u043b\u043e.",
    "\u0421\u0440\u0430\u0431\u043e\u0442\u0430\u043b\u043e! \u2705 \u0422\u0430\u043a \u0434\u0435\u0440\u0436\u0430\u0442\u044c.",
]
_D20_CRIT_SUCCESS = [
    "\u041a\u0420\u0418\u0422\u0418\u0427\u0415\u0421\u041a\u0418\u0419 \u0423\u0421\u041f\u0415\u0425! \U0001f31f \u041b\u0435\u0433\u0435\u043d\u0434\u0430. \u0410\u0431\u0441\u043e\u043b\u044e\u0442\u043d\u0430\u044f \u043b\u0435\u0433\u0435\u043d\u0434\u0430.",
    "\u041a\u0420\u0418\u0422! \U0001f31f \u0411\u043e\u0433\u0438 \u0430\u043f\u043b\u043e\u0434\u0438\u0440\u0443\u044e\u0442.",
    "\u041d\u0410\u0422\u0423\u0420\u0410\u041b\u042c\u041d\u0410\u042f 20! \U0001f31f \u042d\u0442\u043e \u0432\u043e\u0439\u0434\u0451\u0442 \u0432 \u0438\u0441\u0442\u043e\u0440\u0438\u044e \u0431\u0435\u0441\u0435\u0434\u044b.",
]

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
        return "\u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e \u0441\u043a\u043e\u043b\u044c\u043a\u043e"
    delta = datetime.utcnow() - dt
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    if days > 0:
        return f"{days} \u0434\u043d. {hours} \u0447."
    if hours > 0:
        return f"{hours} \u0447. {minutes} \u043c\u0438\u043d."
    return f"{minutes} \u043c\u0438\u043d."


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
            InlineKeyboardButton(text="\u0414\u0430", callback_data=f"brak_yes:{proposer_id}:{target_id}:{chat_id}"),
            InlineKeyboardButton(text="\u041d\u0435\u0442", callback_data=f"brak_no:{proposer_id}:{target_id}:{chat_id}"),
        ]]
    )


async def announce_startup():
    for chat_id in db.get_known_chats():
        with suppress(Exception):
            await bot.send_message(chat_id, "\u0431\u043b\u044f, \u044f \u043e\u0436\u0438\u043b \u0438 \u043e\u0431\u043d\u043e\u0432\u0438\u043b\u0441\u044f")


async def announce_shutdown():
    global shutdown_sent
    if shutdown_sent:
        return
    shutdown_sent = True
    for chat_id in db.get_known_chats():
        with suppress(Exception):
            await bot.send_message(chat_id, "\u0431\u043b\u044f, \u0443\u043c\u0438\u0440\u0430\u044e")


@dp.chat_member()
async def on_new_member(event: ChatMemberUpdated):
    db.remember_chat(event.chat.id)
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
    name = mention_by_user(user)
    await bot.send_message(
        event.chat.id,
        f"\U0001f44b \u041f\u0440\u0438\u0432\u0435\u0442, {name}! \u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c!\n\n{ANKETA_TEXT}\n\n"
        "_\u041d\u0430\u043f\u0438\u0448\u0438 \u0430\u043d\u043a\u0435\u0442\u0443 \u043f\u0440\u044f\u043c\u043e \u0432 \u044d\u0442\u043e\u0442 \u0447\u0430\u0442, \u0438 \u044f \u0435\u0451 \u0441\u043e\u0445\u0440\u0430\u043d\u044e._",
        parse_mode="Markdown",
    )
    db.set_awaiting_anketa(user.id, event.chat.id)


@dp.message(Command("info"))
async def cmd_info(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    target_id, target_name, _ = resolve_target_from_command_or_reply(message, command)
    if not target_id:
        await message.reply("\u041e\u0442\u0432\u0435\u0442\u044c \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f \u0438\u043b\u0438 \u0443\u043a\u0430\u0436\u0438 @username.")
        return
    anketa = db.get_anketa(target_id)
    if not anketa:
        await message.reply(f"\u0423 {target_name} \u0430\u043d\u043a\u0435\u0442\u044b \u043f\u043e\u043a\u0430 \u043d\u0435\u0442. \U0001f937")
        return
    await message.reply(f"\U0001f4cb *\u0410\u043d\u043a\u0435\u0442\u0430* {target_name}\n\n{anketa}", parse_mode="Markdown")


@dp.message(Command("\u0430\u043d\u043a\u0435\u0442\u0430", "anketa"))
async def cmd_anketa(message: Message):
    db.remember_chat(message.chat.id)
    db.ensure_user(message.from_user.id, message.from_user.username or "", message.from_user.full_name)
    db.set_awaiting_anketa(message.from_user.id, message.chat.id)
    await message.reply(
        f"{ANKETA_TEXT}\n\n_\u041d\u0430\u043f\u0438\u0448\u0438 \u043e\u0442\u0432\u0435\u0442 \u0432 \u0447\u0430\u0442, \u044f \u043e\u0431\u043d\u043e\u0432\u043b\u044e \u0430\u043d\u043a\u0435\u0442\u0443._",
        parse_mode="Markdown",
    )


@dp.message(Command("rules", "\u043f\u0440\u0430\u0432\u0438\u043b\u0430"))
async def cmd_rules(message: Message):
    db.remember_chat(message.chat.id)
    await message.reply(RULES_TEXT, parse_mode="Markdown")


@dp.message(Command("d20"))
async def cmd_d20(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)

    threshold = 7
    if command.args:
        try:
            threshold = int(command.args.strip())
            threshold = max(1, min(20, threshold))
        except ValueError:
            pass

    roll = random.randint(1, 20)

    if roll == 1:
        flavor = random.choice(_D20_CRIT_FAIL)
    elif roll == 20:
        flavor = random.choice(_D20_CRIT_SUCCESS)
    elif roll < threshold:
        flavor = random.choice(_D20_FAIL)
    else:
        flavor = random.choice(_D20_SUCCESS)

    context = ""
    if message.reply_to_message and message.reply_to_message.text:
        preview = message.reply_to_message.text[:60]
        if len(message.reply_to_message.text) > 60:
            preview += "\u2026"
        context = f"_\u00ab{preview}\u00bb_\n\n"

    result_line = f"\U0001f3b2 \u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043d\u0430 \u0443\u0441\u043f\u0435\u0445 \u2014 \u043f\u043e\u0440\u043e\u0433 *{threshold}*, \u0432\u044b\u043f\u0430\u043b\u043e *{roll}*."
    await message.reply(
        f"{context}{result_line}\n\n{flavor}",
        parse_mode="Markdown",
    )


@dp.message(Command("brak"))
async def cmd_brak(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    proposer = message.from_user
    db.ensure_user(proposer.id, proposer.username or "", proposer.full_name)
    target_id, target_name, _ = resolve_target_from_command_or_reply(message, command)
    if not target_id:
        await message.reply(
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 `/brak` \u0432 \u043e\u0442\u0432\u0435\u0442 \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0438\u043b\u0438 `/brak @username`.",
            parse_mode="Markdown",
        )
        return
    if proposer.id == target_id:
        await message.reply("\u041d\u0430 \u0441\u0435\u0431\u0435 \u0436\u0435\u043d\u0438\u0442\u044c\u0441\u044f \u043d\u0435\u043b\u044c\u0437\u044f! \U0001f605")
        return
    if db.are_married(proposer.id, target_id):
        await message.reply("\u0412\u044b \u0443\u0436\u0435 \u0441\u043e\u0441\u0442\u043e\u0438\u0442\u0435 \u0432 \u0431\u0440\u0430\u043a\u0435! \U0001f491")
        return
    db.add_marriage_proposal(proposer.id, target_id, message.chat.id)
    proposer_mention = mention_by_user(proposer)
    target_mention = mention_by_db(target_id)
    await message.reply(
        f"\U0001f48d {proposer_mention} \u0434\u0435\u043b\u0430\u0435\u0442 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435 {target_mention}!",
        parse_mode="Markdown",
        reply_markup=brak_keyboard(proposer.id, target_id, message.chat.id),
    )


@dp.callback_query(F.data.startswith("brak_yes:"))
async def callback_brak_yes(callback: CallbackQuery):
    db.remember_chat(callback.message.chat.id)
    _, proposer_id, target_id, chat_id = callback.data.split(":")
    proposer_id, target_id, chat_id = int(proposer_id), int(target_id), int(chat_id)
    if callback.from_user.id != target_id:
        await callback.answer("\u042d\u0442\u043e \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u043d\u0435 \u0442\u0435\u0431\u0435 \U0001f63e", show_alert=True)
        return
    if not db.get_marriage_proposal(proposer_id, target_id, chat_id):
        await callback.answer("\u041f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u0443\u0436\u0435 \u043d\u0435\u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u043e.", show_alert=True)
        return
    if db.are_married(proposer_id, target_id):
        await callback.answer("\u0412\u044b \u0443\u0436\u0435 \u0432 \u0431\u0440\u0430\u043a\u0435.", show_alert=True)
        return
    db.add_marriage(proposer_id, target_id)
    db.delete_marriage_proposal(proposer_id, target_id, chat_id)
    m1 = mention_by_db(proposer_id)
    m2 = mention_by_db(target_id)
    await callback.message.edit_text(
        f"\U0001f492 {m1} \u0438 {m2} \u0442\u0435\u043f\u0435\u0440\u044c \u0441\u043e\u0441\u0442\u043e\u044f\u0442 \u0432 \u0431\u0440\u0430\u043a\u0435! \u041f\u043e\u0437\u0434\u0440\u0430\u0432\u043b\u044f\u0435\u043c! \U0001f389",
        parse_mode="Markdown",
    )
    await callback.answer("\u0421\u043e\u0433\u043b\u0430\u0441\u0438\u0435 \u043f\u0440\u0438\u043d\u044f\u0442\u043e \U0001f49e")


@dp.callback_query(F.data.startswith("brak_no:"))
async def callback_brak_no(callback: CallbackQuery):
    db.remember_chat(callback.message.chat.id)
    _, proposer_id, target_id, chat_id = callback.data.split(":")
    proposer_id, target_id, chat_id = int(proposer_id), int(target_id), int(chat_id)
    if callback.from_user.id != target_id:
        await callback.answer("\u042d\u0442\u043e \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u043d\u0435 \u0442\u0435\u0431\u0435 \U0001f63e", show_alert=True)
        return
    if not db.get_marriage_proposal(proposer_id, target_id, chat_id):
        await callback.answer("\u041f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u0443\u0436\u0435 \u043d\u0435\u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u043e.", show_alert=True)
        return
    db.delete_marriage_proposal(proposer_id, target_id, chat_id)
    m1 = mention_by_db(target_id)
    m2 = mention_by_db(proposer_id)
    await callback.message.edit_text(
        f"\U0001f494 {m1} \u043e\u0442\u043a\u043b\u043e\u043d\u0438\u043b(\u0430) \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u043e\u0442 {m2}.",
        parse_mode="Markdown",
    )
    await callback.answer("\u041d\u0443 \u0438 \u043b\u0430\u0434\u043d\u043e")


@dp.message(Command("razvod", "\u0440\u0430\u0437\u0432\u043e\u0434"))
async def cmd_razvod(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    target_id, _, _ = resolve_target_from_command_or_reply(message, command)
    if not target_id:
        await message.reply(
            "\u041e\u0442\u0432\u0435\u0442\u044c \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0442\u043e\u0433\u043e, \u0441 \u043a\u0435\u043c \u0440\u0430\u0437\u0432\u043e\u0434\u0438\u0448\u044c\u0441\u044f, \u0438\u043b\u0438 \u0443\u043a\u0430\u0436\u0438 @username."
        )
        return
    if not db.are_married(message.from_user.id, target_id):
        await message.reply("\u0412\u044b \u043d\u0435 \u0441\u043e\u0441\u0442\u043e\u0438\u0442\u0435 \u0432 \u0431\u0440\u0430\u043a\u0435. \U0001f937")
        return
    db.remove_marriage(message.from_user.id, target_id)
    from_mention = mention_by_user(message.from_user)
    target_mention = mention_by_db(target_id)
    await message.reply(
        f"\U0001f494 {from_mention} \u0438 {target_mention} \u0440\u0430\u0437\u0432\u0435\u043b\u0438\u0441\u044c.",
        parse_mode="Markdown",
    )


@dp.message(Command("families"))
async def cmd_families(message: Message):
    db.remember_chat(message.chat.id)
    marriages = db.get_all_marriages()
    if not marriages:
        await message.reply("\u0412 \u0431\u0435\u0441\u0435\u0434\u0435 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0431\u0440\u0430\u043a\u043e\u0432. \U0001f622")
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

    lines = ["\U0001f491 *\u0421\u0435\u043c\u044c\u0438 \u0432 \u0431\u0435\u0441\u0435\u0434\u0435:*\n"]
    for i, (root, members) in enumerate(families.items(), 1):
        names = [mention_by_db(uid) for uid in members]
        names_str = RING.join(names)
        oldest = min(family_dates[root]) if family_dates[root] else None
        duration = format_duration_since(oldest) if oldest else "\u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e \u0441\u043a\u043e\u043b\u044c\u043a\u043e"
        lines.append(f"{i}. {names_str} \u2014 \u0432 \u0431\u0440\u0430\u043a\u0435 \u0443\u0436\u0435 {duration}")

    await message.reply("\n".join(lines), parse_mode="Markdown")


@dp.message(Command("\u043e\u0440\u0433\u0438\u044f", "orgy"))
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
            await message.reply(f"\u23f3 \u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0430\u044f \u043e\u0440\u0433\u0438\u044f \u0432\u043e\u0437\u043c\u043e\u0436\u043d\u0430 \u0447\u0435\u0440\u0435\u0437 {h}\u0447 {m}\u043c\u0438\u043d.")
            return
    poll_msg = await bot.send_poll(
        chat_id=chat_id,
        question="\U0001f525 \u041e\u0420\u0413\u0418\u042f \u2014 \u0442\u044b \u0443\u0447\u0430\u0441\u0442\u0432\u0443\u0435\u0448\u044c?",
        options=["\u0414\u0430, \u044f \u0432 \u0434\u0435\u043b\u0435! \U0001f351", "\u041d\u0435\u0442, \u043d\u0435 \u0438\u043d\u0442\u0435\u0440\u0435\u0441\u0443\u0435\u0442 \U0001f607"],
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
        return " ".join(mention_by_db(uid) for uid in ids) if ids else "\u043d\u0438\u043a\u0442\u043e"

    yes_str = mentions(yes_ids)
    no_str = mentions(no_ids)
    await bot.send_message(
        chat_id,
        f"\U0001f525 {yes_str} \u043f\u043e\u0438\u043c\u0435\u043b\u0438 \u0436\u0451\u0441\u0442\u043a\u0438\u0439 \u0441\u0435\u043a\u0441, \u0430 {no_str} \u0441 \u0437\u0430\u0432\u0438\u0441\u0442\u044c\u044e \u0441\u043c\u043e\u0442\u0440\u0435\u043b\u0438.",
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
            name = mention_by_user(user)
            await message.reply(
                f"\u2705 \u0410\u043d\u043a\u0435\u0442\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430, {name}!\n\n{RULES_TEXT}",
                parse_mode="Markdown",
            )


async def setup_commands():
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
    group_commands = [
        BotCommand(command="info",      description="\u0410\u043d\u043a\u0435\u0442\u0430 (reply \u0438\u043b\u0438 @username)"),
        BotCommand(command="anketa",    description="\u0417\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u044c / \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u0430\u043d\u043a\u0435\u0442\u0443 (\u0438\u043b\u0438 /\u0430\u043d\u043a\u0435\u0442\u0430)"),
        BotCommand(command="rules",     description="\u041f\u0440\u0430\u0432\u0438\u043b\u0430 \u0431\u0435\u0441\u0435\u0434\u044b (\u0438\u043b\u0438 /\u043f\u0440\u0430\u0432\u0438\u043b\u0430)"),
        BotCommand(command="d20",       description="\u0411\u0440\u043e\u0441\u043e\u043a d20 \u2014 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043d\u0430 \u0443\u0441\u043f\u0435\u0445"),
        BotCommand(command="brak",      description="\u0421\u0434\u0435\u043b\u0430\u0442\u044c \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u0431\u0440\u0430\u043a\u0430"),
        BotCommand(command="razvod",    description="\u0420\u0430\u0437\u0432\u043e\u0434 (reply \u0438\u043b\u0438 @username)"),
        BotCommand(command="families",  description="\u0412\u0441\u0435 \u0431\u0440\u0430\u043a\u0438 \u0432 \u0431\u0435\u0441\u0435\u0434\u0435"),
        BotCommand(command="orgy",      description="\u041e\u0440\u0433\u0438\u044f-\u043e\u043f\u0440\u043e\u0441 (\u0438\u043b\u0438 /\u043e\u0440\u0433\u0438\u044f, 1 \u0440\u0430\u0437 \u0432 \u0441\u0443\u0442\u043a\u0438)"),
    ]
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())


async def main():
    db.init()
    await setup_commands()
    await announce_startup()
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await announce_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
