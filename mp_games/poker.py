"""Покер, техасский холдем, sit-n-go: садятся с равными фишками и играют, пока один не соберёт все.

Фишки — внутренние, за стол платят ставкой один раз. Кто остался с фишками последним, забирает
банк стола. Блайнды растут каждые восемь раздач, чтобы партия не тянулась вечно.
"""

from casino_engine import GameError
from mp_games.tools import deck, feed, hand_text, rank, suit

TITLE = "Покер"
EMOJI = "♠️"
ABOUT = "Техасский холдем, sit-n-go на 2–6 человек. Фишки кончились — вылетел, последний берёт банк"
MIN_SEATS, MAX_SEATS = 2, 6
PACE = "live"
TURN_SECONDS = 60

CHIPS = 1000           # столько фишек у каждого на старте
BLINDS = (10, 20)      # малый и большой блайнд в начале
BLIND_EVERY = 8        # каждые столько раздач блайнды удваиваются
STREETS = ("pre", "flop", "turn", "river")
CARDS_OUT = {"flop": 3, "turn": 4, "river": 5}
COMBOS = ("старшая карта", "пара", "две пары", "тройка", "стрит", "флеш", "фулл-хаус", "каре", "стрит-флеш")


# ── Сила руки ─────────────────────────────────────────────────────────────────

def _straight(ranks: set[int]) -> int:
    """Старшая карта стрита или -1. Туз умеет быть единицей."""
    wheel = set(ranks) | ({-1} if 12 in ranks else set())
    best = -1
    for high in sorted(wheel):
        if all(high - step in wheel for step in range(1, 5)):
            best = max(best, high)
    return best


def score(cards: list[int]) -> tuple:
    """Сила лучшей пятёрки из семи карт: кортеж, который можно просто сравнивать."""
    ranks = [rank(card) for card in cards]
    counts: dict[int, int] = {}
    for value in ranks:
        counts[value] = counts.get(value, 0) + 1
    suits: dict[int, list[int]] = {}
    for card in cards:
        suits.setdefault(suit(card), []).append(rank(card))

    flush = next((values for values in suits.values() if len(values) >= 5), None)
    if flush:
        high = _straight(set(flush))
        if high >= 0:
            return (8, high)
    groups = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    if groups[0][1] == 4:
        kicker = max(value for value in ranks if value != groups[0][0])
        return (7, groups[0][0], kicker)
    if groups[0][1] == 3 and len(groups) > 1 and groups[1][1] >= 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5, *sorted(flush, reverse=True)[:5])
    high = _straight(set(ranks))
    if high >= 0:
        return (4, high)
    if groups[0][1] == 3:
        kickers = sorted((value for value in ranks if value != groups[0][0]), reverse=True)[:2]
        return (3, groups[0][0], *kickers)
    if groups[0][1] == 2 and len(groups) > 1 and groups[1][1] == 2:
        pairs = sorted((value for value, count in groups if count == 2), reverse=True)[:2]
        kicker = max(value for value in ranks if value not in pairs)
        return (2, *pairs, kicker)
    if groups[0][1] == 2:
        kickers = sorted((value for value in ranks if value != groups[0][0]), reverse=True)[:3]
        return (1, groups[0][0], *kickers)
    return (0, *sorted(ranks, reverse=True)[:5])


def combo_name(cards: list[int]) -> str:
    return COMBOS[score(cards)[0]]


# ── Раздача ───────────────────────────────────────────────────────────────────

def setup(players: list[int]) -> dict:
    state = {
        "players": players,
        "chips": {str(player): CHIPS for player in players},
        "out": [],
        "button": players[0],
        "blinds": list(BLINDS),
        "hands_played": 0,
        "hand": None,
        "winner": None,
        "feed": [],
    }
    return _deal(state)


def _alive(state: dict) -> list[int]:
    return [player for player in state["players"] if player not in state["out"]]


def _chips(state: dict, player: int) -> int:
    return state["chips"].get(str(player), 0)


def _after(state: dict, player: int, among: list[int] | None = None) -> int:
    ring = among if among is not None else _alive(state)
    if player not in ring:
        order = state["players"]
        start = order.index(player)
        for step in range(1, len(order) + 1):
            candidate = order[(start + step) % len(order)]
            if candidate in ring:
                return candidate
        return ring[0]
    return ring[(ring.index(player) + 1) % len(ring)]


