"""Общие столы казино: рулетка и скачки.

Ставят все участники беседы, а колесо крутится и лошади бегут для всех одновременно. Время поделено
на раунды по общему расписанию: сначала окно ставок, потом розыгрыш (анимация в приложении).
Исход решается в момент, когда ставки закрываются, — раньше его не знает даже сервер.
Ставки лежат в базе и переживают перезапуск бота: недоразыгранные раунды разыгрываются, как только бот поднимется.

Раунды разыгрываются, когда кто-то смотрит на стол (запрос состояния) или когда на раунд поставили
(фоновая задача loop), — пустые раунды без зрителей не разыгрываются вовсе.
"""

import asyncio
import hashlib
import logging
import math
import random
import time
from dataclasses import dataclass

import casino_engine as engine
import casino_rig
import members
import texts
from casino_engine import GameError, signed
from config import BOT_TOKEN
from loader import db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Table:
    game: str
    betting: float  # секунд на ставки
    playing: float  # секунд на розыгрыш — столько длится анимация в приложении

    @property
    def cycle(self) -> float:
        return self.betting + self.playing

    def round_at(self, moment: float) -> int:
        return math.floor(moment / self.cycle)

    def closes(self, round_no: int) -> float:
        """Ставки на раунд закрываются, начинается розыгрыш."""
        return round_no * self.cycle + self.betting

    def ends(self, round_no: int) -> float:
        return (round_no + 1) * self.cycle

    def open_round(self, moment: float) -> int:
        """Раунд, в который сейчас попадёт ставка: во время розыгрыша — уже следующий."""
        round_no = self.round_at(moment)
        return round_no if moment < self.closes(round_no) else round_no + 1


ROULETTE = Table("roulette", betting=5, playing=6)
RACE = Table("race", betting=20, playing=10)
TABLES = {table.game: table for table in (ROULETTE, RACE)}

MAX_BETS_PER_ROUND = 10
PRUNE_AFTER_SECONDS = 24 * 3600  # разыгранные ставки старше суток удаляются

# ── Рулетка ───────────────────────────────────────────────────────────────────

RED_NUMBERS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})
ROULETTE_BETS = {  # вид ставки: (множитель выплаты, подпись, выигрывает ли выпавшее число)
    "red": (2, "🔴 красное", lambda n: n in RED_NUMBERS),
    "black": (2, "⚫ чёрное", lambda n: n != 0 and n not in RED_NUMBERS),
    "even": (2, "чёт", lambda n: n != 0 and n % 2 == 0),
    "odd": (2, "нечет", lambda n: n % 2 == 1),
    "low": (2, "1–18", lambda n: 1 <= n <= 18),
    "high": (2, "19–36", lambda n: 19 <= n <= 36),
    "dozen1": (3, "1–12", lambda n: 1 <= n <= 12),
    "dozen2": (3, "13–24", lambda n: 13 <= n <= 24),
    "dozen3": (3, "25–36", lambda n: 25 <= n <= 36),
}
NUMBER_MULTIPLIER = 36


def color_of(number: int) -> str:
    return "green" if number == 0 else ("red" if number in RED_NUMBERS else "black")


# ── Скачки ────────────────────────────────────────────────────────────────────

RACE_SIZE = casino_rig.RACE_SIZE
RACE_EDGE = 0.08            # доля казино: коэффициенты чуть ниже честных
RACE_FINISH = (7.4, 9.4)    # на какой секунде забега финиширует первая и последняя лошадь


def race_lineup(chat_id: int, round_no: int) -> list[dict]:
    """Лошади забега и коэффициенты. Они известны заранее — на них ставят, а победителя решает случай.
    Состав зависит только от беседы и номера раунда, поэтому не меняется после перезапуска бота."""
    seed = hashlib.sha256(f"race:{chat_id}:{round_no}:{BOT_TOKEN}".encode()).digest()
    rng = random.Random(seed)
    names = rng.sample(texts.RACE_HORSES, RACE_SIZE)
    strengths = [rng.uniform(0.6, 2.2) ** 2 for _ in range(RACE_SIZE)]  # есть фавориты и аутсайдеры
    total = sum(strengths)
    lineup = []
    for number, (name, strength) in enumerate(zip(names, strengths), start=1):
        chance = strength / total
        odds = max(1.1, math.floor((1 - RACE_EDGE) / chance * 10) / 10)
        lineup.append({"no": number, "name": name, "odds": odds, "chance": chance})
    return lineup


def _public_lineup(lineup: list[dict]) -> list[dict]:
    return [{"no": horse["no"], "name": horse["name"], "odds": horse["odds"]} for horse in lineup]


