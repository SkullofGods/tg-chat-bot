import asyncio
import logging
import random
import re
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
from import_history import load_history_if_exists

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = Database()

RING = " 💍 "
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")
COMMON_STOP_WORDS = {
    "это", "как", "что", "чтобы", "или", "его", "ее", "её", "она", "они", "оно", "при", "про", "для",
    "без", "под", "над", "тут", "там", "пока", "если", "где", "когда", "потом", "тогда", "типа",
    "блин", "бля", "ага", "нет", "да", "тоже", "ещё", "еще", "только", "очень", "просто", "мне",
    "тебе", "тебя", "меня", "нас", "вам", "тут", "вот", "короче", "чето", "что-то", "какой", "какая",
    "который", "которая", "чел", "люди", "будет", "было", "были", "есть", "нету", "уже", "щас",
}
TAJIK_FLOORS = [
    "ламинат", "паркет", "линолеум", "керамогранит", "наливной пол", "ковролин", "пробковый пол",
    "плитка", "бетонная стяжка", "досчатый пол",
]

ANKETA_TEXT = (
    "📋 *Анкета участника*\n\n"
    "Ответь на вопросы следующим сообщением, не обязательно отвечать на всё и честно :D\n\n"
    "1. Имя / как можно обращаться\n"
    "2. Возраст\n"
    "3. Город\n"
    "4. Работа / учёба\n"
    "5. Увлечения, хобби — всё что хочешь рассказать о себе"
)

RULES_TEXT = (
    "*=ПРАВИЛА=*\n\n"
    "1️⃣ Запрещены оскорбления, срачи, доебки и прочий подобный негатив\n\n"
    "2️⃣ Запрещено агрессивное обсуждение каких-либо тем (т.е. обсуждение, перетекающее в конфликт или в переходы на личности)\n\n"
    "3️⃣ Поднимайте спорные темы (в т.ч. политику) осторожно в связи с пунктом правил № 2\n\n"
    "4️⃣ *НЕЛЬЗЯ* добавлять новых участников без согласования с Азартом или Ариной. Мы не против новых людей, просто хотим знать, кто и откуда появляется\n\n"
    "5️⃣ По возможности прячьте под спойлеры острые темы, которые могут стать чьим-то триггером, и ставьте *TW*\n\n"
    "―――\n"
    "В беседе не действует система варнов/мутов, нет какого-то чёткого количества предупреждений для кика\n"
    "Если чьи-то слова вас задели/обидели или что-то вас не устраивает — обращайтесь в лс к админам, не молчите, пожалуйста. Все разрулим ❤️\n\n"
    "Будьте солнышками и всё будет хорошо <з"
)

_D20_CRIT_FAIL = [
    "КРИТИЧЕСКИЙ ПРОВАЛ! 💀 Вселенная лично против тебя.",
    "ЭПИК ФЕЙЛ! 💀 Даже боги отвернулись.",
    "КРИТ-ПРОВАЛ! 💀 Это... это было невозможно провалить. Но ты смог(ла).",
]
_D20_FAIL = [
    "Провал. 😞 Не сегодня.",
    "Не прошло. 😞 Судьба сказала нет.",
    "Мимо. 😞 Бывает.",
    "Неудача. 😞 Попробуй ещё раз... или нет.",
]
_D20_SUCCESS = [
    "Успех! ✅ Получилось.",
    "Прошло! ✅ Удача улыбнулась.",
    "Успех. ✅ В этот раз повезло.",
    "Сработало! ✅ Так держать.",
]
_D20_CRIT_SUCCESS = [
    "КРИТИЧЕСКИЙ УСПЕХ! 🌟 Легенда. Абсолютная легенда.",
    "КРИТ! 🌟 Боги аплодируют.",
    "НАТУРАЛЬНАЯ 20! 🌟 Это войдёт в историю беседы.",
]

