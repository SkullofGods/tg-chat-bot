import os
import re
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _int_env(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Переменная окружения {name} должна быть целым числом, а сейчас там {raw!r}")


# bothost.ru кладёт токен в BOT_TOKEN (и дублирует в TELEGRAM_BOT_TOKEN / API_TOKEN)
BOT_TOKEN: str = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("API_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")


def _default_db_path() -> Path:
    # На bothost.ru папка /app/data не затирается при обновлении из гита
    hosting_data = Path("/app/data")
    if hosting_data.is_dir():
        return hosting_data / "bot.db"
    return BASE_DIR / "data" / "bot.db"


# Где лежит база. Можно переопределить через DATABASE_PATH
DB_PATH: Path = Path(os.getenv("DATABASE_PATH") or os.getenv("DB_PATH") or _default_db_path())

# Старое расположение базы (в корне проекта) — оттуда база один раз переедет в DB_PATH
LEGACY_DB_PATHS: list[Path] = list(dict.fromkeys([BASE_DIR / "bot.db", Path.cwd() / "bot.db"]))

# Хозяин бота: только ему доступны скрытые /debug и /say (по умолчанию — @SkullOfGods)
OWNER_ID: int = _int_env("OWNER_ID", 384144294)


def _backup_chat_id() -> int | None:
    raw = os.getenv("BACKUP_CHAT_ID", "").strip().lower()
    if not raw:
        return OWNER_ID  # по умолчанию бэкапы идут хозяину в личку
    if raw in ("0", "off", "no", "false", "нет"):
        return None
    return _int_env("BACKUP_CHAT_ID")


# Куда слать бэкапы базы. По умолчанию — личка хозяина (ему нужно один раз написать боту /start).
# BACKUP_CHAT_ID=off — выключить бэкапы.
BACKUP_CHAT_ID: int | None = _backup_chat_id()

# Как часто делать бэкап, если в базе что-то поменялось
BACKUP_INTERVAL_MINUTES: int = max(10, _int_env("BACKUP_INTERVAL_MINUTES", 60))

# Часовой пояс беседы: от него зависят «таджик дня», ночные сообщения и время в бэкапах
TZ_OFFSET_HOURS: int = _int_env("TZ_OFFSET_HOURS", 3)
LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))

# Во сколько рекордсмены бесконечных игр получают ежедневную премию
ARCADE_PRIZE_HOUR: int = _int_env("ARCADE_PRIZE_HOUR", 10)

# Кулдаун оргии в часах
ORGY_COOLDOWN_HOURS: int = _int_env("ORGY_COOLDOWN_HOURS", 24)

# Длительность опроса оргии в секундах
ORGY_POLL_DURATION_SECONDS: int = _int_env("ORGY_POLL_DURATION_SECONDS", 60)

# Мини-приложение казино. Публичный адрес — домен бота на хостинге (bothost кладёт его в DOMAIN,
# когда в настройках включён домен). Этот же адрес указывается в BotFather.
WEBAPP_URL: str = (os.getenv("WEBAPP_URL") or (f"https://{os.getenv('DOMAIN')}" if os.getenv("DOMAIN") else "")).rstrip("/")
# Порт веб-сервера: bothost передаёт его в PORT
WEB_PORT: int | None = _int_env("PORT", 3000 if WEBAPP_URL else None)
# Короткое имя приложения, если оно создано через /newapp в BotFather. Пусто — «главное» мини-приложение бота
CASINO_APP_NAME: str = os.getenv("CASINO_APP_NAME", "").strip()
def _phone_env(name: str) -> str:
    """Телефон в виде +79991234567 (принимает 8…, 7…, +7…, с пробелами и дефисами)."""
    digits = re.sub(r"\D", "", os.getenv(name, ""))
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    return f"+{digits}" if digits else ""


# Донаты рублями по СБП: номер телефона и (необязательно) банк. Показываются только участникам беседы,
# которые сами нажмут «Поддержать» в казино или напишут боту /donate. Пусто — донаты выключены.
# Держи номер в переменных окружения на хостинге, а не в коде: репозиторий публичный.
DONATE_PHONE: str = _phone_env("DONATE_PHONE")
DONATE_BANK: str = os.getenv("DONATE_BANK", "").strip()
# Только для разработки на своём компьютере: пускать в казино без Telegram. На хостинге НЕ задавать!
WEBAPP_DEV_USER_ID: int | None = _int_env("WEBAPP_DEV_USER_ID")

# Кулдаун /d20, /who, /dnd, дуэлей и русской рулетки (на человека и команду)
GAME_COOLDOWN_MINUTES: int = _int_env("GAME_COOLDOWN_MINUTES", 20)

# Спам-день: примерно раз в две недели с этого часа и до 23:59 все кулдауны — несколько секунд
SPAM_DAY_HOUR: int = _int_env("SPAM_DAY_HOUR", 10)
SPAM_DAY_COOLDOWN_SECONDS: int = _int_env("SPAM_DAY_COOLDOWN_SECONDS", 5)

# Сглыпа-режим: бот вставляет случайную фразу раз в столько-то сообщений
SGLYPA_MIN_MESSAGES: int = max(1, _int_env("SGLYPA_MIN_MESSAGES", 150))
SGLYPA_MAX_MESSAGES: int = max(SGLYPA_MIN_MESSAGES, _int_env("SGLYPA_MAX_MESSAGES", 200))

# Экспорт истории чата из Telegram Desktop (JSON)
HISTORY_FILE: str = os.getenv("HISTORY_FILE", "result.json")
