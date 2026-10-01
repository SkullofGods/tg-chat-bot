"""Обратный отсчёт до события и запланированный пост по его наступлении.

Хозяин настраивает всё в личке (команды handlers/countdown.py): целевое время, беседу,
интервал отсчёта, шаблон с {d} {h} {m} {s}, финальный текст и медиа финального поста
(видео/фото пересылается боту в личку). Всё хранится в meta базы — в репозитории
никаких текстов и медиа нет. Луп раз в 30 секунд: до цели — постит отсчёт раз в
interval минут, в момент цели — один раз финальный пост с медиа."""

import asyncio
import json
import logging
from datetime import datetime

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile

from config import LOCAL_TZ
from loader import bot, db

logger = logging.getLogger(__name__)

CFG_KEY = "countdown_cfg"
MEDIA_KEY = "countdown_media"
LAST_KEY = "countdown_last_post"
DONE_KEY = "countdown_done"
WAITING_MEDIA_KEY = "countdown_waiting_media"

DEFAULT_TEXT = "⏳ {left}"
DEFAULT_FINAL = "✅ Пора!"
DEFAULT_INTERVAL = 180


def cfg() -> dict:
    raw = db.get_meta(CFG_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def save_cfg(c: dict) -> None:
    db.set_meta(CFG_KEY, json.dumps(c, ensure_ascii=False), important=True)


def target_dt(c: dict):
    try:
        return datetime.fromisoformat(c["target"]).astimezone(LOCAL_TZ)
    except Exception:
        return None


def _left_str(target: datetime) -> str:
    delta = target - datetime.now(LOCAL_TZ)
    total = max(0, int(delta.total_seconds()))
    d, rest = divmod(total, 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    parts = []
    if d:
        parts.append(f"{d} д")
    if d or h:
        parts.append(f"{h} ч")
    parts.append(f"{m} мин")
    if not d and not h:
        parts.append(f"{s} с")
    return " ".join(parts)


def render(c: dict) -> str:
    target = target_dt(c)
    left = _left_str(target)
    base = c.get("text", DEFAULT_TEXT)
    try:
        return base.format(left=left, d=0, h=0, m=0)
    except Exception:
        return base + " " + left


def _send(chat_id: int, text: str):
    media = db.get_meta(MEDIA_KEY)
    if media:
        kind, _, file_id = media.partition(":")
        if kind == "video":
            return bot.send_video(chat_id, file_id, caption=text or None)
        if kind == "photo":
            return bot.send_photo(chat_id, file_id, caption=text or None)
        if kind == "animation":
            return bot.send_animation(chat_id, file_id, caption=text or None)
    return bot.send_message(chat_id, text)


async def check_once() -> bool:
    """Один тик планировщика: True — что-то запостили."""
    c = cfg()
    if not c or db.get_meta(DONE_KEY):
        return False
    target = target_dt(c)
    chat_id = c.get("chat_id")
    if target is None or not chat_id:
        return False
    now = datetime.now(LOCAL_TZ)
    try:
        if now >= target:
            # сначала пост, потом done: если пост упадёт — флаг не встанет и тик повторит попытку
            await _send(chat_id, c.get("final_text", DEFAULT_FINAL))
            db.set_meta(DONE_KEY, "1", important=True)
            return True
        last = db.get_meta(LAST_KEY)
        interval = int(c.get("interval_min", DEFAULT_INTERVAL)) * 60
        if last and now.timestamp() - float(last) < interval:
            return False
        db.set_meta(LAST_KEY, str(now.timestamp()))
        await bot.send_message(chat_id, render(c))
        return True
    except TelegramForbiddenError:
        logger.warning("Отсчёт: бота выгнали из беседы %s", chat_id)
    except TelegramBadRequest as e:
        logger.warning("Отсчёт: не смог в %s: %s", chat_id, e)
    except Exception as e:
        logger.warning("Отсчёт: %s", e)
    return False


async def loop():
    while True:
        try:
            await check_once()
        except Exception:
            logger.exception("Тик отсчёта не удался")
        await asyncio.sleep(30)


def status_text() -> str:
    c = cfg()
    if not c:
        return ("Таймер не настроен. Команды (только здесь, в личке):\n"
                "/countdown_time 2026-10-03T18:00 — когда наступит\n"
                "/countdown_chat <id> — куда постить (или /cd_here в самой беседе)\n"
                "/countdown_every 180 — как часто постить отсчёт (минуты)\n"
                "/countdown_text ⏳ Осталось: {left} — шаблон отсчёта\n"
                "/countdown_final Текст — что запостить по наступлении\n"
                "🎬 Медиа к финалу: перешли мне видео (или фото), я его запомню\n"
                "/countdown_off — выключить и стереть")
    target = target_dt(c)
    lines = ["⏱ Таймер:"]
    lines.append(f"🎯 Цель: {target:%d.%m.%Y %H:%M} (осталось {(_left_str(target))})" if target else "🎯 Цель: не задана")
    lines.append(f"💬 Беседа: <code>{c.get('chat_id', '—')}</code>")
    lines.append(f"🔁 Отсчёт каждые {c.get('interval_min', DEFAULT_INTERVAL)} мин")
    lines.append(f"📜 Шаблон: {c.get('text', DEFAULT_TEXT)}")
    lines.append(f"🏁 Финал: {c.get('final_text', DEFAULT_FINAL)}")
    lines.append(f"🎬 Медиа: {'загружено' if db.get_meta(MEDIA_KEY) else 'нет'}")
    if db.get_meta(DONE_KEY):
        lines.append("✅ Финал уже отправлен")
    elif c.get("media_sent"):
        lines.append("⏳ Финал отправляется")
    return "\n".join(lines)
