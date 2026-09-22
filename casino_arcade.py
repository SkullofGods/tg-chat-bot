"""«Поиграть»: бесконечные игры без ставок. Денег за саму игру не дают — платят за рекорд.

Раз в день держатель рекорда в каждой игре получает премию и держит её, пока рекорд не побьют
(об этом заботится scheduler.arcade_prize_loop). Очки присылает приложение: на кону только премия,
поэтому сервер лишь отсекает совсем неправдоподобные числа.
"""

import logging

import casino_engine as engine
import members
from casino_engine import GameError, money
from loader import db
from textstats import count_with_word
from utils import local_today

logger = logging.getLogger(__name__)

DAILY_PRIZE = 250   # столько получает каждый рекордсмен раз в день
TOP_SHOWN = 5

GAMES = {
    "yard": {
        "title": "Бесконечный двор", "emoji": "🧹", "units": ("очко", "очка", "очков"),
        "about": "Мусор сыплется всё быстрее. Пять пропущенных — конец смены.",
        "max": 600,
    },
    "post": {
        "title": "Ночная почта", "emoji": "✉️", "units": ("письмо", "письма", "писем"),
        "about": "Письма идут одно за другим, времени всё меньше. Три ошибки — и домой.",
        "max": 400,
    },
    "belt": {
        "title": "Конвейер самсы", "emoji": "🥟", "units": ("очко", "очка", "очков"),
        "about": "Штампуй самсу, пока она не уехала с ленты. Лента разгоняется.",
        "max": 600,
    },
    "stack": {
        "title": "Башня из ящиков", "emoji": "🧱", "units": ("ящик", "ящика", "ящиков"),
        "about": "Ставь ящик на ящик. Промахнулся — башня становится уже.",
        "max": 300,
    },
    "memory": {
        "title": "Память таджика", "emoji": "🧠", "units": ("шаг", "шага", "шагов"),
        "about": "Повторяй всё более длинную последовательность огней.",
        "max": 60,
    },
}


def _game_view(chat_id: int, user_id: int, key: str, game: dict, names: dict[int, dict]) -> dict:
    top = db.arcade_top(chat_id, key, TOP_SHOWN)
    mine = next((row["score"] for row in top if row["user_id"] == user_id), None)
    if mine is None:
        mine = db.arcade_best(chat_id, key, user_id)
    holder = top[0] if top else None
    return {
        "key": key,
        "title": game["title"],
        "emoji": game["emoji"],
        "about": game["about"],
        "units": list(game["units"]),
        "my_best": mine,
        "record": None if holder is None else {
            "name": members.plain_name(holder["user_id"], names.get(holder["user_id"], {})),
            "score": holder["score"],
            "mine": holder["user_id"] == user_id,
        },
        "top": [
            {
                "name": members.plain_name(row["user_id"], names.get(row["user_id"], {})),
                "score": row["score"],
                "mine": row["user_id"] == user_id,
            }
            for row in top
        ],
    }


def state(chat_id: int, user_id: int) -> dict:
    rows = db.arcade_all(chat_id)
    names = db.get_name_rows(row["user_id"] for row in rows)
    return {
        "prize": DAILY_PRIZE,
        "paid_today": db.get_meta(f"arcade_prize:{chat_id}:{local_today()}") is not None,
        "games": [_game_view(chat_id, user_id, key, game, names) for key, game in GAMES.items()],
        "balance": db.get_balance(chat_id, user_id),
    }


def submit(chat_id: int, user_id: int, key, score) -> dict:
    game = GAMES.get(key) if isinstance(key, str) else None
    if game is None:
        raise GameError("Такой игры нет", 404)
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= game["max"]:
        raise GameError("Странный счёт")
    engine.throttle(chat_id, user_id)
    db.bump_counter(chat_id, user_id, "arcade:plays")
    was = db.arcade_record(chat_id, key)
    best = db.save_arcade_score(chat_id, key, user_id, score)
    beaten = score > 0 and (was is None or score > was["score"]) and (was is None or was["user_id"] != user_id)
    if beaten:
        name = engine.player_name(user_id)
        engine.add_feed(chat_id, f"{game['emoji']} {name} ставит рекорд в «{game['title']}»: "
                                 f"{count_with_word(score, game['units'])}")
    return state(chat_id, user_id) | {"beaten": beaten, "my_best": best, "score": score}


def pay_records(chat_id: int) -> list[str]:
    """Премия рекордсменам за день. Возвращает строчки для объявления в беседе."""
    day = local_today()
    meta_key = f"arcade_prize:{chat_id}:{day}"
    if db.get_meta(meta_key) is not None:
        return []
    db.set_meta(meta_key, "paid")
    lines = []
    for key, game in GAMES.items():
        holder = db.arcade_record(chat_id, key)
        if holder is None:
            continue
        db.add_money(chat_id, holder["user_id"], DAILY_PRIZE)
        db.bump_counter(chat_id, holder["user_id"], "arcade:prize")
        name = members.display_name(holder["user_id"])
        lines.append(f"{game['emoji']} <b>{game['title']}</b> — {name}: "
                     f"{count_with_word(holder['score'], game['units'])}")
    if lines:
        engine.add_feed(chat_id, f"🏆 Рекордсмены дня получили по {money(DAILY_PRIZE)}")
    return lines
