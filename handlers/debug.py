"""Скрытая панель хозяина бота: /debug и /say. Для всех остальных этих команд как будто не существует."""

import platform
import time
from contextlib import suppress
from datetime import datetime
from html import escape
from pathlib import Path

import aiogram
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import backup
import limits
import members
import scheduler
import sglypa
import texts
from config import BACKUP_CHAT_ID, DB_PATH, LOCAL_TZ, OWNER_ID, SPAM_DAY_HOUR
from loader import bot, db
from textstats import fmt_num
from utils import local_today, recent_logs, spawn

router = Router(name="debug")
router.message.filter(F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)

_STARTED = time.monotonic()


def _uptime() -> str:
    days, rest = divmod(int(time.monotonic() - _STARTED), 86400)
    hours, rest = divmod(rest, 3600)
    return (f"{days} д " if days else "") + f"{hours} ч {rest // 60} мин"


def _panel_text() -> str:
    now = datetime.now(LOCAL_TZ)
    size = sum(p.stat().st_size for p in (DB_PATH, Path(f"{DB_PATH}-wal")) if p.exists()) / 1024 / 1024
    lines = [
        "🛠 <b>Дебаг-панель</b>",
        "",
        f"⏱ Аптайм: {_uptime()} · Python {platform.python_version()} · aiogram {aiogram.__version__}",
        f"🗄 База: <code>{escape(str(DB_PATH))}</code> — {size:.1f} МБ",
    ]
    if BACKUP_CHAT_ID is None:
        lines.append("💾 Бэкапы: выключены (BACKUP_CHAT_ID=off)")
    else:
        state = backup.state
        last = f"{state.last_backup_at.astimezone(LOCAL_TZ):%d.%m %H:%M}" if state.last_backup_at else "ещё не было"
        pending = db.revision != state.saved_revision or db.important_revision != state.saved_important_revision
        lines.append(
            f"💾 Бэкапы: {'включены' if state.enabled else '⚠️ ВЫКЛЮЧЕНЫ'} · последний {last} · "
            f"несохранённые изменения: {'есть' if pending else 'нет'}"
        )

    main_chat = db.get_main_chat()
    lines.append(f"💬 Бесед: {len(db.get_known_chats())}" + (f" · главная <code>{main_chat}</code>" if main_chat else ""))
    if main_chat:
        rows = db.get_member_rows(main_chat)
        lines.append(f"👥 Участников в базе: {len(rows)}, из них ушли: {sum(1 for r in rows if not r['is_member'])}")
        until = sglypa.messages_until_next(main_chat)
        lines.append(
            f"🐸 Сглыпа: фраз в памяти {fmt_num(db.count_phrases(main_chat))}"
            + (f" · до реплики {until} сообщ." if until is not None else "")
        )
        winner = db.get_tajik_of_day(main_chat, local_today())
        lines.append(f"🇹🇯 Таджик дня: {members.display_name(winner) if winner else 'ещё не выбран'}")

    active = limits.spam_day_active(now)
    planned = limits.next_spam_day(now.date())
    lines.append(
        f"🎰 Спам-день: {'🔥 ИДЁТ СЕЙЧАС' if active else 'нет'} · по плану ближайший {planned:%d.%m.%Y} с {SPAM_DAY_HOUR}:00"
    )
    lines.append(f"⏳ Активных кулдаунов: {limits.active_count()}")
    lines.append(f"📜 Предупреждений и ошибок с запуска: {len(recent_logs)}")
    return "\n".join(lines)


def _keyboard() -> InlineKeyboardMarkup:
    spam = limits.spam_day_active()
    rows = [
        [("🔄 Обновить", "refresh"), ("💾 Бэкап сейчас", "backup")],
        [("🎰 Выключить спам-день" if spam else "🎰 Включить спам-день", "spam_off" if spam else "spam_on"),
         ("⏳ Сбросить кулдауны", "cooldowns")],
        [("🐸 Сглыпа, скажи", "sglypa"), ("👥 Сверить участников", "sync")],
        [("🇹🇯 Перевыбрать таджика", "tajik"), ("📜 Логи", "logs")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=f"dbg:{action}") for text, action in row] for row in rows
    ])


def _logs_text() -> str:
    if not recent_logs:
        return "📜 В логах чисто ✨"
    body = "\n\n".join(recent_logs)[-3500:]
    return f"📜 <b>Последние предупреждения и ошибки</b>\n<pre>{escape(body, quote=False)}</pre>"


@router.message(Command("debug", "дебаг"))
async def cmd_debug(message: Message):
    in_group = message.chat.type != "private"
    if in_group:
        with suppress(TelegramAPIError):
            await message.delete()  # не светим команду в беседе (если бот админ)
    try:
        await bot.send_message(OWNER_ID, _panel_text(), reply_markup=_keyboard())
    except TelegramAPIError:
        if in_group:
            await message.answer(texts.DEBUG_NO_DM)


@router.callback_query(F.data.startswith("dbg:"))
async def on_debug_button(callback: CallbackQuery):
    action = callback.data.split(":", 1)[1]
    main_chat = db.get_main_chat()
    note = "Готово"
    try:
        if action == "backup":
            note = "💾 Бэкап: " + await backup.make_backup("из дебаг-панели")
        elif action in ("spam_on", "spam_off"):
            limits.set_spam_day(datetime.now(LOCAL_TZ).date(), action == "spam_on")
            note = "🎰 Спам-день включён до конца дня" if action == "spam_on" else "🎰 Спам-день выключен"
            if action == "spam_on":
                await scheduler.announce_spam_day_if_needed()
        elif action == "cooldowns":
            limits.reset()
            note = "⏳ Все кулдауны сброшены"
        elif action == "sglypa" and main_chat:
            await sglypa.say_in_chat(main_chat)
            note = "🐸 Сказала"
        elif action == "sync":
            spawn(members.sync_members())
            note = "👥 Сверка запущена в фоне"
        elif action == "tajik" and main_chat:
            db.delete_tajik_of_day(main_chat, local_today())
            note = "🇹🇯 Таджик дня сброшен — можно снова /tajik"
        elif action == "logs" and isinstance(callback.message, Message):
            await callback.message.answer(_logs_text())
    except Exception as e:
        note = f"❌ {e}"
    await callback.answer(note[:200])
    if isinstance(callback.message, Message):
        with suppress(TelegramAPIError):  # «message is not modified», если ничего не поменялось
            await callback.message.edit_text(_panel_text(), reply_markup=_keyboard())


@router.message(Command("say", "скажи"), F.chat.type == "private")
async def cmd_say(message: Message, command: CommandObject):
    text = (command.args or "").strip()
    main_chat = db.get_main_chat()
    if not text or not main_chat:
        await message.answer("📣 <code>/say текст</code> — бот напишет это в главную беседу от своего имени.")
        return
    await bot.send_message(main_chat, text, parse_mode=None)
    await message.answer("📣 Отправлено")
