"""Ачивки: за что игрока хвалят и сколько за это разово платят.

Устроено так: у каждой ачивки три ступени (звёзды). Сколько ступеней взято — считаем каждый раз
заново по обычным данным казино (сыгранные партии, вклады, уровень Толяна, рекорды, найденные
концовки квестов), а база помнит только выданный уровень. Поэтому ачивки можно менять и добавлять
задним числом: старые игроки получат своё при первом же заходе, а награду за ступень нельзя взять
дважды — это стережёт award_achievement одной транзакцией.

Концовки квестов — отдельная россыпь: за каждую найденную платят один раз (record_ending),
а на карточке квеста звёзды растут от числа открытых концовок.
"""

import logging
from typing import Optional

import casino_bum as bum
import casino_engine as engine
import casino_work as work
import members
import quests
import texts
from loader import db

logger = logging.getLogger(__name__)

STARS = 3                       # обычно у ачивки три ступени, но бывают и короче — см. goals карточки
QUEST_REWARDS = (60, 180, 500)  # за первую концовку квеста, за половину и за все
ENDING_MIN, ENDING_MAX = 30, 150

SECTIONS = (
    ("casino", "🎰", "Казино"),
    ("bank", "🏦", "Банк"),
    ("work", "🛠", "Халтура"),
    ("bum", "🧔", texts.BUM_NAME),
    ("arcade", "🕹", "Поиграть"),
    ("walk", "🚶", "Прогулки"),
    ("multi", "👥", "Мультиплеер"),
    ("general", "🎖", "Общее"),
    ("quests", "📖", "Концовки историй"),
)


def _stat(snap: dict, game: str, field: str = "plays") -> int:
    return snap["stats"].get(game, {}).get(field, 0)


def _count(snap: dict, key: str) -> int:
    return snap["counters"].get(key, 0)


def _best_win(snap: dict) -> int:
    return max((row["best_win"] for row in snap["stats"].values()), default=0)


def _jobs_done(snap: dict) -> int:
    return sum(1 for job in work.JOBS if _count(snap, f"work:{job}"))


def _quests_opened(snap: dict) -> int:
    return sum(1 for key in quests.QUESTS if snap["found"].get(key))


def _quests_done(snap: dict) -> int:
    return sum(1 for key in quests.QUESTS if snap["found"].get(key, 0) >= len(quests.ENDINGS[key]))


