"""Движок казино «Золотой казан»: правила игр и расчёт выигрышей.

Исход каждой игры решает сервер, мини-приложение его только красиво показывает,
поэтому подкрутить результат из браузера нельзя.
"""

import asyncio
import logging
import math
import random
import time
from collections import defaultdict, deque

import limits
import members
import texts
from loader import bot, db
from textstats import count_with_word
from utils import local_today, spawn

logger = logging.getLogger(__name__)

CURRENCY_FORMS = ("таджикоин", "таджикоина", "таджикоинов")
MIN_BET = 10
ACTION_INTERVAL_SECONDS = 0.8  # не чаще одной игры за столько секунд — защита от скриптов
NEWS_TO_CHAT = True            # писать в беседу о смертях в русской рулетке и джекпотах
NEWS_DELAY_SECONDS = 5         # новость приходит в беседу, когда анимация в приложении уже доиграла

RED_NUMBERS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})
ROULETTE_BETS = {  # вид ставки: (множитель выплаты, выигрывает ли выпавшее число)
    "red": (2, lambda n: n in RED_NUMBERS),
    "black": (2, lambda n: n != 0 and n not in RED_NUMBERS),
    "even": (2, lambda n: n != 0 and n % 2 == 0),
    "odd": (2, lambda n: n % 2 == 1),
    "low": (2, lambda n: 1 <= n <= 18),
    "high": (2, lambda n: 19 <= n <= 36),
    "dozen1": (3, lambda n: 1 <= n <= 12),
    "dozen2": (3, lambda n: 13 <= n <= 24),
    "dozen3": (3, lambda n: 25 <= n <= 36),
}

# Каждый барабан крутится независимо, редкие символы выпадают реже. Возврат игроку ≈ 93%.
SLOT_SYMBOLS = ("cherry", "lemon", "grapes", "bell", "diamond", "seven")
SLOT_WEIGHTS = (6, 5, 4, 3, 2, 1)
SLOT_TRIPLES = {"cherry": 6, "lemon": 8, "grapes": 12, "bell": 20, "diamond": 40, "seven": 100}
SLOT_TWO_SEVENS = 4  # две семёрки из трёх
SLOT_PAIR = 1        # два одинаковых символа — ставка возвращается

RR_CHAMBERS = 6
RR_REWARD_STEP = 50  # награда за выстрел растёт вместе с риском: 50, 100, 150...

GAME_NAMES = {
    "roulette": "рулетка", "slots": "слоты", "coin": "монетка", "blackjack": "блэкджек",
    "rr": "русская рулетка", "duel": "дуэли",
}


