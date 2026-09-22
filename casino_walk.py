"""«Выйти погулять»: случайный текстовый квест с развилками.

Сами истории лежат в пакете quests, здесь — только ходы: где игрок сейчас, что ему показать
и сколько он принесёт домой в конце. Прогресс хранится в базе, поэтому квест переживает и выход
из приложения, и перезапуск бота: вернулся — продолжил с того же места.

Деньги начисляются один раз, в концовке. Сверху за концовку, которую игрок видит впервые,
ачивки доплачивают разовую награду — так собирается коллекция.
"""

import logging
import math
import random
import time

import achievements
import casino_engine as engine
import quests
from casino_engine import GameError, signed
from loader import db

logger = logging.getLogger(__name__)

COOLDOWN_MIN = 20 * 60        # после концовки гулять можно не сразу…
COOLDOWN_MAX = 30 * 60        # …а через случайное время в этих пределах
WALK_TTL_SECONDS = 7 * 24 * 60 * 60  # брошенная прогулка ждёт неделю, потом забывается
FEED_FROM = 300               # о крупной находке узнаёт вся беседа
MAX_STEPS = 200               # защита от зацикленной истории

_last: dict[tuple[int, int], str] = {}  # чтобы два раза подряд не выпал один и тот же квест


def _now() -> int:
    return int(time.time())


def _cooldown(chat_id: int, user_id: int) -> int:
    return max(0, db.get_counter(chat_id, user_id, "walk:next") - _now())


def _view(chat_id: int, user_id: int, quest_key: str, node_id: str, steps: int) -> dict:
    quest = quests.QUESTS[quest_key]
    node = quest["nodes"][node_id]
    found = achievements.found_endings(chat_id, user_id, quest_key)
    view = {
        "quest": quest_key,
        "title": quest["title"],
        "emoji": quest["emoji"],
        "node": node_id,
        "scene": node["scene"],
        "text": node["text"],
        "steps": steps,
        "end": "pay" in node,
        "found": len(found),
        "total": len(quests.ENDINGS[quest_key]),
    }
    if "pay" in node:
        view["pay"] = node["pay"]
        view["name"] = node["name"]
    else:
        view["choices"] = [{"i": index, "text": choice["text"]} for index, choice in enumerate(node["choices"])]
    return view


def _current(chat_id: int, user_id: int) -> dict | None:
    walk = db.get_walk(chat_id, user_id)
    if walk is None:
        return None
    quest = quests.QUESTS.get(walk["quest"])
    if quest is None or walk["node"] not in quest["nodes"]:  # историю переписали, пока игрок гулял
        db.clear_walk(chat_id, user_id)
        return None
    if _now() - walk["started_at"] > WALK_TTL_SECONDS:
        db.clear_walk(chat_id, user_id)
        return None
    return walk


def _pick(chat_id: int, user_id: int) -> str:
    """Следующая история: сначала те, где остались ненайденные концовки."""
    seen = db.get_achievements(chat_id, user_id)
    fresh, rest = [], []
    for key, endings in quests.ENDINGS.items():
        if key == _last.get((chat_id, user_id)):
            continue
        found = sum(1 for node_id in endings if f"end:{key}:{node_id}" in seen)
        (rest if found >= len(endings) else fresh).append(key)
    return random.choice(fresh or rest or list(quests.QUESTS))


def state(chat_id: int, user_id: int) -> dict:
    walk = _current(chat_id, user_id)
    return {
        "cooldown": _cooldown(chat_id, user_id),
        "walk": _view(chat_id, user_id, walk["quest"], walk["node"], walk["steps"]) if walk else None,
        "balance": db.get_balance(chat_id, user_id),
        "stories": len(quests.QUESTS),
        "endings": {"found": len(achievements.found_endings_total(chat_id, user_id)),
                    "total": quests.total_endings()},
    }


def start(chat_id: int, user_id: int) -> dict:
    walk = _current(chat_id, user_id)
    if walk is not None:  # не бросать же человека на полпути
        return state(chat_id, user_id)
    wait = _cooldown(chat_id, user_id)
    if wait > 0:
        raise GameError(f"Ты уже нагулялся. Следующая прогулка через {_wait_text(wait)}", 429)
    engine.throttle(chat_id, user_id)
    key = _pick(chat_id, user_id)
    _last[(chat_id, user_id)] = key
    db.save_walk(chat_id, user_id, key, quests.QUESTS[key]["start"], 0, _now())
    return state(chat_id, user_id)


def go(chat_id: int, user_id: int, choice) -> dict:
    walk = _current(chat_id, user_id)
    if walk is None:
        raise GameError("Прогулка закончилась — выйди погулять заново", 409)
    quest = quests.QUESTS[walk["quest"]]
    node = quest["nodes"][walk["node"]]
    choices = node.get("choices")
    if not choices:
        raise GameError("Тут уже всё", 409)
    if isinstance(choice, bool) or not isinstance(choice, int) or not 0 <= choice < len(choices):
        raise GameError("Непонятный выбор")

    next_id = choices[choice]["to"]
    next_node = quest["nodes"][next_id]
    steps = walk["steps"] + 1
    if "pay" not in next_node:
        if steps > MAX_STEPS:  # история должна когда-нибудь кончаться
            db.clear_walk(chat_id, user_id)
            raise GameError("Ты заблудился и вышел к дому. Прогулка окончена", 409)
        db.save_walk(chat_id, user_id, walk["quest"], next_id, steps, walk["started_at"])
        return state(chat_id, user_id)

    pay = next_node["pay"]
    view = _view(chat_id, user_id, walk["quest"], next_id, steps)
    db.clear_walk(chat_id, user_id)
    db.set_counter(chat_id, user_id, "walk:next", _now() + random.randint(COOLDOWN_MIN, COOLDOWN_MAX))
    db.bump_counter(chat_id, user_id, "walk:done")
    balance = db.casino_adjust(chat_id, user_id, "walk", pay, special=pay >= FEED_FROM)
    found = achievements.record_ending(chat_id, user_id, walk["quest"], next_id)
    if found:
        balance = db.get_balance(chat_id, user_id)  # награду за новую концовку уже начислили
        view["found"] += 1
    name = engine.player_name(user_id)
    if found and view["found"] == view["total"]:
        engine.add_feed(chat_id, f"📖 {name} открывает все концовки истории «{quest['title']}»")
    elif abs(pay) >= FEED_FROM:
        engine.add_feed(chat_id, f"🚶 {name} вышел погулять «{quest['title']}»: {signed(pay)}")
    return state(chat_id, user_id) | {"walk": view, "balance": balance, "finished": True,
                                      "ending": found or None}


def _wait_text(seconds: float) -> str:
    minutes = math.ceil(seconds / 60)
    if minutes >= 60:
        return f"{minutes // 60} ч {minutes % 60} мин"
    return f"{minutes} мин"