_BONUS_EVENTS = [
    {"tpl": "{member} поцеловал(а) на удачу", "val": (2, 5), "need_member": True},
    {"tpl": "{member} тихонько молился(ась) за тебя", "val": (1, 3), "need_member": True},
    {"tpl": "{member} дунул(а) на кубик", "val": (1, 3), "need_member": True},
    {"tpl": "{member} подарил(а) амулет удачи", "val": (2, 4), "need_member": True},
    {"tpl": "{member} шепнул(а) нужное заклинание", "val": (1, 4), "need_member": True},
    {"tpl": "{member} стоял(а) рядом и верил(а)", "val": (1, 2), "need_member": True},
    {"tpl": "{member} подкинул(а) проклятие", "val": (-3, -1), "need_member": True},
    {"tpl": "{member} сглазил(а)", "val": (-3, -2), "need_member": True},
    {"tpl": "{member} держал(а) кулачки против", "val": (-2, -1), "need_member": True},
    {"tpl": "{member} отвлёк(ла) в самый важный момент", "val": (-2, -1), "need_member": True},
    {"tpl": "выпал день рождения у кого-то в чате", "val": (2, 5), "need_member": False},
    {"tpl": "сегодня пятница", "val": (1, 3), "need_member": False},
    {"tpl": "кто-то варит борщ неподалёку", "val": (1, 2), "need_member": False},
    {"tpl": "твой гороскоп сегодня благоприятный", "val": (1, 3), "need_member": False},
    {"tpl": "кубик упал со стола и закатился под диван, пришлось достать", "val": (1, 5), "need_member": False},
    {"tpl": "ретроградный Меркурий", "val": (-3, -1), "need_member": False},
    {"tpl": "Венера в Козероге", "val": (-2, -1), "need_member": False},
    {"tpl": "кто-то в чате чихнул", "val": (-1, -1), "need_member": False},
    {"tpl": "пролитый кофе на клавиатуру", "val": (-2, -1), "need_member": False},
    {"tpl": "в комнате чёрная кошка", "val": (-3, -1), "need_member": False},
    {"tpl": "полнолуние", "val": (-1, 2), "need_member": False},
]

_LEFT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}
orgy_state: dict[int, dict] = {}
tajikistan_state: dict[int, datetime] = {}
shutdown_sent = False


def extract_words(text: str) -> list[str]:
    words = [w.lower() for w in WORD_RE.findall(text or "")]
    return [w for w in words if w not in COMMON_STOP_WORDS]


def display_name_by_user(user) -> str:
    nick = db.get_nickname(user.id)
    if nick:
        return nick
    if user.first_name:
        return user.first_name
    return user.full_name


def display_name_by_db(user_id: int) -> str:
    nick = db.get_nickname(user_id)
    if nick:
        return nick
    u = db.get_user(user_id)
    if u:
        return u["full_name"] or u["username"] or f"user{user_id}"
    return f"user{user_id}"


def mention_by_user(user) -> str:
    nick = db.get_nickname(user.id)
    if nick:
        return f"[{nick}](tg://user?id={user.id})"
    if user.username:
        return f"@{user.username}"
    return user.full_name


def mention_by_db(user_id: int) -> str:
    nick = db.get_nickname(user_id)
    if nick:
        return f"[{nick}](tg://user?id={user_id})"
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
        return -1, raw, raw
    return None, None, None


def brak_keyboard(proposer_id: int, target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Да", callback_data=f"brak_yes:{proposer_id}:{target_id}:{chat_id}"),
            InlineKeyboardButton(text="Нет", callback_data=f"brak_no:{proposer_id}:{target_id}:{chat_id}"),
        ]]
    )


def roll_bonus(roller_id: int) -> tuple[int, str]:
    if random.random() >= 0.10:
        return 0, ""
    event = random.choice(_BONUS_EVENTS)
    val = random.randint(event["val"][0], event["val"][1])
    sign = "+" if val >= 0 else ""
    if event["need_member"]:
        all_users = db.get_all_users()
        others = [u for u in all_users if u["user_id"] != roller_id]
        member_str = display_name_by_db(random.choice(others)["user_id"]) if others else "кто-то из чата"
        description = event["tpl"].format(member=member_str)
    else:
        description = event["tpl"]
    return val, f"_{description}_ — {sign}{val}"


def random_known_user(exclude_user_id: int | None = None) -> int | None:
    users = db.get_all_users()
    pool = [u for u in users if exclude_user_id is None or u["user_id"] != exclude_user_id]
    if not pool:
        return None
    return random.choice(pool)["user_id"]


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


async def tajikistan_followup(chat_id: int, reply_to_message_id: int, event_type: str):
    await asyncio.sleep(300)
    target_id = random_known_user()
    target = mention_by_db(target_id) if target_id else "Кто-то"
    if event_type == "plankton":
        floor = random.choice(TAJIK_FLOORS)
        text = f"{target} гомосексуализирован! Теперь вы любите {floor}."
    else:
        text = f"{target} накормили пловом бля("
    with suppress(Exception):
        await bot.send_message(chat_id, text, parse_mode="Markdown", reply_to_message_id=reply_to_message_id)


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
    name = mention_by_user(user)
    await bot.send_message(event.chat.id, f"👋 Привет, {name}! Добро пожаловать!\n\n{ANKETA_TEXT}", parse_mode="Markdown")
    db.set_awaiting_anketa(user.id, event.chat.id)


