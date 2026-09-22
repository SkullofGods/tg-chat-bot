"""«Выйти погулять»: случайный текстовый квест с развилками.

Сам квест хранится в quests.py, здесь — только ходы: где игрок сейчас, что ему показать и сколько
он принесёт домой в конце. Прогулка живёт в памяти, деньги начисляются один раз — в концовке.
"""

import math
import random
import time

import casino_engine as engine
import limits
import quests
from casino_engine import GameError, signed
from loader import db

COOLDOWN_SECONDS = 15 * 60   # столько отдыхаем между прогулками
WALK_TTL_SECONDS = 60 * 60   # брошенная на полпути прогулка забывается
FEED_FROM = 300              # о крупной находке узнаёт вся беседа

_walks: dict[tuple[int, int], dict] = {}
_last: dict[tuple[int, int], str] = {}  # чтобы два раза подряд не выпал один и тот же квест


def _view(quest_key: str, node_id: str) -> dict:
    quest = quests.QUESTS[quest_key]
    node = quest["nodes"][node_id]
    view = {
        "quest": quest_key,
        "title": quest["title"],
        "node": node_id,
        "scene": node["scene"],
        "text": node["text"],
        "end": "pay" in node,
    }
    if "pay" in node:
        view["pay"] = node["pay"]
    else:
        view["choices"] = [{"i": index, "text": choice["text"]} for index, choice in enumerate(node["choices"])]
    return view


def _current(chat_id: int, user_id: int) -> dict | None:
    walk = _walks.get((chat_id, user_id))
    if walk is None:
        return None
    if time.monotonic() - walk["started"] > WALK_TTL_SECONDS:
        _walks.pop((chat_id, user_id), None)
        return None
    return walk


def state(chat_id: int, user_id: int) -> dict:
    walk = _current(chat_id, user_id)
    return {
        "cooldown": math.ceil(limits.remaining(chat_id, user_id, "walk", COOLDOWN_SECONDS)),
        "walk": _view(walk["quest"], walk["node"]) if walk else None,
        "balance": db.get_balance(chat_id, user_id),
    }


def start(chat_id: int, user_id: int) -> dict:
    walk = _current(chat_id, user_id)
    if walk is not None:  # не бросать же человека на полпути
        return state(chat_id, user_id)
    wait = limits.remaining(chat_id, user_id, "walk", COOLDOWN_SECONDS)
    if wait > 0:
        raise GameError(f"Ты уже нагулялся. Следующая прогулка через {limits.format_wait(wait)}", 429)
    engine.throttle(chat_id, user_id)
    keys = [key for key in quests.QUESTS if key != _last.get((chat_id, user_id))] or list(quests.QUESTS)
    key = random.choice(keys)
    _last[(chat_id, user_id)] = key
    _walks[(chat_id, user_id)] = {"quest": key, "node": quests.QUESTS[key]["start"], "started": time.monotonic()}
    return state(chat_id, user_id)


def go(chat_id: int, user_id: int, choice) -> dict:
    walk = _current(chat_id, user_id)
    if walk is None:
        raise GameError("Прогулка закончилась — выйди погулять заново", 409)
    node = quests.QUESTS[walk["quest"]]["nodes"][walk["node"]]
    choices = node.get("choices")
    if not choices:
        raise GameError("Тут уже всё", 409)
    if isinstance(choice, bool) or not isinstance(choice, int) or not 0 <= choice < len(choices):
        raise GameError("Непонятный выбор")
    walk["node"] = choices[choice]["to"]
    next_node = quests.QUESTS[walk["quest"]]["nodes"][walk["node"]]
    if "pay" not in next_node:
        return state(chat_id, user_id)

    pay = next_node["pay"]
    limits.mark(chat_id, user_id, "walk")
    balance = db.casino_adjust(chat_id, user_id, "walk", pay, special=pay >= FEED_FROM)
    view = _view(walk["quest"], walk["node"])
    _walks.pop((chat_id, user_id), None)
    if abs(pay) >= FEED_FROM:
        name = engine.player_name(user_id)
        engine.add_feed(chat_id, f"🚶 {name} вышел погулять «{quests.QUESTS[walk['quest']]['title']}»: {signed(pay)}")
    return state(chat_id, user_id) | {"walk": view, "balance": balance, "finished": True}
