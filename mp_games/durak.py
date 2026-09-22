"""Дурак подкидной: 36 карт, шесть на руки, козырь снизу колоды.

Ходит тот, чья очередь: он кладёт карту, защищающийся бьёт или берёт. Подкидывать может
атакующий — теми достоинствами, что уже лежат на столе. Кто остался с картами последним — дурак,
остальные делят банк.
"""

from casino_engine import GameError
from mp_games.tools import card_text, deck, feed, hand_text, order, rank, suit

TITLE = "Дурак"
EMOJI = "🃏"
ABOUT = "Подкидной на 36 карт, 2–6 человек. Последний с картами — дурак, остальные делят банк"
MIN_SEATS, MAX_SEATS = 2, 6
PACE = "live"
TURN_SECONDS = 90

HAND = 6            # столько карт держим на руках, пока есть колода
MAX_TABLE = 6       # больше шести пар за один заход не бывает


# ── Раздача ───────────────────────────────────────────────────────────────────

def setup(players: list[int]) -> dict:
    cards = deck(small=True)
    hands = {str(player): sorted(cards[index * HAND:(index + 1) * HAND])
             for index, player in enumerate(players)}
    talon = cards[HAND * len(players):]
    trump_card = talon[-1] if talon else cards[HAND * len(players) - 1]
    trump = suit(trump_card)
    attacker = _first_attacker(players, hands, trump)
    state = {
        "players": players, "hands": hands, "talon": talon, "trump": trump, "trump_card": trump_card,
        "table": [], "phase": "attack", "attacker": attacker, "defender": None,
        "out": [], "loser": None, "beaten": 0, "feed": [],
    }
    state["defender"] = _next_alive(state, attacker)
    return feed(state, f"Козырь {card_text(trump_card)}")


def _first_attacker(players: list[int], hands: dict, trump: int) -> int:
    """Первым ходит тот, у кого самый мелкий козырь."""
    best, best_card = players[0], 99
    for player in players:
        trumps = [rank(card) for card in hands[str(player)] if suit(card) == trump]
        if trumps and min(trumps) < best_card:
            best, best_card = player, min(trumps)
    return best


# ── Кто где ───────────────────────────────────────────────────────────────────

def _alive(state: dict) -> list[int]:
    return [player for player in state["players"] if player not in state["out"]]


def _next_alive(state: dict, after: int) -> int | None:
    alive = _alive(state)
    if len(alive) < 2:
        return None
    if after not in alive:                       # атакующий уже вышел — берём следующего по кругу
        order_ids = state["players"]
        start = order_ids.index(after)
        for step in range(1, len(order_ids) + 1):
            candidate = order_ids[(start + step) % len(order_ids)]
            if candidate in alive:
                after = candidate
                break
    index = alive.index(after)
    return alive[(index + 1) % len(alive)]


def turn(state: dict):
    if state.get("loser") is not None or len(_alive(state)) < 2:
        return None
    return state["attacker"] if state["phase"] == "attack" else state["defender"]


def _hand(state: dict, player: int) -> list[int]:
    return state["hands"].get(str(player), [])


def _beats(card: int, by: int, trump: int) -> bool:
    if suit(by) == suit(card):
        return rank(by) > rank(card)
    return suit(by) == trump and suit(card) != trump


def _ranks_on_table(state: dict) -> set[int]:
    ranks = set()
    for pair in state["table"]:
        ranks.add(rank(pair["a"]))
        if pair["d"] is not None:
            ranks.add(rank(pair["d"]))
    return ranks


def _unbeaten(state: dict) -> list[int]:
    return [index for index, pair in enumerate(state["table"]) if pair["d"] is None]


def _can_attack(state: dict, player: int) -> list[int]:
    if state["phase"] != "attack" or state["attacker"] != player:
        return []
    hand = _hand(state, player)
    if len(state["table"]) >= min(MAX_TABLE, len(_hand(state, state["defender"])) + len(_unbeaten(state))):
        return []
    if not state["table"]:
        return sorted(hand)
    allowed = _ranks_on_table(state)
    return sorted(card for card in hand if rank(card) in allowed)


def _can_beat(state: dict, player: int) -> list[dict]:
    if state["phase"] != "defend" or state["defender"] != player:
        return []
    hand = _hand(state, player)
    out = []
    for index in _unbeaten(state):
        cards = [card for card in hand if _beats(state["table"][index]["a"], card, state["trump"])]
        if cards:
            out.append({"slot": index, "cards": sorted(cards)})
    return out


# ── Ходы ──────────────────────────────────────────────────────────────────────

def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        return _resign(state, user_id)
    if kind == "attack":
        return _attack(state, user_id, action.get("card"))
    if kind == "beat":
        return _beat(state, user_id, action.get("card"), action.get("slot"))
    if kind == "take":
        return _take(state, user_id)
    if kind == "done":
        return _done(state, user_id)
    raise GameError("Непонятный ход")


def _attack(state: dict, user_id: int, card) -> dict:
    if card not in _can_attack(state, user_id):
        raise GameError("Такой картой не походить")
    hand = [item for item in _hand(state, user_id) if item != card]
    state = state | {
        "hands": state["hands"] | {str(user_id): hand},
        "table": state["table"] + [{"a": card, "d": None}],
        "phase": "defend",
    }
    return feed(state, f"Ходит {card_text(card)}")


