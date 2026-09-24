"""Короткие нарды: два кубика, дубль — четыре хода, сбитая фишка идёт на бар.

Доска — 24 пункта. Плюс — фишки первого игрока (идёт от 0 к 23), минус — второго (от 23 к 0).
Выбрасывать можно, когда все пятнадцать дома: у первого это пункты 18–23, у второго 0–5.
"""

import random

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Нарды"
EMOJI = "🎲"
ABOUT = "Короткие нарды один на один. Кубики бросает сервер, сбитая фишка идёт на бар"
MIN_SEATS, MAX_SEATS = 2, 2
PACE = "slow"
TURN_SECONDS = 12 * 3600

POINTS = 24
CHECKERS = 15
HOME = {0: range(18, 24), 1: range(0, 6)}
OFF = {0: POINTS, 1: -1}


def _start_board() -> list[int]:
    board = [0] * POINTS
    for point, count in ((0, 2), (11, 5), (16, 3), (18, 5)):
        board[point] = count
    for point, count in ((23, -2), (12, -5), (7, -3), (5, -5)):
        board[point] = count
    return board


def setup(players: list[int]) -> dict:
    state = {"players": players, "board": _start_board(), "bar": [0, 0], "off": [0, 0],
             "side": 0, "dice": [], "winner": None, "step": 0, "last": None, "feed": []}
    return feed(_roll(state), "Первый ход за белыми")


def _roll(state: dict) -> dict:
    first, second = random.randint(1, 6), random.randint(1, 6)
    dice = [first] * 4 if first == second else [first, second]
    state = state | {"dice": dice}
    return feed(state, f"Кубики: {first} и {second}" + (" — дубль!" if first == second else ""))


def turn(state: dict):
    if state.get("winner") is not None:
        return None
    return state["players"][state["side"]]


def _mine(count: int, side: int) -> bool:
    return count > 0 if side == 0 else count < 0


def _theirs(count: int, side: int) -> bool:
    return count < 0 if side == 0 else count > 0


def _home_ready(state: dict, side: int) -> bool:
    if state["bar"][side]:
        return False
    outside = [point for point in range(POINTS)
               if _mine(state["board"][point], side) and point not in HOME[side]]
    return not outside


def _target(side: int, point: int, die: int) -> int:
    return point + die if side == 0 else point - die


def legal(state: dict) -> list[dict]:
    """Все ходы, которые можно сделать оставшимися кубиками."""
    side = state["side"]
    board = state["board"]
    out = []
    for die in sorted(set(state["dice"])):
        if state["bar"][side]:
            entry = die - 1 if side == 0 else POINTS - die
            if not _theirs(board[entry], side) or abs(board[entry]) == 1:
                out.append({"from": -1, "to": entry, "die": die})
            continue
        for point in range(POINTS):
            if not _mine(board[point], side):
                continue
            target = _target(side, point, die)
            if 0 <= target < POINTS:
                if not _theirs(board[target], side) or abs(board[target]) == 1:
                    out.append({"from": point, "to": target, "die": die})
            elif _home_ready(state, side):
                exact = (target == POINTS and side == 0) or (target == -1 and side == 1)
                farther = [p for p in HOME[side] if _mine(board[p], side)
                           and (p < point if side == 0 else p > point)]
                if exact or not farther:
                    out.append({"from": point, "to": OFF[side], "die": die})
    return out


def move(state: dict, user_id: int, action: dict) -> dict:
    if action.get("type") == "resign":
        other = [player for player in state["players"] if player != user_id]
        state = feed(state, "Сдался")
        return state | {"winner": other[0] if other else None}
    if state["players"][state["side"]] != user_id:
        raise GameError("Сейчас не твой ход")
    options = [step for step in legal(state)
               if step["from"] == action.get("from") and step["to"] == action.get("to")]
    if not options:
        raise GameError("Так не ходят")
    step = options[0]
    side = state["side"]
    board = list(state["board"])
    bar = list(state["bar"])
    off = list(state["off"])
    sign = 1 if side == 0 else -1
    if step["from"] == -1:
        bar[side] -= 1
    else:
        board[step["from"]] -= sign
    if step["to"] in (POINTS, -1):
        off[side] += 1
        state = feed(state, "Фишка вышла")
    else:
        if _theirs(board[step["to"]], side):
            board[step["to"]] = 0
            bar[1 - side] += 1
            state = feed(state, "Сбил фишку — на бар")
        board[step["to"]] += sign
    dice = list(state["dice"])
    dice.remove(step["die"])
    where = "с бара" if step["from"] == -1 else str(step["from"] + 1)
    target = "домой" if step["to"] in (POINTS, -1) else str(step["to"] + 1)
    state = state | {"board": board, "bar": bar, "off": off, "dice": dice, "step": state["step"] + 1,
                     "last": {"by": user_id, "from": step["from"], "to": step["to"],
                              "text": f"{where} → {target}"}}
    if off[side] >= CHECKERS:
        return feed(state | {"winner": state["players"][side]}, "Все фишки дома")
    if not dice or not legal(state):
        state = state | {"side": 1 - side, "dice": []}
        state = _roll(state)
        if not legal(state):                       # ходить нечем — пропуск
            state = feed(state, "Ходить нечем, пропуск")
            state = state | {"side": 1 - state["side"], "dice": []}
            state = _roll(state)
    return state


def auto(state: dict, user_id: int) -> dict:
    options = legal(state)
    if not options:
        return {"type": "resign"}
    pick = random.choice(options)
    return {"type": "move", "from": pick["from"], "to": pick["to"]}


def view(state: dict, user_id: int) -> dict:
    side = state["players"].index(user_id) if user_id in state["players"] else 0
    return {
        "board": state["board"],
        "bar": state["bar"],
        "off": state["off"],
        "dice": state["dice"],
        "me": side,
        "turn_side": state["side"],
        "home_ready": _home_ready(state, side),
        "last": state.get("last"),
        "winner": state.get("winner"),
    }


def moves(state: dict, user_id: int) -> dict:
    if state.get("winner") is not None or state["players"][state["side"]] != user_id:
        return {}
    return {"steps": legal(state), "dice": state["dice"]}


def result(state: dict):
    if state.get("winner") is None:
        return None
    side = state["players"].index(state["winner"])
    text = "Все 15 фишек вышли" if state["off"][side] >= CHECKERS else "Соперник сдался"
    return {"winners": [state["winner"]], "text": text}