def _deal(state: dict) -> dict:
    alive = _alive(state)
    if len(alive) < 2:
        return state | {"winner": alive[0] if alive else None, "hand": None}
    cards = deck()
    button = state["button"] if state["hands_played"] else alive[0]
    if state["hands_played"]:
        button = _after(state, state["button"], alive)
    small, big = state["blinds"]
    if state["hands_played"] and state["hands_played"] % BLIND_EVERY == 0:
        small, big = small * 2, big * 2
    order = alive[alive.index(button):] + alive[:alive.index(button)]
    if len(order) == 2:                       # на двоих баттон ставит малый блайнд и ходит первым
        blinds = [(order[0], small), (order[1], big)]
        first = order[0]
    else:
        blinds = [(order[1], small), (order[2], big)]
        first = order[3 % len(order)]
    hole = {str(player): [cards.pop(), cards.pop()] for player in order}
    bets = {str(player): 0 for player in order}
    chips = dict(state["chips"])
    for player, amount in blinds:
        paid = min(amount, chips[str(player)])
        chips[str(player)] -= paid
        bets[str(player)] = paid
    hand = {
        "deck": cards, "board": [], "hole": hole, "bets": bets, "paid": dict(bets), "pot": 0,
        "street": "pre", "live": list(order), "acted": [], "to_act": first,
        "current": max(bets.values()), "last_raise": big, "showdown": None,
    }
    state = state | {"chips": chips, "button": button, "blinds": [small, big], "hand": hand,
                     "hands_played": state["hands_played"] + 1}
    return _flow(feed(state, f"Раздача {state['hands_played']}: блайнды {small}/{big}"))


# ── Ход ───────────────────────────────────────────────────────────────────────

def turn(state: dict):
    if state.get("winner") is not None or not state.get("hand"):
        return None
    return state["hand"]["to_act"]


def _to_call(hand: dict, player: int) -> int:
    return max(0, hand["current"] - hand["bets"].get(str(player), 0))


def moves(state: dict, user_id: int) -> dict:
    hand = state.get("hand")
    if not hand or hand["to_act"] != user_id:
        return {}
    chips = _chips(state, user_id)
    call = min(_to_call(hand, user_id), chips)
    min_raise = hand["current"] + hand["last_raise"]
    return {
        "call": call,
        "check": _to_call(hand, user_id) == 0,
        "min_raise": min(min_raise, hand["bets"].get(str(user_id), 0) + chips),
        "max_raise": hand["bets"].get(str(user_id), 0) + chips,
        "chips": chips,
        "pot": hand["pot"] + sum(hand["bets"].values()),
    }


def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        state = feed(state, "Встал из-за стола")
        state = state | {"out": state["out"] + [user_id]}
        if state.get("hand"):
            state = _fold(state, user_id)
        alive = _alive(state)
        if len(alive) < 2:
            return state | {"winner": alive[0] if alive else None, "hand": None}
        return state
    hand = state.get("hand")
    if not hand or hand["to_act"] != user_id:
        raise GameError("Сейчас не твой ход")
    if kind == "fold":
        return _flow(_step(_fold(state, user_id), user_id))
    if kind == "check":
        if _to_call(hand, user_id):
            raise GameError("Нельзя чекать: надо уравнять или сбросить")
        return _flow(_step(_acted(state, user_id), user_id))
    if kind == "call":
        paid = _put(state, user_id, min(_to_call(hand, user_id), _chips(state, user_id)), "уравнял")
        return _flow(_step(paid, user_id))
    if kind == "raise":
        return _flow(_step(_raise(state, user_id, action.get("amount")), user_id))
    raise GameError("Непонятный ход")


def _step(state: dict, user_id: int) -> dict:
    """Ход сделан — передаём очередь следующему живому."""
    hand = state.get("hand")
    if not hand or not hand["live"]:
        return state
    nxt = _first_able(state, hand, _after(state, user_id, hand["live"]))
    return state | {"hand": hand | {"to_act": nxt or hand["to_act"]}}


def _acted(state: dict, user_id: int) -> dict:
    hand = state["hand"]
    acted = hand["acted"] + [user_id] if user_id not in hand["acted"] else hand["acted"]
    return state | {"hand": hand | {"acted": acted}}


