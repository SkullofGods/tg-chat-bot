"""Скрытые команды хозяина бота: /debug, /say и /debug_casino_dev (подкрутка казино).
Для всех остальных этих команд как будто не существует. В мини-приложении подкрутки нет:
там хозяин — такой же игрок, как все."""

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
import casino_arcade as arcade
import casino_engine
import casino_rig
import casino_tables
import limits
import members
import scheduler
import sglypa
import texts
from config import ARCADE_PRIZE_HOUR, BACKUP_CHAT_ID, DB_PATH, LOCAL_TZ, OWNER_ID, SPAM_DAY_HOUR
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
        paid = db.get_meta(f"arcade_prize:{main_chat}:{local_today()}") is not None
        holders = sum(1 for key in arcade.GAMES if db.arcade_record(main_chat, key))
        when = "выдана" if paid else (f"ждёт {ARCADE_PRIZE_HOUR}:00" if now.hour < ARCADE_PRIZE_HOUR
                                      else "ждёт рекордов" if not holders else "вот-вот")
        lines.append(f"🏆 Премия рекордсменам: {when} · рекордов держат {holders} из {len(arcade.GAMES)}")

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
        [("🇹🇯 Перевыбрать таджика", "tajik"), ("🏆 Премия рекордсменам", "prize")],
        [("📜 Логи", "logs")],
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
        elif action == "prize":
            await scheduler.pay_arcade_records_if_needed(force=True)
            note = "🏆 Премия выдана (если было кому)"
        elif action == "logs" and isinstance(callback.message, Message):
            await callback.message.answer(_logs_text())
    except Exception as e:
        note = f"❌ {e}"
    await callback.answer(note[:200])
    if isinstance(callback.message, Message):
        with suppress(TelegramAPIError):  # «message is not modified», если ничего не поменялось
            await callback.message.edit_text(_panel_text(), reply_markup=_keyboard())


# ── Подкрутка казино ──────────────────────────────────────────────────────────
# Каждая срабатывает один раз в главной беседе, игроки ничего не видят. Положение патрона тут нарочно
# не показываем: хозяин играет наравне со всеми.

RIG_TITLES = {"slots": "🎰 Слоты", "blackjack": "🃏 Блэкджек", "coin": "🪙 Монетка", "roulette": "🎡 Рулетка",
              "race": "🏇 Скачки"}
COIN_BUTTONS = {"heads": "🦅 Орёл", "tails": "👑 Решка", "edge": "🪙 На ребро", "stolen": "🇺🇿 Украдут"}


def _buttons(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows
    ])


def _rig_line(item: dict, names: dict[int, str]) -> str:
    value = item["value"]
    what = {"slots": "джекпот 7️⃣7️⃣7️⃣", "blackjack": "сразу 21", "coin": casino_rig.COIN_RESULTS.get(value),
            "roulette": f"выпадет {value}", "race": f"победит №{value}"}[item["game"]]
    who = f" → {escape(names.get(item['user_id'], 'игроку'))}" if item["user_id"] else " → первому, кто сыграет"
    return f"• {RIG_TITLES[item['game']]}: {what}{who if item['game'] in casino_rig.PERSONAL_GAMES else ''}"


def _rig_menu(chat_id: int | None) -> tuple[str, InlineKeyboardMarkup]:
    rigs = casino_rig.active(chat_id) if chat_id else []
    names = {player["id"]: player["name"] for player in casino_engine.players(chat_id)} if rigs else {}
    lines = ["🎰 <b>Подкрутка казино</b>", "Срабатывает один раз, игроки ничего не заметят.", ""]
    lines += [_rig_line(item, names) for item in rigs] or ["Сейчас всё честно 😇"]
    rows = [
        [("🎰 Джекпот в слотах", "rig:slots"), ("🃏 Блэкджек 21", "rig:blackjack")],
        [("🪙 Монетка", "rig:coin"), ("🎡 Число в рулетке", "rig:roulette")],
        [("🏇 Победитель забега", "rig:race"), ("🔫 Пустой барабан", "rig:rr")],
    ]
    if rigs:
        rows.append([("🧹 Сбросить подкрутки", "rig:clear")])
    rows.append([("✖️ Закрыть", "rig:close")])
    return "\n".join(lines), _buttons(rows)