class GameError(Exception):
    """Ошибка, которую можно показать игроку."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def money(amount: int) -> str:
    return count_with_word(amount, CURRENCY_FORMS)


def signed(amount: int) -> str:
    return ("+" if amount >= 0 else "−") + money(abs(amount))


_last_action: dict[tuple[int, int], float] = {}
_feeds: dict[int, deque] = defaultdict(lambda: deque(maxlen=30))
_revolvers: dict[int, dict[str, int]] = {}  # chat_id -> {"bullet": гнездо с патроном, "fired": сколько уже щёлкнули}


def add_feed(chat_id: int, text: str):
    """Событие для ленты казино в приложении."""
    _feeds[chat_id].appendleft((time.time(), text))


def _throttle(chat_id: int, user_id: int):
    now = time.monotonic()
    if now - _last_action.get((chat_id, user_id), 0.0) < ACTION_INTERVAL_SECONDS:
        raise GameError("Не так быстро 🙂", 429)
    _last_action[(chat_id, user_id)] = now


def _check_bet(bet):
    if isinstance(bet, bool) or not isinstance(bet, int):
        raise GameError("Ставка должна быть целым числом")
    if bet < MIN_BET:
        raise GameError(f"Минимальная ставка — {money(MIN_BET)}")


def _take_bet(chat_id: int, user_id: int, bet: int):
    """Проверка, антиспам и списание ставки. Ошибки в самой ставке не считаются попыткой."""
    _check_bet(bet)
    _throttle(chat_id, user_id)
    if not db.take_bet(chat_id, user_id, bet):
        raise GameError(texts.NOT_ENOUGH_MONEY.format(balance=money(db.get_balance(chat_id, user_id))), 409)


def _news(chat_id: int, text: str):
    if NEWS_TO_CHAT:
        spawn(_send_news(chat_id, text))


async def _send_news(chat_id: int, text: str):
    await asyncio.sleep(NEWS_DELAY_SECONDS)
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logger.warning("Новость казино не отправилась в %s: %s", chat_id, e)


def _name(user_id: int) -> str:
    return members.plain_name(user_id)


# ── Лобби ─────────────────────────────────────────────────────────────────────


def state(chat_id: int, user_id: int) -> dict:
    wallet = db.get_wallet(chat_id, user_id)
    richest = db.get_richest(chat_id, limit=10)
    tajik_id = db.get_tajik_of_day(chat_id, local_today())
    names = db.get_name_rows([row["user_id"] for row in richest] + [user_id] + ([tajik_id] if tajik_id else []))
    fired = _revolvers[chat_id]["fired"] if chat_id in _revolvers else 0
    now = time.time()
    return {
        "user": {"id": user_id, "name": members.plain_name(user_id, names.get(user_id, {}))},
        "balance": wallet["balance"],
        "place": db.wealth_place(chat_id, wallet["balance"]),
        "min_bet": MIN_BET,
        "stats": {"games": wallet["games"], "won": wallet["won"], "lost": wallet["lost"]},
        "bonus_available": wallet["last_bonus"] != local_today(),
        "spam_day": limits.spam_day_active(),
        "tajik": {
            "name": members.plain_name(tajik_id, names.get(tajik_id, {})) if tajik_id else None,
            "is_me": tajik_id == user_id,
            "prize": texts.TAJIK_DAY_PRIZE,
        },
        "rr": {
            "chambers_left": RR_CHAMBERS - fired,
            "reward": RR_REWARD_STEP * (fired + 1),
            "cooldown": math.ceil(limits.remaining(chat_id, user_id, "rr")),
        },
        "top": [
            {
                "name": members.plain_name(row["user_id"], names.get(row["user_id"], {})),
                "username": names.get(row["user_id"], {}).get("username") or None,
                "balance": row["balance"],
                "me": row["user_id"] == user_id,
            }
            for row in richest
        ],
        "feed": [{"text": text, "ago": int(now - moment)} for moment, text in list(_feeds[chat_id])[:15]],
        "blackjack": blackjack_state(chat_id, user_id),
    }


def claim_bonus(chat_id: int, user_id: int) -> dict:
    _throttle(chat_id, user_id)
    if db.get_balance(chat_id, user_id) < MIN_BET:
        source, amount = texts.BONUS_BANKRUPT, 500
    else:
        source, amount = random.choice(texts.BONUS_SOURCES), random.randint(15, 40) * 10
    balance = db.claim_bonus(chat_id, user_id, local_today(), amount)
    if balance is None:
        raise GameError(texts.BONUS_ALREADY, 409)
    return {"amount": amount, "source": source, "balance": balance}


# ── Игры ──────────────────────────────────────────────────────────────────────


def play_roulette(chat_id: int, user_id: int, bet, kind, number=None) -> dict:
    if kind == "number":
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 36:
            raise GameError("Выбери число от 0 до 36")
        multiplier = 36
    elif kind in ROULETTE_BETS:
        multiplier = ROULETTE_BETS[kind][0]
    else:
        raise GameError("Непонятная ставка")
    _take_bet(chat_id, user_id, bet)

    result = random.randint(0, 36)
    won = result == number if kind == "number" else ROULETTE_BETS[kind][1](result)
    payout = bet * multiplier if won else 0
    balance = db.casino_settle(chat_id, user_id, "roulette", bet, payout, special=won and kind == "number")
    if won and kind == "number":
        add_feed(chat_id, f"🎡 {_name(user_id)} угадывает число {result}: {signed(payout - bet)}")
    elif payout - bet >= 2000:
        add_feed(chat_id, f"🎡 {_name(user_id)} выигрывает в рулетке {signed(payout - bet)}")
    return {
        "result": result,
        "color": "green" if result == 0 else ("red" if result in RED_NUMBERS else "black"),
        "won": won, "payout": payout, "net": payout - bet, "balance": balance,
    }


def play_slots(chat_id: int, user_id: int, bet) -> dict:
    _take_bet(chat_id, user_id, bet)

    reels = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    if reels[0] == reels[1] == reels[2]:
        combo, multiplier = ("jackpot" if reels[0] == "seven" else "triple"), SLOT_TRIPLES[reels[0]]
    elif reels.count("seven") == 2:
        combo, multiplier = "sevens", SLOT_TWO_SEVENS
    elif len(set(reels)) == 2:
        combo, multiplier = "pair", SLOT_PAIR
    else:
        combo, multiplier = "lose", 0
    payout = bet * multiplier
    balance = db.casino_settle(chat_id, user_id, "slots", bet, payout, special=combo == "jackpot")
    name = _name(user_id)
    if combo == "jackpot":
        add_feed(chat_id, f"💎 {name} срывает джекпот 7️⃣7️⃣7️⃣: {signed(payout - bet)}")
        _news(chat_id, f"💎 <b>ДЖЕКПОТ!</b> {members.display_name(user_id)} выбивает 7️⃣7️⃣7️⃣ в казино "
                       f"и забирает <b>{money(payout)}</b>!\n<i>/casino</i>")
    elif combo == "triple" and multiplier >= 20:
        add_feed(chat_id, f"🎰 {name} собирает три в ряд: {signed(payout - bet)}")
    return {"reels": reels, "combo": combo, "multiplier": multiplier, "payout": payout,
            "net": payout - bet, "balance": balance}


def play_coin(chat_id: int, user_id: int, bet, side) -> dict:
    if side not in ("heads", "tails"):
        raise GameError("Выбери орла или решку")
    _take_bet(chat_id, user_id, bet)

    roll = random.random()
    if roll < 0.01:
        result, payout = "edge", bet  # встала на ребро — ставка возвращается
    elif roll < 0.02:
        result, payout = "stolen", 0  # украли узбеки
    else:
        result = random.choice(("heads", "tails"))
        payout = bet * 2 if result == side else 0
    balance = db.casino_settle(chat_id, user_id, "coin", bet, payout, special=result == "edge")
    if result == "edge":
        add_feed(chat_id, f"🪙 У {_name(user_id)} монетка встала на ребро!")
    return {"result": result, "won": payout > bet, "payout": payout, "net": payout - bet, "balance": balance}


def pull_trigger(chat_id: int, user_id: int) -> dict:
    """Русская рулетка: барабан общий на беседу и не прокручивается — с каждым выстрелом следующему страшнее."""
    _throttle(chat_id, user_id)
    wait = limits.remaining(chat_id, user_id, "rr")
    if wait > 0:
        raise GameError(f"Револьвер перезаряжается: ещё {limits.format_wait(wait)}", 429)
    limits.mark(chat_id, user_id, "rr")

    revolver = _revolvers.setdefault(chat_id, {"bullet": random.randrange(RR_CHAMBERS), "fired": 0})
    chance = RR_CHAMBERS - revolver["fired"]
    dead = revolver["fired"] == revolver["bullet"]
    name = _name(user_id)
    if dead:
        _revolvers.pop(chat_id)
        balance = db.get_balance(chat_id, user_id)
        delta = -min(balance, max(100, balance // 5))
        add_feed(chat_id, f"💥 {name} — БАХ! Шанс был 1/{chance}, {signed(delta)}")
        _news(chat_id, random.choice(texts.RR_DEATH).format(name=f"<b>{members.display_name(user_id)}</b>")
              + f"\n<i>Русская рулетка в /casino · шанс был 1/{chance}</i>")
    else:
        revolver["fired"] += 1
        delta = RR_REWARD_STEP * revolver["fired"]
        add_feed(chat_id, f"🔫 {name} — щёлк! Шанс был 1/{chance}, {signed(delta)}")
    balance = db.casino_russian_roulette(chat_id, user_id, delta, died=dead)
    fired = 0 if dead else revolver["fired"]
    return {
        "dead": dead, "chance": chance, "delta": delta, "balance": balance,
        "chambers_left": RR_CHAMBERS - fired, "next_reward": RR_REWARD_STEP * (fired + 1),
        "cooldown": math.ceil(limits.cooldown()),
    }


# ── Блэкджек ──────────────────────────────────────────────────────────────────
# Одна колода на раздачу, блэкджек платит 3:2, дилер останавливается на 17 (в том числе мягких),
# удвоение на первых двух картах. Раздача хранится в базе, пока её не доиграют.

BJ_RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
BJ_SUITS = ("S", "H", "D", "C")
BJ_PAYOUTS = {"blackjack": 2.5, "win": 2, "dealer_bust": 2, "push": 1, "lose": 0, "bust": 0, "dealer_blackjack": 0}


def hand_value(cards: list[str]) -> tuple[int, bool]:
    """Сумма очков и «мягкая» ли рука (туз ещё считается за 11)."""
    total, aces = 0, 0
    for card in cards:
        rank = card[:-1]
        if rank == "A":
            total, aces = total + 11, aces + 1
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total, aces = total - 10, aces - 1
    return total, aces > 0


def _is_blackjack(cards: list[str]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def _blackjack_view(hand: dict, balance: int, outcome: str | None = None, payout: int = 0) -> dict:
    finished = outcome is not None
    stake = hand["bet"] * (2 if hand["doubled"] else 1)
    player_total, player_soft = hand_value(hand["player"])
    shown_dealer = hand["dealer"] if finished else hand["dealer"][:1]
    view = {
        "active": not finished,
        "bet": stake,
        "player": hand["player"],
        "player_total": player_total,
        "player_soft": player_soft,
        "dealer": shown_dealer + ([] if finished else [None]),  # None — закрытая карта дилера
        "dealer_total": hand_value(shown_dealer)[0],
        "can_double": not finished and len(hand["player"]) == 2 and not hand["doubled"],
        "balance": balance,
    }
    if finished:
        view.update(outcome=outcome, payout=payout, net=payout - stake,
                    message=random.choice(texts.BLACKJACK_RESULTS[outcome]))
    return view


def _blackjack_finish(chat_id: int, user_id: int, hand: dict, outcome: str) -> dict:
    stake = hand["bet"] * (2 if hand["doubled"] else 1)
    payout = int(stake * BJ_PAYOUTS[outcome])
    balance = db.finish_blackjack(chat_id, user_id, stake, payout, blackjack=outcome == "blackjack")
    if outcome == "blackjack" and stake >= 500:
        add_feed(chat_id, f"🃏 {_name(user_id)} собирает блэкджек: {signed(payout - stake)}")
    return _blackjack_view(hand, balance, outcome, payout)


def _dealer_plays(chat_id: int, user_id: int, hand: dict) -> dict:
    while hand_value(hand["dealer"])[0] < 17:
        hand["dealer"].append(hand["deck"].pop())
    player, dealer = hand_value(hand["player"])[0], hand_value(hand["dealer"])[0]
    if dealer > 21:
        outcome = "dealer_bust"
    elif player > dealer:
        outcome = "win"
    elif player == dealer:
        outcome = "push"
    else:
        outcome = "lose"
    return _blackjack_finish(chat_id, user_id, hand, outcome)


def _current_hand(chat_id: int, user_id: int) -> dict:
    hand = db.get_blackjack_hand(chat_id, user_id)
    if hand is None:
        raise GameError("Раздача уже закончилась — сделай новую ставку", 409)
    return hand


def blackjack_state(chat_id: int, user_id: int) -> dict | None:
    hand = db.get_blackjack_hand(chat_id, user_id)
    return _blackjack_view(hand, db.get_balance(chat_id, user_id)) if hand else None


def blackjack_start(chat_id: int, user_id: int, bet) -> dict:
    if db.get_blackjack_hand(chat_id, user_id) is not None:
        raise GameError("Сначала доиграй текущую раздачу", 409)
    _take_bet(chat_id, user_id, bet)
    deck = [rank + suit for rank in BJ_RANKS for suit in BJ_SUITS]
    random.shuffle(deck)
    hand = {"bet": bet, "doubled": False, "deck": deck, "player": [], "dealer": []}
    for _ in range(2):
        hand["player"].append(deck.pop())
        hand["dealer"].append(deck.pop())
    # дилер сразу проверяет блэкджеки: раздача может закончиться, не начавшись
    player_bj, dealer_bj = _is_blackjack(hand["player"]), _is_blackjack(hand["dealer"])
    if player_bj or dealer_bj:
        outcome = "push" if player_bj and dealer_bj else ("blackjack" if player_bj else "dealer_blackjack")
        return _blackjack_finish(chat_id, user_id, hand, outcome)
    db.save_blackjack_hand(chat_id, user_id, hand)
    return _blackjack_view(hand, db.get_balance(chat_id, user_id))


def blackjack_hit(chat_id: int, user_id: int) -> dict:
    hand = _current_hand(chat_id, user_id)
    hand["player"].append(hand["deck"].pop())
    total = hand_value(hand["player"])[0]
    if total > 21:
        return _blackjack_finish(chat_id, user_id, hand, "bust")
    if total == 21:  # на 21 брать дальше нечего — ходит дилер
        return _dealer_plays(chat_id, user_id, hand)
    db.save_blackjack_hand(chat_id, user_id, hand)
    return _blackjack_view(hand, db.get_balance(chat_id, user_id))


def blackjack_stand(chat_id: int, user_id: int) -> dict:
    return _dealer_plays(chat_id, user_id, _current_hand(chat_id, user_id))


def blackjack_double(chat_id: int, user_id: int) -> dict:
    hand = _current_hand(chat_id, user_id)
    if len(hand["player"]) != 2 or hand["doubled"]:
        raise GameError("Удвоить можно только на первых двух картах")
    _take_bet(chat_id, user_id, hand["bet"])
    hand["doubled"] = True
    hand["player"].append(hand["deck"].pop())
    if hand_value(hand["player"])[0] > 21:
        return _blackjack_finish(chat_id, user_id, hand, "bust")
    return _dealer_plays(chat_id, user_id, hand)
