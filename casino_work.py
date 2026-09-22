"""Халтура: мини-игры, где таджикоины зарабатывают руками, а не ставками.

Задание придумывает сервер, он же считает выплату: приложение присылает только то, что игрок успел
сделать (какой мусор собрал, куда разложил письма, сколько раз нажал). Больше, чем заложено в задании,
заработать нельзя, а на смену есть время — так что рисовать себе деньги из браузера бесполезно.

Смена живёт в памяти: перезапуск бота её теряет, но деньги при этом не пропадают — за смену платят
один раз, в самом конце. У каждой работы свой кулдаун (в спам-день он, как и всё остальное, 5 секунд).
"""

import logging
import random
import time

import casino_engine as engine
import limits
import texts
from casino_engine import GameError, money, signed
from loader import db

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 180    # перекур между сменами на одной и той же работе
SHIFT_SLACK_SECONDS = 6   # насколько смена может затянуться из-за связи
FEED_FROM = 250           # с какой выплаты о смене узнаёт вся беседа

# ── Субботник ─────────────────────────────────────────────────────────────────

CLEANUP_SECONDS = 15
CLEANUP_LIFETIME = 2.6                                          # сколько мусоринка висит на экране
CLEANUP_KINDS = ("trash", "bottle", "wallet", "rat")
CLEANUP_WEIGHTS = (58, 27, 4, 11)
CLEANUP_VALUES = {"trash": 6, "bottle": 10, "wallet": 60, "rat": -8}

# ── Почта ─────────────────────────────────────────────────────────────────────

POST_SECONDS = 25
POST_LETTERS = 12
POST_PAY = 13

# ── Цех самсы ─────────────────────────────────────────────────────────────────

SAMSA_SECONDS = 10
SAMSA_MAX_TAPS = 130      # быстрее 13 нажатий в секунду человек не стучит
SAMSA_PAY = 1.1
SAMSA_BONUS_PAY = 12

# ── Рыбалка ───────────────────────────────────────────────────────────────────

FISHING_CASTS = 3
FISHING_REACTION_MS = 800  # успел дёрнуть — рыба твоя
FISHING_CATCH = {          # что клюёт: (вес, выплата)
    "junk": (18, 6), "small": (44, 28), "big": (26, 75), "golden": (4, 300), "boot": (8, 0),
}

# ── Разгрузка фуры ────────────────────────────────────────────────────────────

TRUCK_BOXES = ((35, 0.10), (45, 0.15), (60, 0.22), (80, 0.28), (110, 0.34), (150, 0.40))  # (оплата, шанс уронить)

JOBS = {
    "cleanup": {
        "title": "Субботник", "emoji": "🧹",
        "about": "Собери мусор во дворе за 15 секунд. Кошелёк в траве — удача, крыса — минус.",
        "pay": "до 300",
    },
    "post": {
        "title": "Почта Таджикистана", "emoji": "✉️",
        "about": "Разложи письма по городам. Платят за каждое угаданное, время идёт.",
        "pay": "до 160",
    },
    "samsa": {
        "title": "Цех самсы", "emoji": "🥟",
        "about": "Дави на пресс 10 секунд. Иногда из-под пресса выходит золотая самса.",
        "pay": "до 170",
    },
    "fishing": {
        "title": "Рыбалка на Вахше", "emoji": "🎣",
        "about": "Три заброса. Дёрнешь вовремя — рыба твоя, а что клюнет, решает река.",
        "pay": "0–400",
    },
    "truck": {
        "title": "Разгрузка фуры", "emoji": "🚚",
        "about": "Каждый следующий ящик дороже, но уронишь — останешься без всего.",
        "pay": "0–480",
    },
}

_shifts: dict[tuple[int, int], dict] = {}


# ── Задания ───────────────────────────────────────────────────────────────────


def _cleanup_task() -> tuple[dict, dict]:
    items = []
    for number in range(random.randint(20, 26)):
        kind = random.choices(CLEANUP_KINDS, weights=CLEANUP_WEIGHTS)[0]
        items.append({
            "id": number, "kind": kind,
            "x": round(random.uniform(7, 93), 1), "y": round(random.uniform(12, 88), 1),
            "at": round(random.uniform(0, CLEANUP_SECONDS - CLEANUP_LIFETIME), 2),
        })
    return {"seconds": CLEANUP_SECONDS, "lifetime": CLEANUP_LIFETIME, "items": items,
            "values": CLEANUP_VALUES}, {}


