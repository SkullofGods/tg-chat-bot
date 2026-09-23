"""Мультиплеер: столы, за которыми играют друг с другом, а не с казино.

Стол — строка в базе: кто сидит, сколько в банке и всё состояние партии (JSON). Поэтому партия
переживает перезапуск бота и деплой: вернулся через час — стол на месте, ход ждёт.

Правила самих игр лежат в пакете mp_games, каждая игра — отдельный файл с одинаковым набором
функций (setup/view/move/auto/result). Движок ничего не знает про карты и доски: он рассаживает
людей, держит ставки, следит за временем хода и раздаёт выигрыш.

Деньги: ставка списывается при посадке за стол и лежит в банке стола. Победители забирают банк,
если стол разошёлся — ставки возвращаются.
"""

import asyncio
import logging
import math
import random
import time

import casino_engine as engine
import members
import mp_games
from casino_engine import GameError, money
from loader import db
from textstats import count_with_word

logger = logging.getLogger(__name__)

STAKES = (0, 100, 500, 1000)      # во что можно играть: интерес или деньги
GATHER_SECONDS = 10 * 60          # столько стол ждёт игроков, потом расходится
ANNOUNCE_FROM = 3                 # о столах на столько мест и больше пишем в беседу
KEEP_DONE_SECONDS = 24 * 3600     # сыгранные столы держим сутки — для «последних партий»
PLAYER_FORMS = ("игрок", "игрока", "игроков")


def _now() -> int:
    return int(time.time())


def _game(key: str):
    module = mp_games.GAMES.get(key)
    if module is None:
        raise GameError("Такой игры нет", 404)
    return module


def _seat_names(seats: list[dict]) -> dict[int, dict]:
    return db.get_name_rows([seat["user_id"] for seat in seats])


def _my_table(chat_id: int, user_id: int) -> dict | None:
    """Стол, за которым игрок сидит прямо сейчас. Вставшие из-за стола не считаются."""
    for table in db.mp_live(chat_id):
        if any(seat["user_id"] == user_id and not seat.get("out") for seat in table["seats"]):
            return table
    return None


def _card(table: dict, user_id: int, names: dict | None = None) -> dict:
    """Короткая карточка стола для лобби."""
    module = mp_games.GAMES.get(table["game"])
    names = names or _seat_names(table["seats"])
    return {
        "id": table["id"],
        "game": table["game"],
        "title": module.TITLE if module else table["game"],
        "emoji": module.EMOJI if module else "🎲",
        "stake": table["stake"],
        "pot": table["pot"],
        "status": table["status"],
        "seats": [{"id": seat["user_id"], "name": members.plain_name(seat["user_id"], names.get(seat["user_id"], {})),
                   "me": seat["user_id"] == user_id, "out": seat.get("out", False)} for seat in table["seats"]],
        "max_seats": table["state"].get("max_seats", module.MAX_SEATS if module else 0),
        "mine": any(seat["user_id"] == user_id for seat in table["seats"]),
        "waits": max(0, table["created_at"] + GATHER_SECONDS - _now()) if table["status"] == "gathering" else 0,
    }


def lobby(chat_id: int, user_id: int) -> dict:
    live = db.mp_live(chat_id)
    names = db.get_name_rows([seat["user_id"] for table in live for seat in table["seats"]])
    mine = _my_table(chat_id, user_id)
    return {
        "games": [
            {"key": key, "title": module.TITLE, "emoji": module.EMOJI, "about": module.ABOUT,
             "min": module.MIN_SEATS, "max": module.MAX_SEATS, "pace": module.PACE,
             "turn": module.TURN_SECONDS}
            for key, module in mp_games.GAMES.items()
        ],
        "tables": [_card(table, user_id, names) for table in live],
        "mine": mine["id"] if mine else None,
        "stakes": list(STAKES),
        "balance": db.get_balance(chat_id, user_id),
        "leaders": _leaders(chat_id, user_id),
        "history": [
            {"game": table["game"], "title": mp_games.GAMES[table["game"]].TITLE
             if table["game"] in mp_games.GAMES else table["game"],
             "text": table["state"].get("over", {}).get("text", "")}
            for table in db.mp_done(chat_id, 5) if table["state"].get("over")
        ],
    }


