"""Казино в беседе: кнопка в мини-приложение, статистика и то, что делается с другими людьми (переводы, дуэли).
Сами игры живут в мини-приложении — см. casino_engine.py и webapp/."""

import random
import re
import time
from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

import limits
import members
import texts
import webserver
from casino_engine import GAME_NAMES, MIN_BET, money, signed
from config import WEBAPP_URL
from loader import bot, db
from textstats import count_with_word, fmt_num
from utils import local_today

router = Router(name="casino")

DEFAULT_BET = 100
DUEL_TTL_SECONDS = 10 * 60
TIMES = ("раз", "раза", "раз")


def parse_amount(token: str, balance: int) -> int | None:
    """«100», «5к», «все» / «ва-банк»."""
    token = token.lower().strip()
    if token in ("все", "всё", "ва-банк", "вабанк", "allin", "all", "max"):
        return balance
    match = re.fullmatch(r"(\d{1,9})([кk])?", token)
    if not match:
        return None
    return int(match.group(1)) * (1000 if match.group(2) else 1)


def _split_args(args: str | None, balance: int) -> tuple[list[int], list[str]]:
    """Разделяет аргументы команды на суммы и всё остальное."""
    amounts, rest = [], []
    for token in (args or "").split():
        amount = parse_amount(token, balance)
        if amount is None:
            rest.append(token)
        else:
            amounts.append(amount)
    return amounts, rest


async def _group_only(message: Message) -> bool:
    if members.is_group(message.chat):
        return True
    await message.reply(texts.CASINO_GROUP_ONLY)
    return False


async def _check_bet(message: Message, bet: int) -> bool:
    balance = db.get_balance(message.chat.id, message.from_user.id)
    if balance < MIN_BET or bet > balance:
        await message.reply(texts.NOT_ENOUGH_MONEY.format(balance=money(balance)))
        return False
    if bet < MIN_BET:
        await message.reply(texts.MIN_BET_TEXT.format(min_bet=money(MIN_BET)))
        return False
    return True


# ── Вход в казино и статистика ────────────────────────────────────────────────


@router.message(Command("casino", "казино"))
async def cmd_casino(message: Message):
    if not WEBAPP_URL:
        await message.reply(texts.CASINO_NOT_READY)
        return
    if message.chat.type == "private":
        button = InlineKeyboardButton(text=texts.CASINO_BUTTON, web_app=WebAppInfo(url=f"{WEBAPP_URL}/"))
    else:  # в беседах кнопки web_app запрещены — открываем по ссылке, в ней же передаём, какая это беседа
        me = await bot.me()
        button = InlineKeyboardButton(text=texts.CASINO_BUTTON, url=webserver.app_link(me.username, message.chat.id))
    await message.reply(texts.CASINO_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]))


@router.message(Command("forbes", "форбс", "богачи"))
async def cmd_forbes(message: Message):
    if not await _group_only(message):
        return
    chat_id = message.chat.id
    rows = db.get_richest(chat_id, limit=10)
    if not rows:
        await message.reply(texts.FORBES_EMPTY)
        return
    names = db.get_name_rows(row["user_id"] for row in rows)
    lines = [texts.FORBES_TITLE, ""]
    for i, row in enumerate(rows):
        place = texts.MEDALS[i] if i < len(texts.MEDALS) else f"{i + 1}."
        name = members.titled_display_name(row["user_id"], names.get(row["user_id"], {}))
        lines.append(f"{place} {name} — {money(row['wealth'])}")

    totals = db.get_casino_totals(chat_id)
    house = sum(t["wagered"] - t["returned"] for game, t in totals.items() if game != "duel")
    if totals:
        lines += ["", f"🎰 Казино {'заработало' if house >= 0 else 'раздало'} на беседе {money(abs(house))}"]
    jackpots = (totals.get("slots") or {}).get("special") or 0
    deaths = (totals.get("rr") or {}).get("special") or 0
    extra = []
    if jackpots:
        extra.append(f"💎 джекпотов 777: {jackpots}")
    if deaths:
        extra.append(f"💥 смертей в русской рулетке: {deaths}")
    if extra:
        lines.append(" · ".join(extra))
    await message.reply("\n".join(lines))


