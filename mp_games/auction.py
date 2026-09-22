"""Уникальное число: все втёмную называют число, выигрывает самое маленькое, которое больше никто не назвал."""

import random

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Уникальное число"
EMOJI = "🎯"
ABOUT = "Все втёмную называют число от 1 до 50. Банк берёт самое маленькое, которое больше никто не назвал"
MIN_SEATS, MAX_SEATS = 2, 10
PACE = "live"
TURN_SECONDS = 60
CLOCK_SECONDS = 5

TOP = 50
PICK_SECONDS = 45


def setup(players: list[int]) -> dict:
    state = {"players": players, "picks": {}, "phase": "pick", "waited": 0, "winner": None, "feed": []}
    return feed(state, "Загадывайте число от 1 до 50")


def turn(state: dict):
    return None


def clock(state: dict, now: int) -> dict:
    if state["phase"] != "pick":
        return state
    waited = state["waited"] + CLOCK_SECONDS
    if len(state["picks"]) >= len(state["players"]) or waited >= PICK_SECONDS:
        return _reveal(state | {"waited": waited})
    return state | {"waited": waited}


def _reveal(state: dict) -> dict:
    picks = dict(state["picks"])
    for player in state["players"]:                # не назвал число — назовём за него
        picks.setdefault(str(player), random.randint(1, TOP))
    state = state | {"picks": picks}
    counts: dict[int, int] = {}
    for number in state["picks"].values():
        counts[number] = counts.get(number, 0) + 1
    unique = sorted(number for number, count in counts.items() if count == 1)
    if not unique:
        return feed(state | {"phase": "over"}, "Все числа совпали — победителя нет")
    best = unique[0]
    winner = next(int(player) for player, number in state["picks"].items() if number == best)
    return feed(state | {"phase": "over", "winner": winner, "best": best},
                f"Самое маленькое уникальное — {best}")


def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        picks = {key: value for key, value in state["picks"].items() if key != str(user_id)}
        players = [player for player in state["players"] if player != user_id]
        state = feed(state, "Ушёл, не назвав числа")
        if len(players) < 2:
            return _reveal(state | {"picks": picks, "players": players or state["players"]})
        return state | {"picks": picks, "players": players}
    if kind != "pick":
        raise GameError("Непонятный ход")
    if state["phase"] != "pick":
        raise GameError("Числа уже открыты", 409)
    number = action.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= TOP:
        raise GameError(f"Число от 1 до {TOP}")
    picks = dict(state["picks"])
    picks[str(user_id)] = number
    state = feed(state, "Число загадано")
    state = state | {"picks": picks}
    if len(picks) >= len(state["players"]):
        return _reveal(state)
    return state


def auto(state: dict, user_id: int) -> dict:
    return {"type": "pick", "number": random.randint(1, TOP)}


def view(state: dict, user_id: int) -> dict:
    over = state["phase"] == "over"
    return {
        "top": TOP,
        "phase": state["phase"],
        "my_pick": state["picks"].get(str(user_id)),
        "ready": len(state["picks"]),
        "total": len(state["players"]),
        "left": max(0, PICK_SECONDS - state["waited"]) if state["phase"] == "pick" else 0,
        "picks": state["picks"] if over else {},
        "best": state.get("best"),
        "winner": state.get("winner"),
    }


def moves(state: dict, user_id: int) -> dict:
    return {"pick": state["phase"] == "pick" and str(user_id) not in state["picks"], "top": TOP}


def result(state: dict):
    if state["phase"] != "over":
        return None
    if state.get("winner"):
        return {"winners": [state["winner"]], "text": f"Уникальное число {state['best']}"}
    return {"winners": [], "text": "Все числа совпали — ставки по домам"}
