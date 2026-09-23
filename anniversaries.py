"""Круглые даты в беседе: сколько дней человек с нами и сколько самой беседе.

Когда кто впервые появился в беседе, взято из экспорта истории (FIRST_SEEN — только id и даты: ни имён,
ни сообщений; сам экспорт в репозиторий не едет). Новых людей бот запоминает сам — с первого сообщения
или входа в беседу (members.remember_member).

Поздравляем на круглые даты: каждые сто дней (100, 200, 300…) и каждый год. Беседа родилась 29 мая 2026,
так что годовщины в годах начнутся только в 2027-м, а сотни дней идут уже сейчас. Праздник, который бот
проспал (был выключен или функции ещё не было), догоняем в течение месяца — одним сообщением.
За праздник дарим таджикоины: имениннику — за каждый день в беседе, а на праздник беседы — всем.
"""

import logging
import random
from datetime import date, datetime, timedelta

import casino_engine as engine
import members
import texts
from casino_engine import money
from config import LOCAL_TZ
from loader import db
from textstats import count_with_word, fmt_num

logger = logging.getLogger(__name__)

EXPORT_CHAT_ID = -1003706796442
CHAT_BORN = {EXPORT_CHAT_ID: date(2026, 5, 29)}    # create_group в экспорте
# Первое появление в беседе (сообщение или вход по ссылке) по экспорту от 23.09.2026, время московское
FIRST_SEEN = {
    6713306755: "2026-05-29", 384144294: "2026-05-29", 5267489338: "2026-05-29",
    1725285716: "2026-05-29", 1726230208: "2026-05-29", 718224155: "2026-05-29",
    1901020622: "2026-05-29", 811776057: "2026-05-29", 333399271: "2026-05-29",
    7087538683: "2026-05-29", 8679872037: "2026-06-01", 261640336: "2026-06-02",
    487620054: "2026-06-03", 7135493722: "2026-06-04", 510279599: "2026-06-23",
    8459807357: "2026-06-28", 1470330556: "2026-07-19", 877108496: "2026-07-20",
    1473334202: "2026-07-20", 6913664845: "2026-07-21", 6448494367: "2026-08-05",
    1270216675: "2026-08-10",
}

GREET_HOUR = 12              # поздравляем после полудня
MEMBER_GIFT_PER_DAY = 10     # сто дней в беседе — 1 000 таджикоинов
CHAT_GIFT_PER_DAY = 5        # беседе сто дней — каждому участнику по 500
CATCH_UP_DAYS = 30           # проспанный праздник догоняем, если он был не раньше месяца назад


def seed():
    """Даты из экспорта — в базу. Кого бот уже знает, не трогаем."""
    db.seed_member_since(EXPORT_CHAT_ID, FIRST_SEEN)


def remember(chat_id: int, user_id: int):
    """Новый человек в беседе: с сегодняшнего дня и считаем. Старожилов не перезаписывает."""
    db.remember_member_since(chat_id, user_id, datetime.now(LOCAL_TZ).date().isoformat())


def milestone(first: date, day: date) -> tuple[str, int] | None:
    """Круглая ли дата в этот день: ("years", лет) или ("days", дней)."""
    years = day.year - first.year
    if years >= 1 and (day.month, day.day) == (first.month, first.day):
        return "years", years
    days = (day - first).days
    if days >= 100 and days % 100 == 0:
        return "days", days
    return None


def latest_milestone(first: date, today: date) -> tuple[date, str, int] | None:
    """Последняя круглая дата за CATCH_UP_DAYS дней, сегодня включительно."""
    for back in range(CATCH_UP_DAYS + 1):
        day = today - timedelta(days=back)
        if day <= first:
            return None
        found = milestone(first, day)
        if found:
            return (day, *found)
    return None


def _what(kind: str, value: int) -> str:
    if kind == "years":
        return f"{count_with_word(value, ('год', 'года', 'лет'))}"
    return f"{fmt_num(value)} дней"


def celebrate(chat_id: int, today: date) -> str | None:
    """Раздаёт подарки на сегодняшние (и недавно проспанные) праздники. Возвращает текст для беседы."""
    lines, late = [], False
    for row in db.members_since(chat_id):                  # только те, кто сейчас в беседе
        since = date.fromisoformat(row["since"])
        found = latest_milestone(since, today)
        if not found:
            continue
        day, kind, value = found
        key = f"anniversary:{chat_id}:{row['user_id']}:{kind}:{value}"
        if db.get_meta(key):
            continue
        db.set_meta(key, day.isoformat(), important=True)   # до подарка: упадём на полпути — не подарим дважды
        gift = MEMBER_GIFT_PER_DAY * (day - since).days
        db.add_money(chat_id, row["user_id"], gift)
        db.bump_counter(chat_id, row["user_id"], "anniversary")
        late = late or day < today
        lines.append(texts.ANNIVERSARY_LINE.format(
            name=members.display_name(row["user_id"]), what=_what(kind, value), gift=money(gift)))
        engine.add_feed(chat_id, f"🎂 {engine.player_name(row['user_id'])} — {_what(kind, value)} в беседе: "
                                 f"+{money(gift)}")

    chat_line = None
    born = CHAT_BORN.get(chat_id)
    found = latest_milestone(born, today) if born else None
    if found:
        day, kind, value = found
        key = f"anniversary:{chat_id}:chat:{kind}:{value}"
        if not db.get_meta(key):
            db.set_meta(key, day.isoformat(), important=True)
            gift = CHAT_GIFT_PER_DAY * (day - born).days
            people = [row["user_id"] for row in db.get_member_candidates(chat_id)]
            for user_id in people:
                db.add_money(chat_id, user_id, gift)
            late = late or day < today
            chat_line = texts.ANNIVERSARY_CHAT.format(what=_what(kind, value), gift=money(gift))
            engine.add_feed(chat_id, f"🎂 Беседе {_what(kind, value)}: всем по +{money(gift)}")

    if not lines and not chat_line:
        return None
    parts = [texts.ANNIVERSARY_LATE if late else texts.ANNIVERSARY_TODAY]
    if chat_line:
        parts.append(chat_line)
    if lines:
        parts.append("\n".join(lines))
    parts.append(random.choice(texts.ANNIVERSARY_JOKES))
    parts.append(texts.ANNIVERSARY_FOOTER)
    logger.info("Круглые даты в %s: %s человек%s", chat_id, len(lines), ", и у беседы" if chat_line else "")
    return "\n\n".join(parts)


def due(now: datetime) -> bool:
    return now.hour >= GREET_HOUR
