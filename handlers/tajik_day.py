"""Таджик дня: раз в сутки бот торжественно выбирает таджика беседы."""

import asyncio
import random

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import casino_shop as shop
import members
import texts
from loader import db
from textstats import count_with_word
from utils import local_today

router = Router(name="tajik_day")

_in_progress: set[int] = set()


@router.message(Command("tajik", "таджик", "таджикдня"))
async def cmd_tajik(message: Message):
    if not members.is_group(message.chat):
        await message.reply("Таджика дня выбирают в беседе 🇹🇯")
        return
    chat_id = message.chat.id
    today = local_today()
    winner = db.get_tajik_of_day(chat_id, today)
    if winner:
        await message.reply(random.choice(texts.TAJIK_DAY_ALREADY).format(name=f"<b>{members.display_name(winner)}</b>"))
        return
    if chat_id in _in_progress:
        await message.reply(texts.TAJIK_DAY_BUSY)
        return

    _in_progress.add(chat_id)
    try:
        lobby = db.tajik_lobbied(chat_id, today)       # проплачено в лавке влияния — секрет до самого выбора
        weights: dict[int, int] = {}
        for row in lobby:
            weights[row["target"]] = weights.get(row["target"], 1) + shop.TAJIK_LOBBY_WEIGHT - 1
        candidate = await members.random_member(chat_id, active_days=30, weights=weights)
        if candidate is None:
            await message.reply(texts.TAJIK_DAY_NOBODY)
            return
        # победитель записывается сразу, до всей драмы: перезапуск бота не даст перевыбрать
        winner = db.set_tajik_of_day(chat_id, today, candidate)
        if winner == candidate:
            db.add_money(chat_id, winner, texts.TAJIK_DAY_PRIZE)
        wins = db.count_tajik_wins(chat_id, winner)

        scenario = random.choice(texts.TAJIK_DAY_SCENARIOS)
        await message.reply(scenario[0])
        for line in scenario[1:]:
            await asyncio.sleep(random.uniform(1.8, 2.8))
            await message.answer(line)
        await asyncio.sleep(2.5)
        finale = random.choice(texts.TAJIK_DAY_FINALES).format(winner=members.mention(winner))
        extra = texts.TAJIK_DAY_FIRST_TIME if wins == 1 else texts.TAJIK_DAY_REPEAT.format(n=wins)
        await message.answer(f"{finale}\n{extra}\n\n{texts.TAJIK_DAY_PERK}" + _lobby_reveal(lobby, winner))
    finally:
        _in_progress.discard(chat_id)


def _lobby_reveal(lobby: list[dict], winner: int) -> str:
    """Кто подкупал судьбу в лавке влияния — вскрывается вместе с выбором."""
    if not lobby:
        return ""
    names = members.display_names({row["buyer"] for row in lobby} | {row["target"] for row in lobby})
    paid = [names[row["buyer"]] for row in lobby if row["target"] == winner]
    missed = [f"{names[row['buyer']]} → {names[row['target']]}" for row in lobby if row["target"] != winner]
    lines = [texts.TAJIK_LOBBY_WON.format(buyers=", ".join(paid))] if paid else []
    if missed:
        lines.append(texts.TAJIK_LOBBY_LOST.format(buyers=", ".join(missed)))
    return "\n\n" + "\n".join(lines)


@router.message(Command("tajiktop", "таджиктоп"))
async def cmd_tajik_top(message: Message):
    if not members.is_group(message.chat):
        await message.reply("Зал славы висит в беседе 🇹🇯")
        return
    chat_id = message.chat.id
    top = db.get_tajik_top(chat_id, limit=10)
    if not top:
        await message.reply(texts.TAJIK_TOP_EMPTY)
        return
    names = members.display_names(row["user_id"] for row in top)
    lines = [texts.TAJIK_TOP_TITLE, ""]
    for i, row in enumerate(top):
        place = texts.MEDALS[i] if i < len(texts.MEDALS) else f"{i + 1}."
        lines.append(f"{place} {names[row['user_id']]} — {count_with_word(row['wins'], ('раз', 'раза', 'раз'))}")
    today_winner = db.get_tajik_of_day(chat_id, local_today())
    lines.append("")
    if today_winner:
        lines.append(f"Сегодня таджик дня: <b>{members.display_name(today_winner)}</b>")
    else:
        lines.append("Сегодня таджик дня ещё не выбран — /tajik")
    await message.reply("\n".join(lines))
