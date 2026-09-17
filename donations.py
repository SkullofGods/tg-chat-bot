"""Донаты рублями по СБП на номер хозяина.

Банковские переводы бот не видит, поэтому схема такая: человек переводит сам и пишет сумму с сообщением,
хозяину в личку приходит запрос с кнопками, и только после подтверждения донат объявляется в беседе,
а донатер получает таджикоины: по COINS_PER_RUBLE за каждый рубль.
"""

import logging
import time
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import members
import texts
from casino_engine import GameError, signed
from config import DONATE_BANK, DONATE_PHONE, OWNER_ID
from loader import bot, db
from textstats import fmt_num

logger = logging.getLogger(__name__)

MIN_AMOUNT = 10
MAX_AMOUNT = 1_000_000
MAX_MESSAGE_LENGTH = 200
MAX_PENDING = 3                   # сколько непроверенных донатов может висеть у одного человека
REQUEST_INTERVAL_SECONDS = 60
COINS_PER_RUBLE = 10              # за донат в 300 ₽ — 3 000 таджикоинов

_last_request: dict[int, float] = {}


def pretty_phone(phone: str) -> str:
    """+79991234567 → +7 999 123-45-67"""
    if len(phone) == 12 and phone.startswith("+7"):
        return f"+7 {phone[2:5]} {phone[5:8]}-{phone[8:10]}-{phone[10:12]}"
    return phone


def payment_info() -> dict | None:
    """Куда переводить: для мини-приложения. None — донаты выключены."""
    if not DONATE_PHONE:
        return None
    return {"phone": DONATE_PHONE, "pretty": pretty_phone(DONATE_PHONE), "bank": DONATE_BANK or None,
            "coins_per_ruble": COINS_PER_RUBLE}


def reward(amount: int) -> int:
    """Сколько таджикоинов положено за донат."""
    return amount * COINS_PER_RUBLE


def clean_message(message) -> str:
    return " ".join(message.split())[:MAX_MESSAGE_LENGTH] if isinstance(message, str) else ""


def _owner_keyboard(donation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Пришёл — объявить", callback_data=f"don:ok:{donation_id}"),
        InlineKeyboardButton(text="❌ Не пришёл", callback_data=f"don:no:{donation_id}"),
    ]])


async def request(chat_id: int, user_id: int, amount, message) -> dict:
    """Человек говорит, что перевёл деньги. Хозяин получает запрос на проверку."""
    if not DONATE_PHONE:
        raise GameError("Донаты сейчас выключены", 404)
    if isinstance(amount, bool) or not isinstance(amount, int) or not MIN_AMOUNT <= amount <= MAX_AMOUNT:
        raise GameError(f"Сумма — от {MIN_AMOUNT} до {fmt_num(MAX_AMOUNT)} ₽")
    now = time.monotonic()
    if now - _last_request.get(user_id, -REQUEST_INTERVAL_SECONDS) < REQUEST_INTERVAL_SECONDS:
        raise GameError("Подожди минутку перед следующим донатом 🙂", 429)
    if db.count_pending_donations(user_id) >= MAX_PENDING:
        raise GameError("Хозяин ещё не проверил твои прошлые донаты — подожди немного", 429)
    _last_request[user_id] = now

    text = clean_message(message)
    donation_id = db.add_donation(chat_id, user_id, amount, text)
    try:
        await bot.send_message(
            OWNER_ID,
            texts.DONATION_OWNER_REQUEST.format(
                name=members.mention(user_id), amount=fmt_num(amount),
                message=f"«{escape(text, quote=False)}»" if text else "без сообщения",
            ),
            reply_markup=_owner_keyboard(donation_id),
        )
    except Exception as e:
        logger.error("Не смог написать хозяину про донат %s: %s", donation_id, e)
    return {"ok": True}


async def announce(donation: dict):
    text = texts.DONATION_ANNOUNCE.format(
        name=members.mention(donation["user_id"]),
        amount=fmt_num(donation["amount"]),
        coins=signed(reward(donation["amount"])),
    )
    if donation["message"]:
        text += f"\n\n«{escape(donation['message'], quote=False)}»"
    await bot.send_message(donation["chat_id"], text)


async def decide(donation_id: int, approve: bool) -> str:
    """Решение хозяина. Возвращает, что показать ему в ответ."""
    donation = db.get_donation(donation_id)
    if donation is None:
        return "Такого доната нет"
    if not db.decide_donation(donation_id, "confirmed" if approve else "rejected", COINS_PER_RUBLE):
        return "Этот донат уже проверен"
    if not approve:
        return "Отклонено"
    await announce(donation)
    return f"Объявил в беседе ❤️ Начислил {signed(reward(donation['amount']))}"


async def add_confirmed(chat_id: int, user_id: int, amount: int, message: str):
    """Хозяин отмечает донат сам — например, перевели без формы."""
    donation_id = db.add_donation(chat_id, user_id, amount, clean_message(message), "confirmed", COINS_PER_RUBLE)
    await announce(db.get_donation(donation_id))
