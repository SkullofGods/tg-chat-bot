"""Правила игр мультиплеера: один файл — одна игра.

Движок (multiplayer.py) ничего не знает про карты и доски. От каждой игры ему нужен одинаковый
набор:

    TITLE, EMOJI, ABOUT          — как игра называется и что про неё написать
    MIN_SEATS, MAX_SEATS         — сколько человек садится
    PACE                         — "live" (играем прямо сейчас) или "slow" (ход хоть завтра)
    TURN_SECONDS                 — сколько ждём ход, потом ходим за игрока сами

    setup(players)               — раздать карты и расставить фигуры
    turn(state)                  — чей сейчас ход (None — партия кончилась)
    view(state, user_id)         — состояние глазами игрока, без чужих карт
    moves(state, user_id)        — что ему сейчас можно (подсказка приложению)
    move(state, user_id, action) — сделать ход, вернуть новое состояние; неверный ход — GameError
    auto(state, user_id)         — ход по таймауту
    result(state)                — None или {"winners": [...], "text": "...", "shares": {...}}

Состояние обязано быть JSON-овым: оно лежит в базе и переживает перезапуск бота.
Сломанная игра не роняет остальные — её просто не будет в списке.
"""

import logging
from importlib import import_module

logger = logging.getLogger(__name__)

MODULES = ("durak", "poker", "ochko", "roaches", "auction", "reaction", "crocodile",
           "checkers", "seabattle", "chess", "backgammon")

NEEDED = ("TITLE", "EMOJI", "ABOUT", "MIN_SEATS", "MAX_SEATS", "PACE", "TURN_SECONDS",
          "setup", "turn", "view", "move", "auto", "result")

GAMES: dict = {}


def _load():
    for name in MODULES:
        try:
            module = import_module(f"{__name__}.{name}")
        except ModuleNotFoundError:
            continue                      # игра ещё не написана — не беда
        except Exception:
            logger.exception("Игра %s не загрузилась", name)
            continue
        missing = [attr for attr in NEEDED if not hasattr(module, attr)]
        if missing:
            logger.error("Игра %s не умеет: %s", name, ", ".join(missing))
            continue
        GAMES[name] = module


_load()
