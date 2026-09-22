"""Шахматы: полные правила, включая рокировку, взятие на проходе, превращение и ничьи.

Доска — 64 числа. 0 пусто, 1–6 белые (пешка, конь, слон, ладья, ферзь, король),
7–12 чёрные в том же порядке. Ходит тот, чья очередь; ход проверяется на законность полностью,
поэтому подсунуть невозможный ход нельзя.
"""

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Шахматы"
EMOJI = "♟"
ABOUT = "Классика один на один, ход хоть завтра. Мат, пат, три повторения и правило пятидесяти ходов"
MIN_SEATS, MAX_SEATS = 2, 2
PACE = "slow"
TURN_SECONDS = 24 * 3600

SIDE = 8
PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING = 1, 2, 3, 4, 5, 6
BLACK = 6
START = (
    ROOK, KNIGHT, BISHOP, QUEEN, KING, BISHOP, KNIGHT, ROOK,
)
PROMO = {"q": QUEEN, "r": ROOK, "b": BISHOP, "n": KNIGHT}
STEPS = {
    KNIGHT: ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)),
    KING: ((0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)),
}
SLIDES = {
    BISHOP: ((1, 1), (1, -1), (-1, 1), (-1, -1)),
    ROOK: ((0, 1), (0, -1), (1, 0), (-1, 0)),
    QUEEN: ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)),
}


def _color(piece: int):
    if not piece:
        return None
    return 0 if piece <= BLACK else 1


def _kind(piece: int) -> int:
    return piece if piece <= BLACK else piece - BLACK


def _rc(cell: int):
    return divmod(cell, SIDE)


def _cell(row: int, col: int) -> int:
    return row * SIDE + col


def _on(row: int, col: int) -> bool:
    return 0 <= row < SIDE and 0 <= col < SIDE


def setup(players: list[int]) -> dict:
    board = [0] * 64
    for col, kind in enumerate(START):
        board[_cell(0, col)] = kind
        board[_cell(1, col)] = PAWN
        board[_cell(6, col)] = PAWN + BLACK
        board[_cell(7, col)] = kind + BLACK
    state = {
        "players": players, "board": board, "side": 0,
        "castle": [True, True, True, True],     # белые: короткая, длинная; чёрные: короткая, длинная
        "ep": None, "half": 0, "step": 0, "seen": {}, "winner": None, "over": None, "feed": [],
    }
    return feed(_remember(state), "Белые начинают")


def turn(state: dict):
    if state.get("over"):
        return None
    return state["players"][state["side"]]


# ── Ходы ──────────────────────────────────────────────────────────────────────

def _raw(board: list[int], cell: int, side: int, ep, castle) -> list[dict]:
    """Ходы фигуры без проверки на шах."""
    piece = board[cell]
    kind = _kind(piece)
    row, col = _rc(cell)
    out = []

    def add(r, c, **extra):
        if not _on(r, c):
            return False
        target = board[_cell(r, c)]
        if target and _color(target) == side:
            return False
        out.append({"from": cell, "to": _cell(r, c), **extra})
        return not target

    if kind == PAWN:
        ahead = 1 if side == 0 else -1
        start_row = 1 if side == 0 else 6
        last_row = 7 if side == 0 else 0
        if _on(row + ahead, col) and not board[_cell(row + ahead, col)]:
            step = {"from": cell, "to": _cell(row + ahead, col)}
            out += [step | {"promo": key} for key in PROMO] if row + ahead == last_row else [step]
            if row == start_row and not board[_cell(row + 2 * ahead, col)]:
                out.append({"from": cell, "to": _cell(row + 2 * ahead, col), "double": True})
        for dcol in (-1, 1):
            r, c = row + ahead, col + dcol
            if not _on(r, c):
                continue
            target = board[_cell(r, c)]
            if target and _color(target) != side:
                step = {"from": cell, "to": _cell(r, c)}
                out += [step | {"promo": key} for key in PROMO] if r == last_row else [step]
            elif ep is not None and _cell(r, c) == ep:
                out.append({"from": cell, "to": ep, "ep": True})
    elif kind in STEPS:
        for drow, dcol in STEPS[kind]:
            add(row + drow, col + dcol)
        if kind == KING:
            short, long = (0, 1) if side == 0 else (2, 3)
            home = 0 if side == 0 else 7
            if castle[short] and not any(board[_cell(home, c)] for c in (5, 6)):
                out.append({"from": cell, "to": _cell(home, 6), "castle": "short"})
            if castle[long] and not any(board[_cell(home, c)] for c in (1, 2, 3)):
                out.append({"from": cell, "to": _cell(home, 2), "castle": "long"})
    else:
        for drow, dcol in SLIDES[kind]:
            step = 1
            while add(row + drow * step, col + dcol * step):
                step += 1
    return out


