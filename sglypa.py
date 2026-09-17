"""Сглыпа-режим: бот иногда вставляет в беседу фразу, склеенную из сообщений самой беседы (цепи Маркова)."""

import logging
import random
import time
from collections import defaultdict, deque
from html import escape

from aiogram.types import Message

import texts
from config import SGLYPA_MAX_MESSAGES, SGLYPA_MIN_MESSAGES
from loader import bot, db
from textstats import tokenize

logger = logging.getLogger(__name__)

MAX_PHRASES_PER_CHAT = 20_000   # сколько последних сообщений хранить для генерации
MAX_STICKERS_PER_CHAT = 400
MIN_CORPUS = 50                 # пока сообщений меньше — отвечаем заготовками
CHAIN_TTL_SECONDS = 15 * 60     # как часто пересобирать цепь из свежих сообщений
ADDRESSED_COOLDOWN_SECONDS = 20  # защита от спама, когда бота дёргают ответами

_STRIP_CHARS = ".,!?;:…\"'«»()[]{}-—*_~`"
END = None


def normalize(token: str) -> str:
    return token.lower().strip(_STRIP_CHARS).replace("ё", "е") or token.lower()


class Chain:
    """Цепь Маркова второго порядка с откатом на первый. Помнит, из какого сообщения взято слово,
    чтобы не выдавать чужое сообщение целиком, а склеивать минимум два."""

    def __init__(self, phrases: list[str]):
        self.starts: list[tuple[str, int]] = []
        self.next1: dict[str, list[tuple[str | None, int]]] = defaultdict(list)
        self.next2: dict[tuple[str, str], list[tuple[str | None, int]]] = defaultdict(list)
        self.forms: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self.size = 0
        for idx, phrase in enumerate(phrases):
            tokens = tokenize(phrase)
            if len(tokens) < 2:
                continue
            self.size += 1
            norms = [normalize(t) for t in tokens]
            self.starts.append((tokens[0], idx))
            for i, token in enumerate(tokens):
                following = tokens[i + 1] if i + 1 < len(tokens) else END
                self.next1[norms[i]].append((following, idx))
                if len(self.forms[norms[i]]) < 8:
                    self.forms[norms[i]].append((token, idx))
                if i + 1 < len(tokens):
                    after_pair = tokens[i + 2] if i + 2 < len(tokens) else END
                    self.next2[(norms[i], norms[i + 1])].append((after_pair, idx))
        self.built_at = time.monotonic()

    def _walk(self, first: tuple[str, int], max_tokens: int) -> tuple[list[str], set[int]]:
        tokens, sources = [first[0]], {first[1]}
        while len(tokens) < max_tokens:
            options = None
            if len(tokens) >= 2 and random.random() < 0.75:
                options = self.next2.get((normalize(tokens[-2]), normalize(tokens[-1])))
            if not options:
                options = self.next1.get(normalize(tokens[-1]))
            if not options:
                break
            token, source = random.choice(options)
            if token is END:
                if len(tokens) >= 3:
                    break
                options = [o for o in options if o[0] is not END]
                if not options:
                    break
                token, source = random.choice(options)
            tokens.append(token)
            sources.add(source)
        return tokens, sources

    def generate(self, seed_words: list[str] = (), min_tokens: int = 3, max_tokens: int = 25,
                 attempts: int = 30) -> str | None:
        if not self.starts:
            return None
        seeds = [w for w in {normalize(s) for s in seed_words} if len(w) >= 3 and w in self.forms]
        fallback = None
        for attempt in range(attempts):
            if seeds and attempt < attempts // 2 and random.random() < 0.7:
                first = random.choice(self.forms[random.choice(seeds)])
            else:
                first = random.choice(self.starts)
            tokens, sources = self._walk(first, random.randint(max(min_tokens, max_tokens // 3), max_tokens))
            if len(tokens) < min_tokens:
                continue
            if len(sources) >= 2:
                return " ".join(tokens)
            fallback = fallback or " ".join(tokens)
        return fallback


# ── Состояние по чатам ────────────────────────────────────────────────────────

_chains: dict[int, Chain] = {}
_counters: dict[int, list[int]] = {}  # chat_id -> [сколько сообщений прошло, после скольких говорить]
_saved: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # chat_id -> [фраз, стикеров] с последней чистки
_bot_messages: dict[int, deque] = defaultdict(lambda: deque(maxlen=100))
_last_addressed: dict[int, float] = {}


def reset():
    _chains.clear()
    _counters.clear()
    _saved.clear()


def note_saved(chat_id: int, phrase: bool, sticker: bool):
    """Периодически подрезаем запасы фраз и стикеров, чтобы база не пухла."""
    counters = _saved[chat_id]
    counters[0] += int(phrase)
    counters[1] += int(sticker)
    if counters[0] >= 500:
        counters[0] = 0
        db.prune_phrases(chat_id, MAX_PHRASES_PER_CHAT)
    if counters[1] >= 200:
        counters[1] = 0
        db.prune_stickers(chat_id, MAX_STICKERS_PER_CHAT)


def _chain(chat_id: int) -> Chain:
    chain = _chains.get(chat_id)
    if chain is None or time.monotonic() - chain.built_at > CHAIN_TTL_SECONDS:
        # свежие сообщения + случайные старые: бот и в курсе текущих тем, и помнит мемы
        chain = Chain(db.sample_phrases(chat_id, recent=1500, random_count=2500))
        _chains[chat_id] = chain
    return chain


def _decorate(phrase: str) -> str:
    phrase = phrase.rstrip(",-—:;")
    if len(phrase) > 300:
        phrase = phrase[:300].rsplit(" ", 1)[0]
    if phrase and phrase[-1].isalpha() and random.random() < 0.15:
        phrase += ")"
    return phrase


def generate_phrase(chat_id: int, seed_text: str = "") -> str:
    chain = _chain(chat_id)
    phrase = chain.generate(seed_words=tokenize(seed_text)) if chain.size >= MIN_CORPUS else None
    return _decorate(phrase or "") or random.choice(texts.SGLYPA_FALLBACK)


def _is_addressed(message: Message, bot_username: str) -> bool:
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == bot.id \
            and reply.message_id in _bot_messages[message.chat.id]:
        return True
    text = (message.text or message.caption or "").lower()
    return bool(bot_username) and f"@{bot_username.lower()}" in text


async def on_group_message(message: Message):
    """Вызывается на каждое обычное сообщение человека в беседе."""
    chat_id = message.chat.id
    me = await bot.me()
    if _is_addressed(message, me.username or ""):
        now = time.monotonic()
        if now - _last_addressed.get(chat_id, 0) >= ADDRESSED_COOLDOWN_SECONDS:
            _last_addressed[chat_id] = now
            await speak(message, addressed=True)
        return

    counter = _counters.setdefault(chat_id, [0, random.randint(SGLYPA_MIN_MESSAGES, SGLYPA_MAX_MESSAGES)])
    counter[0] += 1
    if counter[0] >= counter[1]:
        counter[0], counter[1] = 0, random.randint(SGLYPA_MIN_MESSAGES, SGLYPA_MAX_MESSAGES)
        await speak(message)


def messages_until_next(chat_id: int) -> int | None:
    counter = _counters.get(chat_id)
    return counter[1] - counter[0] if counter else None


async def say_in_chat(chat_id: int):
    """Сказать что-нибудь без повода (из дебаг-панели)."""
    sent = await bot.send_message(chat_id, escape(generate_phrase(chat_id), quote=False))
    _bot_messages[chat_id].append(sent.message_id)


async def speak(message: Message, addressed: bool = False):
    chat_id = message.chat.id
    try:
        roll = random.random()
        if not addressed and roll < 0.08:
            sticker = db.random_sticker(chat_id)
            if sticker:
                sent = await message.reply_sticker(sticker)
                _bot_messages[chat_id].append(sent.message_id)
                return
        if not addressed and roll < 0.13 and await _send_poll(message):
            return
        phrase = generate_phrase(chat_id, seed_text=message.text or message.caption or "")
        sent = await message.reply(escape(phrase, quote=False))
        _bot_messages[chat_id].append(sent.message_id)
    except Exception:
        logger.exception("Сглыпа не смогла ответить в чате %s", chat_id)


async def _send_poll(message: Message) -> bool:
    chain = _chain(message.chat.id)
    if chain.size < MIN_CORPUS * 4:
        return False
    question = chain.generate(max_tokens=14)
    options: list[str] = []
    for _ in range(12):
        option = chain.generate(min_tokens=1, max_tokens=6)
        if option and len(option) <= 100 and option not in options:
            options.append(option)
        if len(options) == 4:
            break
    if not question or len(options) < 2:
        return False
    question = question[:290].rstrip(",-—:;.!")
    if not question.endswith("?"):
        question += "?"
    await message.reply_poll(question=question, options=options, is_anonymous=True)
    return True