def _leaders(chat_id: int, user_id: int) -> list[dict]:
    """Кто чаще выигрывает за столами: победы, сыгранные партии и любимая игра."""
    rows = db.counter_top(chat_id, "mp:wins", 5)
    names = db.get_name_rows([row["user_id"] for row in rows])
    out = []
    for row in rows:
        counters = db.get_counters(chat_id, row["user_id"])
        wins = {key[len("mp:win:"):]: value for key, value in counters.items() if key.startswith("mp:win:")}
        best = max(wins, key=lambda key: wins[key], default=None)
        module = mp_games.GAMES.get(best)
        # партии считаем по каждой игре: общий счётчик появился позже, у старых игроков его нет
        played = sum(value for key, value in counters.items()
                     if key.startswith("mp:") and key.split(":", 2)[1] in mp_games.GAMES)
        out.append({
            "name": members.plain_name(row["user_id"], names.get(row["user_id"], {})),
            "wins": row["value"],
            "games": max(played, row["value"]),
            "best": f"{module.EMOJI} {module.TITLE}" if module else None,
            "me": row["user_id"] == user_id,
        })
    return out


def create(chat_id: int, user_id: int, game, stake, seats) -> dict:
    module = _game(game if isinstance(game, str) else "")
    if isinstance(stake, bool) or not isinstance(stake, int) or stake not in STAKES:
        raise GameError("Такой ставки нет")
    if isinstance(seats, bool) or not isinstance(seats, int) or not module.MIN_SEATS <= seats <= module.MAX_SEATS:
        raise GameError(f"За этот стол садятся от {module.MIN_SEATS} до {module.MAX_SEATS}")
    if _my_table(chat_id, user_id):
        raise GameError("Ты уже за столом — сначала выйди", 409)
    engine.throttle(chat_id, user_id)
    if stake and not db.take_bet(chat_id, user_id, stake):
        raise engine.not_enough_money(chat_id, user_id)
    table_id = db.mp_create(chat_id, user_id, game, stake,
                            [{"user_id": user_id}], {"max_seats": seats})
    db.mp_save(table_id, pot=stake)
    logger.info("Стол %s: %s собирает %s на %s мест, ставка %s", table_id, user_id, game, seats, stake)
    return table_view(chat_id, user_id, table_id)


def join(chat_id: int, user_id: int, table_id) -> dict:
    table = _table(table_id, chat_id)
    module = _game(table["game"])
    if any(seat["user_id"] == user_id for seat in table["seats"]):
        return table_view(chat_id, user_id, table_id)
    if _my_table(chat_id, user_id):
        raise GameError("Ты уже за другим столом", 409)
    if table["status"] != "gathering":
        raise GameError("Партия уже идёт — дождись следующей", 409)
    if len(table["seats"]) >= table["state"].get("max_seats", module.MAX_SEATS):
        raise GameError("Мест больше нет", 409)
    engine.throttle(chat_id, user_id)
    if table["stake"] and not db.take_bet(chat_id, user_id, table["stake"]):
        raise engine.not_enough_money(chat_id, user_id)
    seats = table["seats"] + [{"user_id": user_id}]
    db.mp_save(table_id, seats=seats, pot=table["pot"] + table["stake"])
    if len(seats) >= table["state"].get("max_seats", module.MAX_SEATS):
        _begin(table_id)
    return table_view(chat_id, user_id, table_id)