def _cleanup_pay(shift: dict, answer: dict) -> tuple[int, list[str]]:
    kinds = {item["id"]: item["kind"] for item in shift["task"]["items"]}
    collected = answer.get("collected")
    picked = {value for value in collected if isinstance(value, int)} if isinstance(collected, list) else set()
    counts: dict[str, int] = {}
    for item_id in picked:
        kind = kinds.get(item_id)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    pay = sum(CLEANUP_VALUES[kind] * count for kind, count in counts.items())
    detail = [f"{texts.WORK_CLEANUP_NAMES[kind]} × {count}" for kind, count in counts.items()]
    return pay, detail


def _post_task() -> tuple[dict, dict]:
    cities = random.sample(texts.WORK_POST_CITIES, 3)
    letters = [{"id": number, "city": random.randrange(3), "street": random.choice(texts.WORK_POST_STREETS)}
               for number in range(POST_LETTERS)]
    return {"seconds": POST_SECONDS, "cities": cities, "letters": letters, "pay": POST_PAY}, {}


def _post_pay(shift: dict, answer: dict) -> tuple[int, list[str]]:
    right = {letter["id"]: letter["city"] for letter in shift["task"]["letters"]}
    answers = answer.get("answers")
    good = 0
    if isinstance(answers, list):
        seen = set()
        for item in answers[:POST_LETTERS * 2]:
            if not isinstance(item, dict):
                continue
            letter_id, box = item.get("id"), item.get("box")
            if letter_id in right and letter_id not in seen and right[letter_id] == box:
                seen.add(letter_id)
                good += 1
    return good * POST_PAY, [f"верно разложено: {good} из {POST_LETTERS}"]


def _samsa_task() -> tuple[dict, dict]:
    bonus = sorted(random.sample(range(5, SAMSA_MAX_TAPS), 4))
    return {"seconds": SAMSA_SECONDS, "bonus": bonus, "max_taps": SAMSA_MAX_TAPS}, {}


def _samsa_pay(shift: dict, answer: dict) -> tuple[int, list[str]]:
    taps = answer.get("taps")
    taps = min(taps, SAMSA_MAX_TAPS) if isinstance(taps, int) and not isinstance(taps, bool) and taps > 0 else 0
    golden = sum(1 for number in shift["task"]["bonus"] if number <= taps)
    pay = round(taps * SAMSA_PAY) + golden * SAMSA_BONUS_PAY
    detail = [f"самсы налеплено: {taps}"] + ([f"золотая самса × {golden}"] if golden else [])
    return pay, detail


def _fishing_task() -> tuple[dict, dict]:
    return {"casts": FISHING_CASTS, "reaction_ms": FISHING_REACTION_MS}, {"cast": 0, "waiting": False}


def _fishing_step(shift: dict, payload: dict) -> dict:
    secret = shift["secret"]
    if not secret["waiting"]:
        if secret["cast"] >= FISHING_CASTS:
            raise GameError("Забросы кончились — сматывай удочку", 409)
        secret["waiting"] = True
        secret["bite_at"] = time.monotonic() + random.uniform(1.4, 4.6)
        return {"bite_in": round(secret["bite_at"] - time.monotonic(), 2), "cast": secret["cast"] + 1}

    reaction = payload.get("reaction")
    if not isinstance(reaction, int) or isinstance(reaction, bool) or not 0 <= reaction <= 10_000:
        raise GameError("Кривой рывок")
    secret["waiting"] = False
    secret["cast"] += 1
    early = time.monotonic() < secret["bite_at"] - 0.2  # дёрнул до поклёвки — распугал рыбу
    if early or reaction > FISHING_REACTION_MS:
        return {"catch": "miss", "pay": 0, "cast": secret["cast"], "left": FISHING_CASTS - secret["cast"],
                "early": early}
    kinds = list(FISHING_CATCH)
    catch = random.choices(kinds, weights=[FISHING_CATCH[kind][0] for kind in kinds])[0]
    pay = FISHING_CATCH[catch][1]
    shift["pay"] += pay
    if catch == "golden":
        shift["shout"] = "золотая рыбка"
    return {"catch": catch, "pay": pay, "cast": secret["cast"], "left": FISHING_CASTS - secret["cast"]}


def _truck_task() -> tuple[dict, dict]:
    dropped = [random.random() < chance for _, chance in TRUCK_BOXES]
    return ({"boxes": len(TRUCK_BOXES), "values": [value for value, _ in TRUCK_BOXES],
             "risks": [risk for _, risk in TRUCK_BOXES]}, {"box": 0, "dropped": dropped})


