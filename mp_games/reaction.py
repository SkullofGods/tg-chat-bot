"""Кто быстрее: ждём зелёного, жмём первым. Дёрнулся раньше — вылетел."""

import random
import time

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Кто быстрее"
EMOJI = "⚡"
ABOUT = "Ждёшь зелёного и жмёшь первым. Нажал раньше — вылетел. Весь банк первому"
MIN_SEATS, MAX_SEATS = 2, 10
PACE = "live"
TURN_SECONDS = 30
CLOCK_SECONDS = 1           # часы стола проверяют, не пора ли давать зелёный

WAIT_MIN, WAIT_MAX = 3, 9   # столько секунд держим красный
GIVE_UP = 20                # если после зелёного никто не нажал


def setup(players: list[int]) -> dict:
    now = int(time.time())
    state = {"players": players, "phase": "wait", "go_at": now + random.randint(WAIT_MIN, WAIT_MAX),
             "taps": {}, "false": [], "winner": None, "feed": []}
    return feed(state, "Приготовились…")


def turn(state: dict):
    return None                     # очереди нет: жмут все разом


def clock(state: dict, now: int) -> dict:
    if state["phase"] == "wait" and now >= state["go_at"]:
        return feed(state | {"phase": "go", "go_at": now}, "ЗЕЛЁНЫЙ! Жми!")
    if state["phase"] == "go" and now > state["go_at"] + GIVE_UP:
        return feed(state | {"phase": "over"}, "Никто не нажал")
    return state


def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        others = [player for player in state["players"] if player != user_id]
        state = feed(state, "Ушёл, не дождавшись")
        return state | {"false": state["false"] + [user_id],
                        "phase": "over" if len(others) <= 1 else state["phase"],
                        "winner": others[0] if len(others) == 1 else state["winner"]}
    if kind != "tap":
        raise GameError("Непонятный ход")
    if user_id in state["false"]:
        raise GameError("Ты уже дёрнулся раньше времени", 409)
    if state["phase"] == "over":
        raise GameError("Всё уже кончилось", 409)
    if state["phase"] == "wait":
        false = state["false"] + [user_id]
        state = feed(state, "Фальстарт!")
        left = [player for player in state["players"] if player not in false]
        if not left:
            return state | {"false": false, "phase": "over"}
        if len(left) == 1:
            return state | {"false": false, "phase": "over", "winner": left[0]}
        return state | {"false": false}
    taps = dict(state["taps"])
    taps[str(user_id)] = round(time.time() - state["go_at"], 3)
    state = feed(state, f"Нажал за {taps[str(user_id)]:.2f} с")
    return state | {"taps": taps, "phase": "over", "winner": user_id}


def auto(state: dict, user_id: int) -> dict:
    return {"type": "resign"}


def view(state: dict, user_id: int) -> dict:
    return {
        "phase": state["phase"],
        "left": max(0, state["go_at"] - int(time.time())) if state["phase"] == "wait" else 0,
        "mine_out": user_id in state["false"],
        "taps": state["taps"],
        "false": state["false"],
        "winner": state.get("winner"),
        "players": state["players"],
    }


def moves(state: dict, user_id: int) -> dict:
    return {"tap": state["phase"] in ("wait", "go") and user_id not in state["false"]}


def result(state: dict):
    if state["phase"] != "over":
        return None
    if state.get("winner"):
        seconds = state["taps"].get(str(state["winner"]))
        return {"winners": [state["winner"]],
                "text": f"Реакция {seconds:.2f} с" if seconds else "Остался один"}
    return {"winners": [], "text": "Никто не успел — ставки по домам"}
