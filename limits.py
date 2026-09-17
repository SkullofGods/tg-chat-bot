"""Кулдауны игр и спам-день."""

import random
import time
from datetime import date, datetime, timedelta

from aiogram.types import Message

import members
import texts
from config import GAME_COOLDOWN_MINUTES, LOCAL_TZ, SPAM_DAY_COOLDOWN_SECONDS, SPAM_DAY_HOUR
from loader import db

GAME_COOLDOWN_SECONDS = GAME_COOLDOWN_MINUTES * 60
WARN_EVERY_SECONDS = 60  # не напоминать о кулдауне чаще раза в минуту, чтобы бот сам не спамил

# Спам-день выпадает на случайный (но заранее вычислимый) день каждого двухнедельного периода,
# поэтому переживает перезапуски и не нуждается в базе
_EPOCH = date(2026, 1, 5)
_PERIOD_DAYS = 14

_last_used: dict[tuple[int, int, str], float] = {}
_last_warned: dict[tuple[int, int, str], float] = {}


# ── Спам-день ─────────────────────────────────────────────────────────────────


def _planned_day(period: int) -> date:
    offset = random.Random(f"spam-day-{period}").randint(3, 10)  # между спам-днями 8–20 дней
    return _EPOCH + timedelta(days=period * _PERIOD_DAYS + offset)


def next_spam_day(after: date) -> date:
    period = (after - _EPOCH).days // _PERIOD_DAYS
    return next(day for day in (_planned_day(p) for p in range(period, period + 3)) if day >= after)


def _override(day: date) -> str | None:
    return db.get_meta(f"spam_day:{day.isoformat()}")


def set_spam_day(day: date, enabled: bool):
    db.set_meta(f"spam_day:{day.isoformat()}", "on" if enabled else "off")


def spam_day_active(now: datetime | None = None) -> bool:
    now = now or datetime.now(LOCAL_TZ)
    override = _override(now.date())
    if override is not None:
        return override == "on"
    return _planned_day((now.date() - _EPOCH).days // _PERIOD_DAYS) == now.date() and now.hour >= SPAM_DAY_HOUR


# ── Кулдауны ──────────────────────────────────────────────────────────────────


def cooldown(normal_seconds: float = GAME_COOLDOWN_SECONDS) -> float:
    return SPAM_DAY_COOLDOWN_SECONDS if spam_day_active() else normal_seconds


def remaining(chat_id: int, user_id: int, command: str, normal_seconds: float = GAME_COOLDOWN_SECONDS) -> float:
    last = _last_used.get((chat_id, user_id, command))
    if last is None:
        return 0.0
    return max(0.0, cooldown(normal_seconds) - (time.monotonic() - last))


def mark(chat_id: int, user_id: int, command: str):
    _last_used[(chat_id, user_id, command)] = time.monotonic()


def reset():
    _last_used.clear()
    _last_warned.clear()


def active_count() -> int:
    now, limit = time.monotonic(), cooldown()
    return sum(1 for used in _last_used.values() if now - used < limit)


def format_wait(seconds: float) -> str:
    minutes, secs = divmod(int(seconds + 0.999), 60)
    return f"{minutes} мин {secs} сек" if minutes else f"{secs} сек"


async def allow(message: Message, command: str) -> bool:
    """True — можно играть. Если нельзя, иногда напоминает, сколько ждать. Использование не засчитывает."""
    chat_id, user_id = message.chat.id, message.from_user.id
    wait = remaining(chat_id, user_id, command)
    if wait <= 0:
        return True
    key = (chat_id, user_id, command)
    now = time.monotonic()
    if now - _last_warned.get(key, 0.0) >= WARN_EVERY_SECONDS:
        _last_warned[key] = now
        await message.reply(texts.COOLDOWN_TEXT.format(
            name=members.display_name(user_id),
            what=texts.COOLDOWN_WHAT.get(command, "команда"),
            wait=format_wait(wait),
        ))
    return False
