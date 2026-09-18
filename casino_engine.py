"""Движок казино «Золотой казан»: личные игры, русская рулетка и то, что нужно всем играм.

Исход каждой игры решает сервер, мини-приложение его только красиво показывает, поэтому
подкрутить результат из браузера нельзя. Может только хозяин — скрытой командой /debug_casino_dev в личке с ботом (casino_rig.py).
Общие столы (рулетка и скачки) живут в casino_tables.py, банк — в casino_bank.py.
"""

import asyncio
import logging
import math
import random
import time
from collections import defaultdict, deque

import casino_rig
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
HISTORY_SHOWN = 12             # сколько последних результатов показывать в приложении

# Каждый барабан крутится независимо, редкие символы выпадают реже. Возврат игроку ≈ 93%.
SLOT_SYMBOLS = ("cherry", "lemon", "grapes", "bell", "diamond", "seven")
SLOT_WEIGHTS = (6, 5, 4, 3, 2, 1)
SLOT_TRIPLES = {"cherry": 6, "lemon": 8, "grapes": 12, "bell": 20, "diamond": 40, "seven": 100}
SLOT_TWO_SEVENS = 4  # две семёрки из трёх
SLOT_PAIR = 1        # два одинаковых символа — ставка возвращается

RR_CHAMBERS = 6
# Награда за выстрел — доля кошелька стрелка (не меньше минимума). Чем ближе патрон, тем щедрее
RR_REWARDS = ((5, 50), (10, 100), (15, 150), (20, 200), (40, 400), (100, 1000))
RR_EMPTY_CHANCE = 0.05   # изредка барабан заряжают без патрона — тогда можно пережить и шестой выстрел
RR_FUNERAL_PERCENT = 20  # смерть — минус 20% кошелька на похороны…
RR_FUNERAL_MIN = 100     # …но не меньше сотни

