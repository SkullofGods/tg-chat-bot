"""Кто есть в чате: имена, упоминания, выбор случайного участника."""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Iterable

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Chat, ChatMemberRestricted, Message, User

from config import LOCAL_TZ
from db import parse_utc
from loader import bot, db

logger = logging.getLogger(__name__)

# Если участника проверяли через Telegram недавно — верим базе и не дёргаем API
MEMBER_CHECK_TTL = timedelta(hours=12)
_PRESENT_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}

# Кэши, чтобы не писать в базу на каждое сообщение одно и то же
_known_chats: set[int] = set()
_user_cache: dict[int, tuple[str, str, bool]] = {}
_member_cache: set[tuple[int, int]] = set()


def reset_caches():
    _known_chats.clear()
    _user_cache.clear()
    _member_cache.clear()


def is_group(chat: Chat) -> bool:
    return chat.type in ("group", "supergroup")


def is_group_id(chat_id: int) -> bool:
    return chat_id < 0  # у групп и супергрупп id всегда отрицательный


def remember_chat(chat_id: int):
    if chat_id not in _known_chats:
        db.remember_chat(chat_id)
        _known_chats.add(chat_id)


def remember_user(user: User):
    data = (user.username or "", user.full_name or "", bool(user.is_bot))
    if _user_cache.get(user.id) != data:
        db.upsert_user(user.id, *data)
        _user_cache[user.id] = data


def remember_member(chat_id: int, user_id: int):
    if (chat_id, user_id) not in _member_cache:
        db.set_member(chat_id, user_id, True)
        db.remember_member_since(chat_id, user_id, datetime.now(LOCAL_TZ).date().isoformat())  # для круглых дат
        _member_cache.add((chat_id, user_id))


def forget_member(chat_id: int, user_id: int):
    db.set_member(chat_id, user_id, False, checked=True)
    _member_cache.discard((chat_id, user_id))


# ── Имена ─────────────────────────────────────────────────────────────────────


def plain_name(user_id: int, row: dict | None = None) -> str:
    """Ник, если есть, иначе имя из Telegram. Без экранирования — для подсчётов и логов."""
    if row is None:
        row = db.get_name_rows([user_id]).get(user_id, {})
    name = (row.get("nickname") or row.get("full_name") or row.get("username") or "").strip() or "кто-то"
    badge = (row.get("badge") or "").strip()                       # значок из лавки влияния — везде
    return f"{badge} {name}" if badge else name


def titled_name(user_id: int, row: dict | None = None) -> str:
    """Имя со званием из лавки: для Форбса, рейтингов и ленты казино."""
    if row is None:
        row = db.get_name_rows([user_id]).get(user_id, {})
    title = (row.get("title") or "").strip()
    name = plain_name(user_id, row)
    return f"{name} «{title}»" if title else name


def display_name(user_id: int, row: dict | None = None) -> str:
    """Кликабельное имя для HTML. С юзернеймом — ссылка на профиль t.me, она не присылает уведомление,
    поэтому годится и для списков. Без юзернейма иначе на профиль не сослаться — будет обычное упоминание."""
    if row is None:
        row = db.get_name_rows([user_id]).get(user_id, {})
    name = escape(plain_name(user_id, row), quote=False)
    username = (row.get("username") or "").strip()
    if username:
        return f'<a href="https://t.me/{escape(username)}">{name}</a>'
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def mention(user_id: int, row: dict | None = None) -> str:
    """Упоминание: человек получит уведомление. Для тех, кого зовут лично (брак, дуэль, ивенты)."""
    if row is None:
        row = db.get_name_rows([user_id]).get(user_id, {})
    return f'<a href="tg://user?id={user_id}">{escape(plain_name(user_id, row), quote=False)}</a>'


def titled_display_name(user_id: int, row: dict | None = None) -> str:
    """Кликабельное имя со званием из лавки влияния — для /forbes в беседе."""
    if row is None:
        row = db.get_name_rows([user_id]).get(user_id, {})
    title = (row.get("title") or "").strip()
    return display_name(user_id, row) + (f" «{escape(title, quote=False)}»" if title else "")


def display_names(user_ids: Iterable[int]) -> dict[int, str]:
    ids = list(user_ids)
    rows = db.get_name_rows(ids)
    return {uid: display_name(uid, rows.get(uid, {})) for uid in ids}


