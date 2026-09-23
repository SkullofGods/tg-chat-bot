"""Крокодил: бот загадывает слово ведущему, тот объясняет его в беседе, остальные угадывают там же.

Стол живёт в приложении (кто играет, сколько у кого очков, какое слово у ведущего), а сама игра
идёт в чате: бот читает сообщения сидящих за столом и сам замечает угаданное слово.
"""

import random
import time

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Крокодил"
EMOJI = "🐊"
ABOUT = "Ведущий объясняет слово в беседе, остальные угадывают там же. Три угаданных — забрал банк"
MIN_SEATS, MAX_SEATS = 3, 10
PACE = "live"
TURN_SECONDS = 60
CLOCK_SECONDS = 10

TARGET = 3                 # столько слов надо угадать, чтобы забрать банк
ROUND_SECONDS = 180        # столько живёт одно слово

WORDS = (
    "шаурма", "казан", "плов", "маршрутка", "гараж", "подъезд", "лавочка", "дворник", "сосед",
    "балкон", "кастрюля", "тапки", "холодильник", "телевизор", "пульт", "ковёр", "сервант",
    "дача", "огород", "теплица", "шашлык", "мангал", "рыбалка", "удочка", "червяк", "велосипед",
    "самокат", "троллейбус", "трамвай", "вокзал", "плацкарт", "проводник", "чемодан", "билет",
    "аптека", "поликлиника", "очередь", "справка", "паспорт", "участковый", "штраф", "квитанция",
    "сберкнижка", "кредит", "зарплата", "аванс", "премия", "начальник", "бухгалтер", "отпуск",
    "субботник", "метла", "лопата", "сугроб", "гололёд", "валенки", "шапка", "варежки", "ёлка",
    "мандарины", "оливье", "холодец", "селёдка", "тамада", "свадьба", "фата", "букет", "гости",
    "баян", "гармошка", "частушка", "дискотека", "диджей", "колонка", "караоке", "микрофон",
    "футбол", "ворота", "вратарь", "пенальти", "судья", "болельщик", "шахматы", "домино", "нарды",
    "лото", "бочонок", "карты", "козырь", "дурак", "туз", "джокер", "кубик", "ставка", "выигрыш",
    "базар", "весы", "арбуз", "дыня", "абрикос", "урюк", "лепёшка", "самса", "чайхана", "пиала",
    "ишак", "верблюд", "отара", "чабан", "кишлак", "арык", "хлопок", "джигит", "тюбетейка",
    "толян", "бомж", "тележка", "коробка", "палатка", "собака", "кот", "голубь", "крыса", "таракан",
    # лор беседы
    "нотариус", "доверенность", "печать", "ксерокс", "заверение", "перевод", "бюро", "сигара",
    "кабинет", "помощница", "ламинат", "морковка", "планктон", "трусы", "гольфы", "нунчаки",
    "ниндзя", "плита", "отпечаток", "зрение", "молочко", "сюрстрёмминг", "тетрадь", "маркер",
    "зира", "сюзане", "чайник", "гравитация", "орбита", "перевал", "тюбетейка", "пиала",
)


def setup(players: list[int]) -> dict:
    host = random.choice(players)
    state = {
        "players": players, "scores": {str(player): 0 for player in players},
        "host": host, "word": random.choice(WORDS), "used": [], "round": 1,
        "started": int(time.time()), "winner": None, "last": None, "feed": [],
    }
    return feed(state, "Слово загадано — ведущий объясняет в беседе")


def turn(state: dict):
    return None                 # в приложении ходов нет: играют в чате


def _fresh_word(state: dict) -> str:
    used = set(state["used"]) | {state["word"]}
    left = [word for word in WORDS if word not in used]
    return random.choice(left or list(WORDS))


def _next_round(state: dict, host: int, line: str) -> dict:
    state = feed(state, line)
    return state | {"host": host, "word": _fresh_word(state), "used": state["used"] + [state["word"]],
                    "round": state["round"] + 1, "started": int(time.time())}


def clock(state: dict, now: int) -> dict:
    if state.get("winner") or now - state["started"] < ROUND_SECONDS:
        return state
    players = state["players"]
    nxt = players[(players.index(state["host"]) + 1) % len(players)] if state["host"] in players else players[0]
    return _next_round(state, nxt, f"Слово «{state['word']}» не угадали — ведущий меняется")


def _clean(text: str) -> str:
    return "".join(letter for letter in text.lower().replace("ё", "е") if letter.isalpha() or letter == " ")


def guess(state: dict, user_id: int, text: str) -> dict | None:
    """Сообщение из беседы. Вернёт новое состояние, если слово угадали, иначе None."""
    if state.get("winner") or user_id == state["host"] or user_id not in state["players"]:
        return None
    word = state["word"].replace("ё", "е")
    if word not in _clean(text).split():
        return None
    scores = dict(state["scores"])
    scores[str(user_id)] = scores.get(str(user_id), 0) + 1
    state = state | {"scores": scores, "last": {"user": user_id, "word": state["word"]}}
    state = feed(state, f"Угадал слово «{state['word']}»")
    if scores[str(user_id)] >= TARGET:
        return state | {"winner": user_id}
    return _next_round(state, user_id, "Теперь его очередь объяснять")


def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        players = [player for player in state["players"] if player != user_id]
        state = feed(state, "Ушёл из-за стола")
        if len(players) < MIN_SEATS - 1 or not players:
            best = max(players, key=lambda player: state["scores"].get(str(player), 0), default=None)
            return state | {"players": players or state["players"], "winner": best}
        state = state | {"players": players}
        if state["host"] == user_id:
            return _next_round(state, players[0], "Ведущий ушёл — слово меняется")
        return state
    if kind != "skip":
        raise GameError("Непонятный ход")
    if user_id != state["host"]:
        raise GameError("Менять слово может только ведущий", 403)
    return _next_round(state, state["host"], f"Ведущий пропустил слово «{state['word']}»")


def auto(state: dict, user_id: int) -> dict:
    return {"type": "skip"}


def view(state: dict, user_id: int) -> dict:
    return {
        "host": state["host"],
        "am_host": user_id == state["host"],
        "word": state["word"] if user_id == state["host"] else None,
        "left": max(0, ROUND_SECONDS - (int(time.time()) - state["started"])),
        "round": state["round"],
        "target": TARGET,
        "scores": state["scores"],
        "players": state["players"],
        "last": state.get("last"),
        "winner": state.get("winner"),
    }


def moves(state: dict, user_id: int) -> dict:
    return {"skip": user_id == state["host"] and not state.get("winner")}


def result(state: dict):
    if not state.get("winner"):
        return None
    return {"winners": [state["winner"]], "text": f"Угадал {TARGET} слова"}
