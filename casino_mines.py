"""Мины: поле 5×5, под частью клеток — мины. Каждая пустая клетка поднимает множитель, забрать выигрыш
можно в любой момент, мина сжигает ставку.

Где мины, знает только сервер: поле лежит в базе (переживает перезапуск бота) и открывается игроку
целиком, только когда партия кончилась. Множитель честный, с долей казино EDGE: после k пустых клеток
он равен (1 − EDGE) / (шанс открыть k пустых подряд) — при любой тактике казино в среднем берёт EDGE.
"""

import math
import random

import casino_engine as engine
from casino_engine import GameError, signed
from loader import db

CELLS = 25
MINE_CHOICES = (3, 5, 10)
EDGE = 0.03
MAX_MULTIPLIER = 1000.0
FEED_FROM = 3000          # с какого выигрыша о нём узнаёт лента казино


def multiplier(mines: int, opened: int) -> float:
    """Множитель после opened пустых клеток — с долей казино и не выше MAX_MULTIPLIER."""
    if opened <= 0:
        return 1.0
    chance = 1.0
    for index in range(opened):
        chance *= (CELLS - mines - index) / (CELLS - index)
    return min(MAX_MULTIPLIER, math.floor((1 - EDGE) / chance * 100) / 100)


def _payout(game: dict) -> int:
    # множитель с двумя знаками: считаем в целых, чтобы 100 × 1.1 было 110, а не 110.00000000000001
    return game["bet"] * round(multiplier(game["mines"], len(game["opened"])) * 100) // 100


def _game_view(game: dict) -> dict:
    opened = len(game["opened"])
    safe = CELLS - game["mines"]
    return {
        "bet": game["bet"],
        "mines": game["mines"],
        "opened": game["opened"],
        "multiplier": multiplier(game["mines"], opened),
        "next": multiplier(game["mines"], opened + 1) if opened < safe else None,
        "cashout": _payout(game) if opened else None,
        "left": safe - opened,
    }


def state(chat_id: int, user_id: int, result: dict | None = None) -> dict:
    game = db.get_mines(chat_id, user_id)
    return {
        "game": _game_view(game) if game else None,
        "result": result,
        "cells": CELLS,
        "choices": list(MINE_CHOICES),
        "min_bet": engine.MIN_BET,
        "balance": db.get_balance(chat_id, user_id),
    }


def start(chat_id: int, user_id: int, bet, mines) -> dict:
    if isinstance(mines, bool) or not isinstance(mines, int) or mines not in MINE_CHOICES:
        raise GameError("Выбери, сколько мин: " + ", ".join(map(str, MINE_CHOICES)))
    engine.check_bet(bet)
    engine.throttle(chat_id, user_id)
    started = db.start_mines(chat_id, user_id, bet, mines, random.sample(range(CELLS), mines))
    if started is None:
        raise GameError("Сначала доиграй поле, которое уже открыто", 409)
    if not started:
        raise engine.not_enough_money(chat_id, user_id)
    return state(chat_id, user_id)


def _finish(chat_id: int, user_id: int, game: dict, payout: int, how: str) -> dict:
    cleared = len(game["opened"]) == CELLS - game["mines"]
    balance = db.finish_mines(chat_id, user_id, game["bet"], payout, special=cleared)
    if balance is None:
        raise GameError("Поле уже закрыто", 409)
    net = payout - game["bet"]
    if payout and (net >= FEED_FROM or cleared):
        name = engine.player_name(user_id)
        engine.add_feed(chat_id, f"💣 {name} {'проходит всё минное поле' if cleared else 'уходит с минного поля'} "
                                 f"с ×{multiplier(game['mines'], len(game['opened'])):g}: {signed(net)}")
    return state(chat_id, user_id, {
        "how": how, "payout": payout, "net": net, "layout": game["layout"], "opened": game["opened"],
        "multiplier": multiplier(game["mines"], len(game["opened"])) if payout else 0,
    })


def open_cell(chat_id: int, user_id: int, cell) -> dict:
    game = db.get_mines(chat_id, user_id)
    if game is None:
        raise GameError("Поле не начато — поставь и жми «Начать»", 409)
    if isinstance(cell, bool) or not isinstance(cell, int) or not 0 <= cell < CELLS:
        raise GameError("Такой клетки нет")
    if cell in game["opened"]:
        return state(chat_id, user_id)
    if cell in game["layout"]:
        view = _finish(chat_id, user_id, game, 0, "boom")
        view["result"]["boom"] = cell
        return view
    game["opened"].append(cell)
    if len(game["opened"]) == CELLS - game["mines"]:        # открыл всё пустое — забираем сами
        return _finish(chat_id, user_id, game, _payout(game), "cleared")
    db.save_mines_opened(chat_id, user_id, game["opened"])
    return state(chat_id, user_id)


def cash_out(chat_id: int, user_id: int) -> dict:
    game = db.get_mines(chat_id, user_id)
    if game is None:
        raise GameError("Поле не начато", 409)
    if not game["opened"]:
        raise GameError("Открой хотя бы одну клетку", 409)
    engine.throttle(chat_id, user_id)
    return _finish(chat_id, user_id, game, _payout(game), "cashout")
