"""Очко: дворовая двадцать одно на 36 картах, без казино — друг против друга.

Считаем по-нашему: шестёрка-десятка по номиналу, валет 2, дама 3, король 4, туз 11 (или 1,
если иначе перебор). Два туза — золотое очко, оно бьёт даже честную двадцать одну.
"""

from casino_engine import GameError
from mp_games.tools import card_text, deck, feed, hand_text, rank

TITLE = "Очко"
EMOJI = "🂡"
ABOUT = "Дворовое очко на 36 карт, все против всех. Ближе к 21 без перебора — забирает банк"
MIN_SEATS, MAX_SEATS = 2, 6
PACE = "live"
TURN_SECONDS = 45

#            6  7  8  9 10   В  Д  К   Т
VALUES = (0, 0, 0, 0, 6, 7, 8, 9, 10, 2, 3, 4, 11)
GOLDEN = 22          # два туза
TARGET = 21


def points(cards: list[int]) -> int:
    """Сколько очков в руке. Два туза — золотое очко, лишние тузы падают до единицы."""
    if len(cards) == 2 and all(rank(card) == 12 for card in cards):
        return GOLDEN
    total = sum(VALUES[rank(card)] for card in cards)
    aces = sum(1 for card in cards if rank(card) == 12)
    while total > TARGET and aces:
        total -= 10
        aces -= 1
    return total


def setup(players: list[int]) -> dict:
    cards = deck(small=True)
    hands = {str(player): [cards.pop(), cards.pop()] for player in players}
    state = {"players": players, "deck": cards, "hands": hands, "done": [], "busted": [],
             "turn": players[0], "feed": []}
    golden = [player for player in players if points(hands[str(player)]) == GOLDEN]
    if golden:
        state = feed(state, "У кого-то золотое очко!")
    return feed(state, "Раздали по две")


def turn(state: dict):
    if len(state["done"]) >= len(state["players"]):
        return None
    return state["turn"]


def _next(state: dict, after: int):
    players = state["players"]
    index = players.index(after)
    for step in range(1, len(players) + 1):
        candidate = players[(index + step) % len(players)]
        if candidate not in state["done"]:
            return candidate
    return None


def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        done = state["done"] + ([user_id] if user_id not in state["done"] else [])
        state = feed(state, "Встал из-за стола")
        state = state | {"done": done, "busted": state["busted"] + [user_id]}
        return state | {"turn": _next(state, user_id) or state["turn"]}
    if state["turn"] != user_id or user_id in state["done"]:
        raise GameError("Сейчас не твой ход")
    if kind == "hit":
        if not state["deck"]:
            raise GameError("Колода кончилась — придётся остановиться", 409)
        cards = list(state["deck"])
        hand = list(state["hands"][str(user_id)]) + [cards.pop()]
        state = state | {"deck": cards, "hands": state["hands"] | {str(user_id): hand}}
        total = points(hand)
        state = feed(state, f"Берёт {card_text(hand[-1])} — {total}")
        if total >= TARGET:
            state = feed(state, "Перебор!" if total > TARGET else "Очко!")
            state = state | {"done": state["done"] + [user_id],
                             "busted": state["busted"] + ([user_id] if total > TARGET else [])}
            return state | {"turn": _next(state, user_id) or state["turn"]}
        return state
    if kind == "stand":
        state = feed(state, f"Хватит, {points(state['hands'][str(user_id)])}")
        state = state | {"done": state["done"] + [user_id]}
        return state | {"turn": _next(state, user_id) or state["turn"]}
    raise GameError("Непонятный ход")


def auto(state: dict, user_id: int) -> dict:
    """Просрочил — добираем до семнадцати и встаём, как приличный банкомёт."""
    hand = state["hands"].get(str(user_id), [])
    return {"type": "hit"} if points(hand) < 17 and state["deck"] else {"type": "stand"}


def view(state: dict, user_id: int) -> dict:
    over = turn(state) is None
    return {
        "my_cards": state["hands"].get(str(user_id), []),
        "my_points": points(state["hands"].get(str(user_id), [])),
        "my_text": hand_text(state["hands"].get(str(user_id), [])),
        "turn": state["turn"],
        "left": len(state["deck"]),
        "target": TARGET,
        "players": [{
            "id": player,
            "cards": state["hands"][str(player)] if (over or player == user_id)
            else [state["hands"][str(player)][0]] + [-1] * (len(state["hands"][str(player)]) - 1),
            "points": points(state["hands"][str(player)]) if (over or player == user_id) else None,
            "done": player in state["done"],
            "busted": player in state["busted"],
        } for player in state["players"]],
        "over": over,
    }


def moves(state: dict, user_id: int) -> dict:
    if state["turn"] != user_id or user_id in state["done"]:
        return {}
    return {"hit": bool(state["deck"]), "stand": True,
            "points": points(state["hands"].get(str(user_id), []))}


def result(state: dict):
    if turn(state) is not None:
        return None
    alive = [player for player in state["players"] if player not in state["busted"]]
    if not alive:
        return {"winners": [], "text": "Перебрали все — ставки по домам"}
    best = max(points(state["hands"][str(player)]) for player in alive)
    winners = [player for player in alive if points(state["hands"][str(player)]) == best]
    name = "золотое очко" if best == GOLDEN else "очко" if best == TARGET else f"{best}"
    return {"winners": winners, "text": f"Лучшая рука: {name}"}