GAME_NAMES = {
    "roulette": "рулетка", "race": "скачки", "slots": "слоты", "coin": "монетка", "blackjack": "блэкджек",
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
# chat_id -> {"bullet": гнездо с патроном (0–5) или None, если барабан пустой, "fired": сколько уже щёлкнули}
_revolvers: dict[int, dict] = {}


def add_feed(chat_id: int, text: str):
    """Событие для ленты казино в приложении."""
    _feeds[chat_id].appendleft((time.time(), text))


def throttle(chat_id: int, user_id: int):
    now = time.monotonic()
    if now - _last_action.get((chat_id, user_id), 0.0) < ACTION_INTERVAL_SECONDS:
        raise GameError("Не так быстро 🙂", 429)
    _last_action[(chat_id, user_id)] = now


def check_bet(bet):
    if isinstance(bet, bool) or not isinstance(bet, int):
        raise GameError("Ставка должна быть целым числом")
    if bet < MIN_BET:
        raise GameError(f"Минимальная ставка — {money(MIN_BET)}")


def not_enough_money(chat_id: int, user_id: int) -> GameError:
    return GameError(texts.NOT_ENOUGH_MONEY.format(balance=money(db.get_balance(chat_id, user_id))), 409)


def take_bet(chat_id: int, user_id: int, bet: int):
    """Проверка, антиспам и списание ставки. Ошибки в самой ставке не считаются попыткой."""
    check_bet(bet)
    throttle(chat_id, user_id)
    if not db.take_bet(chat_id, user_id, bet):
        raise not_enough_money(chat_id, user_id)


def news(chat_id: int, text: str):
    if NEWS_TO_CHAT:
        spawn(_send_news(chat_id, text))


async def _send_news(chat_id: int, text: str):
    await asyncio.sleep(NEWS_DELAY_SECONDS)
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logger.warning("Новость казино не отправилась в %s: %s", chat_id, e)


def player_name(user_id: int) -> str:
    return members.plain_name(user_id)


def players(chat_id: int) -> list[dict]:
    """Участники беседы с кошельками — кого можно порадовать подкруткой."""
    rows = db.get_richest(chat_id, limit=500)
    names = db.get_name_rows(row["user_id"] for row in rows)
    found = [{"id": row["user_id"], "name": members.plain_name(row["user_id"], names.get(row["user_id"], {}))}
             for row in rows]
    return sorted(found, key=lambda player: player["name"].lower())


# ── Лобби ─────────────────────────────────────────────────────────────────────


def state(chat_id: int, user_id: int) -> dict:
    wallet = db.get_wallet(chat_id, user_id)
    richest = db.get_richest(chat_id, limit=10)
    tajik_id = db.get_tajik_of_day(chat_id, local_today())
    names = db.get_name_rows([row["user_id"] for row in richest] + [user_id] + ([tajik_id] if tajik_id else []))
    now = time.time()
    return {
        "user": {"id": user_id, "name": members.plain_name(user_id, names.get(user_id, {}))},
        "balance": wallet["balance"],
        "place": db.wealth_place(chat_id, wallet["balance"] + db.bank_total(chat_id, user_id)),
        "min_bet": MIN_BET,
        "stats": {"games": wallet["games"], "won": wallet["won"], "lost": wallet["lost"]},
        "bonus_available": wallet["last_bonus"] != local_today(),
        "spam_day": limits.spam_day_active(),
        "tajik": {
            "name": members.plain_name(tajik_id, names.get(tajik_id, {})) if tajik_id else None,
            "is_me": tajik_id == user_id,
            "prize": texts.TAJIK_DAY_PRIZE,
        },
        "rr": rr_state(chat_id, user_id, wallet["balance"]),
        "coin_history": db.get_history(chat_id, "coin", HISTORY_SHOWN),
        "top": [
            {
                "name": members.plain_name(row["user_id"], names.get(row["user_id"], {})),
                "username": names.get(row["user_id"], {}).get("username") or None,
                "balance": row["wealth"],
                "me": row["user_id"] == user_id,
            }
            for row in richest
        ],
        "feed": [{"text": text, "ago": int(now - moment)} for moment, text in list(_feeds[chat_id])[:15]],
        "blackjack": blackjack_state(chat_id, user_id),
    }


def claim_bonus(chat_id: int, user_id: int) -> dict:
    throttle(chat_id, user_id)
    if db.get_balance(chat_id, user_id) < MIN_BET:
        source, amount = texts.BONUS_BANKRUPT, 500
    else:
        source, amount = random.choice(texts.BONUS_SOURCES), random.randint(15, 40) * 10
    balance = db.claim_bonus(chat_id, user_id, local_today(), amount)
    if balance is None:
        raise GameError(texts.BONUS_ALREADY, 409)
    return {"amount": amount, "source": source, "balance": balance}


# ── Слоты и монетка ───────────────────────────────────────────────────────────


def play_slots(chat_id: int, user_id: int, bet) -> dict:
    take_bet(chat_id, user_id, bet)

    if casino_rig.take(chat_id, "slots", user_id):
        reels = ["seven"] * 3
    else:
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
    name = player_name(user_id)
    if combo == "jackpot":
        add_feed(chat_id, f"💎 {name} срывает джекпот 7️⃣7️⃣7️⃣: {signed(payout - bet)}")
        news(chat_id, f"💎 <b>ДЖЕКПОТ!</b> {members.display_name(user_id)} выбивает 7️⃣7️⃣7️⃣ в казино "
                      f"и забирает <b>{money(payout)}</b>!\n<i>/casino</i>")
    elif combo == "triple" and multiplier >= 20:
        add_feed(chat_id, f"🎰 {name} собирает три в ряд: {signed(payout - bet)}")
    return {"reels": reels, "combo": combo, "multiplier": multiplier, "payout": payout,
            "net": payout - bet, "balance": balance}


def play_coin(chat_id: int, user_id: int, bet, side) -> dict:
    if side not in ("heads", "tails"):
        raise GameError("Выбери орла или решку")
    take_bet(chat_id, user_id, bet)

    result = casino_rig.take(chat_id, "coin", user_id)
    if result is None:
        roll = random.random()
        if roll < 0.01:
            result = "edge"  # встала на ребро — ставка возвращается
        elif roll < 0.02:
            result = "stolen"  # украли узбеки
        else:
            result = random.choice(("heads", "tails"))
    payout = bet if result == "edge" else (bet * 2 if result == side else 0)
    balance = db.casino_settle(chat_id, user_id, "coin", bet, payout, special=result == "edge")
    name = player_name(user_id)
    if result == "edge":
        add_feed(chat_id, f"🪙 У {name} монетка встала на ребро!")
    db.add_history(chat_id, "coin", {"result": result, "name": name, "net": payout - bet})
    return {"result": result, "won": payout > bet, "payout": payout, "net": payout - bet, "balance": balance,
            "history": db.get_history(chat_id, "coin", HISTORY_SHOWN)}


# ── Русская рулетка ───────────────────────────────────────────────────────────
# Барабан общий на беседу и не прокручивается: с каждым выстрелом следующему страшнее, зато и награда больше.


def _load_cylinder() -> dict:
    bullet = None if random.random() < RR_EMPTY_CHANCE else random.randrange(RR_CHAMBERS)
    return {"bullet": bullet, "fired": 0}


def rr_reward(shot: int, balance: int) -> int:
    """Награда за выстрел номер shot (с нуля) при таком кошельке."""
    percent, minimum = RR_REWARDS[shot]
    return max(minimum, balance * percent // 100)


def rr_state(chat_id: int, user_id: int, balance: int) -> dict:
    """Что видит игрок. Про пустой барабан молчим: шанс показываем классический (1 из оставшихся гнёзд),
    а шестой выстрел выглядит верной смертью без награды — пусть пустой барабан будет сюрпризом."""
    fired = _revolvers[chat_id]["fired"] if chat_id in _revolvers else 0
    last = fired == RR_CHAMBERS - 1
    percent, minimum = RR_REWARDS[fired]
    return {
        "chambers_left": RR_CHAMBERS - fired,
        "reward": None if last else rr_reward(fired, balance),
        "reward_percent": None if last else percent,
        "reward_min": None if last else minimum,
        "cooldown": math.ceil(limits.remaining(chat_id, user_id, "rr")),
        "history": db.get_history(chat_id, "rr", 8),
    }


def pull_trigger(chat_id: int, user_id: int) -> dict:
    throttle(chat_id, user_id)
    wait = limits.remaining(chat_id, user_id, "rr")
    if wait > 0:
        raise GameError(f"Револьвер перезаряжается: ещё {limits.format_wait(wait)}", 429)
    limits.mark(chat_id, user_id, "rr")

    revolver = _revolvers.setdefault(chat_id, _load_cylinder())
    shot = revolver["fired"]
    chance = RR_CHAMBERS - shot  # как видят игроки: 1 из оставшихся гнёзд
    balance = db.get_balance(chat_id, user_id)
    dead = revolver["bullet"] == shot
    survived_all = not dead and shot == RR_CHAMBERS - 1  # барабан был пустой, и его прошли до конца
    name = player_name(user_id)
    if dead:
        delta = -min(balance, max(RR_FUNERAL_MIN, balance * RR_FUNERAL_PERCENT // 100))
        add_feed(chat_id, f"💥 {name} — БАХ! Шанс был 1/{chance}, {signed(delta)}")
        news(chat_id, random.choice(texts.RR_DEATH).format(name=f"<b>{members.display_name(user_id)}</b>")
             + f"\n<i>Русская рулетка в /casino · шанс был 1/{chance}</i>")
    else:
        delta = rr_reward(shot, balance)
        revolver["fired"] += 1
        add_feed(chat_id, f"🍀 {name} — щёлк! Барабан был пустой, {signed(delta)}" if survived_all
                 else f"🔫 {name} — щёлк! Шанс был 1/{chance}, {signed(delta)}")
    if survived_all:
        news(chat_id, texts.RR_EMPTY.format(name=f"<b>{members.display_name(user_id)}</b>", reward=signed(delta)))
    if dead or survived_all:
        _revolvers.pop(chat_id)
        db.add_history(chat_id, "rr", {"bullet": shot + 1 if dead else None, "name": name})
    balance = db.casino_russian_roulette(chat_id, user_id, delta, died=dead)
    return {
        "dead": dead, "survived_all": survived_all, "shot": shot + 1, "chance": chance, "delta": delta,
        "balance": balance, "cooldown": math.ceil(limits.cooldown()), "rr": rr_state(chat_id, user_id, balance),
    }


def empty_cylinder(chat_id: int):
    """Подкрутка: вынуть патрон. Все оставшиеся выстрелы, включая шестой, будут холостыми."""
    _revolvers.setdefault(chat_id, _load_cylinder())["bullet"] = None


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


def _deal_natural(deck: list[str]) -> tuple[list[str], list[str]]:
    """Пульт хозяина: игроку сразу блэкджек, а у дилера своего нет."""
    ace = next(card for card in deck if card[:-1] == "A")
    deck.remove(ace)
    ten = next(card for card in deck if card[:-1] in ("10", "J", "Q", "K"))
    deck.remove(ten)
    dealer = [deck.pop(), deck.pop()]
    while _is_blackjack(dealer):
        deck.insert(0, dealer.pop())
        dealer.append(deck.pop())
    return [ace, ten], dealer


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
        add_feed(chat_id, f"🃏 {player_name(user_id)} собирает блэкджек: {signed(payout - stake)}")
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
    take_bet(chat_id, user_id, bet)
    deck = [rank + suit for rank in BJ_RANKS for suit in BJ_SUITS]
    random.shuffle(deck)
    hand = {"bet": bet, "doubled": False, "deck": deck, "player": [], "dealer": []}
    if casino_rig.take(chat_id, "blackjack", user_id):
        hand["player"], hand["dealer"] = _deal_natural(deck)
    else:
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
    take_bet(chat_id, user_id, hand["bet"])
    hand["doubled"] = True
    hand["player"].append(hand["deck"].pop())
    if hand_value(hand["player"])[0] > 21:
        return _blackjack_finish(chat_id, user_id, hand, "bust")
    return _dealer_plays(chat_id, user_id, hand)
