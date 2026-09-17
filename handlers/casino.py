"""Казино «Золотой казан»: таджикоины, рулетка, русская рулетка, слоты, монетка, дуэли и /dnd."""

import asyncio
import random
import re
import time
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import limits
import members
import texts
from loader import bot, db
from textstats import count_with_word, fmt_num
from utils import local_today

router = Router(name="casino")

CURRENCY_FORMS = ("таджикоин", "таджикоина", "таджикоинов")
MIN_BET = 10
DEFAULT_BET = 100
DUEL_TTL_SECONDS = 10 * 60
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
SLOT_SYMBOLS = ["BAR", "🍇", "🍋", "7️⃣"]


def money(amount: int) -> str:
    return count_with_word(amount, CURRENCY_FORMS)


def signed(amount: int) -> str:
    return f"{'+' if amount >= 0 else '−'}{money(abs(amount))}"


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


async def _start_game(message: Message, command: str, bet: int | None = None) -> bool:
    """Кулдаун, проверка ставки и списание её из кошелька. True — можно играть."""
    if not await limits.allow(message, command):
        return False
    if bet is not None:
        if not await _check_bet(message, bet):
            return False
        if not db.take_bet(message.chat.id, message.from_user.id, bet):
            await message.reply(texts.NOT_ENOUGH_MONEY.format(balance=money(db.get_balance(message.chat.id, message.from_user.id))))
            return False
    limits.mark(message.chat.id, message.from_user.id, command)
    return True


async def _edit(message: Message, text: str):
    try:
        await message.edit_text(text)
    except TelegramBadRequest:
        await bot.send_message(message.chat.id, text)


# ── Кошелёк ───────────────────────────────────────────────────────────────────


@router.message(Command("casino", "казино"))
async def cmd_casino(message: Message):
    await message.reply(texts.CASINO_HELP)


@router.message(Command("balance", "баланс", "кошелек", "кошелёк", "таджикоины"))
async def cmd_balance(message: Message):
    if not await _group_only(message):
        return
    wallet = db.get_wallet(message.chat.id, message.from_user.id)
    lines = [
        f"💰 {members.display_name(message.from_user.id)}: <b>{money(wallet['balance'])}</b>",
        f"🏦 Место в Форбсе беседы: {db.wealth_place(message.chat.id, wallet['balance'])}",
    ]
    if wallet["games"]:
        lines.append(
            f"🎰 Игр: {fmt_num(wallet['games'])} · выиграно {money(wallet['won'])} · проиграно {money(wallet['lost'])}"
        )
    await message.reply("\n".join(lines))


@router.message(Command("bonus", "бонус"))
async def cmd_bonus(message: Message):
    if not await _group_only(message):
        return
    chat_id, user_id = message.chat.id, message.from_user.id
    if db.get_balance(chat_id, user_id) < MIN_BET:
        source, amount = texts.BONUS_BANKRUPT, 500
    else:
        source, amount = random.choice(texts.BONUS_SOURCES), random.randint(15, 40) * 10
    balance = db.claim_bonus(chat_id, user_id, local_today(), amount)
    if balance is None:
        await message.reply(texts.BONUS_ALREADY)
        return
    await message.reply(f"{source}: <b>{signed(amount)}</b>\n💰 Теперь у тебя {money(balance)}")


@router.message(Command("forbes", "форбс", "богачи"))
async def cmd_forbes(message: Message):
    if not await _group_only(message):
        return
    rows = db.get_richest(message.chat.id, limit=10)
    if not rows:
        await message.reply(texts.FORBES_EMPTY)
        return
    names = members.display_names(row["user_id"] for row in rows)
    lines = [texts.FORBES_TITLE, ""]
    for i, row in enumerate(rows):
        place = texts.MEDALS[i] if i < len(texts.MEDALS) else f"{i + 1}."
        lines.append(f"{place} {names[row['user_id']]} — {money(row['balance'])}")
    await message.reply("\n".join(lines))


