"""Толян Эйр — краш на всю беседу.

Рейс: окно ставок (BET_SECONDS), взлёт, полёт — множитель растёт как e^(GROWTH·t), — и в какой-то момент
самолёт падает. Кто успел выпрыгнуть, забирает ставку × множитель; кто не успел — теряет ставку.
Можно заранее поставить автовыход: сервер сам выведет на нужном множителе.

Где самолёт упадёт, решается при создании рейса и до падения никому не показывается. Шанс долететь
до x — (1 − EDGE) / x, так что при любой тактике казино в среднем берёт свои EDGE.
Рейсы живут в памяти: пока никто не смотрит и никто не поставил, самолёт не летает. Ставки лежат
в базе; если бот перезапустился посреди рейса, недолетевшие ставки возвращаются владельцам.
"""

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import casino_engine as engine
import members
import texts
from casino_engine import GameError, signed
from loader import db

logger = logging.getLogger(__name__)

BET_SECONDS = 10        # окно ставок перед взлётом
PAUSE_SECONDS = 4       # после падения — пауза, чтобы все увидели, где он упал
GROWTH = 0.12           # скорость роста: ×2 за ~5,8 с, ×10 за ~19 с
EDGE = 0.03
MAX_CRASH = 1000.0
AUTO_MIN, AUTO_MAX = 1.01, 1000.0
FEED_FROM = 5000        # с какого выигрыша о нём узнаёт лента казино…
FEED_MULTIPLIER = 10    # …или с какого множителя


@dataclass
class Flight:
    no: int
    takeoff: float
    crash: float          # на каком множителе упадёт — секрет до падения
    crash_at: float
    reason: str
    settled: bool = False


_flights: dict[int, Flight] = {}
_recovered: set[int] = set()


def _now() -> float:
    return time.time()


def crash_point() -> float:
    roll = random.random()
    return min(MAX_CRASH, max(1.0, math.floor((1 - EDGE) / (1 - roll) * 100) / 100))


def multiplier_at(flight: Flight, moment: float) -> float:
    if moment <= flight.takeoff:
        return 1.0
    return min(flight.crash, math.floor(math.exp(GROWTH * (moment - flight.takeoff)) * 100) / 100)


def _new_flight(chat_id: int, no: int, opens: float) -> Flight:
    crash = crash_point()
    takeoff = opens + BET_SECONDS
    flight = Flight(no, takeoff, crash, takeoff + math.log(crash) / GROWTH, random.choice(texts.CRASH_REASONS))
    _flights[chat_id] = flight
    return flight


def _recover(chat_id: int):
    """После перезапуска бота: ставки рейсов, которые не долетели, возвращаем владельцам."""
    if chat_id in _recovered:
        return
    _recovered.add(chat_id)
    refunded = db.refund_open_crash_bets(chat_id)
    if refunded:
        logger.info("Толян Эйр в %s: после перезапуска вернули %s ставок", chat_id, refunded)


def current(chat_id: int, now: float | None = None) -> Flight:
    """Текущий рейс беседы. Прошлый упал и пауза вышла — открывается следующий."""
    now = _now() if now is None else now
    _recover(chat_id)
    flight = _flights.get(chat_id)
    if flight is None:
        return _new_flight(chat_id, db.last_crash_round(chat_id) + 1, now)
    if now >= flight.crash_at and not flight.settled:
        _settle(chat_id, flight)
    if flight.settled and now >= flight.crash_at + PAUSE_SECONDS:
        return _new_flight(chat_id, flight.no + 1, max(now, flight.crash_at + PAUSE_SECONDS))
    return flight


def _announce(chat_id: int, bets: list[dict]):
    for bet in bets:
        net = bet["payout"] - bet["amount"]
        if bet["payout"] and (net >= FEED_FROM or bet["cashed"] >= FEED_MULTIPLIER * 100):
            engine.add_feed(chat_id, f"🛩 {engine.player_name(bet['user_id'])} выпрыгивает из Толян Эйр "
                                     f"на ×{bet['cashed'] / 100:g}: {signed(net)}")


def _settle(chat_id: int, flight: Flight):
    flight.settled = True
    _announce(chat_id, db.settle_crash_round(chat_id, flight.no, round(flight.crash * 100)))
    db.add_history(chat_id, "crash", {"round": flight.no, "crash": flight.crash})


def _auto_cash_out(chat_id: int, flight: Flight, now: float):
    _announce(chat_id, db.cash_crash_autos(chat_id, flight.no, round(multiplier_at(flight, now) * 100)))