@router.message(Command("casinostat", "казиностат", "balance", "баланс", "кошелек", "кошелёк", "таджикоины"))
async def cmd_casinostat(message: Message, command: CommandObject):
    if not await _group_only(message):
        return
    chat_id = message.chat.id
    args = (command.args or "").strip()
    target_id = message.from_user.id
    if args.lower() not in ("", "я", "me") or message.reply_to_message:
        found, raw = members.resolve_target(message, args)
        if found == -1:
            await message.reply(f"Не знаю такого человека: <code>@{escape(raw)}</code> 🤷")
            return
        target_id = found or target_id

    wallet = db.get_wallet(chat_id, target_id)
    in_bank = db.bank_total(chat_id, target_id)
    lines = [
        f"🎰 <b>Казино: {members.display_name(target_id)}</b>",
        "",
        f"💰 {money(wallet['balance'])}" + (f" · в банке ещё {money(in_bank)}" if in_bank else ""),
        f"🏦 {db.wealth_place(chat_id, wallet['balance'] + in_bank)}-е место в Форбсе",
    ]
    games = {row["game"]: row for row in db.get_casino_stats(chat_id, target_id)}
    if not games:
        lines.append("Игр пока не было — заходи в /casino")
        await message.reply("\n".join(lines))
        return

    plays = sum(row["plays"] for row in games.values())
    lines.append(f"🎲 Игр: {fmt_num(plays)} · итог: {signed(wallet['won'] - wallet['lost'])}")
    best = max(games.values(), key=lambda row: row["best_win"])
    if best["best_win"] > 0:
        lines.append(f"🏆 Лучший выигрыш: {signed(best['best_win'])} ({GAME_NAMES.get(best['game'], best['game'])})")
    favorite = max(games.values(), key=lambda row: row["plays"])
    lines.append(f"❤️ Любимая игра: {GAME_NAMES.get(favorite['game'], favorite['game'])} "
                 f"({count_with_word(favorite['plays'], TIMES)})")

    specials = []
    for game, label in (("slots", "💎 Джекпотов 777"), ("roulette", "🎯 Угаданных чисел"),
                        ("race", "🏇 Угаданных забегов"), ("blackjack", "🃏 Блэкджеков"),
                        ("coin", "🪙 Монеток на ребре")):
        if games.get(game, {}).get("special"):
            specials.append(f"{label}: {games[game]['special']}")
    if "rr" in games:
        rr = games["rr"]
        specials.append(f"🔫 Русская рулетка: {count_with_word(rr['plays'], ('выстрел', 'выстрела', 'выстрелов'))}, "
                        f"{count_with_word(rr['special'], ('смерть', 'смерти', 'смертей'))}")
    if specials:
        lines += [""] + specials
    await message.reply("\n".join(lines))


# ── Переводы ──────────────────────────────────────────────────────────────────


@router.message(Command("give", "дать", "перевод", "подарить"))
async def cmd_give(message: Message, command: CommandObject):
    if not await _group_only(message):
        return
    chat_id, sender_id = message.chat.id, message.from_user.id
    amounts, rest = _split_args(command.args, db.get_balance(chat_id, sender_id))
    target_id, _ = members.resolve_target(message, " ".join(rest))
    if target_id is None or not amounts:
        await message.reply(
            "🤝 Ответь на сообщение человека командой <code>/give 100</code> или напиши <code>/give @username 100</code>"
        )
        return
    if target_id == -1:
        await message.reply("Не знаю такого человека 🤷")
        return
    if target_id == sender_id:
        await message.reply("Переводить таджикоины самому себе — это уже схема 🙃")
        return
    if (db.get_user(target_id) or {}).get("is_bot"):
        await message.reply("Ботам таджикоины не нужны 🤖")
        return
    amount = amounts[0]
    if amount <= 0:
        await message.reply("Сумма должна быть больше нуля.")
        return
    if not db.transfer(chat_id, sender_id, target_id, amount):
        await message.reply(texts.NOT_ENOUGH_MONEY.format(balance=money(db.get_balance(chat_id, sender_id))))
        return
    await message.reply(
        f"🤝 {members.display_name(sender_id)} переводит <b>{money(amount)}</b> → {members.mention(target_id)}"
    )


# ── Дуэли ─────────────────────────────────────────────────────────────────────

_duels: dict[str, dict] = {}


def _duel_keyboard(duel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔️ Принять", callback_data=f"duel_yes:{duel_id}"),
        InlineKeyboardButton(text="🏳️ Отказаться", callback_data=f"duel_no:{duel_id}"),
    ]])