def _put(state: dict, user_id: int, amount: int, what: str) -> dict:
    hand = state["hand"]
    amount = max(0, min(amount, _chips(state, user_id)))
    chips = dict(state["chips"])
    chips[str(user_id)] -= amount
    bets = dict(hand["bets"])
    bets[str(user_id)] = bets.get(str(user_id), 0) + amount
    paid = dict(hand.get("paid", {}))
    paid[str(user_id)] = paid.get(str(user_id), 0) + amount
    state = state | {"chips": chips, "hand": hand | {"bets": bets, "paid": paid}}
    if amount and not chips[str(user_id)]:
        line = f"ва-банк на {bets[str(user_id)]}"
    elif amount:
        line = f"{what} {amount}"
    else:
        line = "чек"
    state = state | {"last": {"by": user_id, "text": line}}
    state = feed(state, line.capitalize())
    return _acted(state, user_id)


def _raise(state: dict, user_id: int, amount) -> dict:
    hand = state["hand"]
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise GameError("Сколько ставим?")
    mine = hand["bets"].get(str(user_id), 0)
    stack = mine + _chips(state, user_id)
    if amount > stack:
        raise GameError("Столько фишек нет")
    min_raise = min(hand["current"] + hand["last_raise"], stack)
    if amount < min_raise:
        raise GameError(f"Поднимать можно от {min_raise}")
    state = _put(state, user_id, amount - mine, "поднял до")
    hand = state["hand"]
    return state | {"hand": hand | {"current": amount, "last_raise": max(hand["last_raise"], amount - hand["current"]),
                                    "acted": [user_id]}}


def _fold(state: dict, user_id: int) -> dict:
    hand = state.get("hand")
    if not hand:
        return state
    live = [player for player in hand["live"] if player != user_id]
    state = feed(state, "Сбросил")
    state = state | {"last": {"by": user_id, "text": "пас"}}
    return state | {"hand": hand | {"live": live, "acted": [p for p in hand["acted"] if p != user_id]}}


def _able(state: dict, hand: dict) -> list[int]:
    """Кто ещё может ставить: не сбросил и не в ва-банке."""
    return [player for player in hand["live"] if _chips(state, player) > 0]


def _first_able(state: dict, hand: dict, start: int):
    """Первый, кому есть чем ставить, начиная со start по кругу."""
    ring = hand["live"]
    if not ring:
        return None
    player = start if start in ring else _after(state, start, ring)
    for _ in range(len(ring)):
        if _chips(state, player) > 0:
            return player
        player = _after(state, player, ring)
    return None


def _flow(state: dict) -> dict:
    """Двигает раздачу: передаёт ход, открывает следующую улицу или вскрывается."""
    for _ in range(12):
        hand = state.get("hand")
        if not hand or state.get("winner") is not None:
            return state
        if len(hand["live"]) < 2:
            state = _finish(state)
            continue
        able = _able(state, hand)
        matched = all(hand["bets"].get(str(player), 0) == hand["current"] or _chips(state, player) == 0
                      for player in hand["live"])
        if matched and (len(able) < 2 or all(player in hand["acted"] for player in able)):
            state = _street(state)
            continue
        actor = hand["to_act"]
        if actor not in hand["live"] or _chips(state, actor) <= 0:
            nxt = _first_able(state, hand, actor)
            if nxt is None:
                state = _street(state)
                continue
            state = state | {"hand": hand | {"to_act": nxt}}
        return state
    return state


def _street(state: dict) -> dict:
    """Ставки собраны — открываем карты или идём к вскрытию."""
    hand = state["hand"]
    pot = hand["pot"] + sum(hand["bets"].values())
    index = STREETS.index(hand["street"])
    if index == len(STREETS) - 1:
        return _finish(state | {"hand": hand | {"pot": pot, "bets": {str(p): 0 for p in hand["live"]}}})
    street = STREETS[index + 1]
    cards = list(hand["deck"])
    board = list(hand["board"])
    while len(board) < CARDS_OUT[street]:
        board.append(cards.pop())
    first = _first_able(state, hand, _after(state, state["button"], hand["live"])) or hand["live"][0]
    state = state | {"hand": hand | {
        "street": street, "board": board, "deck": cards, "pot": pot,
        "bets": {str(player): 0 for player in hand["live"]}, "acted": [],
        "current": 0, "last_raise": state["blinds"][1], "to_act": first,
    }}
    return feed(state, f"{'Флоп' if street == 'flop' else 'Тёрн' if street == 'turn' else 'Ривер'}: "
                       f"{hand_text(board)}")