def _attacked(board: list[int], cell: int, by: int) -> bool:
    """Бьёт ли сторона by эту клетку."""
    for index, piece in enumerate(board):
        if not piece or _color(piece) != by:
            continue
        kind = _kind(piece)
        row, col = _rc(index)
        target_row, target_col = _rc(cell)
        if kind == PAWN:
            ahead = 1 if by == 0 else -1
            if target_row == row + ahead and abs(target_col - col) == 1:
                return True
            continue
        for step in _raw(board, index, by, None, [False] * 4):
            if step["to"] == cell:
                return True
    return False


def _king(board: list[int], side: int):
    wanted = KING + (BLACK if side else 0)
    for cell, piece in enumerate(board):
        if piece == wanted:
            return cell
    return None


def _apply(state: dict, step: dict) -> dict:
    """Двигает фигуру и обновляет права на рокировку, взятие на проходе и счётчик тишины."""
    board = list(state["board"])
    side = state["side"]
    piece = board[step["from"]]
    kind = _kind(piece)
    captured = board[step["to"]]
    board[step["from"]] = 0
    board[step["to"]] = piece
    if step.get("promo"):
        board[step["to"]] = PROMO[step["promo"]] + (BLACK if side else 0)
    if step.get("ep"):
        row, col = _rc(step["to"])
        board[_cell(row - (1 if side == 0 else -1), col)] = 0
        captured = PAWN
    if step.get("castle"):
        home = 0 if side == 0 else 7
        if step["castle"] == "short":
            board[_cell(home, 5)], board[_cell(home, 7)] = board[_cell(home, 7)], 0
        else:
            board[_cell(home, 3)], board[_cell(home, 0)] = board[_cell(home, 0)], 0
    castle = list(state["castle"])
    if kind == KING:
        castle[0 if side == 0 else 2] = False
        castle[1 if side == 0 else 3] = False
    for index, (cell, flag) in enumerate(((_cell(0, 7), 0), (_cell(0, 0), 1), (_cell(7, 7), 2), (_cell(7, 0), 3))):
        if step["from"] == cell or step["to"] == cell:
            castle[flag] = False
    ep = None
    if step.get("double"):
        row, col = _rc(step["from"])
        ep = _cell(row + (1 if side == 0 else -1), col)
    half = 0 if (kind == PAWN or captured) else state["half"] + 1
    return state | {"board": board, "castle": castle, "ep": ep, "half": half,
                    "side": 1 - side, "step": state["step"] + 1}


def legal(state: dict) -> list[dict]:
    """Только те ходы, после которых свой король не под боем."""
    side = state["side"]
    board = state["board"]
    out = []
    for cell, piece in enumerate(board):
        if not piece or _color(piece) != side:
            continue
        for step in _raw(board, cell, side, state["ep"], state["castle"]):
            if step.get("castle"):
                home = 0 if side == 0 else 7
                path = (4, 5, 6) if step["castle"] == "short" else (4, 3, 2)
                if any(_attacked(board, _cell(home, col), 1 - side) for col in path):
                    continue
            after = _apply(state, step)
            king = _king(after["board"], side)
            if king is None or _attacked(after["board"], king, 1 - side):
                continue
            out.append(step)
    return out