@router.message(Command("duel", "дуэль"))
async def cmd_duel(message: Message, command: CommandObject):
    if not await _group_only(message):
        return
    chat_id, challenger = message.chat.id, message.from_user.id
    amounts, rest = _split_args(command.args, db.get_balance(chat_id, challenger))
    target, _ = members.resolve_target(message, " ".join(rest))
    if target is None:
        await message.reply("⚔️ Ответь на сообщение соперника командой <code>/duel 100</code> или напиши <code>/duel @username 100</code>")
        return
    if target == -1:
        await message.reply("Не знаю такого человека 🤷")
        return
    if target == challenger:
        await message.reply("Сам с собой не подерёшься 🙃")
        return
    if (db.get_user(target) or {}).get("is_bot"):
        await message.reply("Боты на дуэлях не дерутся 🤖")
        return
    bet = amounts[0] if amounts else DEFAULT_BET
    if not await _check_bet(message, bet):
        return
    target_balance = db.get_balance(chat_id, target)
    if target_balance < bet:
        await message.reply(f"💸 У {members.display_name(target)} столько нет: всего {money(target_balance)}.")
        return
    if not await limits.allow(message, "duel"):
        return
    limits.mark(chat_id, challenger, "duel")

    duel_id = str(time.monotonic_ns())
    _duels[duel_id] = {"chat_id": chat_id, "challenger": challenger, "target": target, "bet": bet,
                       "created": time.monotonic()}
    await message.reply(
        texts.DUEL_CALL.format(challenger=members.display_name(challenger), target=members.mention(target), bet=money(bet)),
        reply_markup=_duel_keyboard(duel_id),
    )


async def _edit_duel(callback: CallbackQuery, chat_id: int, text: str):
    if isinstance(callback.message, Message):
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(text)
            return
    await bot.send_message(chat_id, text)


@router.callback_query(F.data.startswith("duel_"))
async def on_duel_button(callback: CallbackQuery):
    action, duel_id = callback.data.split(":", 1)
    duel = _duels.get(duel_id)
    if duel is None:
        await callback.answer(texts.DUEL_EXPIRED, show_alert=True)
        return
    if callback.from_user.id != duel["target"]:
        await callback.answer("Это не твоя дуэль 😾", show_alert=True)
        return
    _duels.pop(duel_id, None)  # до всех await: второе нажатие уже ничего не сделает
    chat_id, challenger, target, bet = duel["chat_id"], duel["challenger"], duel["target"], duel["bet"]
    name_a, name_b = members.display_name(challenger), members.display_name(target)

    if time.monotonic() - duel["created"] > DUEL_TTL_SECONDS:
        await callback.answer(texts.DUEL_EXPIRED, show_alert=True)
        await _edit_duel(callback, chat_id, f"⌛ Вызов {name_a} на дуэль устарел.")
        return
    if action == "duel_no":
        await callback.answer("Ну и ладно")
        await _edit_duel(callback, chat_id,
                         random.choice(texts.DUEL_DECLINED).format(target=name_b, challenger=name_a))
        return
    if not db.take_bet(chat_id, target, bet):
        await callback.answer("Тебе не хватает таджикоинов на эту дуэль 💸", show_alert=True)
        return
    if not db.take_bet(chat_id, challenger, bet):
        db.add_money(chat_id, target, bet)
        await callback.answer("У зачинщика уже нет столько таджикоинов 💸", show_alert=True)
        await _edit_duel(callback, chat_id, f"💸 Дуэль сорвалась: у {name_a} кончились таджикоины.")
        return
    await callback.answer("Дуэль началась! ⚔️")

    tajik = db.get_tajik_of_day(chat_id, local_today())
    rounds, winner = [], None
    for _ in range(3):
        a = random.randint(1, 20) + (2 if tajik == challenger else 0)
        b = random.randint(1, 20) + (2 if tajik == target else 0)
        rounds.append(texts.DUEL_ROUND.format(
            challenger=name_a, target=name_b, a=a, b=b,
            a_bonus=" 🇹🇯" if tajik == challenger else "", b_bonus=" 🇹🇯" if tajik == target else "",
        ))
        if a != b:
            winner, loser = (challenger, target) if a > b else (target, challenger)
            break
    if winner is None:
        db.add_money(chat_id, challenger, bet)
        db.add_money(chat_id, target, bet)
        result = texts.DUEL_DRAW
    else:
        db.casino_settle(chat_id, winner, "duel", bet, bet * 2)
        db.casino_settle(chat_id, loser, "duel", bet, 0)
        result = texts.DUEL_WIN.format(winner=members.mention(winner), pot=money(bet * 2))
    await _edit_duel(
        callback, chat_id,
        f"⚔️ Дуэль: {name_a} vs {name_b} · ставка {money(bet)}\n\n" + "\n".join(rounds) + f"\n\n{result}",
    )