def _race_result(chat_id: int, round_no: int, forced_winner: int | None) -> dict:
    lineup = race_lineup(chat_id, round_no)
    pool = list(lineup)
    order = []
    if forced_winner is not None:
        winner = next(horse for horse in pool if horse["no"] == forced_winner)
        pool.remove(winner)
        order.append(winner)
    while pool:  # остальные места — тоже по силе лошадей
        horse = random.choices(pool, weights=[h["chance"] for h in pool])[0]
        pool.remove(horse)
        order.append(horse)
    # время на финише: отрывы случайные, но все укладываются в анимацию
    first, last = RACE_FINISH
    start = random.uniform(first, first + 0.5)
    gaps = [random.uniform(0.06, 0.5) for _ in order[1:]]
    squeeze = min(1.0, (last - start) / sum(gaps))
    times, moment = {}, start
    for horse, gap in zip(order, [0.0] + gaps):
        moment += gap * squeeze
        times[horse["no"]] = round(moment, 2)
    return {
        "order": [horse["no"] for horse in order],
        "times": [times[horse["no"]] for horse in lineup],  # по номерам лошадей
        "seed": random.randrange(1, 2 ** 31),                 # по нему приложение рисует одинаковый забег у всех
        "horses": _public_lineup(lineup),
    }


# ── Розыгрыш ──────────────────────────────────────────────────────────────────

_resolved: dict[tuple[int, str], int] = {}  # последний разыгранный раунд — чтобы не ходить в базу на каждый запрос


def _now() -> float:
    return time.time()


