"""Лавка влияния в беседе: кнопка «Ловить!» под дождём из таджикоинов. Всё остальное покупается в приложении."""

from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

import casino_shop as shop
import members
from casino_engine import money
from loader import db

router = Router(name="shop")


@router.callback_query(F.data.startswith("rain:"))
async def on_rain(callback: CallbackQuery):
    try:
        rain_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    user_id = callback.from_user.id
    rain = db.get_rain(rain_id)
    if rain is None or rain["closed"]:
        await callback.answer("🌤 Этот дождь уже кончился", show_alert=True)
        return
    if rain["user_id"] == user_id:
        await callback.answer("Свой дождь ловить нельзя 😏", show_alert=True)
        return
    members.remember_user(callback.from_user)
    caught = db.catch_rain(rain_id, user_id)
    if caught is None:
        await callback.answer("Твоя доля уже у тебя 😉" if user_id in rain["caught"] else "Опоздание: всё уже поймали",
                              show_alert=True)
        return
    await callback.answer(f"💸 +{money(caught['share'])}!")
    if isinstance(callback.message, Message):
        with suppress(TelegramBadRequest):   # два ловца почти разом: второй правит уже устаревший текст
            await callback.message.edit_text(shop.rain_text(caught),
                                             reply_markup=None if caught["closed"] else shop.rain_keyboard(rain_id))
