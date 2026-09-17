"""/stats — статистика беседы и досье на участника."""

import asyncio
import time
from collections import Counter
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import members
import texts
from loader import db
from textstats import count_with_word, distinctive_words, fmt_num, top_words, word_category

router = Router(name="stats")

FACTS_TTL_SECONDS = 180
NIGHT_HOURS = range(0, 6)
MESSAGES = ("сообщение", "сообщения", "сообщений")
WORDS = ("слово", "слова", "слов")
TIMES = ("раз", "раза", "раз")

# (ключ, минимальное значение, чтобы получить номинацию)
_COUNT_NOMINATIONS = {
    "swears": 10, "laughs": 10, "tajiks": 5, "night": 10, "voices": 3, "stickers": 10,
    "media": 5, "questions": 10, "caps": 5, "tajik_wins": 2,
}

_facts_cache: dict[int, tuple[float, dict]] = {}


# ── Сбор данных (в отдельном потоке, со своим подключением к базе) ───────────


def _load_chat_facts(chat_id: int) -> dict:
    conn = db.connect_reader()
    try:
        users: dict[int, dict] = {}
        rows = conn.execute(
            """
            SELECT ms.*, COALESCE(u.is_bot, 0) AS is_bot, COALESCE(cm.is_member, 1) AS is_member
            FROM message_stats ms
            LEFT JOIN users u ON u.user_id = ms.user_id
            LEFT JOIN chat_members cm ON cm.chat_id = ms.chat_id AND cm.user_id = ms.user_id
            WHERE ms.chat_id = ?
            """,
            (chat_id,),
        )
        for row in rows:
            if not row["is_bot"]:
                users[row["user_id"]] = dict(row) | {
                    "swears": 0, "laughs": 0, "tajiks": 0, "night": 0, "tajik_wins": 0, "hours": [0] * 24,
                }

        words: Counter = Counter()
        for user_id, word, count in conn.execute(
            "SELECT user_id, word, count FROM word_stats WHERE chat_id = ?", (chat_id,)
        ):
            user = users.get(user_id)
            if user is None:
                continue
            words[word] += count
            category = word_category(word)
            if category == "swear":
                user["swears"] += count
            elif category == "laugh":
                user["laughs"] += count
            elif category == "tajik":
                user["tajiks"] += count

        for user_id, hour, count in conn.execute(
            "SELECT user_id, hour, count FROM hourly_stats WHERE chat_id = ?", (chat_id,)
        ):
            if user_id in users:
                users[user_id]["hours"][hour] += count
                if hour in NIGHT_HOURS:
                    users[user_id]["night"] += count

        for user_id, wins in conn.execute(
            "SELECT user_id, COUNT(*) FROM tajik_of_day WHERE chat_id = ? GROUP BY user_id", (chat_id,)
        ):
            if user_id in users:
                users[user_id]["tajik_wins"] = wins
        return {"users": users, "words": words}
    finally:
        conn.close()