# Ачивка: ключ, раздел, значок, название, пояснение, три порога, три награды и откуда брать число
ACHIEVEMENTS = (
    # ── Казино ────────────────────────────────────────────────────────────────
    ("slots", "casino", "🎰", "Однорукий", "Крутить слоты",
     (20, 100, 500), (150, 400, 1200), lambda s: _stat(s, "slots")),
    ("jackpot", "casino", "7️⃣", "Три семёрки", "Срывать джекпот в слотах",
     (1, 3, 10), (500, 1500, 5000), lambda s: _stat(s, "slots", "special")),
    ("blackjack", "casino", "🃏", "Двадцать одно", "Собрать блэкджек с первых двух карт",
     (1, 5, 15), (200, 600, 2000), lambda s: _stat(s, "blackjack", "special")),
    ("blackjack_win", "casino", "♠️", "Против дилера", "Обыгрывать дилера",
     (5, 30, 120), (150, 500, 1800), lambda s: _count(s, "win:blackjack")),
    ("roulette", "casino", "🔴", "Красное или чёрное", "Ставить на рулетку",
     (10, 50, 200), (150, 400, 1200), lambda s: _stat(s, "roulette")),
    ("roulette_hit", "casino", "🎯", "Прямое попадание", "Угадывать число в рулетке",
     (1, 5, 20), (300, 900, 3000), lambda s: _stat(s, "roulette", "special")),
    ("coin", "casino", "🪙", "Орёл и решка", "Выигрывать на монетке",
     (5, 25, 100), (100, 350, 1000), lambda s: _count(s, "win:coin")),
    ("coin_edge", "casino", "🌗", "На ребре", "Поймать монетку на ребре",
     (1, 2, 3), (500, 1500, 4000), lambda s: _stat(s, "coin", "special")),
    ("race", "casino", "🐎", "Знаток лошадей", "Выигрывать на скачках",
     (3, 15, 50), (200, 600, 1800), lambda s: _count(s, "win:race")),
    ("rr", "casino", "🔫", "Храбрец", "Нажимать на спуск в русской рулетке",
     (5, 25, 100), (200, 600, 2000), lambda s: _stat(s, "rr")),
    ("rr_death", "casino", "💀", "Едва присутствует", "Ловить патрон",
     (1, 5, 15), (100, 300, 900), lambda s: _stat(s, "rr", "special")),
    ("duel", "casino", "⚔️", "Дуэлянт", "Побеждать в дуэлях",
     (1, 10, 30), (150, 500, 1500), lambda s: _count(s, "win:duel")),
    ("big_win", "casino", "💎", "Крупный куш", "Выиграть за раз",
     (1_000, 5_000, 20_000), (300, 1000, 3000), _best_win),
    ("bonus", "casino", "🎁", "Каждый день", "Забирать ежедневный бонус",
     (3, 15, 60), (100, 400, 1500), lambda s: _count(s, "bonus")),

    # ── Банк ──────────────────────────────────────────────────────────────────
    ("bank_open", "bank", "🏦", "Вкладчик", "Открывать вклады",
     (1, 10, 50), (100, 400, 1500), lambda s: _count(s, "bank:opened")),
    ("bank_interest", "bank", "📈", "Проценты капают", "Заработать на процентах",
     (500, 5_000, 50_000), (150, 600, 2500), lambda s: _count(s, "bank:interest")),
    ("bank_week", "bank", "🗓", "Терпеливый", "Досидеть недельный вклад до конца",
     (1, 5, 20), (300, 900, 3000), lambda s: _count(s, "bank:term:week")),

    # ── Халтура ───────────────────────────────────────────────────────────────
    ("work_shift", "work", "🧹", "Трудяга", "Отработать смену",
     (5, 50, 200), (150, 500, 2000), lambda s: _count(s, "work:any")),
    ("work_all", "work", "🧰", "Мастер на все руки", "Попробовать разные халтуры",
     (2, 4, len(work.JOBS)), (150, 400, 1200), _jobs_done),
    ("work_earn", "work", "💪", "Честный заработок", "Заработать руками",
     (1_000, 10_000, 100_000), (150, 600, 2500), lambda s: _stat(s, "work", "returned")),

    # ── Толян ─────────────────────────────────────────────────────────────────
    ("bum_level", "bum", "🧔", f"{texts.BUM_NAME} растёт", "Поднимать его уровень",
     (3, 7, 10), (300, 1000, 5000), lambda s: s["bum"].get("level", 0)),   # 10 — сеть шаурмичных
    ("bum_empire", "bum", "👑", f"Империя {texts.BUM_NAME}а", "Вывести его дальше шаурмичных",
     (11, 13, len(bum.LEVELS) - 1), (2_000, 6_000, 20_000), lambda s: s["bum"].get("level", 0)),
    ("bum_skins", "bum", "👕", "Модник", "Собрать образы для него",
     (3, 10, len(bum.SKINS)), (200, 800, 3000), lambda s: s["skins"]),
    ("bum_invest", "bum", "💼", "Меценат", "Вложить в него",
     (1_000, 10_000, 50_000), (150, 600, 2500), lambda s: s["bum"].get("invested", 0)),
    ("bum_feed", "bum", "🍜", "Кормилец", "Кормить его",
     (5, 25, 100), (100, 400, 1500), lambda s: _count(s, "bum:feed")),
    ("bum_earn", "bum", "🌯", "Пассивный доход", "Собрать с него выручки",
     (1_000, 20_000, 100_000), (150, 600, 2500), lambda s: s["bum"].get("earned", 0)),

    # ── Поиграть ──────────────────────────────────────────────────────────────
    ("arcade_play", "arcade", "🕹", "Залипание", "Сыграть в бесконечные игры",
     (10, 50, 200), (100, 400, 1500), lambda s: _count(s, "arcade:plays")),
    ("arcade_record", "arcade", "🏅", "Рекордсмен", "Побить рекорд беседы",
     (1,), (500,), lambda s: _count(s, "arcade:records")),   # одна ступень: держать все рекорды нереально
    ("arcade_prize", "arcade", "💰", "Премия за рекорд", "Получать премию рекордсмена",
     (1, 10, 50), (150, 500, 2000), lambda s: _count(s, "arcade:prize")),

    # ── Прогулки ──────────────────────────────────────────────────────────────
    ("walk_done", "walk", "🚶", "Гуляка", "Дойти до конца прогулки",
     (1, 20, 100), (100, 500, 2000), lambda s: _count(s, "walk:done")),
    ("walk_ends", "walk", "📖", "Коллекционер", "Найти концовки в историях",
     (5, 50, 200), (200, 1000, 4000), lambda s: sum(s["found"].values())),
    ("walk_quests", "walk", "🗺", "Все дворы", "Побывать в разных историях",
     (5, 25, len(quests.QUESTS)), (200, 800, 3000), _quests_opened),
    ("walk_full", "walk", "🔖", "Заверено нотариально", "Открыть в истории все концовки",
     (1, 10, 30), (300, 1200, 5000), _quests_done),
    ("walk_rich", "walk", "🍀", "Удачная прогулка", "Принести с прогулки за раз",
     (200, 400, 600), (150, 400, 1200), lambda s: _stat(s, "walk", "best_win")),

    # ── Столы с людьми ────────────────────────────────────────────────────────
    ("mp_games", "multi", "👥", "Компанейский", "Сыграть за столом с людьми",
     (3, 25, 100), (150, 600, 2500), lambda s: _count(s, "mp:games")),
    ("mp_wins", "multi", "🏆", "Мастер стола", "Выигрывать у людей",
     (1, 10, 50), (200, 800, 3000), lambda s: _count(s, "mp:wins")),
    ("mp_durak", "multi", "🃏", "Не дурак", "Оставлять дураком кого-то другого",
     (1, 10, 30), (200, 700, 2500), lambda s: _count(s, "mp:win:durak")),
    ("mp_poker", "multi", "♠️", "Покерфейс", "Забирать все фишки за покерным столом",
     (1, 5, 20), (300, 1000, 3500), lambda s: _count(s, "mp:win:poker")),
    ("mp_party", "multi", "🪳", "Душа компании", "Побеждать в бегах, числах, реакции и крокодиле",
     (1, 10, 40), (200, 700, 2500),
     lambda s: sum(_count(s, f"mp:win:{game}") for game in ("roaches", "auction", "reaction", "crocodile"))),
    ("mp_board", "multi", "♟", "Настольный", "Побеждать в шахматах, шашках, нардах и морском бою",
     (1, 10, 40), (200, 800, 3000),
     lambda s: sum(_count(s, f"mp:win:{game}") for game in ("chess", "checkers", "backgammon", "seabattle"))),

    # ── Общее ─────────────────────────────────────────────────────────────────
    ("rich", "general", "🏦", "Самый богатый человек Таджикистана", "Держать на руках",
     (5_000, 50_000, 250_000), (200, 800, 3000), lambda s: s["balance"]),
    ("games", "general", "🎲", "Лудоман", "Сыграть партий",
     (50, 500, 5_000), (150, 600, 2500), lambda s: s["games"]),
    ("donate", "general", "☕", "Спонсор казана", "Поддержать казан",
     (1, 3, 10), (500, 1500, 5000), lambda s: _count(s, "donate")),
    ("stars", "general", "⭐", "Собиратель", "Набрать звёзд за другие ачивки",
     (10, 40, 100), (300, 1000, 4000), lambda s: s["stars"]),
)

