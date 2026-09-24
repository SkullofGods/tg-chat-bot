"""Лавка влияния: таджикоины на то, что видно в беседе.

Всё покупается в приложении — новых команд в чате нет. Что уходит в беседу, подписано покупателем.
Сорвалось в Telegram (нет прав, беседа недоступна) — деньги возвращаются.
Благословение d20 и лоббирование таджика дня — секрет: вскрываются, когда сработают.
"""

import asyncio
import json
import logging
import random
import time
import unicodedata
from contextlib import suppress
from datetime import datetime, timedelta
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import casino_engine as engine
import members
import sglypa
import texts
from casino_engine import GameError, money
from config import LOCAL_TZ
from loader import bot, db
from utils import local_today

logger = logging.getLogger(__name__)

RAIN_MIN = 10_000
RAIN_PEOPLE = (3, 10)          # сколько человек ловят дождь: от и до
RAIN_SECONDS = 10 * 60         # кто не успел за десять минут — доля возвращается хозяину
PIN_SECONDS = 3600
TEXT_LIMIT = 200
TITLE_LIMIT = 20
D20_DELTA = 3
TAJIK_LOBBY_WEIGHT = 3       # пролоббированного выбирают таджиком дня втрое чаще, каждый следующий лоббист — +2
RIGHTS_CACHE_SECONDS = 300
PINS_KEY = "shop_pins"         # закрепы помнит база: деплой посреди часа не оставит их висеть навсегда

# ключ, значок, название, цена, что вводит покупатель, пояснение
ITEMS = (
    ("announce", "📢", "Объявление", 30_000, "text",
     "Бот напишет твой текст в беседу — с подписью, от кого. До 200 символов"),
    ("toast", "🥂", "Тост", 30_000, "member", "Бот торжественно поднимет бокал за выбранного человека"),
    ("prophecy", "🔮", "Пророчество сглыпы", 15_000, "member",
     "Сглыпа выдаст в беседу пророчество про выбранного человека"),
    ("pin", "📌", "Закреп на час", 50_000, "text", "Твой текст час провисит в закрепе беседы — тихо, без уведомления"),
    ("title", "🎖", "Звание", 75_000, "title", "Подпись рядом с именем в Форбсе, рейтингах и ленте казино"),
    ("badge", "✨", "Значок у имени", 100_000, "badge", "Эмодзи перед твоим именем во всех сообщениях бота и в приложении"),
    ("rain", "💸", "Дождь из таджикоинов", RAIN_MIN, "rain",
     "В беседе появится кнопка — первые нажавшие поровну делят сумму"),
    ("d20", "🎲", "Благословение или проклятие", 25_000, "member_sign",
     "Следующий бросок d20 человека получит +3 или −3. Кто заплатил — узнают только в момент броска"),
    ("tajik", "👑", "Лоббирование таджика дня", 50_000, "member",
     "Шанс человека стать следующим таджиком дня — втрое выше (заносят несколько — ещё выше). "
     "Держим в секрете до выбора"),
    ("event", "🇹🇯", "Событие Таджикистана", 50_000, "member", "Лорное событие про выбранного человека — прямо сейчас"),
)
ITEM = {item[0]: item for item in ITEMS}
COOLDOWNS = {"announce": 3600, "toast": 600, "prophecy": 300, "event": 600}   # на беседу, секунд
SECRET = {"d20", "tajik"}      # о них лента молчит: вскроются, когда сработают

_last: dict[tuple[int, str], float] = {}
_pins: dict[int, tuple[int, float]] = {}                  # беседа → (закреплённое сообщение, когда снять)
_rights: dict[int, tuple[bool, float]] = {}


def _now() -> float:
    return time.time()


def _wait(chat_id: int, key: str) -> int:
    """Сколько секунд ещё ждать, пока товар снова можно купить в этой беседе."""
    if key == "pin" and chat_id in _pins:
        return max(0, int(_pins[chat_id][1] - _now()))
    cooldown = COOLDOWNS.get(key)
    if not cooldown:
        return 0
    return max(0, int(_last.get((chat_id, key), 0) + cooldown - _now()))


async def _can_pin(chat_id: int) -> bool:
    known = _rights.get(chat_id)
    if known and _now() - known[1] < RIGHTS_CACHE_SECONDS:
        return known[0]
    try:
        me = await bot.get_chat_member(chat_id, (await bot.me()).id)
        allowed = me.status == "creator" or bool(getattr(me, "can_pin_messages", False))
    except Exception as e:
        logger.warning("Не узнал права бота в %s: %s", chat_id, e)
        allowed = False
    _rights[chat_id] = (allowed, _now())
    return allowed