def leave(chat_id: int, user_id: int, table_id) -> dict:
    table = _table(table_id, chat_id)
    seat = next((seat for seat in table["seats"] if seat["user_id"] == user_id), None)
    if seat is None:
        raise GameError("Ты не за этим столом", 409)
    if table["status"] == "playing":
        move(chat_id, user_id, table_id, {"type": "resign"})
        fresh = db.mp_get(table_id)
        if fresh and fresh["status"] != "done":   # в играх, которые идут дальше без него, место освобождаем
            db.mp_save(table_id, seats=[seat | {"out": True} if seat["user_id"] == user_id else seat
                                        for seat in fresh["seats"]])
        return lobby(chat_id, user_id)
    seats = [row for row in table["seats"] if row["user_id"] != user_id]
    if table["stake"]:
        db.add_money(chat_id, user_id, table["stake"])
    if not seats:
        db.mp_save(table_id, seats=seats, pot=0, status="done",
                   state=table["state"] | {"over": {"text": "Стол разошёлся"}})
        return lobby(chat_id, user_id)
    db.mp_save(table_id, seats=seats, pot=max(0, table["pot"] - table["stake"]),
               owner_id=seats[0]["user_id"])
    return lobby(chat_id, user_id)


def start(chat_id: int, user_id: int, table_id) -> dict:
    """Владелец начинает партию, не дожидаясь полного стола."""
    table = _table(table_id, chat_id)
    module = _game(table["game"])
    if table["owner_id"] != user_id:
        raise GameError("Начать может только тот, кто собрал стол", 403)
    if table["status"] != "gathering":
        raise GameError("Партия уже идёт", 409)
    if len(table["seats"]) < module.MIN_SEATS:
        raise GameError(f"Нужно хотя бы {module.MIN_SEATS} игрока", 409)
    _begin(table_id)
    return table_view(chat_id, user_id, table_id)


def _begin(table_id: int):
    table = db.mp_get(table_id)
    module = _game(table["game"])
    players = [seat["user_id"] for seat in table["seats"]]
    random.shuffle(players)                     # место за столом решает случай, а не кто первый сел
    state = table["state"] | module.setup(players)
    turn_id = module.turn(state)
    db.mp_save(table_id, status="playing", state=state, turn_id=turn_id,
               turn_until=_wake(module, state, turn_id),
               seats=[{"user_id": user_id} for user_id in players])
    engine.add_feed(table["chat_id"], f"{module.EMOJI} За стол «{module.TITLE}» сели "
                                      f"{count_with_word(len(players), PLAYER_FORMS)}")
    logger.info("Стол %s: партия началась, игроки %s", table_id, players)


def _table(table_id, chat_id: int) -> dict:
    if isinstance(table_id, bool) or not isinstance(table_id, int):
        raise GameError("Кривой номер стола")
    table = db.mp_get(table_id)
    if table is None or table["chat_id"] != chat_id:
        raise GameError("Стол не найден", 404)
    return table


def table_view(chat_id: int, user_id: int, table_id) -> dict:
    table = _table(table_id, chat_id)
    module = _game(table["game"])
    names = _seat_names(table["seats"])
    view = _card(table, user_id, names)
    state = table["state"]
    seats_out = {seat["user_id"] for seat in table["seats"] if seat.get("out")}
    view["owner"] = table["owner_id"] == user_id
    view["turn"] = table["turn_id"]
    view["turn_name"] = members.plain_name(table["turn_id"], names.get(table["turn_id"], {})) \
        if table["turn_id"] else None
    view["turn_left"] = max(0, (table["turn_until"] or 0) - _now()) if table["status"] == "playing" else 0
    view["my_turn"] = table["turn_id"] == user_id and table["status"] == "playing"
    view["about"] = module.ABOUT
    view["over"] = state.get("over")
    view["out"] = user_id in seats_out
    if table["status"] == "playing" or state.get("over"):
        view["play"] = module.view(state, user_id)
        view["moves"] = module.moves(state, user_id) if hasattr(module, "moves") else {}
    view["feed"] = state.get("feed", [])[-8:]
    view["balance"] = db.get_balance(chat_id, user_id)
    return view