@router.message(Command("give", "дать", "перевод", "подарить"))
async def cmd_give(message: Message, command: CommandObject):
    if not await _group_only(message):
        return
    chat_id, sender_id = message.chat.id, message.from_user.id
    amounts, rest = _split_args(command.args, db.get_balance(chat_id, sender_id))
    target_id, raw = members.resolve_target(message, " ".join(rest))
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


# ── Рулетка ───────────────────────────────────────────────────────────────────

_ROULETTE_BETS = {  # вид ставки: (подпись, множитель выплаты, выигрывает ли число)
    "red": ("🔴 красное", 2, lambda n: n in RED_NUMBERS),
    "black": ("⚫ чёрное", 2, lambda n: n != 0 and n not in RED_NUMBERS),
    "even": ("чёт", 2, lambda n: n != 0 and n % 2 == 0),
    "odd": ("нечет", 2, lambda n: n % 2 == 1),
    "low": ("1–18", 2, lambda n: 1 <= n <= 18),
    "high": ("19–36", 2, lambda n: 19 <= n <= 36),
}
_ROULETTE_WORDS = {
    **dict.fromkeys(("красное", "красный", "красная", "красн", "кр", "red"), "red"),
    **dict.fromkeys(("черное", "черный", "черная", "черн", "black"), "black"),
    **dict.fromkeys(("чет", "четное", "even"), "even"),
    **dict.fromkeys(("нечет", "нечетное", "odd"), "odd"),
    **dict.fromkeys(("1-18", "малые", "малое", "low"), "low"),
    **dict.fromkeys(("19-36", "большие", "большое", "high"), "high"),
    **dict.fromkeys(("зеро", "zero", "ноль"), "zero"),
}


def _parse_roulette(args: str | None, balance: int) -> tuple[int, str, int | None] | None:
    """(ставка, вид ставки, число) или None, если непонятно."""
    kind = None
    amounts = []
    for token in (args or "").lower().replace("ё", "е").split():  # слова вроде «на» просто пропускаем
        if token in _ROULETTE_WORDS:
            kind = _ROULETTE_WORDS[token]
        elif (amount := parse_amount(token, balance)) is not None:
            amounts.append(amount)
    if kind == "zero":
        return (amounts[0] if amounts else DEFAULT_BET), "number", 0
    if kind:
        return (amounts[0] if amounts else DEFAULT_BET), kind, None
    if len(amounts) == 2 and 0 <= amounts[1] <= 36:
        return amounts[0], "number", amounts[1]
    return None


@router.message(Command("roulette", "рулетка"))
async def cmd_roulette(message: Message, command: CommandObject):
    if not await _group_only(message):
        return
    chat_id, user_id = message.chat.id, message.from_user.id
    parsed = _parse_roulette(command.args, db.get_balance(chat_id, user_id))
    if parsed is None:
        await message.reply(texts.ROULETTE_HELP)
        return
    bet, kind, number = parsed
    if not await _start_game(message, "roulette", bet):
        return

    if kind == "number":
        label = "🟢 зеро" if number == 0 else f"число {number}"
    else:
        label = _ROULETTE_BETS[kind][0]
    name = members.display_name(user_id)
    sent = await message.reply(texts.ROULETTE_SPIN.format(name=name, bet=money(bet), target=label))
    await asyncio.sleep(2.5)

    result = random.randint(0, 36)
    if kind == "number":
        multiplier, won = 36, result == number
    else:
        _, multiplier, wins = _ROULETTE_BETS[kind]
        won = wins(result)
    payout = bet * multiplier if won else 0
    balance = db.pay_out(chat_id, user_id, bet, payout)
    color = "🟢" if result == 0 else ("🔴" if result in RED_NUMBERS else "⚫")
    if won and multiplier == 36:
        verdict = f"💥 <b>В ЯБЛОЧКО!</b> {signed(payout - bet)}"
    elif won:
        verdict = f"🎉 Победа! {signed(payout - bet)}"
    else:
        verdict = f"😢 Мимо. {signed(-bet)}"
    await _edit(sent, (
        f"🎡 {name} ставит {money(bet)} на {label}\n"
        f"Выпало: <b>{result}</b> {color}\n"
        f"{verdict} · баланс {money(balance)}{random.choice(texts.ROULETTE_FLAVOR)}"
    ))


