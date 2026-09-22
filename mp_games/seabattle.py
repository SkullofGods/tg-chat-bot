"""Морской бой: поле 10×10, флот расставляется сам, попал — стреляй ещё."""

import random

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Морской бой"
EMOJI = "🚢"
ABOUT = "Классика один на один. Флот расставляется сам, попал — стреляешь ещё раз"
MIN_SEATS, MAX_SEATS = 2, 2
PACE = "slow"
TURN_SECONDS = 12 * 3600

SIDE = 10
CELLS = SIDE * SIDE
FLEET = (4, 3, 3, 2, 2, 2, 1, 1, 1, 1)
EMPTY, SHIP, MISS, HIT = 0, 1, 2, 3


def _place() -> list[int]:
    """Расставляет флот, пока не уляжется: корабли не касаются даже углами."""
    for _ in range(200):
        board = [EMPTY] * CELLS
        ok = True
        for size in FLEET:
            for attempt in range(200):
                horizontal = random.random() < 0.5
                row = random.randrange(SIDE - (0 if horizontal else size - 1))
                col = random.randrange(SIDE - (size - 1 if horizontal else 0))
                cells = [(row + (0 if horizontal else step)) * SIDE + col + (step if horizontal else 0)
                         for step in range(size)]
                if all(_free(board, cell) for cell in cells):
                    for cell in cells:
                        board[cell] = SHIP
                    break
            else:
                ok = False
                break
        if ok:
            return board
    raise GameError("Не смог расставить флот, попробуй ещё раз")


def _free(board: list[int], cell: int) -> bool:
    row, col = divmod(cell, SIDE)
    for drow in (-1, 0, 1):
        for dcol in (-1, 0, 1):
            r, c = row + drow, col + dcol
            if 0 <= r < SIDE and 0 <= c < SIDE and board[r * SIDE + c] == SHIP:
                return False
    return True


def setup(players: list[int]) -> dict:
    state = {
        "players": players,
        "boards": {str(player): _place() for player in players},
        "shots": {str(player): [EMPTY] * CELLS for player in players},
        "side": 0, "winner": None, "shuffled": [], "feed": [],
    }
    return feed(state, "Флоты в море")


def turn(state: dict):
    if state.get("winner") is not None:
        return None
    return state["players"][state["side"]]


def _left(state: dict, player: int) -> int:
    board = state["boards"][str(player)]
    return sum(1 for cell in board if cell == SHIP)


def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        other = [player for player in state["players"] if player != user_id]
        state = feed(state, "Сдался")
        return state | {"winner": other[0] if other else None}
    if state["players"][state["side"]] != user_id:
        raise GameError("Сейчас не твой ход")
    if kind == "shuffle":
        if user_id in state["shuffled"]:
            raise GameError("Перемешать флот можно один раз", 409)
        state = feed(state, "Перемешал флот")
        return state | {"boards": state["boards"] | {str(user_id): _place()},
                        "shuffled": state["shuffled"] + [user_id]}
    if kind != "shot":
        raise GameError("Непонятный ход")
    cell = action.get("cell")
    if isinstance(cell, bool) or not isinstance(cell, int) or not 0 <= cell < CELLS:
        raise GameError("Мимо поля")
    enemy = [player for player in state["players"] if player != user_id][0]
    shots = list(state["shots"][str(user_id)])
    if shots[cell] != EMPTY:
        raise GameError("Сюда уже стреляли")
    board = list(state["boards"][str(enemy)])
    hit = board[cell] == SHIP
    shots[cell] = HIT if hit else MISS
    if hit:
        board[cell] = HIT
    state = state | {"shots": state["shots"] | {str(user_id): shots},
                     "boards": state["boards"] | {str(enemy): board}}
    state = feed(state, "Попал!" if hit else "Мимо")
    if hit and not _left(state, enemy):
        return feed(state | {"winner": user_id}, "Флот потоплен")
    if not hit:
        state = state | {"side": 1 - state["side"]}
    return state


def auto(state: dict, user_id: int) -> dict:
    shots = state["shots"][str(user_id)]
    free = [cell for cell, mark in enumerate(shots) if mark == EMPTY]
    if not free:
        return {"type": "resign"}
    return {"type": "shot", "cell": random.choice(free)}


def view(state: dict, user_id: int) -> dict:
    enemy = [player for player in state["players"] if player != user_id]
    enemy = enemy[0] if enemy else user_id
    over = state.get("winner") is not None
    return {
        "side": SIDE,
        "my_board": state["boards"].get(str(user_id), []),
        "my_shots": state["shots"].get(str(user_id), []),
        "enemy_shots": state["shots"].get(str(enemy), []),
        "enemy_board": state["boards"].get(str(enemy), []) if over else None,
        "my_left": _left(state, user_id),
        "enemy_left": _left(state, enemy),
        "can_shuffle": user_id not in state["shuffled"] and not any(state["shots"][str(user_id)]),
        "turn_side": state["side"],
        "me": state["players"].index(user_id) if user_id in state["players"] else 0,
        "winner": state.get("winner"),
    }


def moves(state: dict, user_id: int) -> dict:
    if state["players"][state["side"]] != user_id or state.get("winner") is not None:
        return {}
    shots = state["shots"].get(str(user_id), [])
    return {"cells": [cell for cell, mark in enumerate(shots) if mark == EMPTY],
            "shuffle": user_id not in state["shuffled"] and not any(shots)}


def result(state: dict):
    if state.get("winner") is None:
        return None
    return {"winners": [state["winner"]], "text": "Флот противника на дне"}