@dp.message(Command("info"))
async def cmd_info(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    target_id, target_name, _ = resolve_target_from_command_or_reply(message, command)
    if target_id is None:
        await message.reply("Ответь на сообщение пользователя или укажи @username.")
        return
    if target_id == -1:
        await message.reply(f"Пользователь @{target_name} не найден в базе — возможно, ещё не писал в чат. 🤷")
        return
    anketa = db.get_anketa(target_id)
    display = mention_by_db(target_id)
    if not anketa:
        await message.reply(f"У {display} анкеты пока нет. 🤷", parse_mode="Markdown")
        return
    await message.reply(f"📋 *Анкета* {display}\n\n{anketa}", parse_mode="Markdown")


@dp.message(Command("анкета", "anketa"))
async def cmd_anketa(message: Message):
    db.remember_chat(message.chat.id)
    db.ensure_user(message.from_user.id, message.from_user.username or "", message.from_user.full_name)
    db.set_awaiting_anketa(message.from_user.id, message.chat.id)
    await message.reply(ANKETA_TEXT, parse_mode="Markdown")


@dp.message(Command("rules", "правила"))
async def cmd_rules(message: Message):
    db.remember_chat(message.chat.id)
    await message.reply(RULES_TEXT, parse_mode="Markdown")


@dp.message(Command("ник", "nick"))
async def cmd_nick(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    user = message.from_user
    db.ensure_user(user.id, user.username or "", user.full_name)
    if not command.args or not command.args.strip():
        current = db.get_nickname(user.id)
        if current:
            await message.reply(f"Твой текущий никнейм: *{current}*\nЧтобы убрать его, напиши `/ник -`", parse_mode="Markdown")
        else:
            await message.reply("У тебя пока нет никнейма.\nУстанови: `/ник ИмяКоторое Хочешь`", parse_mode="Markdown")
        return
    new_nick = command.args.strip()
    if new_nick == "-":
        db.delete_nickname(user.id)
        await message.reply("Никнейм удалён. Бот будет обращаться по тэгу/имени. ✅")
        return
    if len(new_nick) > 32:
        await message.reply("Никнейм слишком длинный (макс. 32 символа). 😕")
        return
    db.set_nickname(user.id, new_nick)
    await message.reply(f"Никнейм установлен: [{new_nick}](tg://user?id={user.id}) ✅\nТеперь бот будет обращаться к тебе именно так.", parse_mode="Markdown")


@dp.message(Command("d20"))
async def cmd_d20(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    user = message.from_user
    db.ensure_user(user.id, user.username or "", user.full_name)
    if command.args:
        try:
            threshold = int(command.args.strip())
            threshold = max(1, min(22, threshold))
        except ValueError:
            threshold = random.randint(7, 22)
    else:
        threshold = random.randint(7, 22)
    roll = random.randint(1, 20)
    bonus, bonus_desc = roll_bonus(user.id)
    effective = roll + bonus
    bonus_str = f"\n🎰 {bonus_desc} → итого *{effective}*" if bonus != 0 else ""
    if roll == 1:
        flavor = random.choice(_D20_CRIT_FAIL)
    elif roll == 20:
        flavor = random.choice(_D20_CRIT_SUCCESS)
    elif effective < threshold:
        flavor = random.choice(_D20_FAIL)
    else:
        flavor = random.choice(_D20_SUCCESS)
    roller = mention_by_user(user)
    context = ""
    if message.reply_to_message and message.reply_to_message.text:
        preview = message.reply_to_message.text[:60]
        if len(message.reply_to_message.text) > 60:
            preview += "…"
        context = f"_«{preview}»_\n\n"
    result_line = f"🎲 {roller} бросает d20 — порог *{threshold}*, выпало *{roll}*{bonus_str}."
    await message.reply(f"{context}{result_line}\n\n{flavor}", parse_mode="Markdown")


@dp.message(Command("stats", "stat"))
async def cmd_stats(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    target_id, target_name, _ = resolve_target_from_command_or_reply(message, command)
    if target_id is None and command.args:
        target_id = -1
    if target_id == -1:
        await message.reply(f"Пользователь @{target_name} не найден в базе — возможно, ещё не писал в чат.")
        return

    if target_id is not None:
        stats = db.get_user_stats(message.chat.id, target_id)
        if not stats:
            await message.reply("По этому пользователю пока нет статистики в этом чате.")
            return
        top_words = db.get_user_top_words(message.chat.id, target_id, limit=5)
        top_word = top_words[0]["word"] if top_words else "—"
        top_word_count = top_words[0]["count"] if top_words else 0
        avg_words = round(stats["word_count"] / max(stats["message_count"], 1), 1)
        weird_fact = ""
        if stats["message_count"] >= 1000:
            weird_fact = "\n🏆 Лютый флудер беседы."
        elif avg_words >= 12:
            weird_fact = "\n🗣 Любит писать развернуто."
        elif avg_words <= 3:
            weird_fact = "\n🤏 Человек немногословный."
        text = (
            f"📊 *Статистика пользователя* {mention_by_db(target_id)}\n\n"
            f"⏳ В статистике уже: *{format_duration_since(stats['created_at'])}*\n"
            f"💬 Сообщений: *{stats['message_count']}*\n"
            f"📝 Написано слов: *{stats['word_count']}*\n"
            f"📏 В среднем слов на сообщение: *{avg_words}*\n"
            f"🔤 Самое частое слово: *{top_word}* ({top_word_count})"
            f"{weird_fact}"
        )
        if len(top_words) > 1:
            extra = ", ".join(f"{w['word']} ({w['count']})" for w in top_words[:5])
            text += f"\n\nТоп слов: {extra}"
        await message.reply(text, parse_mode="Markdown")
        return

    top_users = db.get_chat_top_users(message.chat.id, limit=10)
    top_words = db.get_chat_top_words(message.chat.id, limit=10)
    if not top_users and not top_words:
        await message.reply("Пока статистики маловато — сначала поболтайте немного.")
        return
    user_lines = []
    for i, user_row in enumerate(top_users, 1):
        user_lines.append(f"{i}. {mention_by_db(user_row['user_id'])} — {user_row['message_count']} сообщений")
    word_lines = []
    for i, row in enumerate(top_words, 1):
        word_lines.append(f"{i}. *{row['word']}* — {row['count']}")
    text = "📈 *Общая статистика чата*\n\n"
    text += "👥 *Топ по сообщениям:*\n" + ("\n".join(user_lines) if user_lines else "пусто")
    text += "\n\n🔤 *Топ слов чата:*\n" + ("\n".join(word_lines) if word_lines else "пусто")
    await message.reply(text, parse_mode="Markdown")


@dp.message(Command("таджикистан", "tadjikistan"))
async def cmd_tadjikistan(message: Message):
    db.remember_chat(message.chat.id)
    chat_id = message.chat.id
    now = datetime.utcnow()
    last = tajikistan_state.get(chat_id)
    if last and now - last < timedelta(minutes=5):
        remain = timedelta(minutes=5) - (now - last)
        mins = int(remain.total_seconds() // 60)
        secs = int(remain.total_seconds() % 60)
        await message.reply(f"🇹🇯 Таджикистан пока отдыхает. Попробуй через {mins}м {secs}с.")
        return
    tajikistan_state[chat_id] = now

    event_type = random.choice(["legs", "plankton", "horde", "plain"])
    target_id = random_known_user()
    target = mention_by_db(target_id) if target_id else "Кто-то"

    if event_type == "legs":
        await message.reply(f"🇹🇯 {target} показывает ножки 0_0", parse_mode="Markdown")
        return
    if event_type == "plain":
        await message.reply("🇹🇯 даа... таджикистан...")
        return
    if event_type == "plankton":
        sent = await message.reply("🇹🇯 вы замечаете тонкие усики планктона в небе... не к добру...")
        asyncio.create_task(tajikistan_followup(chat_id, sent.message_id, "plankton"))
        return
    sent = await message.reply("🇹🇯 орда таджиков на горизонте!!!")
    asyncio.create_task(tajikistan_followup(chat_id, sent.message_id, "horde"))


@dp.message(Command("brak"))
async def cmd_brak(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    proposer = message.from_user
    db.ensure_user(proposer.id, proposer.username or "", proposer.full_name)
    target_id, target_name, _ = resolve_target_from_command_or_reply(message, command)
    if target_id is None:
        await message.reply("Используй `/brak` в ответ на сообщение или `/brak @username`.", parse_mode="Markdown")
        return
    if target_id == -1:
        await message.reply(f"Пользователь @{target_name} не найден в базе — возможно, ещё не писал в чат. 🤷")
        return
    if proposer.id == target_id:
        await message.reply("На себе жениться нельзя! 😅")
        return
    if db.are_married(proposer.id, target_id):
        await message.reply("Вы уже состоите в браке! 💑")
        return
    db.add_marriage_proposal(proposer.id, target_id, message.chat.id)
    await message.reply(f"💍 {mention_by_user(proposer)} делает предложение {mention_by_db(target_id)}!", parse_mode="Markdown", reply_markup=brak_keyboard(proposer.id, target_id, message.chat.id))


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
    await callback.message.edit_text(f"💒 {mention_by_db(proposer_id)} и {mention_by_db(target_id)} теперь состоят в браке! Поздравляем! 🎉", parse_mode="Markdown")
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
    await callback.message.edit_text(f"💔 {mention_by_db(target_id)} отклонил(а) предложение от {mention_by_db(proposer_id)}.", parse_mode="Markdown")
    await callback.answer("Ну и ладно")


@dp.message(Command("razvod", "развод"))
async def cmd_razvod(message: Message, command: CommandObject):
    db.remember_chat(message.chat.id)
    target_id, _, _ = resolve_target_from_command_or_reply(message, command)
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
        union(item["user1_id"], item["user2_id"])

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
        names_str = RING.join(mention_by_db(uid) for uid in members)
        oldest = min(family_dates[root]) if family_dates[root] else None
        duration = format_duration_since(oldest) if oldest else "неизвестно сколько"
        lines.append(f"{i}. {names_str} — в браке уже {duration}")
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
    poll_msg = await bot.send_poll(chat_id=chat_id, question="🔥 ОРГИЯ — ты участвуешь?", options=["Да, я в деле! 🍑", "Нет, не интересует 😇"], is_anonymous=False, allows_multiple_answers=False)
    orgy_state[chat_id] = {"last_time": now, "poll_message_id": poll_msg.message_id, "poll_id": poll_msg.poll.id, "yes_voters": [], "no_voters": [], "all_voter_ids": set()}
    asyncio.create_task(finish_orgy_after(chat_id, poll_msg.message_id))


async def finish_orgy_after(chat_id: int, poll_message_id: int):
    await asyncio.sleep(ORGY_POLL_DURATION_SECONDS)
    await finish_orgy(chat_id, poll_message_id)


async def finish_orgy(chat_id: int, poll_message_id: int):
    with suppress(Exception):
        await bot.stop_poll(chat_id, poll_message_id)
    with suppress(Exception):
        await bot.delete_message(chat_id, poll_message_id)
    state = orgy_state.get(chat_id, {})
    yes_ids = state.get("yes_voters", [])
    no_ids = state.get("no_voters", [])
    yes_str = " ".join(mention_by_db(uid) for uid in yes_ids) if yes_ids else "никто"
    no_str = " ".join(mention_by_db(uid) for uid in no_ids) if no_ids else "никто"
    await bot.send_message(chat_id, f"🔥 {yes_str} поимели жёсткий секс, а {no_str} с завистью смотрели.", parse_mode="Markdown")


@dp.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer):
    user_id = poll_answer.user.id
    for _, state in orgy_state.items():
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

    text_for_stats = message.text or message.caption or ""
    if text_for_stats and not text_for_stats.startswith("/"):
        db.record_message(message.chat.id, user.id, extract_words(text_for_stats))

    if db.is_awaiting_anketa(user.id, message.chat.id):
        text = message.text or message.caption or ""
        if text and not text.startswith("/"):
            db.save_anketa(user.id, text)
            db.clear_awaiting_anketa(user.id, message.chat.id)
            await message.reply(f"✅ Анкета сохранена, {mention_by_user(user)}!\n\n{RULES_TEXT}", parse_mode="Markdown")


async def setup_commands():
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
    group_commands = [
        BotCommand(command="info", description="Анкета (reply или @username)"),
        BotCommand(command="anketa", description="Заполнить / обновить анкету (или /анкета)"),
        BotCommand(command="rules", description="Правила беседы (или /правила)"),
        BotCommand(command="nick", description="Установить никнейм (или /ник)"),
        BotCommand(command="d20", description="Бросок d20 — проверка на успех"),
        BotCommand(command="stats", description="Статистика чата или пользователя"),
        BotCommand(command="tadjikistan", description="Случайный таджикистан-ивент"),
        BotCommand(command="brak", description="Сделать предложение брака"),
        BotCommand(command="razvod", description="Развод (reply или @username)"),
        BotCommand(command="families", description="Все браки в беседе"),
        BotCommand(command="orgy", description="Оргия-опрос (или /оргия)"),
    ]
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())


async def main():
    # Загружаем историю из result.json при первом старте (если файл есть и ещё не импортировался)
    imported = load_history_if_exists(db)
    if imported:
        logger.info("History import completed on startup.")

    await setup_commands()
    await announce_startup()
    try:
        await dp.start_polling(bot, allowed_updates=["message", "chat_member", "poll_answer", "callback_query"])
    finally:
        await announce_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
