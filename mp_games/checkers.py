"""Русские шашки: бой обязателен, дамка летает, съел — бей дальше.

Доска — список из 64 чисел: 0 пусто, 1/2 — шашка и дамка первого, 3/4 — второго.
"""

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Шашки"
EMOJI = "⛂"
ABOUT = "Русские шашки один на один. Бой обязателен, дамка ходит на любое расстояние"
MIN_SEATS, MAX_SEATS = 2, 2
PACE = "slow"
TURN_SECONDS = 24 * 3600

SIDE = 8
DIRS = ((1, 1), (1, -1), (-1, 1), (-1, -1))


def _cell(row: int, col: int) -> int:
    return row * SIDE + col


def _rc(cell: int) -> tuple[int, int]:
    return divmod(cell, SIDE)


def _mine(piece: int, side: int) -> bool:
    return piece in ((1, 2) if side == 0 else (3, 4))


def _king(piece: int) -> bool:
    return piece in (2, 4)


def setup(players: list[int]) -> dict:
    board = [0] * (SIDE * SIDE)
    for row in range(3):
        for col in range(SIDE):
            if (row + col) % 2:
                board[_cell(row, col)] = 1
    for row in range(SIDE - 3, SIDE):
        for col in range(SIDE):
            if (row + col) % 2:
                board[_cell(row, col)] = 3
    state = {"players": players, "board": board, "side": 0, "chain": None,
             "winner": None, "steps": 0, "quiet": 0, "feed": []}
    return feed(state, "Расставились")


def turn(state: dict):
    if state.get("winner") is not None:
        return None
    return state["players"][state["side"]]


def _steps(board: list[int], cell: int, side: int) -> list[dict]:
    """Тихие ходы шашки или дамки из клетки."""
    piece = board[cell]
    row, col = _rc(cell)
    out = []
    for drow, dcol in DIRS:
        if not _king(piece):
            forward = 1 if side == 0 else -1
            if drow != forward:
                continue
            r, c = row + drow, col + dcol
            if 0 <= r < SIDE and 0 <= c < SIDE and not board[_cell(r, c)]:
                out.append({"from": cell, "to": _cell(r, c), "eat": None})
            continue
        step = 1
        while True:
            r, c = row + drow * step, col + dcol * step
            if not (0 <= r < SIDE and 0 <= c < SIDE) or board[_cell(r, c)]:
                break
            out.append({"from": cell, "to": _cell(r, c), "eat": None})
            step += 1
    return out


def _jumps(board: list[int], cell: int, side: int) -> list[dict]:
    """Бои из клетки: шашка через соседнюю, дамка — с разбега."""
    piece = board[cell]
    row, col = _rc(cell)
    out = []
    for drow, dcol in DIRS:
        step = 1
        while True:
            r, c = row + drow * step, col + dcol * step
            if not (0 <= r < SIDE and 0 <= c < SIDE):
                break
            target = board[_cell(r, c)]
            if target:
                if _mine(target, side):
                    break
                land = 1
                while True:
                    lr, lc = r + drow * land, c + dcol * land
                    if not (0 <= lr < SIDE and 0 <= lc < SIDE) or board[_cell(lr, lc)]:
                        break
                    out.append({"from": cell, "to": _cell(lr, lc), "eat": _cell(r, c)})
                    if not _king(piece):
                        break
                    land += 1
                break
            if not _king(piece):
                break
            step += 1
    return out


def legal(state: dict) -> list[dict]:
    """Все ходы стороны. Если есть бой — только бои, и только продолжение начатой цепочки."""
    board, side = state["board"], state["side"]
    if state["chain"] is not None:
        return _jumps(board, state["chain"], side)
    jumps, steps = [], []
    for cell, piece in enumerate(board):
        if not piece or not _mine(piece, side):
            continue
        jumps += _jumps(board, cell, side)
        steps += _steps(board, cell, side)
    return jumps or steps


def move(state: dict, user_id: int, action: dict) -> dict:
    if action.get("type") == "resign":
        other = [player for player in state["players"] if player != user_id]
        state = feed(state, "Сдался")
        return state | {"winner": other[0] if other else None}
    if state["players"][state["side"]] != user_id:
        raise GameError("Сейчас не твой ход")
    wanted = {"from": action.get("from"), "to": action.get("to")}
    options = [item for item in legal(state)
               if item["from"] == wanted["from"] and item["to"] == wanted["to"]]
    if not options:
        raise GameError("Так не ходят" if not any(item["eat"] for item in legal(state)) else "Бей, раз можешь")
    step = options[0]
    board = list(state["board"])
    piece = board[step["from"]]
    board[step["from"]] = 0
    if step["eat"] is not None:
        board[step["eat"]] = 0
    row, _ = _rc(step["to"])
    if piece == 1 and row == SIDE - 1:
        piece = 2
    elif piece == 3 and row == 0:
        piece = 4
    board[step["to"]] = piece
    state = state | {"board": board, "steps": state["steps"] + 1,
                     "quiet": 0 if step["eat"] is not None else state["quiet"] + 1}
    if step["eat"] is not None:
        more = _jumps(board, step["to"], state["side"])
        if more:
            return feed(state | {"chain": step["to"]}, "Бьёт дальше")
        state = feed(state, "Съел")
    state = state | {"chain": None, "side": 1 - state["side"]}
    return _check_end(state)


def _check_end(state: dict) -> dict:
    """Проигрывает тот, у кого не осталось шашек или ходов."""
    side = state["side"]
    has_pieces = any(_mine(piece, side) for piece in state["board"] if piece)
    if not has_pieces or not legal(state):
        winner = state["players"][1 - side]
        return feed(state | {"winner": winner}, "Ходить нечем")
    if state["quiet"] >= 60:                 # 30 ходов каждым без боя — ничья
        return feed(state | {"winner": "draw"}, "Ничья: тридцать ходов без боя")
    return state


def auto(state: dict, user_id: int) -> dict:
    """Просрочил — ходим первым разрешённым, чтобы партия не висла."""
    options = legal(state)
    if not options:
        return {"type": "resign"}
    eats = [item for item in options if item["eat"] is not None]
    pick = (eats or options)[0]
    return {"type": "step", "from": pick["from"], "to": pick["to"]}


def view(state: dict, user_id: int) -> dict:
    side = state["players"].index(user_id) if user_id in state["players"] else 0
    return {
        "board": state["board"],
        "side": SIDE,
        "me": side,
        "turn_side": state["side"],
        "chain": state["chain"],
        "winner": state.get("winner"),
    }


def moves(state: dict, user_id: int) -> dict:
    if state["players"][state["side"]] != user_id or state.get("winner") is not None:
        return {}
    return {"steps": legal(state)}


def result(state: dict):
    winner = state.get("winner")
    if winner is None:
        return None
    if winner == "draw":
        return {"winners": [], "text": "Ничья: тридцать ходов без боя"}
    return {"winners": [winner], "text": "Партия выиграна"}