async def state(chat_id: int, user_id: int, story: str | None = None) -> dict:
    can_pin = await _can_pin(chat_id)
    people = db.get_member_candidates(chat_id)
    names = db.get_name_rows(row["user_id"] for row in people)
    mark = db.get_name_mark(user_id)
    return {
        "items": [
            {"key": key, "emoji": emoji, "title": title, "price": price, "input": kind, "about": about,
             "wait": _wait(chat_id, key), "disabled": "Боту нужно право закреплять сообщения" if key == "pin"
             and not can_pin else None}
            for key, emoji, title, price, kind, about in ITEMS
        ],
        "members": sorted(
            ({"id": row["user_id"], "name": members.plain_name(row["user_id"], names.get(row["user_id"], {}))}
             for row in people),
            key=lambda person: person["name"].lower(),
        ),
        "mine": {"badge": mark["badge"], "title": mark["title"]},
        "rain": {"min": RAIN_MIN, "people": list(RAIN_PEOPLE)},
        "limits": {"text": TEXT_LIMIT, "title": TITLE_LIMIT, "d20": D20_DELTA},
        "balance": db.get_balance(chat_id, user_id),
        "story": story,
    }


# ── Проверки того, что ввёл покупатель ────────────────────────────────────────


def _text(value, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameError("Напиши текст")
    text = " ".join(value.split()) if limit <= TITLE_LIMIT else value.strip()
    if len(text) > limit:
        raise GameError(f"Слишком длинно: не больше {limit} символов")
    return text


def _badge(value) -> str:
    """Один-два эмодзи: без букв, цифр и пробелов."""
    if not isinstance(value, str) or not value.strip():
        raise GameError("Выбери эмодзи")
    value = value.strip()
    symbols = [ch for ch in value if unicodedata.category(ch) == "So"]
    helpers = {"‍", "️", "⃣"}                    # склейка, вариант, клавиша
    if not symbols or len(value) > 10 or any(
            unicodedata.category(ch) not in ("So", "Sk", "Mn") and ch not in helpers for ch in value):
        raise GameError("Значок — это эмодзи, без букв и цифр")
    return value


def _member(chat_id: int, value) -> int:
    """Живой человек из беседы: не бот и не ушедший."""
    if isinstance(value, bool) or not isinstance(value, int) \
            or value not in {row["user_id"] for row in db.get_member_candidates(chat_id)}:
        raise GameError("Выбери человека из беседы")
    return value


# ── Покупка ───────────────────────────────────────────────────────────────────


async def buy(chat_id: int, user_id: int, body: dict) -> dict:
    key = body.get("item")
    if key not in ITEM:
        raise GameError("Такого в лавке нет", 404)
    _, emoji, title, price, kind, _ = ITEM[key]
    wait = _wait(chat_id, key)
    if wait:
        raise GameError(f"{emoji} Сейчас нельзя — ещё {max(1, wait // 60)} мин", 429)
    # сначала всё проверяем, потом берём деньги
    target = _member(chat_id, body.get("target")) if kind in ("member", "member_sign") else None
    text = _text(body.get("text"), TEXT_LIMIT) if kind == "text" else None
    sign = body.get("sign")
    if kind == "member_sign" and sign not in (1, -1):
        raise GameError("Благословить или проклясть?")
    if key == "pin" and not await _can_pin(chat_id):
        raise GameError("Боту нужно право закреплять сообщения в беседе", 409)
    if key == "rain":
        price = body.get("amount")
        people = body.get("people")
        if isinstance(price, bool) or not isinstance(price, int) or price < RAIN_MIN:
            raise GameError(f"Дождь — от {money(RAIN_MIN)}")
        if isinstance(people, bool) or not isinstance(people, int) or not RAIN_PEOPLE[0] <= people <= RAIN_PEOPLE[1]:
            raise GameError(f"Ловцов — от {RAIN_PEOPLE[0]} до {RAIN_PEOPLE[1]}")
    value = _text(body.get("text"), TITLE_LIMIT) if kind == "title" else _badge(body.get("text")) if kind == "badge" else None
    engine.throttle(chat_id, user_id)
    if not db.spend(chat_id, user_id, price, f"shop:{key}"):
        raise engine.not_enough_money(chat_id, user_id)

    buyer = members.display_name(user_id)
    try:
        story = await _deliver(chat_id, user_id, key, buyer, target, text, sign, value, price, body.get("people"))
    except GameError:
        db.add_money(chat_id, user_id, price)
        raise
    except Exception:
        logger.exception("Лавка: «%s» не доставилась в %s", key, chat_id)
        db.add_money(chat_id, user_id, price)
        raise GameError("Telegram не принял — деньги вернулись", 502)
    if key in COOLDOWNS:
        _last[(chat_id, key)] = _now()
    if key not in SECRET:
        engine.add_feed(chat_id, f"🛍 {engine.player_name(user_id)} покупает в лавке: {emoji} {title.lower()}")
    return await state(chat_id, user_id, story)


async def _deliver(chat_id: int, user_id: int, key: str, buyer: str, target: int | None, text: str | None,
                   sign: int | None, value: str | None, price: int, people: int | None) -> str:
    whom = members.mention(target) if target else ""      # тост и пророчество адресные — человек их увидит
    if key == "announce":
        await bot.send_message(chat_id, texts.SHOP_ANNOUNCE.format(buyer=buyer, text=escape(text, quote=False)))
        return "📢 Объявление ушло в беседу"
    if key == "toast":
        toast = random.choice(texts.SHOP_TOASTS).format(target=whom)
        await bot.send_message(chat_id, texts.SHOP_TOAST.format(buyer=buyer, toast=toast))
        return "🥂 Бокал поднят"
    if key == "prophecy":
        phrase = escape(sglypa.generate_phrase(chat_id) or random.choice(texts.SGLYPA_FALLBACK), quote=False)
        await bot.send_message(chat_id, texts.SHOP_PROPHECY.format(buyer=buyer, target=whom, phrase=phrase))
        return "🔮 Сглыпа изрекла"
    if key == "pin":
        sent = await bot.send_message(chat_id, texts.SHOP_PIN.format(buyer=buyer, text=escape(text, quote=False)))
        try:
            await bot.pin_chat_message(chat_id, sent.message_id, disable_notification=True)
        except Exception:
            _rights[chat_id] = (False, _now())
            with suppress(Exception):          # без закрепа текст не заказывали — убираем
                await bot.delete_message(chat_id, sent.message_id)
            raise GameError("Боту не дали закрепить — деньги вернулись", 409)
        _pins[chat_id] = (sent.message_id, _now() + PIN_SECONDS)
        _save_pins()
        return "📌 Висит в закрепе час"
    if key == "title":
        db.set_name_mark(user_id, "title", value)
        return f"🎖 Теперь ты «{value}»"
    if key == "badge":
        db.set_name_mark(user_id, "badge", value)
        return f"✨ Значок {value} уже у имени"
    if key == "rain":
        rain_id = db.create_rain(chat_id, user_id, price, people, int(_now()))
        try:
            sent = await bot.send_message(chat_id, texts.SHOP_RAIN.format(
                buyer=buyer, amount=money(price), people=people, share=money(price // people)),
                reply_markup=rain_keyboard(rain_id))
        except Exception:
            db.cancel_rain(rain_id)              # сообщения нет — и дождя нет; деньги вернёт buy
            raise
        db.set_rain_message(rain_id, sent.message_id)
        return f"💸 Дождь пошёл: {people} человек поймают по {money(price // people)}"
    if key == "d20":
        db.add_d20_effect(chat_id, target, user_id, D20_DELTA * sign)
        return "🎲 Сработает на следующем броске — пусть это будет сюрпризом"
    if key == "tajik":
        today = local_today()
        tomorrow = (datetime.now(LOCAL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        day = today if db.get_tajik_of_day(chat_id, today) is None else tomorrow   # сегодня ещё не выбрали — лоббируем сегодня
        if not db.add_tajik_lobby(chat_id, day, target, user_id):
            raise GameError("Ты уже лоббируешь этого человека — жди выбора", 409)
        return "👑 Судьба подкуплена. Никому не говори"
    if key == "event":
        from handlers import fun          # здесь, а не наверху: обработчики чата сами тянут за собой казино
        await fun.paid_tajikistan_event(chat_id, target, user_id)
        return "🇹🇯 Таджикистан случился"
    raise GameError("Такого в лавке нет", 404)


# ── Дождь: кнопка в беседе ────────────────────────────────────────────────────


def rain_keyboard(rain_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💸 Ловить!", callback_data=f"rain:{rain_id}")]])


def rain_text(rain: dict) -> str:
    buyer = members.display_name(rain["user_id"])
    share = rain["amount"] // rain["people"]
    head = texts.SHOP_RAIN.format(buyer=buyer, amount=money(rain["amount"]), people=rain["people"], share=money(share))
    if rain["caught"]:
        head += "\n\n" + texts.SHOP_RAIN_CAUGHT.format(
            names=", ".join(members.display_name(uid) for uid in rain["caught"]))
    if rain["closed"]:
        head += "\n" + (texts.SHOP_RAIN_DONE if len(rain["caught"]) >= rain["people"] else texts.SHOP_RAIN_OVER)
    return head


async def expire(now: float):
    for rain in db.expire_rains(int(now) - RAIN_SECONDS):
        if rain.get("message_id"):
            try:
                await bot.edit_message_text(rain_text(rain | {"closed": 1}), chat_id=rain["chat_id"],
                                            message_id=rain["message_id"])
            except Exception as e:
                logger.info("Дождь %s не обновился: %s", rain["id"], e)
    for chat_id, (message_id, until) in list(_pins.items()):
        if now < until:
            continue
        _pins.pop(chat_id, None)
        _save_pins()
        try:
            await bot.unpin_chat_message(chat_id, message_id=message_id)
        except Exception as e:
            logger.warning("Не снял закреп %s в %s: %s", message_id, chat_id, e)


def _save_pins():
    db.set_meta(PINS_KEY, json.dumps({str(chat): [message, until] for chat, (message, until) in _pins.items()}))


def _load_pins():
    try:
        saved = json.loads(db.get_meta(PINS_KEY) or "{}")
        _pins.update({int(chat): (int(message), float(until)) for chat, (message, until) in saved.items()})
    except (ValueError, TypeError) as e:
        logger.warning("Закрепы лавки не прочитались: %s", e)


async def loop():
    _load_pins()
    while True:
        try:
            await expire(_now())
        except Exception:
            logger.exception("Лавка: не разобрались с дождями и закрепами")
        await asyncio.sleep(30)
