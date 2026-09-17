"""Бэкапы базы в Telegram и восстановление из них.

- Если база менялась, бот раз в BACKUP_INTERVAL_MINUTES отправляет её сжатую копию в чат BACKUP_CHAT_ID
  и закрепляет. После «ценных» изменений (браки, анкеты, ники, таджик дня) — уже через ~5 минут,
  и ещё раз при выключении (например, перед обновлением из гита).
- При запуске, если базы нет (хостинг почистил диск), бот скачивает закреплённый бэкап.
- В чате остаётся последний бэкап за каждый день, промежуточные удаляются.
"""

import asyncio
import gzip
import logging
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram.types import BufferedInputFile, Document, Message

from config import BACKUP_CHAT_ID, BACKUP_INTERVAL_MINUTES, DB_PATH, LEGACY_DB_PATHS, LOCAL_TZ
from db import summarize
from loader import bot, db
from textstats import count_with_word

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "chatbot-db-"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # больше Bot API скачать не даст
IMPORTANT_DELAY_SECONDS = 5 * 60
_MESSAGES_RE = re.compile(r"msgs=(\d+)")


@dataclass
class _State:
    enabled: bool = False
    last_backup_at: datetime | None = None
    last_message_id: int | None = None
    last_day: str | None = None
    last_messages: int = 0
    saved_revision: int = 0
    saved_important_revision: int = 0
    last_attempt: float = 0.0


state = _State()
_lock = asyncio.Lock()


# ── Работа с файлами (выполняется в отдельном потоке) ────────────────────────


