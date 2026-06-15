"""
import_history.py — загружает экспорт истории Telegram (result.json) в базу бота.

Запуск вручную:  python import_history.py result.json
Автоматический:  бот сам вызывает load_history_if_exists() при старте;
                 если файл уже был импортирован — пропустит.

Как получить result.json:
  Telegram Desktop → название беседы → ⋮ → Экспорт истории чата
  Формат: JSON  |  снять галки с медиа  →  result.json
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime
from typing import Any

from db import Database

logger = logging.getLogger(__name__)

HISTORY_FILE = os.getenv("HISTORY_FILE", "result.json")
IMPORT_STAMP = "history_imported.stamp"   # сигнальный файл — уже импортировано

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")
COMMON_STOP_WORDS = {
    "это", "как", "что", "чтобы", "или", "его", "ее", "её", "она", "они", "оно", "при", "про", "для",
    "без", "под", "над", "тут", "там", "пока", "если", "где", "когда", "потом", "тогда", "типа",
    "блин", "бля", "ага", "нет", "да", "тоже", "ещё", "еще", "только", "очень", "просто", "мне",
    "тебе", "тебя", "меня", "нас", "вам", "вот", "короче", "чето", "что-то", "какой", "какая",
    "который", "которая", "чел", "люди", "будет", "было", "были", "есть", "нету", "уже", "щас",
}


def _extract_words(text: str) -> list[str]:
    words = [w.lower() for w in WORD_RE.findall(text or "")]
    return [w for w in words if w not in COMMON_STOP_WORDS]


def _message_text(msg: dict[str, Any]) -> str:
    """Достаёт текст из сообщения Telegram JSON-экспорта.
    Поле text может быть строкой или списком кусков.
    """
    raw = msg.get("text", "")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for chunk in raw:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                parts.append(chunk.get("text", ""))
        return "".join(parts)
    return ""


def import_history(path: str, db: Database, chat_id: int | None = None) -> dict[str, int]:
    """Импортирует result.json в базу.
    Возвращает статистику: {'messages': N, 'users': M}.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Telegram Desktop кладёт сообщения прямо в data или в data["messages"]
    messages: list[dict] = data if isinstance(data, list) else data.get("messages", [])

    # chat_id: берём из файла или из параметра
    if chat_id is None:
        chat_id = data.get("id") if isinstance(data, dict) else None
    if chat_id is None:
        chat_id = 0  # fallback — статистика всё равно накопится

    db.remember_chat(chat_id)

    # user_id → {username, full_name, message_count, word_count, words: Counter}
    users: dict[int, dict] = {}

    skipped = 0
    for msg in messages:
        if msg.get("type") != "message":
            continue
        from_id_raw = msg.get("from_id", "")
        # Telegram Desktop пишет id как "user123456789"
        if isinstance(from_id_raw, str) and from_id_raw.startswith("user"):
            try:
                user_id = int(from_id_raw[4:])
            except ValueError:
                skipped += 1
                continue
        elif isinstance(from_id_raw, int):
            user_id = from_id_raw
        else:
            skipped += 1
            continue

        username = msg.get("from", "") or ""
        # В экспорте нет отдельного username — используем display name
        full_name = msg.get("from", "") or f"user{user_id}"

        text = _message_text(msg)
        if not text or text.startswith("/"):
            continue

        words = _extract_words(text)

        if user_id not in users:
            users[user_id] = {
                "username": username,
                "full_name": full_name,
                "message_count": 0,
                "word_count": 0,
                "words": Counter(),
        }
        u = users[user_id]
        u["message_count"] += 1
        u["word_count"] += len(words)
        u["words"].update(words)

    # Записываем в БД батчами
    for user_id, u in users.items():
        db.ensure_user(user_id, u["username"], u["full_name"])
        db.bulk_import_stats(
            chat_id=chat_id,
            user_id=user_id,
            message_count=u["message_count"],
            word_count=u["word_count"],
            words=dict(u["words"]),
        )

    logger.info(
        "import_history: processed %d messages, %d users, %d skipped",
        sum(u["message_count"] for u in users.values()),
        len(users),
        skipped,
    )
    return {"messages": sum(u["message_count"] for u in users.values()), "users": len(users)}


def load_history_if_exists(db: Database) -> bool:
    """Вызывается ботом при старте.
    Если result.json есть и stamp-файла нет — импортирует и ставит stamp.
    Возвращает True, если импорт был выполнен.
    """
    if os.path.exists(IMPORT_STAMP):
        return False
    if not os.path.exists(HISTORY_FILE):
        return False
    try:
        stats = import_history(HISTORY_FILE, db)
        # Ставим stamp, чтобы при следующем старте не дублировать
        with open(IMPORT_STAMP, "w") as f:
            f.write(datetime.utcnow().isoformat())
        logger.info("History imported: %s messages, %s users", stats["messages"], stats["users"])
        return True
    except Exception as exc:
        logger.error("Failed to import history: %s", exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    path = sys.argv[1] if len(sys.argv) > 1 else HISTORY_FILE
    if not os.path.exists(path):
        print(f"Файл не найден: {path}")
        sys.exit(1)
    db = Database()
    stats = import_history(path, db)
    print(f"✅ Импорт завершён: {stats['messages']} сообщений, {stats['users']} пользователей")
