import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Кулдаун оргии в часах
ORGY_COOLDOWN_HOURS: int = int(os.getenv("ORGY_COOLDOWN_HOURS", "24"))

# Длительность опроса оргии в секундах (по умолчанию 60)
ORGY_POLL_DURATION_SECONDS: int = int(os.getenv("ORGY_POLL_DURATION_SECONDS", "60"))