def _beat(state: dict, user_id: int, card, slot) -> dict:
    options = {item["slot"]: item["cards"] for item in _can_beat(state, user_id)}
    if slot is None:
        slot = next((index for index, cards in options.items() if card in cards), None)
    if slot not in options or card not in options[slot]:
        raise GameError("Этой картой не побить")
    table = [dict(pair) for pair in state["table"]]
    table[slot]["d"] = card
    hand = [item for item in _hand(state, user_id) if item != card]
    state = state | {"hands": state["hands"] | {str(user_id): hand}, "table": table}
    state = feed(state, f"Кроет {card_text(card)}")
    if not _unbeaten(state):
        state = state | {"phase": "attack"}       # отбился — атакующий подкидывает или говорит «бито»
    return state


def _take(state: dict, user_id: int) -> dict:
    if state["phase"] != "defend" or state["defender"] != user_id:
        raise GameError("Брать сейчас нечего")
    taken = [pair["a"] for pair in state["table"]] + [pair["d"] for pair in state["table"] if pair["d"] is not None]
    hand = sorted(_hand(state, user_id) + taken)
    state = state | {"hands": state["hands"] | {str(user_id): hand}}
    state = feed(state, f"Забирает {hand_text(taken)}")
    return _end_round(state, took=True)


def _done(state: dict, user_id: int) -> dict:
    if state["phase"] != "attack" or state["attacker"] != user_id:
        raise GameError("Сейчас не твой ход")
    if not state["table"]:
        raise GameError("Сначала походи")
    if _unbeaten(state):
        raise GameError("Ещё не всё побито")
    state = feed(state, "Бито")
    return _end_round(state, took=False)


def _resign(state: dict, user_id: int) -> dict:
    """Ушёл из-за стола — считается дураком, партия для остальных кончается."""
    if user_id not in _alive(state):
        return state
    others = [player for player in _alive(state) if player != user_id]
    state = feed(state, "Встал из-за стола")
    return state | {"loser": user_id, "out": state["out"] + others}


def _end_round(state: dict, took: bool) -> dict:
    """Стол уходит (в отбой или в руки), все добирают до шести, ход переходит дальше."""
    defender = state["defender"]
    state = state | {"table": [], "beaten": state["beaten"] + 1}
    state = _refill(state)
    out = list(state["out"])
    for player in state["players"]:
        if player not in out and not _hand(state, player) and not state["talon"]:
            out.append(player)
    state = state | {"out": out}
    alive = _alive(state)
    if len(alive) <= 1:
        return state | {"loser": alive[0] if alive else None, "phase": "over"}
    attacker = _next_alive(state, defender) if took else defender
    if attacker not in alive:
        attacker = alive[0]
    state = state | {"attacker": attacker, "phase": "attack"}
    return state | {"defender": _next_alive(state, attacker)}


def _refill(state: dict) -> dict:
    """Добирают по очереди: сначала атакующий, защищающийся — последним."""
    talon = list(state["talon"])
    hands = dict(state["hands"])
    queue = []
    start = state["players"].index(state["attacker"])
    for step in range(len(state["players"])):
        player = state["players"][(start + step) % len(state["players"])]
        if player != state["defender"] and player not in state["out"]:
            queue.append(player)
    if state["defender"] not in state["out"]:
        queue.append(state["defender"])
    for player in queue:
        hand = list(hands.get(str(player), []))
        while len(hand) < HAND and talon:
            hand.append(talon.pop(0))
        hands[str(player)] = sorted(hand)
    return state | {"hands": hands, "talon": talon}


def auto(state: dict, user_id: int) -> dict:
    """За просрочившего: защищающийся отбивается дешёвой картой или берёт, атакующий ходит мелочью."""
    if state["phase"] == "defend" and state["defender"] == user_id:
        options = _can_beat(state, user_id)
        if options:
            slot = options[0]
            card = sorted(slot["cards"], key=lambda item: (suit(item) == state["trump"], rank(item)))[0]
            return {"type": "beat", "card": card, "slot": slot["slot"]}
        return {"type": "take"}
    if state["table"] and not _unbeaten(state):
        return {"type": "done"}
    cards = _can_attack(state, user_id)
    if not cards:
        return {"type": "done"} if state["table"] else {"type": "resign"}
    cheap = sorted(cards, key=lambda card: (suit(card) == state["trump"], rank(card)))
    return {"type": "attack", "card": cheap[0]}


# ── Что видит игрок ───────────────────────────────────────────────────────────

def view(state: dict, user_id: int) -> dict:
    return {
        "hand": order(_hand(state, user_id), state["trump"]),
        "table": state["table"],
        "trump": state["trump"],
        "trump_card": state["trump_card"],
        "talon": len(state["talon"]),
        "phase": state["phase"],
        "attacker": state["attacker"],
        "defender": state["defender"],
        "loser": state.get("loser"),
        "players": [{"id": player, "cards": len(_hand(state, player)), "out": player in state["out"]}
                    for player in state["players"]],
    }


def moves(state: dict, user_id: int) -> dict:
    beats = _can_beat(state, user_id)
    return {
        "attack": _can_attack(state, user_id),
        "beat": beats,
        "take": bool(beats or (state["phase"] == "defend" and state["defender"] == user_id and state["table"])),
        "done": state["phase"] == "attack" and state["attacker"] == user_id
                and bool(state["table"]) and not _unbeaten(state),
    }


def result(state: dict):
    if state.get("phase") != "over" and state.get("loser") is None:
        return None
    loser = state.get("loser")
    winners = [player for player in state["players"] if player != loser]
    if loser is None:
        return {"winners": [], "text": "Ничья — все вышли разом"}
    return {"winners": winners, "text": "Дурак определён"}