def _same(one: dict, two: dict) -> bool:
    return one["from"] == two["from"] and one["to"] == two["to"] \
        and one.get("promo", "q") == two.get("promo", "q")


def move(state: dict, user_id: int, action: dict) -> dict:
    if action.get("type") == "resign":
        other = [player for player in state["players"] if player != user_id]
        state = feed(state, "Сдался")
        return state | {"winner": other[0] if other else None, "over": "resign"}
    if state["players"][state["side"]] != user_id:
        raise GameError("Сейчас не твой ход")
    wanted = {"from": action.get("from"), "to": action.get("to"), "promo": action.get("promo", "q")}
    options = [step for step in legal(state) if _same(step, wanted)]
    if not options:
        raise GameError("Так не ходят")
    state = _apply(state, options[0])
    state = _remember(state)
    return _check_end(state)


def _remember(state: dict) -> dict:
    """Запоминаем позицию — для ничьей по трём повторениям."""
    key = f"{''.join(map(chr, (piece + 48 for piece in state['board'])))}|{state['side']}" \
          f"|{''.join('1' if flag else '0' for flag in state['castle'])}|{state['ep']}"
    seen = dict(state.get("seen", {}))
    seen[key] = seen.get(key, 0) + 1
    return state | {"seen": seen}


def _material_draw(board: list[int]) -> bool:
    pieces = [_kind(piece) for piece in board if piece]
    heavy = [kind for kind in pieces if kind in (PAWN, ROOK, QUEEN)]
    minors = [kind for kind in pieces if kind in (KNIGHT, BISHOP)]
    return not heavy and len(minors) <= 1


def _check_end(state: dict) -> dict:
    side = state["side"]
    king = _king(state["board"], side)
    in_check = king is not None and _attacked(state["board"], king, 1 - side)
    if not legal(state):
        if in_check:
            winner = state["players"][1 - side]
            return feed(state | {"winner": winner, "over": "mate"}, "Мат")
        return feed(state | {"over": "stalemate"}, "Пат — ничья")
    if state["half"] >= 100:
        return feed(state | {"over": "fifty"}, "Ничья: пятьдесят ходов без взятий")
    if max(state["seen"].values(), default=0) >= 3:
        return feed(state | {"over": "repeat"}, "Ничья: три повторения")
    if _material_draw(state["board"]):
        return feed(state | {"over": "material"}, "Ничья: матовать нечем")
    if in_check:
        state = feed(state, "Шах")
    return state


def auto(state: dict, user_id: int) -> dict:
    """Сутки на ход прошли — партия засчитывается сопернику."""
    return {"type": "resign"}


def view(state: dict, user_id: int) -> dict:
    side = state["players"].index(user_id) if user_id in state["players"] else 0
    king = _king(state["board"], state["side"])
    return {
        "board": state["board"],
        "size": SIDE,
        "me": side,
        "turn_side": state["side"],
        "check": bool(king is not None and _attacked(state["board"], king, 1 - state["side"])),
        "over": state.get("over"),
        "winner": state.get("winner"),
        "step": state["step"],
    }


def moves(state: dict, user_id: int) -> dict:
    if state.get("over") or state["players"][state["side"]] != user_id:
        return {}
    return {"steps": legal(state)}


def result(state: dict):
    over = state.get("over")
    if not over:
        return None
    if state.get("winner"):
        return {"winners": [state["winner"]], "text": "Мат" if over == "mate" else "Соперник сдался"}
    texts = {"stalemate": "Пат", "fifty": "Пятьдесят ходов без взятий",
             "repeat": "Три повторения", "material": "Матовать нечем"}
    return {"winners": [], "text": f"Ничья: {texts.get(over, over)}"}