def _share(paid: dict, live: list[int], scores: dict) -> dict:
    """Делёж банка слоями. Кто пошёл ва-банк на мелкую сумму, больше неё с каждого не возьмёт,
    а лишнее, которое никто не покрыл, возвращается хозяину."""
    payouts: dict[int, int] = {}
    levels = sorted({amount for amount in paid.values() if amount > 0})
    previous = 0
    for level in levels:
        part = sum(min(level, amount) - min(previous, amount) for amount in paid.values())
        eligible = [player for player in live if paid.get(str(player), 0) >= level]
        if part and eligible:
            best = max(scores[player] for player in eligible)
            winners = [player for player in eligible if scores[player] == best]
            share, extra = divmod(part, len(winners))
            for index, player in enumerate(winners):
                payouts[player] = payouts.get(player, 0) + share + (extra if index == 0 else 0)
        elif part:                                   # ставку никто не покрыл — вернуть
            for key, amount in paid.items():
                mine = min(level, amount) - min(previous, amount)
                if mine:
                    payouts[int(key)] = payouts.get(int(key), 0) + mine
        previous = level
    return payouts


def _finish(state: dict) -> dict:
    """Вскрываемся, делим банк (с учётом сайд-потов) и раздаём заново."""
    hand = state["hand"]
    pot = hand["pot"] + sum(hand["bets"].values())
    chips = dict(state["chips"])
    paid = hand.get("paid", {})
    live = hand["live"]
    shows = []
    if len(live) == 1:
        scores = {live[0]: (99,)}
        state = feed(state, "Все сбросили — банк уходит без вскрытия")
    else:
        board = list(hand["board"])
        cards = list(hand["deck"])
        while len(board) < 5:
            board.append(cards.pop())
        scores = {player: score(hand["hole"][str(player)] + board) for player in live}
        ranked = sorted(live, key=lambda player: scores[player], reverse=True)
        shows = [{"id": player, "cards": hand["hole"][str(player)],
                  "combo": combo_name(hand["hole"][str(player)] + board)} for player in ranked]
        state = state | {"hand": hand | {"board": board}}
        state = feed(state, f"Вскрытие: {combo_name(hand['hole'][str(ranked[0])] + board)} забирает {pot}")
    for player, amount in _share(paid, live, scores).items():
        chips[str(player)] = chips.get(str(player), 0) + amount
    state = state | {"chips": chips}
    busted = [player for player in _alive(state) if chips.get(str(player), 0) <= 0]
    if busted:
        state = state | {"out": state["out"] + busted}
        state = feed(state, "Кто-то остался без фишек и вылетел")
    alive = _alive(state)
    if len(alive) < 2:
        return state | {"winner": alive[0] if alive else None, "hand": None,
                        "showdown": shows}
    state = state | {"hand": state["hand"] | {"showdown": shows}}
    return _deal(state)


def auto(state: dict, user_id: int) -> dict:
    """Просрочил ход — чек, если бесплатно, иначе фолд."""
    hand = state.get("hand")
    if hand and _to_call(hand, user_id) == 0:
        return {"type": "check"}
    return {"type": "fold"}


def view(state: dict, user_id: int) -> dict:
    hand = state.get("hand") or {}
    return {
        "chips": {str(player): _chips(state, player) for player in state["players"]},
        "out": state["out"],
        "button": state.get("button"),
        "blinds": state["blinds"],
        "hand_no": state["hands_played"],
        "board": hand.get("board", []),
        "pot": hand.get("pot", 0) + sum(hand.get("bets", {}).values()),
        "bets": hand.get("bets", {}),
        "live": hand.get("live", []),
        "street": hand.get("street"),
        "to_act": hand.get("to_act"),
        "my_cards": hand.get("hole", {}).get(str(user_id), []),
        "my_combo": combo_name(hand["hole"][str(user_id)] + hand.get("board", []))
        if hand.get("hole", {}).get(str(user_id)) and len(hand.get("board", [])) >= 3 else None,
        "showdown": hand.get("showdown") or state.get("showdown"),
        "last": state.get("last"),
        "winner": state.get("winner"),
    }


def result(state: dict):
    if state.get("winner") is None:
        return None
    return {"winners": [state["winner"]], "text": "Собрал все фишки"}