MAX_STARS = sum(len(item[5]) for item in ACHIEVEMENTS) + len(quests.QUESTS) * STARS


def ending_reward(quest_key: str) -> int:
    """Чем длиннее история, тем дороже её концовки."""
    return max(ENDING_MIN, min(ENDING_MAX, 30 + 3 * len(quests.QUESTS[quest_key]["nodes"])))


def _quest_goals(quest_key: str) -> tuple[int, int, int]:
    total = len(quests.ENDINGS[quest_key])
    return (1, max(2, (total + 1) // 2), total)


def _snapshot(chat_id: int, user_id: int) -> dict:
    wallet = db.get_wallet(chat_id, user_id)
    earned = db.get_achievements(chat_id, user_id)
    found: dict[str, int] = {}
    for key in earned:
        if key.startswith("end:"):
            quest_key = key.split(":")[1]
            found[quest_key] = found.get(quest_key, 0) + 1
    return {
        "balance": wallet["balance"],
        "games": wallet["games"],
        "stats": {row["game"]: row for row in db.get_casino_stats(chat_id, user_id)},
        "counters": db.get_counters(chat_id, user_id),
        "bum": db.peek_bum(chat_id, user_id) or {},
        "skins": len(db.get_bum_skins(chat_id, user_id)),
        "earned": earned,
        "found": found,
        "stars": sum(level for key, level in earned.items() if not key.startswith("end:")),
    }


def _stars(snap: dict, key: str, goals, value: int) -> int:
    """Сколько звёзд у карточки. Взятую ступень не отбираем: деньги на руках тают, цели у ачивок растут,
    когда добавляются новые работы и истории, — а заслуженная звезда остаётся навсегда."""
    reached = sum(1 for goal in goals if value >= goal)
    return max(reached, min(len(goals), snap["earned"].get(key, 0)))


def _cards(snap: dict) -> list[dict]:
    """Все ачивки с посчитанным прогрессом: обычные и по одной на каждую историю."""
    cards = []
    for key, section, emoji, title, about, goals, rewards, metric in ACHIEVEMENTS:
        try:
            value = int(metric(snap))
        except Exception:
            logger.exception("Не посчиталась ачивка %s", key)
            value = 0
        cards.append({"key": key, "section": section, "emoji": emoji, "title": title, "about": about,
                      "goals": list(goals), "rewards": list(rewards), "value": value,
                      "stars": _stars(snap, key, goals, value)})
    for quest_key, quest in quests.QUESTS.items():
        goals = _quest_goals(quest_key)
        value = snap["found"].get(quest_key, 0)
        cards.append({"key": f"quest:{quest_key}", "section": "quests", "emoji": quest.get("emoji", "📖"),
                      "title": quest["title"], "about": "Открыть все концовки истории",
                      "goals": list(goals), "rewards": list(QUEST_REWARDS), "value": value,
                      "stars": _stars(snap, f"quest:{quest_key}", goals, value), "quest": quest_key})
    return cards


def check(chat_id: int, user_id: int) -> list[dict]:
    """Выдаёт всё, что игрок успел заслужить. Возвращает только что открытые ступени."""
    snap = _snapshot(chat_id, user_id)
    unlocked = []
    for card in _cards(snap):
        have = snap["earned"].get(card["key"], 0)
        while have < card["stars"]:
            have += 1
            reward = card["rewards"][have - 1]
            if db.award_achievement(chat_id, user_id, card["key"], have, reward) is None:
                break  # эту ступень уже выдали из соседней вкладки
            top = len(card["goals"])
            unlocked.append({"key": card["key"], "emoji": card["emoji"], "title": card["title"],
                             "star": have, "stars": top, "reward": reward, "goal": card["goals"][have - 1]})
            if have == top:
                done = {1: "полностью", 2: "на две звезды"}.get(top, "на три звезды")
                engine.add_feed(chat_id, f"{card['emoji']} {engine.player_name(user_id)} закрывает ачивку "
                                         f"«{card['title']}» {done}")
    if unlocked:
        logger.info("Ачивки %s в %s: %s", user_id, chat_id, ", ".join(item["title"] for item in unlocked))
    return unlocked


def record_ending(chat_id: int, user_id: int, quest_key: str, node_id: str) -> Optional[dict]:
    """Первая встреча с концовкой — разовая награда. Второй раз за ту же концовку не платят."""
    reward = ending_reward(quest_key)
    if db.award_achievement(chat_id, user_id, f"end:{quest_key}:{node_id}", 1, reward) is None:
        return None
    db.bump_counter(chat_id, user_id, "walk:ends")
    return {"reward": reward, "name": quests.ending_name(quest_key, node_id)}


def found_endings(chat_id: int, user_id: int, quest_key: str) -> set[str]:
    """Какие концовки истории игрок уже видел."""
    prefix = f"end:{quest_key}:"
    return {key[len(prefix):] for key in db.get_achievements(chat_id, user_id) if key.startswith(prefix)}


def found_endings_total(chat_id: int, user_id: int) -> set[str]:
    """Все найденные концовки игрока — ключами вида end:<история>:<узел>."""
    return {key for key in db.get_achievements(chat_id, user_id) if key.startswith("end:")}


def state(chat_id: int, user_id: int) -> dict:
    """Экран ачивок: разделы с карточками и коллекция концовок по историям."""
    snap = _snapshot(chat_id, user_id)
    cards = _cards(snap)
    by_section: dict[str, list[dict]] = {}
    for card in cards:
        view = {key: card[key] for key in ("key", "emoji", "title", "about", "goals", "rewards", "value", "stars")}
        view["done"] = view["stars"] >= len(card["goals"])
        view["next_goal"] = None if view["done"] else card["goals"][view["stars"]]
        view["next_reward"] = None if view["done"] else card["rewards"][view["stars"]]
        if "quest" in card:
            quest_key = card["quest"]
            reward = ending_reward(quest_key)
            view["endings"] = [
                {"name": quests.ending_name(quest_key, node) if f"end:{quest_key}:{node}" in snap["earned"] else None,
                 "found": f"end:{quest_key}:{node}" in snap["earned"], "reward": reward}
                for node in quests.ENDINGS[quest_key]
            ]
            view["total"] = len(view["endings"])
        by_section.setdefault(card["section"], []).append(view)
    sections = [{"key": key, "emoji": emoji, "title": title,
                 "cards": sorted(by_section.get(key, []), key=lambda card: (card["done"], card["title"]))}
                for key, emoji, title in SECTIONS]
    names = db.get_name_rows([row["user_id"] for row in db.achievement_leaders(chat_id)])
    return {
        "sections": [section for section in sections if section["cards"]],
        "stars": sum(card["stars"] for card in cards),
        "max_stars": MAX_STARS,
        "done": sum(1 for card in cards if card["stars"] >= len(card["goals"])),
        "total": len(cards),
        "earned": db.achievement_earned(chat_id, user_id),
        "endings": {"found": sum(snap["found"].values()), "total": sum(len(ends) for ends in quests.ENDINGS.values())},
        "leaders": [{"name": members.plain_name(row["user_id"], names.get(row["user_id"], {})),
                     "stars": row["stars"], "me": row["user_id"] == user_id}
                    for row in db.achievement_leaders(chat_id)],
        "balance": snap["balance"],
    }