def _payout(game: str, result: dict, bet: dict) -> tuple[int, bool]:
    if game == "roulette":
        number = result["number"]
        won = bet["pick"] == number if bet["kind"] == "number" else ROULETTE_BETS[bet["kind"]][2](number)
        special = won and bet["kind"] == "number"
    else:
        won = special = bet["pick"] == result["order"][0]
    # коэффициент с одним знаком после запятой: считаем в целых, чтобы 1000 × 3.3 было 3300, а не 3299
    return (bet["amount"] * round(bet["odds"] * 10) // 10 if won else 0), special


def resolve(chat_id: int, game: str, round_no: int) -> bool:
    """Разыгрывает раунд, если ставки на него уже закрыты и его ещё не разыграли."""
    table = TABLES[game]
    if _now() < table.closes(round_no) or db.round_result(chat_id, game, round_no) is not None:
        return False
    if game == "roulette":
        forced = casino_rig.take(chat_id, "roulette")
        number = forced if forced is not None else random.randint(0, 36)
        result = {"number": number, "color": color_of(number)}
    else:
        result = _race_result(chat_id, round_no, casino_rig.take(chat_id, "race"))
    settled = db.settle_table_round(chat_id, game, round_no, result, lambda bet: _payout(game, result, bet))
    if settled is None:
        return False
    key = (chat_id, game)
    _resolved[key] = max(_resolved.get(key, round_no), round_no)
    _announce(chat_id, game, result, settled)
    return True


def _announce(chat_id: int, game: str, result: dict, settled: list[dict]):
    for bet in settled:
        net = bet["payout"] - bet["amount"]
        if bet["payout"] == 0:
            continue
        name = engine.player_name(bet["user_id"])
        if game == "roulette":
            if bet["kind"] == "number":
                engine.add_feed(chat_id, f"🎡 {name} угадывает число {result['number']}: {signed(net)}")
            elif net >= 2000:
                engine.add_feed(chat_id, f"🎡 {name} выигрывает в рулетке {signed(net)}")
        elif bet["odds"] >= 8 or net >= 2000:
            horse = next(h for h in result["horses"] if h["no"] == bet["pick"])
            engine.add_feed(chat_id, f"🏇 {name} ставит на «{horse['name']}» (×{horse['odds']:g}) и забирает {signed(net)}")


def advance(chat_id: int, game: str):
    """Разыгрывает текущий раунд, если его ставки уже закрыты. Вызывается на каждый запрос состояния стола."""
    table = TABLES[game]
    now = _now()
    round_no = table.round_at(now)
    if now >= table.closes(round_no) and _resolved.get((chat_id, game), -1) < round_no:
        if not resolve(chat_id, game, round_no):
            _resolved[(chat_id, game)] = max(_resolved.get((chat_id, game), -1), round_no)


def resolve_due():
    """Разыгрывает раунды со ставками, у которых вышло время, — даже если приложение у всех закрыто."""
    now = _now()
    for chat_id, game, round_no in db.open_bet_rounds():
        table = TABLES.get(game)
        if table is not None and now >= table.closes(round_no):
            resolve(chat_id, game, round_no)


async def loop():
    last_prune = 0.0
    while True:
        try:
            resolve_due()
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                for table in TABLES.values():
                    db.prune_table_bets(table.game, table.round_at(_now() - PRUNE_AFTER_SECONDS))
        except Exception:
            logger.exception("Общие столы казино: не получилось разыграть раунд")
        await asyncio.sleep(0.5)


# ── Ставки и состояние стола ──────────────────────────────────────────────────


def _table(game) -> Table:
    if game not in TABLES:
        raise GameError("Такого стола нет", 404)
    return TABLES[game]


def place_bet(chat_id: int, user_id: int, game, amount, kind=None, pick=None, expected_round=None) -> dict:
    """expected_round — забег, в котором игрок выбирал лошадь: если он уже стартовал, ставку не принимаем,
    ведь в следующем забеге под тем же номером бежит другая лошадь."""
    table = _table(game)
    round_no = table.open_round(_now())
    whole = isinstance(pick, int) and not isinstance(pick, bool)
    if game == "roulette":
        if kind == "number":
            if not whole or not 0 <= pick <= 36:
                raise GameError("Выбери число от 0 до 36")
            odds = NUMBER_MULTIPLIER
        elif kind in ROULETTE_BETS:
            odds, pick = ROULETTE_BETS[kind][0], None
        else:
            raise GameError("Непонятная ставка")
    else:
        if expected_round is not None and expected_round != round_no:
            raise GameError("Забег уже стартовал — выбери лошадь в следующем", 409)
        horses = {horse["no"]: horse for horse in race_lineup(chat_id, round_no)}
        if not whole or pick not in horses:
            raise GameError("Выбери лошадь")
        kind, odds = "horse", horses[pick]["odds"]
    engine.check_bet(amount)
    if db.count_round_bets(chat_id, game, round_no, user_id) >= MAX_BETS_PER_ROUND:
        raise GameError("На этот раунд ставок уже хватит 🙂", 429)
    engine.throttle(chat_id, user_id)
    if db.add_table_bet(chat_id, game, round_no, user_id, kind, pick, amount, odds) is None:
        raise engine.not_enough_money(chat_id, user_id)
    return table_state(chat_id, user_id, game)


def _bet_label(game: str, bet: dict, horses: dict[int, dict]) -> str:
    if game == "roulette":
        return f"число {bet['pick']}" if bet["kind"] == "number" else ROULETTE_BETS[bet["kind"]][1]
    horse = horses.get(bet["pick"])
    return f"№{bet['pick']} {horse['name']}" if horse else f"№{bet['pick']}"


def _bets_view(game: str, bets: list[dict], user_id: int, names: dict[int, dict], horses: dict[int, dict]) -> list[dict]:
    return [
        {
            "name": members.plain_name(bet["user_id"], names.get(bet["user_id"], {})),
            "me": bet["user_id"] == user_id,
            "amount": bet["amount"],
            "label": _bet_label(game, bet, horses),
            "kind": bet["kind"],
            "pick": bet["pick"],
            "odds": bet["odds"],
            "payout": bet["payout"],  # None — ещё не разыграна
        }
        for bet in bets
    ]


def _history_view(game: str, item: dict) -> dict:
    if game == "roulette":
        return {"round": item["round"], "number": item["number"], "color": item["color"]}
    winner = next(horse for horse in item["horses"] if horse["no"] == item["order"][0])
    return {"round": item["round"], "no": winner["no"], "name": winner["name"], "odds": winner["odds"]}


def table_state(chat_id: int, user_id: int, game) -> dict:
    table = _table(game)
    advance(chat_id, game)
    now = _now()
    round_no = table.round_at(now)
    playing = now >= table.closes(round_no)
    open_round = round_no + 1 if playing else round_no
    bets = db.round_bets(chat_id, game, round_no)
    upcoming = db.round_bets(chat_id, game, open_round) if playing else []
    names = db.get_name_rows({bet["user_id"] for bet in bets + upcoming})
    result = db.round_result(chat_id, game, round_no) if playing else None

    view = {
        "game": game,
        "now": now,
        "round": round_no,
        "phase": "playing" if playing else "betting",
        "open_round": open_round,  # в этот раунд попадёт ставка
        "closes_at": table.closes(round_no),
        "ends_at": table.ends(round_no),
        "betting": table.betting,
        "playing": table.playing,
        "result": result,
        "history": [_history_view(game, item) for item in db.get_history(chat_id, game, engine.HISTORY_SHOWN)],
        "balance": db.get_balance(chat_id, user_id),
    }
    current_horses, open_horses = {}, {}
    if game == "race":
        current_lineup = race_lineup(chat_id, round_no)
        open_lineup = race_lineup(chat_id, open_round) if playing else current_lineup
        current_horses = {horse["no"]: horse for horse in current_lineup}
        open_horses = {horse["no"]: horse for horse in open_lineup}
        view["race_horses"] = _public_lineup(current_lineup)  # кто бежит сейчас (или вот-вот)
        view["lineup"] = _public_lineup(open_lineup)          # на кого можно поставить
    view["bets"] = _bets_view(game, bets, user_id, names, current_horses)
    view["next_bets"] = _bets_view(game, upcoming, user_id, names, open_horses)
    return view


def lineup_for_rig(chat_id: int) -> list[dict]:
    """Состав ближайшего забега, которому ещё можно подкрутить победителя (для подкрутки)."""
    advance(chat_id, "race")  # идущий забег уже разыгран — подкрутка достанется следующему
    return _public_lineup(race_lineup(chat_id, RACE.open_round(_now())))
