import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Coroutine

from config import LOCAL_TZ
from db import parse_utc
from textstats import count_with_word

_background_tasks: set[asyncio.Task] = set()


class RecentLogs(logging.Handler):
    """Хранит последние предупреждения и ошибки — их видно в /debug без доступа к логам хостинга."""

    def __init__(self, capacity: int = 30):
        super().__init__(level=logging.WARNING)
        self.records: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord):
        try:
            text = f"{datetime.now(LOCAL_TZ):%d.%m %H:%M:%S} {record.levelname} {record.name}: {record.getMessage()}"
            if record.exc_info:
                text += " | " + logging.Formatter().formatException(record.exc_info).strip().splitlines()[-1]
            self.records.append(text[:700])
        except Exception:
            pass


recent_logs_handler = RecentLogs()
recent_logs = recent_logs_handler.records


def spawn(coro: Coroutine) -> asyncio.Task:
    """asyncio.create_task, но задачу не соберёт сборщик мусора на полпути."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def local_today() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def format_duration_since(iso_dt: str | None) -> str:
    """«3 месяца 2 дня» — сколько прошло с момента iso_dt."""
    started = parse_utc(iso_dt)
    if started is None:
        return "неизвестно сколько"
    delta = datetime.now(timezone.utc) - started
    days, hours, minutes = delta.days, delta.seconds // 3600, delta.seconds % 3600 // 60
    years_f, months_f, days_f = ("год", "года", "лет"), ("месяц", "месяца", "месяцев"), ("день", "дня", "дней")
    hours_f, minutes_f = ("час", "часа", "часов"), ("минуту", "минуты", "минут")
    if days >= 365:
        big, small = (days // 365, years_f), ((days % 365) // 30, months_f)
    elif days >= 30:
        big, small = (days // 30, months_f), (days % 30, days_f)
    elif days >= 1:
        big, small = (days, days_f), (hours, hours_f)
    elif hours >= 1:
        big, small = (hours, hours_f), (minutes, minutes_f)
    else:
        return count_with_word(max(minutes, 1), minutes_f)
    parts = [count_with_word(*big)]
    if small[0]:
        parts.append(count_with_word(*small))
    return " ".join(parts)