def move(chat_id: int, user_id: int, table_id, action) -> dict:
    table = _table(table_id, chat_id)
    module = _game(table["game"])
    if table["status"] != "playing":
        raise GameError("Партия не идёт", 409)
    if not any(seat["user_id"] == user_id for seat in table["seats"]):
        raise GameError("Ты не за этим столом", 403)
    if not isinstance(action, dict):
        raise GameError("Непонятный ход")
    whose = module.turn(table["state"])
    if action.get("type") != "resign" and whose is not None and whose != user_id:
        raise GameError("Сейчас не твой ход", 409)
    state = module.move(table["state"], user_id, action)
    _after_move(table, module, state)
    return table_view(chat_id, user_id, table_id)


def _after_move(table: dict, module, state: dict):
    """Сохраняет новое состояние, двигает таймер хода и, если партия кончилась, платит."""
    over = module.result(state)
    if over:
        state = state | {"over": over}
        _payout(table, module, over)
        db.mp_save(table["id"], state=state, status="done", turn_id=None, turn_until=None, pot=0)
        return
    turn_id = module.turn(state)
    db.mp_save(table["id"], state=state, turn_id=turn_id, turn_until=_wake(module, state, turn_id))


def _wake(module, state: dict, turn_id) -> int | None:
    """Когда столу снова понадобится движок: ход просрочен или пора тикать часам стола."""
    if turn_id:
        return _now() + module.TURN_SECONDS
    if hasattr(module, "clock"):
        return _now() + getattr(module, "CLOCK_SECONDS", 2)
    return None


def _payout(table: dict, module, over: dict):
    """Банк уходит победителям. shares — если игра делит банк не поровну."""
    pot = table["pot"]
    winners = over.get("winners") or []
    shares = over.get("shares")
    names = _seat_names(table["seats"])
    lines = []
    for seat in table["seats"]:
        user_id = seat["user_id"]
        if shares:
            payout = int(shares.get(str(user_id), shares.get(user_id, 0)))
        elif winners:
            payout = pot // len(winners) if user_id in winners else 0
        else:
            payout = table["stake"]           # ничья — ставки по домам
        if payout:
            db.casino_settle(table["chat_id"], user_id, table["game"], table["stake"], payout)
        elif table["stake"]:
            db.casino_settle(table["chat_id"], user_id, table["game"], table["stake"], 0)
        db.bump_counter(table["chat_id"], user_id, f"mp:{table['game']}")
        db.bump_counter(table["chat_id"], user_id, "mp:games")
        if user_id in winners:
            db.bump_counter(table["chat_id"], user_id, "mp:wins")
            db.bump_counter(table["chat_id"], user_id, f"mp:win:{table['game']}")
            lines.append(members.plain_name(user_id, names.get(user_id, {})))
    if lines:
        prize = f" и забирает {money(pot)}" if pot else ""
        engine.add_feed(table["chat_id"], f"{module.EMOJI} {', '.join(lines)} выигрывает "
                                          f"в «{module.TITLE}»{prize}")
    logger.info("Стол %s закончен: %s, банк %s", table["id"], over.get("text"), pot)


def timeout(table: dict) -> bool:
    """Время вышло: ходим за игрока сами или двигаем часы стола. True — со столом что-то сделали."""
    module = _game(table["game"])
    user_id = module.turn(table["state"])
    if user_id is None:
        if not hasattr(module, "clock"):
            return False
        state = module.clock(table["state"], _now())   # часы стола: бочонки, отсчёт, автораздача
        _after_move(table, module, state)
        return True
    try:
        state = module.move(table["state"], user_id, module.auto(table["state"], user_id))
    except GameError:
        state = module.move(table["state"], user_id, {"type": "resign"})
    _after_move(table, module, state)
    return True


def cancel(table: dict, reason: str):
    """Стол расходится: ставки возвращаются всем, кто успел сесть."""
    for seat in table["seats"]:
        if table["stake"]:
            db.add_money(table["chat_id"], seat["user_id"], table["stake"])
    db.mp_save(table["id"], status="done", pot=0,
               state=table["state"] | {"over": {"text": reason, "winners": []}})