async def loop():
    for chat_id in db.get_known_chats():
        _recover(chat_id)
    last_prune = 0.0
    while True:
        try:
            now = _now()
            for chat_id, flight in list(_flights.items()):
                if flight.settled:
                    if now >= flight.crash_at + PAUSE_SECONDS and db.has_crash_bets(chat_id, flight.no + 1):
                        current(chat_id, now)          # на следующий рейс уже поставили — летим и без зрителей
                elif now >= flight.crash_at:
                    _settle(chat_id, flight)
                elif now >= flight.takeoff:
                    _auto_cash_out(chat_id, flight, now)
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                db.prune_crash_bets((datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat())
        except Exception:
            logger.exception("Толян Эйр: рейс не долетел до расчёта")
        await asyncio.sleep(0.1)


# ── Ставки и выход ────────────────────────────────────────────────────────────


def _auto_value(auto) -> int | None:
    if auto is None or auto == "":
        return None
    if isinstance(auto, bool) or not isinstance(auto, (int, float)) or not AUTO_MIN <= auto <= AUTO_MAX:
        raise GameError(f"Автовыход — от ×{AUTO_MIN:g} до ×{AUTO_MAX:g}")
    return round(auto * 100)


def place_bet(chat_id: int, user_id: int, amount, auto=None) -> dict:
    engine.check_bet(amount)
    auto_x100 = _auto_value(auto)
    now = _now()
    flight = current(chat_id, now)
    target = flight.no if now < flight.takeoff else flight.no + 1   # в полёте — на следующий рейс
    engine.throttle(chat_id, user_id)
    added = db.add_crash_bet(chat_id, target, user_id, amount, auto_x100)
    if added is None:
        raise engine.not_enough_money(chat_id, user_id)
    if not added:
        raise GameError("Ты уже на этом рейсе", 409)
    return state(chat_id, user_id)


def cash_out(chat_id: int, user_id: int) -> dict:
    now = _now()
    flight = current(chat_id, now)
    if now < flight.takeoff:
        raise GameError("Самолёт ещё на земле", 409)
    if now >= flight.crash_at:
        raise GameError("Поздно — Толян Эйр уже упал 💥", 409)
    value = multiplier_at(flight, now)
    bet = db.cash_out_crash_bet(chat_id, flight.no, user_id, round(value * 100))
    if bet is None:
        raise GameError("Тебя нет на этом рейсе — или ты уже выпрыгнул(а)", 409)
    _announce(chat_id, [bet])
    return state(chat_id, user_id) | {"cashed": {"multiplier": value, "payout": bet["payout"]}}


# ── Что видит приложение ──────────────────────────────────────────────────────


def _bets_view(bets: list[dict], user_id: int, names: dict[int, dict]) -> list[dict]:
    return [
        {
            "name": members.plain_name(bet["user_id"], names.get(bet["user_id"], {})),
            "me": bet["user_id"] == user_id,
            "amount": bet["amount"],
            "auto": bet["auto"] / 100 if bet["auto"] else None,
            "cashed": bet["cashed"] / 100 if bet["cashed"] else None,
            "payout": bet["payout"],
            "status": bet["status"],
        }
        for bet in bets
    ]


def state(chat_id: int, user_id: int) -> dict:
    now = _now()
    flight = current(chat_id, now)
    crashed = now >= flight.crash_at
    phase = "betting" if now < flight.takeoff else ("crashed" if crashed else "flying")
    bets = db.crash_round_bets(chat_id, flight.no)
    upcoming = db.crash_round_bets(chat_id, flight.no + 1) if phase != "betting" else []
    names = db.get_name_rows({bet["user_id"] for bet in bets + upcoming})
    return {
        "now": now,
        "round": flight.no,
        "phase": phase,
        "takeoff": flight.takeoff,
        "growth": GROWTH,
        "bet_seconds": BET_SECONDS,
        "multiplier": flight.crash if crashed else multiplier_at(flight, now),
        "crash": flight.crash if crashed else None,
        "crash_at": flight.crash_at if crashed else None,
        "next_at": flight.crash_at + PAUSE_SECONDS if crashed else None,
        "reason": flight.reason if crashed else None,
        "bets": _bets_view(bets, user_id, names),
        "next_bets": _bets_view(upcoming, user_id, names),
        "history": [{"round": item.get("round"), "crash": item["crash"]}
                    for item in db.get_history(chat_id, "crash", engine.HISTORY_SHOWN)],
        "auto_limits": [AUTO_MIN, AUTO_MAX],
        "balance": db.get_balance(chat_id, user_id),
    }
