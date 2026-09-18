"""Подкрутка казино: исход следующей игры. Хозяин ставит её скрытой командой /debug_casino_dev (handlers/debug.py).

В мини-приложении подкрутки нет вовсе — там хозяин такой же игрок, как все. Каждая срабатывает
один раз и живёт в памяти — перезапуск бота её сбрасывает. Пустой барабан в русской рулетке
ставится сразу (см. casino_engine.empty_cylinder), поэтому здесь его нет.
Модуль ни от чего не зависит: его используют и движок, и общие столы.
"""

# Для личных игр подкрутку можно отдать конкретному игроку, для общих столов — только следующему раунду
PERSONAL_GAMES = ("slots", "blackjack", "coin")
COIN_RESULTS = {"heads": "орёл", "tails": "решка", "edge": "ребро", "stolen": "украли узбеки"}
RACE_SIZE = 6

_rigs: dict[int, dict[str, dict]] = {}


def _check_value(game: str, value):
    """Проверяет значение. ValueError — с текстом, который можно показать хозяину."""
    if game in ("slots", "blackjack"):
        return True
    if game == "coin":
        if value not in COIN_RESULTS:
            raise ValueError("Выбери, как упадёт монетка")
        return value
    whole = isinstance(value, int) and not isinstance(value, bool)
    if game == "roulette":
        if not whole or not 0 <= value <= 36:
            raise ValueError("Число — от 0 до 36")
        return value
    if game == "race":
        if not whole or not 1 <= value <= RACE_SIZE:
            raise ValueError(f"Лошадь — от 1 до {RACE_SIZE}")
        return value
    raise ValueError("Это подкрутить нельзя")


def set_rig(chat_id: int, game: str, value, user_id: int | None = None):
    value = _check_value(game, value)
    if game not in PERSONAL_GAMES:
        user_id = None
    _rigs.setdefault(chat_id, {})[game] = {"value": value, "user_id": user_id}


def clear(chat_id: int, game: str | None = None):
    if game is None:
        _rigs.pop(chat_id, None)
    else:
        _rigs.get(chat_id, {}).pop(game, None)


def take(chat_id: int, game: str, user_id: int | None = None):
    """Забирает подкрутку, если она есть и подходит этому игроку. None — играем честно."""
    rig = _rigs.get(chat_id, {}).get(game)
    if rig is None or (rig["user_id"] is not None and rig["user_id"] != user_id):
        return None
    del _rigs[chat_id][game]
    return rig["value"]


def active(chat_id: int) -> list[dict]:
    return [{"game": game, "value": rig["value"], "user_id": rig["user_id"]} for game, rig in _rigs.get(chat_id, {}).items()]