def _rig_targets(chat_id: int, game: str, value: str) -> tuple[str, InlineKeyboardMarkup]:
    rows = [[("🎲 Первому, кто сыграет", f"rig:set:{game}:{value}:0")]]
    players = casino_engine.players(chat_id)[:60]
    for i in range(0, len(players), 2):
        rows.append([(player["name"][:32], f"rig:set:{game}:{value}:{player['id']}") for player in players[i:i + 2]])
    rows.append([("« Назад", "rig:menu")])
    what = COIN_BUTTONS[value] if game == "coin" else {"slots": "джекпот", "blackjack": "сразу 21"}[game]
    return f"{RIG_TITLES[game]}: {what}\nКого порадовать?", _buttons(rows)


def _rig_numbers() -> tuple[str, InlineKeyboardMarkup]:
    rows = [[("0 🟢", "rig:set:roulette:0:0")]]
    for start in range(1, 37, 6):
        rows.append([(f"{n}{'🔴' if n in casino_tables.RED_NUMBERS else '⚫'}", f"rig:set:roulette:{n}:0")
                     for n in range(start, start + 6)])
    rows.append([("« Назад", "rig:menu")])
    return "🎡 Какое число выпадет в следующем спине?", _buttons(rows)


def _rig_horses(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rows = [[(f"№{horse['no']} {horse['name']} ×{horse['odds']:g}", f"rig:set:race:{horse['no']}:0")]
            for horse in casino_tables.lineup_for_rig(chat_id)]
    rows.append([("« Назад", "rig:menu")])
    return "🏇 Кто победит в ближайшем забеге?", _buttons(rows)


def _rig_coins() -> tuple[str, InlineKeyboardMarkup]:
    items = [(label, f"rig:coin:{key}") for key, label in COIN_BUTTONS.items()]
    return "🪙 Как упадёт монетка?", _buttons([items[:2], items[2:], [("« Назад", "rig:menu")]])


async def _show(callback: CallbackQuery, note: str | None, text: str, markup: InlineKeyboardMarkup):
    await callback.answer(note)
    if isinstance(callback.message, Message):
        with suppress(TelegramAPIError):  # «message is not modified», если ничего не поменялось
            await callback.message.edit_text(text, reply_markup=markup)


@router.message(Command("debug_casino_dev"))
async def cmd_debug_casino(message: Message):
    """Скрытая команда подкрутки. Меню приходит хозяину в личку, в беседе команда стирается."""
    in_group = message.chat.type != "private"
    if in_group:
        with suppress(TelegramAPIError):
            await message.delete()
    try:
        text, markup = _rig_menu(db.get_main_chat())
        await bot.send_message(OWNER_ID, text, reply_markup=markup)
    except TelegramAPIError:
        if in_group:
            await message.answer(texts.DEBUG_NO_DM)


@router.callback_query(F.data.startswith("rig:"))
async def on_rig_button(callback: CallbackQuery):
    chat_id = db.get_main_chat()
    if chat_id is None:
        await callback.answer("Бот ещё не знает ни одной беседы")
        return
    parts = callback.data.split(":")
    action, note = parts[1], None
    if action == "close":
        await callback.answer()
        if isinstance(callback.message, Message):
            with suppress(TelegramAPIError):
                await callback.message.delete()
        return
    if action == "set" and len(parts) == 5:
        _, _, game, value, target = parts
        try:
            casino_rig.set_rig(chat_id, game, value if game == "coin" else int(value), int(target) or None)
        except ValueError as e:
            await callback.answer(str(e))
            return
        view, note = _rig_menu(chat_id), "Подкручено 🤫"
    elif action == "rr":
        casino_engine.empty_cylinder(chat_id)
        view, note = _rig_menu(chat_id), "🔫 Патрон вынут: до перезарядки барабан пустой"
    elif action == "clear":
        casino_rig.clear(chat_id)
        view, note = _rig_menu(chat_id), "Всё снова честно"
    elif action in ("slots", "blackjack"):
        view = _rig_targets(chat_id, action, "1")
    elif action == "coin":
        view = _rig_targets(chat_id, "coin", parts[2]) if len(parts) > 2 and parts[2] in COIN_BUTTONS else _rig_coins()
    elif action == "roulette":
        view = _rig_numbers()
    elif action == "race":
        view = _rig_horses(chat_id)
    else:
        view = _rig_menu(chat_id)
    await _show(callback, note, *view)


@router.message(Command("say", "скажи"), F.chat.type == "private")
async def cmd_say(message: Message, command: CommandObject):
    text = (command.args or "").strip()
    main_chat = db.get_main_chat()
    if not text or not main_chat:
        await message.answer("📣 <code>/say текст</code> — бот напишет это в главную беседу от своего имени.")
        return
    await bot.send_message(main_chat, text, parse_mode=None)
    await message.answer("📣 Отправлено")
