"""Кто это сказал? Настоящая фраза из беседы и четыре участника на выбор — угадай автора.

Фразы — только смешные и знаковые (собравшие реакции), и только те, что хозяин проверил и прислал боту
файлом в личку: в публичный репозиторий переписка беседы не едет. Пока файла нет, игра ждёт.
Автора знает только сервер, пока ты не ответишь, и серию угаданных подряд считает тоже он: это рекорд
в «Поиграть», так что прислать счёт из приложения нельзя. Грустное и личное (питомцы, смерть, болезни)
и ссылки отсеиваются ещё раз и здесь.
"""

import random
import re
import time

import casino_arcade as arcade
import casino_engine as engine
import members
from casino_engine import GameError
from loader import db

GAME = "quote"
SECONDS = 15              # на ответ
SLACK_SECONDS = 2         # запас на связь
OPTIONS = 4
MIN_LENGTH, MAX_LENGTH = 8, 260
TRIES = 60                # сколько случайных фраз перебрать, прежде чем сдаться

SKIP = re.compile(
    r"https?://|www\.|@\w"
    r"|\b(?:кош\w*|кот(?!ор)\w*|кис[аеиоуыя]\w*|кисик\w*|питом\w*|мурч\w*"
    r"|тромб\w*|умер\w*|умир\w*|помер\w*|похорон\w*|смерт\w*|сдох\w*|болезн\w*|болел\w*|больниц\w*"
    r"|рит[аеуыо]й?|ритк\w*)\b",
    re.IGNORECASE,
)

_sessions: dict[tuple[int, int], dict] = {}


def usable(text: str) -> bool:
    text = text.strip()
    return MIN_LENGTH <= len(text) <= MAX_LENGTH and not text.startswith("/") and not SKIP.search(text)


def _question(chat_id: int, session: dict) -> dict:
    authors = db.quote_authors(chat_id)
    if len(authors) < OPTIONS:
        raise GameError("Фразы для игры ещё на проверке — скоро вернёмся", 409)
    for _ in range(TRIES):
        phrase = db.random_quote(chat_id)
        if phrase is None:
            break
        if phrase["id"] in session["seen"] or not usable(phrase["text"]):
            continue
        author = phrase["user_id"]
        options = [author] + random.sample([uid for uid in authors if uid != author], OPTIONS - 1)
        random.shuffle(options)
        session["seen"].add(phrase["id"])
        session.update(answer=author, options=options, asked=time.monotonic())
        names = db.get_name_rows(options)
        return {
            "text": phrase["text"].strip(),
            "options": [{"id": uid, "name": members.plain_name(uid, names.get(uid, {}))} for uid in options],
            "seconds": SECONDS,
            "streak": session["streak"],
        }
    raise GameError("Фразы закончились — ты угадал(а) всё", 409)


def start(chat_id: int, user_id: int) -> dict:
    engine.throttle(chat_id, user_id)
    session = {"streak": 0, "seen": set(), "answer": None, "options": [], "asked": 0.0}
    _sessions[(chat_id, user_id)] = session
    return {"question": _question(chat_id, session), "streak": 0}


def answer(chat_id: int, user_id: int, choice) -> dict:
    session = _sessions.get((chat_id, user_id))
    if session is None or session["answer"] is None:
        raise GameError("Игра не начата", 409)
    if choice is not None and (isinstance(choice, bool) or not isinstance(choice, int)
                               or choice not in session["options"]):
        raise GameError("Выбери одного из четырёх")                # None — время вышло, так и не выбрал
    engine.throttle(chat_id, user_id)
    right = session["answer"]
    late = time.monotonic() - session["asked"] > SECONDS + SLACK_SECONDS
    right_view = {"id": right, "name": members.plain_name(right)}
    if choice == right and not late:
        session["streak"] += 1
        if session["streak"] < arcade.GAMES[GAME]["max"]:
            try:
                return {"correct": True, "right": right_view, "streak": session["streak"],
                        "question": _question(chat_id, session)}
            except GameError:
                pass                                              # фразы кончились — серия засчитывается
    _sessions.pop((chat_id, user_id), None)                       # серия кончилась — рекорд считает сервер
    return arcade.record(chat_id, user_id, GAME, session["streak"]) | {
        "correct": choice == right and not late, "late": late, "right": right_view, "streak": session["streak"],
    }
