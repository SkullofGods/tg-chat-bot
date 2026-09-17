"""Развлечения: /d20, /tadjikistan, /who, /orgy."""

import asyncio
import logging
import random
import time
from contextlib import suppress
from html import escape

import aiohttp
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, PollAnswer

import limits
import members
import sglypa
import texts
from config import ORGY_COOLDOWN_HOURS, ORGY_POLL_DURATION_SECONDS
from loader import bot, db
from utils import local_today, spawn

logger = logging.getLogger(__name__)
router = Router(name="fun")

TAJIKISTAN_COOLDOWN_SECONDS = 5 * 60
TAJIKISTAN_FOLLOWUP_DELAY_SECONDS = 60
WIKI_API = "https://ru.wikipedia.org/w/api.php"
WIKI_HEADERS = {"User-Agent": "tg-chat-bot/2.0 (https://github.com/SkullofGods/tg-chat-bot)"}

_tajikistan_last: dict[int, float] = {}
orgy_state: dict[int, dict] = {}


async def random_wikipedia_title() -> str:
    params = {"action": "query", "format": "json", "list": "random", "rnnamespace": "0", "rnlimit": "1"}
    try:
        async with aiohttp.ClientSession(headers=WIKI_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(WIKI_API, params=params) as response:
                data = await response.json(content_type=None)
        return data["query"]["random"][0]["title"] or texts.WIKI_FALLBACK
    except Exception as e:
        logger.warning("Википедия не ответила: %s", e)
        return texts.WIKI_FALLBACK


# ── /d20 ──────────────────────────────────────────────────────────────────────


async def _random_bonus(chat_id: int, roller_id: int) -> tuple[int, str]:
    event = random.choice(texts.D20_BONUS_EVENTS)
    value = random.randint(*event["val"])
    member = "кто-то из чата"
    if "{member}" in event["tpl"] and members.is_group_id(chat_id):
        member_id = await members.random_member(chat_id, exclude={roller_id})
        if member_id:
            member = members.display_name(member_id)
    return value, f"🎰 <i>{event['tpl'].format(member=member)}</i>"


@router.message(Command("d20"))
async def cmd_d20(message: Message, command: CommandObject):
    user = message.from_user
    chat_id = message.chat.id
    if not await limits.allow(message, "d20"):
        return
    limits.mark(chat_id, user.id, "d20")
    try:
        threshold = max(1, min(22, int((command.args or "").strip())))
    except ValueError:
        threshold = random.randint(7, 22)
    roll = random.randint(1, 20)

    bonuses: list[tuple[int, str]] = []
    if members.is_group(message.chat) and db.get_tajik_of_day(chat_id, local_today()) == user.id:
        bonuses.append((2, f"<i>{texts.D20_TAJIK_BLESSING}</i>"))
    if random.random() < 0.10:
        bonuses.append(await _random_bonus(chat_id, user.id))
    effective = roll + sum(value for value, _ in bonuses)

    if roll == 1:
        flavor = random.choice(texts.D20_CRIT_FAIL)
    elif roll == 20:
        flavor = random.choice(texts.D20_CRIT_SUCCESS)
    elif effective < threshold:
        flavor = random.choice(texts.D20_FAIL)
    else:
        flavor = random.choice(texts.D20_SUCCESS)

    context = ""
    replied_text = message.reply_to_message.text if message.reply_to_message else None
    if replied_text:
        preview = replied_text[:60] + ("…" if len(replied_text) > 60 else "")
        context = f"<i>«{escape(preview, quote=False)}»</i>\n\n"

    lines = [f"🎲 {members.display_name(user.id)} бросает d20 — порог <b>{threshold}</b>, выпало <b>{roll}</b>"]
    for value, description in bonuses:
        lines.append(f"{description} — {value:+d}")
    if bonuses:
        lines.append(f"Итого: <b>{effective}</b>")
    await message.reply(f"{context}" + "\n".join(lines) + f"\n\n{flavor}")


# ── /tadjikistan ──────────────────────────────────────────────────────────────


@router.message(Command("tadjikistan", "таджикистан"))
async def cmd_tadjikistan(message: Message):
    chat_id = message.chat.id
    now = time.monotonic()
    last = _tajikistan_last.get(chat_id)
    cooldown = limits.cooldown(TAJIKISTAN_COOLDOWN_SECONDS)
    if last and now - last < cooldown:
        await message.reply(f"🇹🇯 Таджикистан пока отдыхает. Попробуй через {limits.format_wait(cooldown - (now - last))}.")
        return
    _tajikistan_last[chat_id] = now

    event = texts.TAJIKISTAN_EVENTS[random.choice(list(texts.TAJIKISTAN_EVENTS))]
    intro = event["intro"]
    if "{target}" in intro:
        target_id = await members.random_member(chat_id)
        intro = intro.format(target=members.mention(target_id) if target_id else "Кто-то")
    sent = await message.reply(intro)
    if "followup" in event:
        spawn(_tajikistan_followup(chat_id, sent.message_id, event["followup"], message.from_user.id))


async def _tajikistan_followup(chat_id: int, reply_to: int, template: str, caller_id: int):
    await asyncio.sleep(TAJIKISTAN_FOLLOWUP_DELAY_SECONDS)
    try:
        target_id = await members.random_member(chat_id)
        values = {
            "target": members.mention(target_id) if target_id else "Кто-то",
            "caller": members.mention(caller_id),
            "wiki": "",
            "prophecy": "",
        }
        if "{wiki}" in template:
            values["wiki"] = escape(await random_wikipedia_title(), quote=False)
        if "{prophecy}" in template:
            values["prophecy"] = escape(sglypa.generate_phrase(chat_id), quote=False)
        await bot.send_message(
            chat_id, template.format(**values), reply_to_message_id=reply_to, allow_sending_without_reply=True
        )
    except Exception:
        logger.exception("Продолжение таджикистан-ивента не отправилось")


# ── /who ──────────────────────────────────────────────────────────────────────


@router.message(Command("who", "кто"))
async def cmd_who(message: Message, command: CommandObject):
    question = " ".join((command.args or "").split())
    if not question:
        await message.reply(texts.WHO_EMPTY)
        return
    if not await limits.allow(message, "who"):
        return
    limits.mark(message.chat.id, message.from_user.id, "who")
    if members.is_group(message.chat):
        chosen = await members.random_member(message.chat.id)
    else:
        chosen = message.from_user.id  # в личке выбор небогатый
    if not chosen:
        await message.reply("🤷 Я пока никого тут не знаю — поболтайте немного!")
        return
    if len(question) > 200:
        question = question[:200] + "…"
    answer = random.choice(texts.WHO_ANSWERS).format(name=f"<b>{members.display_name(chosen)}</b>")
    await message.reply(f"❓ <i>{escape(question, quote=False)}</i>\n{answer}")


# ── /orgy ─────────────────────────────────────────────────────────────────────


@router.message(Command("orgy", "оргия"))
async def cmd_orgy(message: Message):
    if not members.is_group(message.chat):
        await message.reply("Оргии бывают только в беседе 😏")
        return
    chat_id = message.chat.id
    now = time.monotonic()
    state = orgy_state.get(chat_id)
    if state and state.get("active"):
        await message.reply("🔥 Оргия уже идёт — голосуй в опросе!")
        return
    if state:
        wait = limits.cooldown(ORGY_COOLDOWN_HOURS * 3600) - (now - state["last_time"])
        if wait > 0:
            hours, rest = divmod(int(wait), 3600)
            text = f"{hours}ч {rest // 60}мин" if hours else limits.format_wait(wait)
            await message.reply(f"⏳ Следующая оргия возможна через {text}.")
            return
    poll = await message.answer_poll(
        question="🔥 ОРГИЯ — ты участвуешь?",
        options=["Да, я в деле! 🍑", "Нет, не интересует 😇"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    orgy_state[chat_id] = {
        "last_time": now,
        "active": True,
        "poll_id": poll.poll.id,
        "yes_voters": [],
        "no_voters": [],
    }
    spawn(_finish_orgy_after(chat_id, poll.message_id))


async def _finish_orgy_after(chat_id: int, poll_message_id: int):
    await asyncio.sleep(ORGY_POLL_DURATION_SECONDS)
    with suppress(Exception):
        await bot.stop_poll(chat_id, poll_message_id)
    with suppress(Exception):
        await bot.delete_message(chat_id, poll_message_id)
    state = orgy_state.get(chat_id, {})
    state["active"] = False
    yes = " ".join(members.mention(uid) for uid in state.get("yes_voters", [])) or "никто"
    no = " ".join(members.mention(uid) for uid in state.get("no_voters", [])) or "никто"
    with suppress(Exception):
        await bot.send_message(chat_id, f"🔥 {yes} поимели жёсткий секс, а {no} с завистью смотрели.")


@router.poll_answer()
async def on_poll_answer(poll_answer: PollAnswer):
    user = poll_answer.user
    if user is None:
        return
    for state in orgy_state.values():
        if poll_answer.poll_id != state.get("poll_id"):
            continue
        members.remember_user(user)
        state["yes_voters"] = [uid for uid in state["yes_voters"] if uid != user.id]
        state["no_voters"] = [uid for uid in state["no_voters"] if uid != user.id]
        if 0 in poll_answer.option_ids:
            state["yes_voters"].append(user.id)
        elif poll_answer.option_ids:  # пустой список — человек отозвал голос
            state["no_voters"].append(user.id)
        break
