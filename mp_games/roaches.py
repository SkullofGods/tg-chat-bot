"""Тараканьи бега: ставим друг против друга. Выбрал таракана — и смотришь, как он ползёт."""

import random

from casino_engine import GameError
from mp_games.tools import feed

TITLE = "Тараканьи бега"
EMOJI = "🪳"
ABOUT = "Выбираешь таракана и ждёшь. Кто поставил на победителя — делит банк между собой"
MIN_SEATS, MAX_SEATS = 2, 8
PACE = "live"
TURN_SECONDS = 30
CLOCK_SECONDS = 2

ROACHES = ("Усатый", "Чёрный", "Рыжий", "Хромой")
TRACK = 24            # столько ползти до финиша
PICK_SECONDS = 20     # столько дают на выбор


def setup(players: list[int]) -> dict:
    state = {"players": players, "picks": {}, "phase": "pick", "waited": 0,
             "spots": [0] * len(ROACHES), "winner": None, "champion": None, "feed": []}
    return feed(state, "Тараканов выпустили на старт — выбирай")


def turn(state: dict):
    return None                 # ставят все разом, очереди нет


def clock(state: dict, now: int) -> dict:
    if state["phase"] == "pick":
        waited = state["waited"] + CLOCK_SECONDS
        if len(state["picks"]) >= len(state["players"]) or waited >= PICK_SECONDS:
            picks = dict(state["picks"])
            for player in state["players"]:        # кто не выбрал — тому таракана выбирает судьба
                picks.setdefault(str(player), random.randrange(len(ROACHES)))
            return feed(state | {"phase": "run", "waited": waited, "picks": picks}, "Побежали!")
        return state | {"waited": waited}
    if state["phase"] != "run":
        return state
    spots = [spot + random.randint(0, 3) for spot in state["spots"]]
    state = state | {"spots": spots}
    best = max(spots)
    if best >= TRACK:
        champions = [index for index, spot in enumerate(spots) if spot == best]
        champion = random.choice(champions)
        state = feed(state, f"Финиш! Первым приполз {ROACHES[champion]}")
        return state | {"phase": "over", "champion": champion}
    return state


def move(state: dict, user_id: int, action: dict) -> dict:
    kind = action.get("type")
    if kind == "resign":
        picks = {key: value for key, value in state["picks"].items() if key != str(user_id)}
        players = [player for player in state["players"] if player != user_id]
        state = feed(state, "Ушёл до финиша")
        return state | {"picks": picks, "players": players or state["players"]}
    if kind != "pick":
        raise GameError("Непонятный ход")
    if state["phase"] != "pick":
        raise GameError("Забег уже начался", 409)
    roach = action.get("roach")
    if isinstance(roach, bool) or not isinstance(roach, int) or not 0 <= roach < len(ROACHES):
        raise GameError("Такого таракана нет")
    picks = dict(state["picks"])
    picks[str(user_id)] = roach
    state = feed(state, f"Ставит на {ROACHES[roach]}")
    return state | {"picks": picks}


def auto(state: dict, user_id: int) -> dict:
    return {"type": "pick", "roach": random.randrange(len(ROACHES))}


def view(state: dict, user_id: int) -> dict:
    return {
        "roaches": list(ROACHES),
        "spots": state["spots"],
        "track": TRACK,
        "phase": state["phase"],
        "my_pick": state["picks"].get(str(user_id)),
        "picks": state["picks"],
        "champion": state.get("champion"),
        "left": max(0, PICK_SECONDS - state["waited"]) if state["phase"] == "pick" else 0,
    }


def moves(state: dict, user_id: int) -> dict:
    return {"pick": list(range(len(ROACHES))) if state["phase"] == "pick" else []}


def result(state: dict):
    if state["phase"] != "over":
        return None
    champion = state.get("champion")
    if champion is None:
        return {"winners": [], "text": "Забег не состоялся"}
    winners = [int(player) for player, roach in state["picks"].items() if roach == champion]
    if not winners:
        return {"winners": [], "text": f"Победил {ROACHES[champion]} — на него никто не поставил"}
    return {"winners": winners, "text": f"Первым приполз {ROACHES[champion]}"}
