"""Толян: тамагочи, в которого вкладывают таджикоины, а он приносит доход.

Доход капает сам по себе и копится, пока его не заберут, но не больше CAP_HOURS часов — так что
заходить приходится. Голодный работает вполсилы. Вложения поднимают уровень: от картонной коробки
до сети шаурмичных. Поначалу приносит гроши, изредка — находку до 500, а вот с вложениями доход растёт всерьёз.
"""

import random
import time

import casino_engine as engine
import texts
from casino_engine import GameError, money
from loader import db

# Уровень: (название, эмодзи, цена подъёма на него, доход в час)
LEVELS = [
    ("У подъезда", "🧔", 0, 12),
    ("Картонная коробка", "📦", 400, 22),
    ("Тележка из супермаркета", "🛒", 700, 40),
    ("Кроссовки", "👟", 1_200, 72),
    ("Зонт от дождя", "☔", 2_100, 130),
    ("Собака Шарик", "🐕", 3_600, 230),
    ("Палатка", "🏕", 6_200, 410),
    ("Пункт приёма стекла", "♻️", 10_500, 720),
    ("Мангал", "🔥", 18_000, 1_300),
    ("Шаурмичная", "🌯", 31_000, 2_300),
    ("Сеть шаурмичных", "🏪", 53_000, 4_200),
]
CAP_HOURS = 6              # больше шести часов выручка не копится
FEED_COST = 100
FEED_HOURS = 8
HUNGRY_RATE = 0.4          # голодный приносит меньше
FIND_CHANCE = 0.12         # шанс находки при получении выручки
TROUBLE_CHANCE = 0.08      # …и шанс, что часть выручки отберут
TROUBLE_SHARE = 0.6
MIN_HOURS_FOR_LUCK = 1 / 6  # за десять минут ничего интересного не случается
FEED_FROM = 400            # с какой находки о ней узнаёт беседа


def _now() -> int:
    return int(time.time())


def _bum(chat_id: int, user_id: int, now: int) -> dict:
    """Толян игрока. Новый достаётся сытым — чтобы сразу было видно, как он работает."""
    return db.get_bum(chat_id, user_id, texts.BUM_NAME, now, now + FEED_HOURS * 3600)


def _pending(bum: dict, now: int) -> tuple[int, float]:
    """Сколько накопилось и за сколько часов (сытое время считается полностью, голодное — вполсилы)."""
    rate = LEVELS[bum["level"]][3]
    until = min(now, bum["collected_at"] + CAP_HOURS * 3600)
    if until <= bum["collected_at"]:
        return 0, 0.0
    hours = (until - bum["collected_at"]) / 3600
    fed_hours = max(0, min(until, bum["fed_until"]) - bum["collected_at"]) / 3600
    return int(rate * (fed_hours + (hours - fed_hours) * HUNGRY_RATE)), hours


def _level_view(index: int) -> dict:
    title, emoji, cost, income = LEVELS[index]
    return {"level": index, "title": title, "emoji": emoji, "cost": cost, "income": income}


def _state(bum: dict, story: str | None = None) -> dict:
    now = _now()
    pending, hours = _pending(bum, now)
    view = _level_view(bum["level"])
    return {
        "name": texts.BUM_NAME,
        "now": now,
        "level": view,
        "levels": [_level_view(index) for index in range(len(LEVELS))],
        "next": _level_view(bum["level"] + 1) if bum["level"] + 1 < len(LEVELS) else None,
        "top": bum["level"] + 1 == len(LEVELS),
        "pending": pending,
        "hours": round(hours, 3),
        "cap_hours": CAP_HOURS,
        "hungry_rate": HUNGRY_RATE,
        "collected_at": bum["collected_at"],
        "fed_until": bum["fed_until"],
        "hungry": bum["fed_until"] <= now,
        "mood": texts.BUM_HUNGRY if bum["fed_until"] <= now else texts.BUM_FED,
        "feed_cost": FEED_COST,
        "feed_hours": FEED_HOURS,
        "invested": bum["invested"],
        "earned": bum["earned"],
        "balance": db.get_balance(bum["chat_id"], bum["user_id"]),
        "story": story,
    }


def bum_state(chat_id: int, user_id: int) -> dict:
    now = _now()
    return _state(_bum(chat_id, user_id, now))


def collect(chat_id: int, user_id: int) -> dict:
    engine.throttle(chat_id, user_id)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    pending, hours = _pending(bum, now)
    story = None
    if hours >= MIN_HOURS_FOR_LUCK:
        roll = random.random()
        if roll < FIND_CHANCE:
            # находки редкие и обычно скромные, но иногда попадается кошелёк потолще
            bonus = random.choice([random.randint(50, 150)] * 6      # обычно мелочь…
                                  + [random.randint(150, 300)] * 3
                                  + [random.randint(300, 500)])           # …и совсем редко настоящий кошелёк
            pending += bonus
            luck = f"{random.choice(texts.BUM_FINDS)}: +{money(bonus)}"
            story = f"🎁 {texts.BUM_NAME} {luck}"
            if bonus >= FEED_FROM:
                engine.add_feed(chat_id, f"🧔 {texts.BUM_NAME} у {engine.player_name(user_id)} {luck}")
        elif roll < FIND_CHANCE + TROUBLE_CHANCE and pending > 0:
            lost = pending - int(pending * TROUBLE_SHARE)
            pending -= lost
            story = f"😔 {random.choice(texts.BUM_TROUBLES)}: −{money(lost)}"
    if pending <= 0:  # время не обнуляем: пусть копится дальше
        return _state(bum, story or "Пока пусто — зайди попозже")
    db.collect_bum(chat_id, user_id, pending, now)
    return _state(_bum(chat_id, user_id, now), story or f"💰 Выручка в кармане: +{money(pending)}")


def feed(chat_id: int, user_id: int) -> dict:
    engine.throttle(chat_id, user_id)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    if not db.feed_bum(chat_id, user_id, FEED_COST, max(now, bum["fed_until"]) + FEED_HOURS * 3600):
        raise engine.not_enough_money(chat_id, user_id)
    return _state(_bum(chat_id, user_id, now), f"🍲 Поел плова. Сыт ещё {FEED_HOURS} часов")


def upgrade(chat_id: int, user_id: int) -> dict:
    engine.throttle(chat_id, user_id)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    if bum["level"] + 1 >= len(LEVELS):
        raise GameError("Дальше некуда: это уже бизнес-империя", 409)
    title, emoji, cost, income = LEVELS[bum["level"] + 1]
    pending, _ = _pending(bum, now)  # то, что накопилось, при вложении не сгорает
    if not db.upgrade_bum(chat_id, user_id, cost, pending, now):
        raise engine.not_enough_money(chat_id, user_id)
    if cost >= 10_000:
        engine.add_feed(chat_id, f"{emoji} {texts.BUM_NAME} у {engine.player_name(user_id)} дорос до «{title}»")
    return _state(_bum(chat_id, user_id, now), f"{emoji} Теперь это {title.lower()}: {income} 🪙/час")