def _truck_step(shift: dict, payload: dict) -> dict:
    secret = shift["secret"]
    box = secret["box"]
    if box >= len(TRUCK_BOXES):
        raise GameError("Фура пустая — неси деньги в кассу", 409)
    secret["box"] += 1
    if secret["dropped"][box]:
        lost, shift["pay"], shift["done"] = shift["pay"], 0, True
        return {"dropped": True, "box": box + 1, "lost": lost, "pay": 0,
                "reason": random.choice(texts.WORK_TRUCK_FAILS)}
    shift["pay"] += TRUCK_BOXES[box][0]
    if secret["box"] == len(TRUCK_BOXES):
        shift["shout"] = "вся фура"
    return {"dropped": False, "box": box + 1, "gain": TRUCK_BOXES[box][0], "pay": shift["pay"],
            "left": len(TRUCK_BOXES) - secret["box"]}


_TASKS = {"cleanup": _cleanup_task, "post": _post_task, "samsa": _samsa_task,
          "fishing": _fishing_task, "truck": _truck_task}
_PAYS = {"cleanup": _cleanup_pay, "post": _post_pay, "samsa": _samsa_pay}
_STEPS = {"fishing": _fishing_step, "truck": _truck_step}


# ── Смена ─────────────────────────────────────────────────────────────────────


def job_list(chat_id: int, user_id: int) -> list[dict]:
    return [
        dict(job, key=key, cooldown=round(limits.remaining(chat_id, user_id, f"work:{key}", COOLDOWN_SECONDS)))
        for key, job in JOBS.items()
    ]


def state(chat_id: int, user_id: int) -> dict:
    return {"jobs": job_list(chat_id, user_id), "balance": db.get_balance(chat_id, user_id),
            "summary": summary(chat_id, user_id)}


def start(chat_id: int, user_id: int, job) -> dict:
    if job not in JOBS:
        raise GameError("Такой работы нет", 404)
    wait = limits.remaining(chat_id, user_id, f"work:{job}", COOLDOWN_SECONDS)
    if wait > 0:
        raise GameError(f"Перекур: до следующей смены {limits.format_wait(wait)}", 429)
    engine.throttle(chat_id, user_id)
    task, secret = _TASKS[job]()
    _shifts[(chat_id, user_id)] = {
        "job": job, "started": time.monotonic(), "task": task, "secret": secret, "pay": 0, "done": False,
        "shout": None,
    }
    return {"job": job, "task": task}


def _current(chat_id: int, user_id: int) -> dict:
    shift = _shifts.get((chat_id, user_id))
    if shift is None:
        raise GameError("Смена не начата — жми «Начать»", 409)
    return shift


def step(chat_id: int, user_id: int, payload: dict) -> dict:
    shift = _current(chat_id, user_id)
    if shift["done"]:
        raise GameError("Смена уже закончилась", 409)
    if shift["job"] not in _STEPS:
        raise GameError("На этой работе ходов нет")
    # антиспам тут не нужен: ходов в смене ровно столько, сколько заложено в задании
    return _STEPS[shift["job"]](shift, payload if isinstance(payload, dict) else {})


def finish(chat_id: int, user_id: int, answer) -> dict:
    shift = _shifts.pop((chat_id, user_id), None)
    if shift is None:
        raise GameError("Смена не начата — жми «Начать»", 409)
    answer = answer if isinstance(answer, dict) else {}
    job = shift["job"]
    seconds = shift["task"].get("seconds")
    if seconds and time.monotonic() - shift["started"] > seconds + SHIFT_SLACK_SECONDS:
        raise GameError("Смена затянулась — начни заново", 409)

    if job in _PAYS:
        pay, detail = _PAYS[job](shift, answer)
    else:
        pay, detail = shift["pay"], []
    pay = max(0, pay)
    limits.mark(chat_id, user_id, f"work:{job}")
    balance = db.casino_settle(chat_id, user_id, "work", 0, pay, special=bool(shift["shout"]))
    name = engine.player_name(user_id)
    if shift["shout"]:
        engine.add_feed(chat_id, f"{JOBS[job]['emoji']} {name}: {shift['shout']}! {signed(pay)}")
    elif pay >= FEED_FROM:
        engine.add_feed(chat_id, f"{JOBS[job]['emoji']} {name} отпахал смену «{JOBS[job]['title']}»: {signed(pay)}")
    return {
        "job": job, "pay": pay, "detail": detail, "balance": balance,
        "praise": random.choice(texts.WORK_PRAISE if pay else texts.WORK_PITY),
        "jobs": job_list(chat_id, user_id),
    }


def summary(chat_id: int, user_id: int) -> dict:
    """Сколько заработано руками — для лобби."""
    stats = {row["game"]: row for row in db.get_casino_stats(chat_id, user_id)}
    work = stats.get("work") or {}
    return {"shifts": work.get("plays", 0), "earned": work.get("returned", 0), "money": money(work.get("returned", 0))}
