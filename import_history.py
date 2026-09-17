"""
import_history.py — загружает экспорт истории Telegram (result.json) в базу бота.

Автоматически: положи result.json рядом с bot.py (или в папку с базой) и перезапусти бота.
Вручную:       python import_history.py result.json

Что импортируется:
  - статистика сообщений и слов — если этот чат ещё не импортировался;
  - последние 20 000 сообщений для сглыпа-режима — если ещё не импортировались.
Что уже импортировано, записано в самой базе, так что повторный запуск ничего не задвоит.

Как получить result.json:
  Telegram Desktop → название беседы → ⋮ → Экспорт истории чата
  Формат: JSON  |  снять галки с медиа  →  result.json
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from db import SUPERGROUP_ID_SHIFT, Database, utcnow_iso
from textstats import extract_words, is_corpus_worthy

logger = logging.getLogger(__name__)

MAX_PHRASES = 20_000
_CHUNK = 1 << 20
_MESSAGES_KEY_RE = re.compile(r'"messages"\s*:\s*\[')
_ID_RE = re.compile(r'"id"\s*:\s*(-?\d+)')
_TYPE_RE = re.compile(r'"type"\s*:\s*"([^"]+)"')


def read_export(path: Path) -> tuple[int | None, str | None, Iterator[dict[str, Any]]]:
    """Потоковое чтение экспорта: файл может весить сотни мегабайт, а памяти на хостинге мало.
    Возвращает (id чата из экспорта, тип чата, итератор сообщений)."""
    f = open(path, encoding="utf-8")
    buffer = ""
    while True:
        match = _MESSAGES_KEY_RE.search(buffer)
        if match:
            break
        chunk = f.read(_CHUNK)
        if not chunk:
            f.close()
            raise ValueError("в файле нет списка messages — это точно экспорт одной беседы?")
        buffer += chunk
    header = buffer[:match.start()]
    id_match, type_match = _ID_RE.search(header), _TYPE_RE.search(header)
    export_id = int(id_match.group(1)) if id_match else None
    export_type = type_match.group(1) if type_match else None

    def messages() -> Iterator[dict[str, Any]]:
        nonlocal buffer
        decoder = json.JSONDecoder()
        pos = match.end()
        try:
            while True:
                while pos < len(buffer) and buffer[pos] in " \t\r\n,":
                    pos += 1
                if pos >= len(buffer):
                    chunk = f.read(_CHUNK)
                    if not chunk:
                        return
                    buffer, pos = buffer[pos:] + chunk, 0
                    continue
                if buffer[pos] == "]":
                    return
                try:
                    item, end = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    chunk = f.read(_CHUNK)
                    if not chunk:
                        raise
                    buffer, pos = buffer[pos:] + chunk, 0
                    continue
                pos = end
                if isinstance(item, dict):
                    yield item
                if pos > _CHUNK:
                    buffer, pos = buffer[pos:], 0
        finally:
            f.close()

    return export_id, export_type, messages()


def bot_chat_id(export_id: int, export_type: str | None) -> int:
    """Экспорт хранит id без префикса: супергруппа 3706796442 для бота — это -1003706796442."""
    if export_id < 0 or export_type in ("personal_chat", "bot_chat", "saved_messages"):
        return export_id
    if export_type == "private_group":
        return -export_id
    return -(SUPERGROUP_ID_SHIFT + export_id)


def _message_text(msg: dict[str, Any]) -> str:
    """Поле text бывает строкой или списком кусков с разметкой."""
    raw = msg.get("text", "")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(chunk if isinstance(chunk, str) else chunk.get("text", "") for chunk in raw)
    return ""


def _author_id(msg: dict[str, Any]) -> int | None:
    raw = msg.get("from_id", "")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.startswith("user"):
        try:
            return int(raw[4:])
        except ValueError:
            return None
    return None


def _message_time(msg: dict[str, Any]) -> str | None:
    try:
        stamp = int(msg["date_unixtime"])
    except (KeyError, TypeError, ValueError):
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None).isoformat()


def import_history(path: Path, db: Database, chat_id: int | None = None) -> dict[str, int]:
    export_id, export_type, messages = read_export(path)
    if chat_id is None:
        if export_id is None:
            raise ValueError("не нашёл id беседы в экспорте")
        chat_id = bot_chat_id(export_id, export_type)

    stats_done = db.get_meta(f"history_import:{chat_id}") is not None
    phrases_done = db.get_meta(f"phrases_import:{chat_id}") is not None
    result = {"messages": 0, "users": 0, "phrases": 0}
    if stats_done and phrases_done:
        return result

    users: dict[int, dict] = {}
    phrases: deque[tuple[int, str]] = deque(maxlen=MAX_PHRASES)
    for msg in messages:
        if msg.get("type") != "message":
            continue
        user_id = _author_id(msg)
        text = _message_text(msg)
        if user_id is None or not text or text.startswith("/"):
            continue
        if not stats_done:
            words = extract_words(text)
            user = users.setdefault(
                user_id, {"message_count": 0, "word_count": 0, "words": Counter(), "last": None, "name": ""}
            )
            user["message_count"] += 1
            user["word_count"] += len(words)
            user["words"].update(words)
            user["last"] = _message_time(msg) or user["last"]
            user["name"] = msg.get("from") or user["name"]
        if not phrases_done and not msg.get("forwarded_from") and not msg.get("via_bot") and is_corpus_worthy(text):
            phrases.append((user_id, text))

    if not stats_done:
        for user_id, user in users.items():
            # Имя из экспорта — это имя из контактов того, кто экспортировал. Берём его, только если
            # другого нет; настоящее имя бот узнает, когда сверит участников или человек напишет сам.
            db.add_user_if_missing(user_id, user["name"])
            db.bulk_import_stats(
                chat_id, user_id, user["message_count"], user["word_count"], dict(user["words"]), user["last"]
            )
        db.set_meta(f"history_import:{chat_id}", utcnow_iso(), important=True)
        result["messages"] = sum(u["message_count"] for u in users.values())
        result["users"] = len(users)
    if not phrases_done:
        bots = {uid for uid in {uid for uid, _ in phrases} if (db.get_user(uid) or {}).get("is_bot")}
        rows = [(uid, text) for uid, text in phrases if uid not in bots]
        db.import_phrases(chat_id, rows)
        db.set_meta(f"phrases_import:{chat_id}", utcnow_iso(), important=True)
        result["phrases"] = len(rows)
    db.remember_chat(chat_id)
    return result


def find_history_file() -> Path | None:
    from config import BASE_DIR, DB_PATH, HISTORY_FILE

    for candidate in (Path(HISTORY_FILE), BASE_DIR / HISTORY_FILE, DB_PATH.parent / HISTORY_FILE):
        if candidate.is_file():
            return candidate
    return None


def load_history_if_exists(db: Database) -> bool:
    """Вызывается ботом при старте. Возвращает True, если что-то импортировалось."""
    path = find_history_file()
    if not path:
        return False
    try:
        stats = import_history(path, db)
    except Exception:
        logger.exception("Не получилось импортировать историю из %s", path)
        return False
    if any(stats.values()):
        logger.info(
            "История импортирована из %s: %s сообщений от %s человек, %s фраз для сглыпы",
            path, stats["messages"], stats["users"], stats["phrases"],
        )
        return True
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from config import DB_PATH

    file_path = Path(sys.argv[1] if len(sys.argv) > 1 else "result.json")
    if not file_path.is_file():
        print(f"Файл не найден: {file_path}")
        sys.exit(1)
    database = Database(DB_PATH)
    database.open()
    stats = import_history(file_path, database)
    database.close()
    print(
        f"✅ Импорт завершён: {stats['messages']} сообщений, {stats['users']} пользователей, "
        f"{stats['phrases']} фраз для сглыпы"
    )