def _load_user_words(chat_id: int, user_id: int) -> dict[str, int]:
    conn = db.connect_reader()
    try:
        return dict(conn.execute(
            "SELECT word, count FROM word_stats WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchall())
    finally:
        conn.close()


async def _chat_facts(chat_id: int) -> dict:
    cached = _facts_cache.get(chat_id)
    if cached and time.monotonic() - cached[0] < FACTS_TTL_SECONDS:
        return cached[1]
    facts = await asyncio.to_thread(_load_chat_facts, chat_id)
    _facts_cache[chat_id] = (time.monotonic(), facts)
    return facts


def _nominations(users: dict[int, dict]) -> dict[str, tuple[int, float]]:
    """Номинация → (кто, значение)."""
    result: dict[str, tuple[int, float]] = {}
    for key, minimum in _COUNT_NOMINATIONS.items():
        value, user_id = max(((u[key], uid) for uid, u in users.items()), default=(0, None))
        if value >= minimum:
            result[key] = (user_id, value)
    talkers = [(u["word_count"] / u["message_count"], uid) for uid, u in users.items() if u["message_count"] >= 200]
    if len(talkers) >= 2:
        avg, user_id = max(talkers)
        result["writer"] = (user_id, avg)
        avg, user_id = min(talkers)
        result["brief"] = (user_id, avg)
    return result


# ── Оформление ────────────────────────────────────────────────────────────────


def _format_overview(facts: dict) -> str | None:
    users = facts["users"]
    total_messages = sum(u["message_count"] for u in users.values())
    if not total_messages:
        return None
    total_words = sum(u["word_count"] for u in users.values())
    ranking = sorted((u for u in users.values() if u["message_count"]), key=lambda u: -u["message_count"])
    nominations = _nominations(users)
    names = members.display_names({u["user_id"] for u in ranking[:10]} | {uid for uid, _ in nominations.values()})

    def label(user_id: int) -> str:
        return names[user_id] + ("" if users[user_id]["is_member"] else " 👻")

    left = sum(1 for u in ranking if not u["is_member"])
    lines = [
        "📈 <b>Статистика беседы</b>",
        "",
        f"💬 {count_with_word(total_messages, MESSAGES)} · 📝 {count_with_word(total_words, WORDS)}",
        f"👥 Болтунов: <b>{len(ranking)}</b>" + (f" (из них покинули чат: {left} 👻)" if left else ""),
        "",
        "🏆 <b>Главные болтуны</b>",
    ]
    for i, user in enumerate(ranking[:10]):
        place = texts.MEDALS[i] if i < len(texts.MEDALS) else f"{i + 1}."
        share = 100 * user["message_count"] / total_messages
        lines.append(f"{place} {label(user['user_id'])} — {fmt_num(user['message_count'])} ({share:.0f}%)")

    chat_top = top_words(facts["words"], 10)
    if chat_top:
        lines += ["", "🔤 <b>Слова беседы:</b> " + ", ".join(f"{escape(w)} ({fmt_num(n)})" for w, n in chat_top)]

    if nominations:
        lines += ["", "🏅 <b>Номинации</b>"]
        for key, title, forms in texts.NOMINATIONS:
            if key not in nominations:
                continue
            user_id, value = nominations[key]
            if forms is None:
                value_text = f"≈{fmt_num(round(value, 1))} слова на сообщение"
            else:
                value_text = count_with_word(int(value), forms)
            lines.append(f"{title} — {label(user_id)} ({value_text})")
    return "\n".join(lines)


def _chronotype(hours: list[int]) -> str | None:
    total = sum(hours)
    if total < 30:
        return None
    peak = max(range(24), key=lambda h: hours[h])
    if sum(hours[0:6]) / total >= 0.25:
        kind = "ночная сова 🦉"
    elif sum(hours[6:11]) / total >= 0.35:
        kind = "жаворонок 🐦"
    elif sum(hours[18:24]) / total >= 0.5:
        kind = "вечерний болтун 🌆"
    else:
        kind = "дневной житель ☀️"
    return f"🕰 Чаще всего пишет в {peak:02d}:00–{(peak + 1) % 24:02d}:00 — {kind}"


def _format_dossier(user_id: int, facts: dict, user_words: dict[str, int]) -> str | None:
    users = facts["users"]
    user = users.get(user_id)
    if not user:
        return None
    total_messages = sum(u["message_count"] for u in users.values()) or 1
    name = members.display_name(user_id)
    lines = [f"📊 <b>Досье: {name}</b>" + ("" if user["is_member"] else " 👻"), ""]

    messages = user["message_count"]
    if messages:
        place = 1 + sum(1 for u in users.values() if u["message_count"] > messages)
        medal = f"{texts.MEDALS[place - 1]} " if place <= len(texts.MEDALS) else ""
        share = 100 * messages / total_messages
        lines.append(
            f"💬 Сообщений: <b>{fmt_num(messages)}</b> — {medal}{place}-е место, "
            f"{'меньше 1' if share < 1 else f'{share:.0f}'}% всей болтовни"
        )
        avg = user["word_count"] / messages
        wordiness = next(title for limit, title in texts.WORDINESS_TITLES if avg < limit)
        lines.append(f"📝 Слов: <b>{fmt_num(user['word_count'])}</b> — ≈{fmt_num(round(avg, 1))} на сообщение, {wordiness}")
        pages = user["word_count"] // 250
        if pages:
            joke = next(joke for limit, joke in texts.PAGES_JOKES if pages >= limit)
            pages_text = count_with_word(pages, ("книжная страница", "книжные страницы", "книжных страниц"))
            lines.append(f"📚 Это ≈{pages_text}" + (f" — {joke}" if joke else ""))

    favorite = [(w, n) for w, n in top_words(user_words, 6) if n >= 3]
    signature = distinctive_words(user_words, facts["words"])
    if favorite or signature:
        lines.append("")
    if favorite:
        word, count = favorite[0]
        lines.append(f"🔤 Любимое слово: «{escape(word)}» ×{fmt_num(count)}")
        if len(favorite) > 1:
            lines.append("📋 Ещё часто: " + ", ".join(f"{escape(w)} ({fmt_num(n)})" for w, n in favorite[1:]))
    if signature:
        lines.append("🧬 Фирменные словечки: " + ", ".join(escape(w) for w in signature))

    if user["swears"]:
        rate = ""
        if messages >= 50:
            per_hundred = 100 * user["swears"] / messages
            rate = " — почти без мата 😇" if per_hundred < 1 else f" (≈{fmt_num(round(per_hundred))} на 100 сообщений)"
        lines.append(f"🤬 Мат: {count_with_word(user['swears'], WORDS)}{rate}")
    elif messages >= 100:
        lines.append("😇 Ни одного матерного слова. Святой человек")
    if user["laughs"]:
        lines.append(f"😂 Смешков: {fmt_num(user['laughs'])}")
    if user["tajiks"]:
        lines.append(f"🇹🇯 Упоминаний Таджикистана: {fmt_num(user['tajiks'])}")

    habits = []
    chronotype = _chronotype(user["hours"])
    extras = []
    if user["stickers"]:
        extras.append(f"🃏 {count_with_word(user['stickers'], ('стикер', 'стикера', 'стикеров'))}")
    if user["voices"]:
        extras.append(f"🎙 {count_with_word(user['voices'], ('голосовое', 'голосовых', 'голосовых'))}")
    if user["media"]:
        extras.append(f"📸 {fmt_num(user['media'])} фото/видео")
    if user["questions"]:
        extras.append(f"❓ {count_with_word(user['questions'], ('вопрос', 'вопроса', 'вопросов'))}")
    if user["caps"]:
        extras.append(f"🔊 {fmt_num(user['caps'])} капсом")
    if chronotype:
        habits.append(chronotype)
    if extras:
        habits.append(" · ".join(extras))
    if habits:
        lines += [""] + habits

    social = []
    spouses = db.get_spouses(user_id)
    if spouses:
        spouse_names = members.display_names(spouses)
        social.append("💍 В браке с: " + ", ".join(spouse_names[s] for s in spouses))
    if user["tajik_wins"]:
        social.append(f"👑 Таджик дня: {count_with_word(user['tajik_wins'], TIMES)}")
    nominations = _nominations(users)
    titles = [title for key, title, _ in texts.NOMINATIONS if nominations.get(key, (None,))[0] == user_id]
    if titles:
        social.append("🏅 Титулы беседы: " + ", ".join(titles))
    if social:
        lines += [""] + social
    return "\n".join(lines)


@router.message(Command("stats", "stat", "стата", "статистика"))
async def cmd_stats(message: Message, command: CommandObject):
    if not members.is_group(message.chat):
        await message.reply("Статистика живёт в беседе — вызови /stats там 📊")
        return
    chat_id = message.chat.id
    args = (command.args or "").strip()
    if args.lower() in ("я", "me", "мне", "моя", "мой"):
        target_id = message.from_user.id
    else:
        target_id, raw = members.resolve_target(message, args)
        if target_id == -1:
            await message.reply(f"Пользователь <code>@{escape(raw)}</code> не найден в базе — возможно, ещё не писал в чат.")
            return

    facts = await _chat_facts(chat_id)
    if target_id is None:
        text = _format_overview(facts) or "Пока статистики маловато — сначала поболтайте немного."
    else:
        user_words = await asyncio.to_thread(_load_user_words, chat_id, target_id)
        text = _format_dossier(target_id, facts, user_words) or "По этому человеку пока нет статистики в этой беседе."
    await message.reply(text)