def resolve_target(message: Message, args: str | None) -> tuple[int | None, str]:
    """Кого имели в виду: ответ на сообщение, упоминание или @username.
    Возвращает (user_id, что написали): user_id=None — цель не указана, -1 — не нашли в базе."""
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.forum_topic_created:
        remember_user(reply.from_user)
        return reply.from_user.id, ""
    for entity in message.entities or []:
        if entity.type == "text_mention" and entity.user:
            remember_user(entity.user)
            return entity.user.id, ""
    if args and args.strip():
        raw = args.strip().split()[0].lstrip("@")
        found = db.get_user_by_username(raw)
        if found:
            return found["user_id"], raw
        return -1, raw
    return None, ""


# ── Участники чата ────────────────────────────────────────────────────────────


async def check_member(chat_id: int, user_id: int) -> bool | None:
    """Спрашивает у Telegram, состоит ли человек в чате. None — узнать не получилось."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return None
    except TelegramBadRequest as e:
        if any(s in str(e).lower() for s in ("user not found", "participant_id_invalid", "member not found")):
            forget_member(chat_id, user_id)
            return False
        return None
    except Exception as e:  # сеть, бота выгнали и т.п. — не считаем человека ушедшим
        logger.debug("getChatMember %s/%s failed: %s", chat_id, user_id, e)
        return None

    remember_user(member.user)
    present = member.status in _PRESENT_STATUSES or (
        isinstance(member, ChatMemberRestricted) and member.is_member
    )
    if present:
        db.set_member(chat_id, user_id, True, checked=True)
        _member_cache.add((chat_id, user_id))
    else:
        forget_member(chat_id, user_id)
    return present


async def random_member(chat_id: int, exclude: Iterable[int] = (), active_days: int | None = None,
                        weights: dict[int, int] | None = None) -> int | None:
    """Случайный живой участник чата: не бот и не ушедший. active_days — предпочитать тех, кто недавно писал,
    weights — во сколько раз чаще выпадать кому-то (лоббирование в лавке влияния; тихоням оно тоже работает)."""
    excluded = set(exclude)
    weights = weights or {}
    candidates = [c for c in db.get_member_candidates(chat_id) if c["user_id"] not in excluded]
    now = datetime.now(timezone.utc)
    if active_days:
        border = now - timedelta(days=active_days)
        active = [c for c in candidates if (parse_utc(c["last_active"]) or border) > border or c["user_id"] in weights]
        candidates = active or candidates
    if weights:   # взвешенное перемешивание: первым встаёт человек с вероятностью, пропорциональной весу
        candidates.sort(key=lambda c: random.random() ** (1 / weights.get(c["user_id"], 1)), reverse=True)
    else:
        random.shuffle(candidates)

    api_checks = 0
    for candidate in candidates:
        checked_at = parse_utc(candidate["checked_at"])
        if (checked_at and now - checked_at < MEMBER_CHECK_TTL) or api_checks >= 5:
            return candidate["user_id"]
        api_checks += 1
        if await check_member(chat_id, candidate["user_id"]) is not False:
            return candidate["user_id"]
    return None


async def sync_members():
    """Фоновая сверка после запуска: кто из известных боту людей всё ещё в чате."""
    for chat_id in db.get_known_chats():
        confirmed: set[int] = set()
        try:
            admins = await bot.get_chat_administrators(chat_id)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("Не могу получить админов чата %s: %s", chat_id, e)
            continue
        except Exception as e:
            logger.warning("Сверка участников чата %s не удалась: %s", chat_id, e)
            continue
        for admin in admins:
            remember_user(admin.user)
            if not admin.user.is_bot:
                db.set_member(chat_id, admin.user.id, True, checked=True)
                _member_cache.add((chat_id, admin.user.id))
                confirmed.add(admin.user.id)

        recently = datetime.now(timezone.utc) - timedelta(hours=6)
        for row in db.get_member_rows(chat_id):
            checked_at = parse_utc(row["checked_at"])
            if row["user_id"] in confirmed or (checked_at and checked_at > recently):
                continue
            await check_member(chat_id, row["user_id"])
            await asyncio.sleep(0.3)
    logger.info("Сверка участников чатов завершена")