# ── Русская рулетка ───────────────────────────────────────────────────────────

_revolvers: dict[int, dict[str, int]] = {}  # chat_id -> {"bullet": гнездо с патроном, "fired": сколько уже щёлкнули}


@router.message(Command("rr", "рр", "russian", "русская"))
async def cmd_russian_roulette(message: Message):
    if not await _group_only(message):
        return
    if not await _start_game(message, "rr"):
        return
    chat_id, user_id = message.chat.id, message.from_user.id

    # Барабан общий на беседу и не прокручивается: с каждым выстрелом следующему страшнее.
    # Состояние меняем до любых await, чтобы двое одновременно не выстрелили из одного гнезда.
    revolver = _revolvers.setdefault(chat_id, {"bullet": random.randrange(6), "fired": 0})
    chambers_left = 6 - revolver["fired"]
    dead = revolver["fired"] == revolver["bullet"]
    if dead:
        _revolvers.pop(chat_id)
    else:
        revolver["fired"] += 1

    name = members.display_name(user_id)
    pull = texts.RR_PULL.format(name=name)
    sent = await message.reply(pull)
    await asyncio.sleep(2)
    if dead:
        balance = db.get_balance(chat_id, user_id)
        penalty = min(balance, max(100, balance // 5))
        balance = db.add_money(chat_id, user_id, -penalty)
        outcome = (
            f"{random.choice(texts.RR_DEATH).format(name=name)}\n"
            f"⚰️ На похороны: {signed(-penalty)} · баланс {money(balance)}\n"
            f"🔄 Шанс был 1/{chambers_left}. Барабан перезаряжен."
        )
    else:
        reward = 50 * (7 - chambers_left)
        balance = db.add_money(chat_id, user_id, reward)
        outcome = (
            f"{random.choice(texts.RR_SURVIVE)}\n"
            f"🎖 За храбрость: {signed(reward)} · баланс {money(balance)}\n"
            f"🔫 Шанс был 1/{chambers_left}. Гнёзд осталось: {chambers_left - 1} — следующему страшнее."
        )
    await _edit(sent, f"{pull}\n\n{outcome}")


# ── Слоты ─────────────────────────────────────────────────────────────────────


def slot_reels(value: int) -> list[int]:
    """Telegram кодирует три барабана 🎰 в одном числе 1–64: 1 — BAR BAR BAR, 64 — 777."""
    return [((value - 1) >> (2 * i)) & 3 for i in range(3)]


@router.message(Command("slots", "слоты", "автомат"))
async def cmd_slots(message: Message, command: CommandObject):
    if not await _group_only(message):
        return
    chat_id, user_id = message.chat.id, message.from_user.id
    amounts, _ = _split_args(command.args, db.get_balance(chat_id, user_id))
    bet = amounts[0] if amounts else DEFAULT_BET
    if not await _start_game(message, "slots", bet):
        return

    dice = await message.reply_dice(emoji="🎰")
    reels = slot_reels(dice.dice.value)
    await asyncio.sleep(2.2)  # ждём, пока докрутится анимация
    name = members.display_name(user_id)
    if reels == [3, 3, 3]:
        payout, verdict = bet * 20, texts.SLOTS_WIN_JACKPOT
    elif reels[0] == reels[1] == reels[2]:
        payout, verdict = bet * 7, texts.SLOTS_WIN_TRIPLE
    elif reels.count(3) == 2:
        payout, verdict = bet * 2, texts.SLOTS_WIN_SEVENS
    else:
        payout, verdict = 0, f"{random.choice(texts.SLOTS_LOSE)} {signed(-bet)}"
    balance = db.pay_out(chat_id, user_id, bet, payout)
    combo = " ".join(SLOT_SYMBOLS[reel] for reel in reels)
    verdict = verdict.format(name=name, win=money(payout - bet))
    await dice.reply(f"🎰 {combo}\n{verdict}\n💰 Баланс: {money(balance)}")


# ── Монетка ───────────────────────────────────────────────────────────────────

_HEADS = {"орел", "о", "heads", "h"}
_TAILS = {"решка", "р", "tails", "t"}


@router.message(Command("coin", "монетка", "монета"))
async def cmd_coin(message: Message, command: CommandObject):
    chat_id, user_id = message.chat.id, message.from_user.id
    balance = db.get_balance(chat_id, user_id)
    side, amounts = None, []
    for token in (command.args or "").lower().replace("ё", "е").split():
        if token in _HEADS:
            side = "heads"
        elif token in _TAILS:
            side = "tails"
        elif (amount := parse_amount(token, balance)) is not None:
            amounts.append(amount)
    name = members.display_name(user_id)

    if side is None and not amounts:  # просто подбросить
        if not await _start_game(message, "coin"):
            return
        result = texts.COIN_HEADS if random.random() < 0.5 else texts.COIN_TAILS
        await message.reply(f"🪙 {name} подбрасывает монетку... <b>{result}</b>!")
        return
    if side is None:
        await message.reply("🪙 На что ставим? Например: <code>/coin 100 орёл</code>")
        return
    if not await _group_only(message):
        return
    bet = amounts[0] if amounts else DEFAULT_BET
    if not await _start_game(message, "coin", bet):
        return

    roll = random.random()
    if roll < 0.01:
        payout, text = bet, texts.COIN_EDGE
    elif roll < 0.02:
        payout, text = 0, texts.COIN_STOLEN
    else:
        result = "heads" if random.random() < 0.5 else "tails"
        payout = bet * 2 if result == side else 0
        shown = texts.COIN_HEADS if result == "heads" else texts.COIN_TAILS
        text = f"<b>{shown}</b>! " + ("🎉 Угадал(а)!" if payout else "😢 Не угадал(а).")
    balance = db.pay_out(chat_id, user_id, bet, payout)
    await message.reply(f"🪙 {name} ставит {money(bet)}...\n{text} {signed(payout - bet)} · баланс {money(balance)}")


# ── /dnd ──────────────────────────────────────────────────────────────────────


@router.message(Command("dnd", "днд"))
async def cmd_dnd(message: Message):
    if not await _start_game(message, "dnd"):
        return
    user_id = message.from_user.id
    event = random.choice(texts.DND_EVENTS)
    if "{member}" in event:
        member_id = None
        if members.is_group(message.chat):
            member_id = await members.random_member(message.chat.id, exclude={user_id})
        event = event.format(member=members.display_name(member_id) if member_id else "Случайный прохожий")
    await message.reply(texts.DND_TEMPLATE.format(name=f"<b>{members.display_name(user_id)}</b>", event=event))


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
    if not await _start_game(message, "duel"):
        return

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
        await _edit_duel(callback, chat_id, random.choice(texts.DUEL_DECLINED).format(target=name_b))
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
        db.pay_out(chat_id, winner, bet, bet * 2)
        db.pay_out(chat_id, loser, bet, 0)
        result = texts.DUEL_WIN.format(winner=members.mention(winner), pot=money(bet * 2))
    await _edit_duel(
        callback, chat_id,
        f"⚔️ Дуэль: {name_a} vs {name_b} · ставка {money(bet)}\n\n" + "\n".join(rounds) + f"\n\n{result}",
    )
