"""Банк казино: вклады под процент на срок.

Деньги уходят из кошелька сразу, а когда срок выйдет, вклад вместе с процентами сам вернётся в кошелёк
(фоновая задача loop). Забрать раньше можно, но тогда без процентов. Вклады считаются в капитал для Форбса.
"""

import asyncio
import logging
import time

import casino_engine as engine
from casino_engine import GameError, money, signed
from loader import db

logger = logging.getLogger(__name__)

TERMS = {  # срок: (секунд, процент, подпись)
    "1h": (3600, 1, "1 час"),
    "2h": (2 * 3600, 2, "2 часа"),
    "3h": (3 * 3600, 3, "3 часа"),
    "12h": (12 * 3600, 10, "12 часов"),
    "24h": (24 * 3600, 20, "сутки"),
    "7d": (7 * 24 * 3600, 100, "неделя"),
}
MIN_DEPOSIT = 100
MAX_ACTIVE = 5
FEED_FROM = 5000              # о вкладах от этой суммы пишем в ленту казино
KEEP_CLOSED_SECONDS = 30 * 24 * 3600


def _now() -> int:
    return int(time.time())


def pay_matured() -> int:
    """Возвращает в кошельки вклады, у которых вышел срок. Сколько закрыли."""
    now = _now()
    paid = 0
    for deposit in db.due_deposits(now):
        if not db.close_deposit(deposit["id"], "paid", deposit["amount"] + deposit["interest"], now):
            continue
        paid += 1
        db.bump_counter(deposit["chat_id"], deposit["user_id"], "bank:interest", deposit["interest"])
        db.bump_counter(deposit["chat_id"], deposit["user_id"], f"bank:term:{deposit['term']}")
        if deposit["amount"] >= FEED_FROM:
            engine.add_feed(deposit["chat_id"], f"🏦 Вклад {engine.player_name(deposit['user_id'])} "
                                                f"принёс {signed(deposit['interest'])}")
    return paid


def _deposit_view(deposit: dict) -> dict:
    seconds, percent, label = TERMS.get(deposit["term"], (0, 0, deposit["term"]))
    return {
        "id": deposit["id"],
        "amount": deposit["amount"],
        "interest": deposit["interest"],
        "percent": percent,
        "label": label,
        "opened_at": deposit["opened_at"],
        "ends_at": deposit["ends_at"],
        "status": deposit["status"],  # active, paid (со всеми процентами), withdrawn (досрочно, без процентов)
        "closed_at": deposit["closed_at"],
    }


def bank_state(chat_id: int, user_id: int) -> dict:
    pay_matured()
    deposits = db.get_deposits(chat_id, user_id)
    return {
        "now": _now(),
        "terms": [{"key": key, "seconds": seconds, "percent": percent, "label": label}
                  for key, (seconds, percent, label) in TERMS.items()],
        "min": MIN_DEPOSIT,
        "max_active": MAX_ACTIVE,
        "deposits": [_deposit_view(deposit) for deposit in deposits],
        "total": sum(deposit["amount"] for deposit in deposits if deposit["status"] == "active"),
        "balance": db.get_balance(chat_id, user_id),
    }


def summary(chat_id: int, user_id: int) -> dict:
    """Коротко для лобби: сколько лежит во вкладах."""
    return {"total": db.bank_total(chat_id, user_id), "count": db.count_active_deposits(chat_id, user_id)}


def deposit(chat_id: int, user_id: int, amount, term) -> dict:
    if term not in TERMS:
        raise GameError("Выбери срок вклада")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise GameError("Сумма должна быть целым числом")
    if amount < MIN_DEPOSIT:
        raise GameError(f"Вклад — от {money(MIN_DEPOSIT)}")
    if db.count_active_deposits(chat_id, user_id) >= MAX_ACTIVE:
        raise GameError(f"Больше {MAX_ACTIVE} вкладов сразу держать нельзя", 409)
    engine.throttle(chat_id, user_id)
    seconds, percent, label = TERMS[term]
    now = _now()
    if db.open_deposit(chat_id, user_id, amount, amount * percent // 100, term, now, now + seconds) is None:
        raise engine.not_enough_money(chat_id, user_id)
    db.bump_counter(chat_id, user_id, "bank:opened")
    if amount >= FEED_FROM:
        engine.add_feed(chat_id, f"🏦 {engine.player_name(user_id)} кладёт {money(amount)} в банк: {label}, +{percent}%")
    return bank_state(chat_id, user_id)


def withdraw(chat_id: int, user_id: int, deposit_id) -> dict:
    """Забрать вклад раньше срока — без процентов. Если срок уже вышел — со всеми процентами."""
    found = db.get_deposit(deposit_id) if isinstance(deposit_id, int) and not isinstance(deposit_id, bool) else None
    if not found or found["chat_id"] != chat_id or found["user_id"] != user_id or found["status"] != "active":
        raise GameError("Такого вклада нет — может, он уже закрыт", 404)
    engine.throttle(chat_id, user_id)
    now = _now()
    matured = now >= found["ends_at"]
    paid = found["amount"] + (found["interest"] if matured else 0)
    if not db.close_deposit(found["id"], "paid" if matured else "withdrawn", paid, now):
        raise GameError("Вклад уже закрыт", 409)
    if matured:
        db.bump_counter(chat_id, user_id, "bank:interest", found["interest"])
        db.bump_counter(chat_id, user_id, f"bank:term:{found['term']}")
    return bank_state(chat_id, user_id) | {"paid": paid, "early": not matured}


async def loop():
    last_prune = 0.0
    while True:
        try:
            pay_matured()
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                db.prune_deposits(_now() - KEEP_CLOSED_SECONDS)
        except Exception:
            logger.exception("Банк казино: не получилось выплатить вклады")
        await asyncio.sleep(20)