def _database_summary(path: Path, readonly: bool = False) -> dict[str, int] | None:
    """readonly — только посмотреть, не трогая файл (обычное подключение при закрытии вливает -wal в базу)."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        if readonly:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(path)
        try:
            return summarize(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None


def _database_has_data(path: Path) -> bool:
    return any((_database_summary(path) or {}).values())


def _move_legacy_database():
    """Старая версия бота держала базу в корне проекта — переносим её в DB_PATH.
    Срабатывает, даже если после обновления бот успел немного поработать на новой пустой базе:
    тогда старая база должна быть намного больше новой, а новая сохраняется рядом на всякий случай."""
    current = _database_summary(DB_PATH) or {}
    for legacy in LEGACY_DB_PATHS:
        if legacy.resolve() == DB_PATH.resolve():
            continue
        old = _database_summary(legacy, readonly=True) or {}
        if not any(old.values()):
            continue
        if any(current.values()) and old["messages"] <= 10 * current["messages"]:
            logger.warning("Нашёл старую базу %s, но текущая уже не пустая — не трогаю", legacy)
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if any(current.values()):
            shutil.copy2(DB_PATH, DB_PATH.with_name(DB_PATH.name + ".before-legacy"))
        src = sqlite3.connect(legacy)
        try:
            dst = sqlite3.connect(DB_PATH)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        # переименовываем, чтобы старый файл больше никогда не подхватился вместо свежего бэкапа
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{legacy}{suffix}")
            if path.exists():
                with suppress(OSError):
                    path.replace(path.with_name(path.name + ".moved"))
        logger.warning("База перенесена из %s в %s", legacy, DB_PATH)
        return


def _snapshot(gz_path: Path) -> dict:
    """Согласованная копия живой базы → gzip. Возвращает сводку по копии."""
    raw_path = gz_path.with_suffix("")
    src = sqlite3.connect(DB_PATH)
    try:
        try:
            src.execute("VACUUM INTO ?", (str(raw_path),))
        except sqlite3.OperationalError:
            dst = sqlite3.connect(raw_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
    finally:
        src.close()
    conn = sqlite3.connect(raw_path)
    try:
        info = summarize(conn)
    finally:
        conn.close()
    with open(raw_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    raw_path.unlink()
    return info


def _unpack_and_check(downloaded: Path, target: Path):
    with open(downloaded, "rb") as f:
        is_gzip = f.read(2) == b"\x1f\x8b"
    if is_gzip:
        with gzip.open(downloaded, "rb") as f_in, open(target, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        shutil.copyfile(downloaded, target)
    try:
        conn = sqlite3.connect(target)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"это не база SQLite ({e})")
    if result != "ok":
        raise RuntimeError(f"бэкап повреждён: {result}")
    if "users" not in tables:
        raise RuntimeError("в файле нет таблиц бота")


def _install_database(new_db: Path):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, DB_PATH.with_name(DB_PATH.name + ".before-restore"))
    for suffix in ("-wal", "-shm"):
        Path(f"{DB_PATH}{suffix}").unlink(missing_ok=True)
    shutil.move(str(new_db), str(DB_PATH))


# ── Telegram ──────────────────────────────────────────────────────────────────


async def _pinned_backup() -> Message | None:
    chat = await bot.get_chat(BACKUP_CHAT_ID)
    message = chat.pinned_message
    if message and message.document and (message.document.file_name or "").startswith(BACKUP_PREFIX):
        return message
    return None


async def _download(document: Document) -> Path:
    """Скачивает и проверяет бэкап. Возвращает путь к готовому файлу базы во временной папке."""
    if document.file_size and document.file_size > MAX_DOWNLOAD_BYTES:
        raise RuntimeError("файл больше 20 МБ — Bot API такой не скачает")
    tmp_dir = Path(tempfile.mkdtemp(prefix="chatbot-restore-"))
    try:
        downloaded = tmp_dir / "downloaded"
        await bot.download(document, destination=downloaded, timeout=120)
        target = tmp_dir / "restored.sqlite"
        await asyncio.to_thread(_unpack_and_check, downloaded, target)
        downloaded.unlink(missing_ok=True)
        return target
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _remember_backup_message(message: Message):
    state.last_backup_at = message.date
    state.last_message_id = message.message_id
    state.last_day = message.date.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
    found = _MESSAGES_RE.search(message.caption or "")
    state.last_messages = int(found.group(1)) if found else 0


async def prepare_database():
    """При запуске: переносим старую базу, при необходимости восстанавливаемся из бэкапа, открываем базу."""
    await asyncio.to_thread(_move_legacy_database)
    had_data = await asyncio.to_thread(_database_has_data, DB_PATH)
    if BACKUP_CHAT_ID is None:
        logger.warning("Бэкапы базы в Telegram выключены (BACKUP_CHAT_ID=off)")
        db.open()
        return

    pinned, lookup_error = None, None
    for attempt in range(3):
        try:
            pinned, lookup_error = await _pinned_backup(), None
            break
        except Exception as e:
            lookup_error = e
            logger.warning("Не получилось найти бэкап (попытка %s): %s", attempt + 1, e)
            await asyncio.sleep(3 * (attempt + 1))

    restore_error = None
    if pinned and not had_data:
        for attempt in range(3):
            try:
                restored = await _download(pinned.document)
                await asyncio.to_thread(_install_database, restored)
                shutil.rmtree(restored.parent, ignore_errors=True)
                logger.warning("База восстановлена из бэкапа от %s", pinned.date)
                restore_error = None
                break
            except Exception as e:
                restore_error = e
                logger.error("Не получилось восстановить базу (попытка %s): %s", attempt + 1, e)
                await asyncio.sleep(3 * (attempt + 1))

    db.open()
    if pinned:
        _remember_backup_message(pinned)
    state.saved_revision = 0
    state.saved_important_revision = -1 if had_data else 0  # локальная база могла уйти вперёд бэкапа
    state.last_attempt = time.monotonic()

    if lookup_error and not had_data:
        # Не знаем, есть ли бэкап. Лучше не бэкапиться, чем затереть хороший бэкап пустой базой.
        logger.error("Бэкапы выключены до перезапуска: чат BACKUP_CHAT_ID недоступен (%s)", lookup_error)
    elif restore_error:
        with suppress(Exception):
            await bot.send_message(
                BACKUP_CHAT_ID,
                f"⚠️ Не смог восстановить базу из закреплённого бэкапа: <code>{escape(str(restore_error))}</code>\n\n"
                "Автобэкапы выключены, чтобы не затереть его пустой базой. "
                "Ответь /restore на нужный файл бэкапа, а если текущая база устраивает — отправь /backup.",
            )
    else:
        state.enabled = True


async def make_backup(reason: str) -> str:
    """Делает бэкап прямо сейчас. Возвращает короткое описание результата."""
    if BACKUP_CHAT_ID is None:
        raise RuntimeError("BACKUP_CHAT_ID не задан")
    async with _lock:
        state.last_attempt = time.monotonic()
        revision, important_revision = db.revision, db.important_revision
        now = datetime.now(LOCAL_TZ)
        file_name = f"{BACKUP_PREFIX}{now:%Y-%m-%d_%H-%M}.sqlite.gz"
        tmp_dir = Path(tempfile.mkdtemp(prefix="chatbot-backup-"))
        try:
            gz_path = tmp_dir / file_name
            info = await asyncio.to_thread(_snapshot, gz_path)
            data = gz_path.read_bytes()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        size_mb = len(data) / 1024 / 1024
        caption = (
            f"🗄 Бэкап базы · {now:%d.%m.%Y %H:%M}\n"
            f"💬 {count_with_word(info['messages'], ('сообщение', 'сообщения', 'сообщений'))} · "
            f"👥 {count_with_word(info['users'], ('человек', 'человека', 'человек'))} · "
            f"💍 {count_with_word(info['marriages'], ('брак', 'брака', 'браков'))}\n"
            f"📦 {size_mb:.1f} МБ · {reason}\n"
            f"#backup msgs={info['messages']}"
        )
        if len(data) > MAX_DOWNLOAD_BYTES * 0.9:
            caption += "\n⚠️ Бэкап почти 20 МБ — скоро Telegram не даст скачать его обратно!"
        sent = await bot.send_document(
            BACKUP_CHAT_ID,
            BufferedInputFile(data, filename=file_name),
            caption=caption,
            disable_notification=True,
        )

        pinned = False
        try:
            pinned = await bot.pin_chat_message(BACKUP_CHAT_ID, sent.message_id, disable_notification=True)
        except Exception as e:
            logger.error("Не получилось закрепить бэкап: %s", e)

        previous_id, previous_day, previous_messages = state.last_message_id, state.last_day, state.last_messages
        today = now.strftime("%Y-%m-%d")
        if pinned and previous_id and previous_id != sent.message_id:
            with suppress(Exception):
                if previous_day == today and info["messages"] >= previous_messages:
                    await bot.delete_message(BACKUP_CHAT_ID, previous_id)
                else:
                    await bot.unpin_chat_message(BACKUP_CHAT_ID, message_id=previous_id)
        if pinned:
            _remember_backup_message(sent)
        state.last_backup_at = sent.date
        state.saved_revision, state.saved_important_revision = revision, important_revision
        state.enabled = True
        logger.info("Бэкап готов: %.1f МБ (%s)", size_mb, reason)
        return f"{size_mb:.1f} МБ, {count_with_word(info['messages'], ('сообщение', 'сообщения', 'сообщений'))}"


async def restore_from_document(document: Document) -> str:
    """Откатывает базу к присланному файлу бэкапа (команда /restore)."""
    import members
    import sglypa

    restored = await _download(document)
    async with _lock:
        # между закрытием и открытием нет await — обработчики не успеют обратиться к закрытой базе
        db.close()
        try:
            _install_database(restored)
        finally:
            db.open()
            shutil.rmtree(restored.parent, ignore_errors=True)
        members.reset_caches()
        sglypa.reset()
        state.enabled = True
        state.saved_important_revision = -1  # через пару минут свежий бэкап закрепится вместо старого
        info = db.summary()
    return count_with_word(info["messages"], ("сообщение", "сообщения", "сообщений"))


async def backup_loop():
    while True:
        await asyncio.sleep(60)
        if not state.enabled or not db.is_open:
            continue
        important = db.important_revision != state.saved_important_revision
        if db.revision == state.saved_revision and not important:
            continue
        elapsed = time.monotonic() - state.last_attempt
        if elapsed >= BACKUP_INTERVAL_MINUTES * 60 or (important and elapsed >= IMPORTANT_DELAY_SECONDS):
            try:
                await make_backup("по расписанию")
            except Exception:
                logger.exception("Автобэкап не удался, попробую позже")


async def final_backup():
    if not state.enabled or not db.is_open:
        return
    if db.revision == state.saved_revision and db.important_revision == state.saved_important_revision:
        return
    try:
        await asyncio.wait_for(make_backup("перед выключением"), timeout=25)
    except Exception as e:
        logger.error("Бэкап перед выключением не удался: %s", e)