def tick() -> int:
    """Фоновая работа: просроченные ходы и столы, которые никто не собрал. Сколько тронули."""
    touched = 0
    now = _now()
    for table in db.mp_due(now):
        try:
            touched += bool(timeout(table))
        except Exception:
            logger.exception("Не смог доиграть за просрочившего ход, стол %s", table["id"])
    for table in db.mp_gathering():
        if now - table["created_at"] < GATHER_SECONDS:
            continue
        module = mp_games.GAMES.get(table["game"])
        if module and len(table["seats"]) >= module.MIN_SEATS:
            _begin(table["id"])
        else:
            cancel(table, "Стол так и не собрался — ставки вернулись")
        touched += 1
    return touched


def chat_guess(chat_id: int, user_id: int, text: str) -> dict | None:
    """Сообщение из беседы — вдруг им угадали слово в крокодиле."""
    for table in db.mp_live(chat_id):
        if table["status"] != "playing":
            continue
        module = mp_games.GAMES.get(table["game"])
        if module is None or not hasattr(module, "guess"):
            continue
        if not any(seat["user_id"] == user_id for seat in table["seats"]):
            continue
        state = module.guess(table["state"], user_id, text)
        if state is None:
            continue
        word = (state.get("last") or {}).get("word", "")
        _after_move(table, module, state)
        name = members.mention(user_id)
        if state.get("winner") == user_id:
            prize = f" и забирает {money(table['pot'])}" if table["pot"] else ""
            return {"text": f"🐊 {name} угадал «{word}» и выигрывает крокодила{prize}!"}
        return {"text": f"🐊 {name} угадал слово «{word}» — теперь объясняет он"}
    return None


async def announce():
    """Пишет в беседу про столы, которые собираются. Один раз на стол."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    import webserver
    from loader import bot

    me = None
    for chat_id in db.get_known_chats():
        for table in waiting_announcement(chat_id):
            module = mp_games.GAMES.get(table["game"])
            if module is None:
                continue
            me = me or await bot.me()
            seats = table["state"].get("max_seats", module.MAX_SEATS)
            stake = f"вход {money(table['stake'])}" if table["stake"] else "за респект"
            text = "\n".join([
                f"{module.EMOJI} <b>Собирается стол: {module.TITLE}</b>",
                f"{len(table['seats'])} из {seats} · {stake}",
                f"<i>{module.ABOUT}</i>",
            ])
            button = InlineKeyboardButton(text="🎮 Сесть за стол",
                                          url=webserver.app_link(me.username, chat_id))
            try:
                sent = await bot.send_message(chat_id, text,
                                              reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]))
            except Exception as e:
                logger.warning("Не смог объявить стол %s: %s", table["id"], e)
                db.mp_save(table["id"], message_id=0)     # чтобы не долбиться повторно
                continue
            db.mp_save(table["id"], message_id=sent.message_id)


async def loop():
    """Фоновая жизнь столов: ходы по таймауту, часы игр и объявления в беседу."""
    last_prune = 0.0
    while True:
        try:
            tick()
            await announce()
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                db.mp_prune(_now() - KEEP_DONE_SECONDS)
        except Exception:
            logger.exception("Столы мультиплеера споткнулись")
        await asyncio.sleep(2)


def waiting_announcement(chat_id: int) -> list[dict]:
    """Столы, о которых стоит написать в беседу: собираются, мест хватает, ещё не объявляли."""
    return [table for table in db.mp_live(chat_id)
            if table["status"] == "gathering" and table["message_id"] is None
            and (table["stake"] or table["state"].get("max_seats", 2) >= ANNOUNCE_FROM)]


def format_wait(seconds: float) -> str:
    minutes = math.ceil(seconds / 60)
    return f"{minutes} мин" if minutes < 60 else f"{minutes // 60} ч {minutes % 60} мин"
