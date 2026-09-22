import json
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

SCHEMA_VERSION = 1
SUPERGROUP_ID_SHIFT = 1_000_000_000_000  # экспорт Telegram пишет id супергруппы без префикса -100

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        user_id   INTEGER PRIMARY KEY,
        username  TEXT DEFAULT '',
        full_name TEXT DEFAULT '',
        is_bot    INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS nicknames (
        user_id  INTEGER PRIMARY KEY,
        nickname TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS anketas (
        user_id INTEGER PRIMARY KEY,
        text    TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS awaiting_anketa (
        user_id INTEGER,
        chat_id INTEGER,
        PRIMARY KEY (user_id, chat_id)
    )""",
    """CREATE TABLE IF NOT EXISTS marriages (
        user1_id   INTEGER,
        user2_id   INTEGER,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user1_id, user2_id),
        CHECK (user1_id < user2_id)
    )""",
    """CREATE TABLE IF NOT EXISTS marriage_proposals (
        proposer_id INTEGER,
        target_id   INTEGER,
        chat_id     INTEGER,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (proposer_id, target_id, chat_id)
    )""",
    """CREATE TABLE IF NOT EXISTS known_chats (
        chat_id INTEGER PRIMARY KEY
    )""",
    """CREATE TABLE IF NOT EXISTS chat_members (
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        is_member  INTEGER NOT NULL DEFAULT 1,
        checked_at TEXT,
        PRIMARY KEY (chat_id, user_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS message_stats (
        chat_id       INTEGER NOT NULL,
        user_id       INTEGER NOT NULL,
        message_count INTEGER NOT NULL DEFAULT 0,
        word_count    INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        stickers      INTEGER NOT NULL DEFAULT 0,
        voices        INTEGER NOT NULL DEFAULT 0,
        media         INTEGER NOT NULL DEFAULT 0,
        questions     INTEGER NOT NULL DEFAULT 0,
        caps          INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS word_stats (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        word    TEXT NOT NULL,
        count   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id, word)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS hourly_stats (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        hour    INTEGER NOT NULL,
        count   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id, hour)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS phrases (
        id      INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        text    TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS phrases_by_chat ON phrases (chat_id, id)",
    """CREATE TABLE IF NOT EXISTS stickers (
        chat_id        INTEGER NOT NULL,
        file_unique_id TEXT NOT NULL,
        file_id        TEXT NOT NULL,
        uses           INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (chat_id, file_unique_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS tajik_of_day (
        chat_id    INTEGER NOT NULL,
        day        TEXT NOT NULL,
        user_id    INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, day)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS wallets (
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        balance    INTEGER NOT NULL,
        last_bonus TEXT,
        games      INTEGER NOT NULL DEFAULT 0,
        won        INTEGER NOT NULL DEFAULT 0,
        lost       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    ) WITHOUT ROWID""",
    # Статистика казино по играм. special — джекпоты 777, угаданные числа, монетки на ребре, смерти в русской рулетке
    """CREATE TABLE IF NOT EXISTS casino_stats (
        chat_id  INTEGER NOT NULL,
        user_id  INTEGER NOT NULL,
        game     TEXT NOT NULL,
        plays    INTEGER NOT NULL DEFAULT 0,
        wagered  INTEGER NOT NULL DEFAULT 0,
        returned INTEGER NOT NULL DEFAULT 0,
        best_win INTEGER NOT NULL DEFAULT 0,
        special  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id, game)
    ) WITHOUT ROWID""",
    # Недоигранные раздачи блэкджека: ставка уже списана, карты лежат тут (JSON), переживут перезапуск бота
    """CREATE TABLE IF NOT EXISTS blackjack_hands (
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        hand       TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS donations (
        id         INTEGER PRIMARY KEY,
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        amount     INTEGER NOT NULL,
        message    TEXT NOT NULL DEFAULT '',
        status     TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        decided_at TEXT
    )""",
    # Ставки за общими столами (рулетка, скачки). Деньги списаны сразу, payout = NULL — раунд ещё не разыгран
    """CREATE TABLE IF NOT EXISTS table_bets (
        id         INTEGER PRIMARY KEY,
        chat_id    INTEGER NOT NULL,
        game       TEXT NOT NULL,
        round      INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        kind       TEXT NOT NULL,
        pick       INTEGER,
        amount     INTEGER NOT NULL,
        odds       REAL NOT NULL,
        payout     INTEGER,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS table_bets_round ON table_bets (chat_id, game, round)",
    "CREATE INDEX IF NOT EXISTS table_bets_open ON table_bets (game, round) WHERE payout IS NULL",
    # Последние результаты игр для истории в приложении (JSON). У общих столов есть номер раунда
    """CREATE TABLE IF NOT EXISTS casino_history (
        id         INTEGER PRIMARY KEY,
        chat_id    INTEGER NOT NULL,
        game       TEXT NOT NULL,
        round      INTEGER,
        data       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS casino_history_round ON casino_history (chat_id, game, round) "
    "WHERE round IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS casino_history_recent ON casino_history (chat_id, game, id)",
    # Вклады в банке казино. Время — unix-секунды
    """CREATE TABLE IF NOT EXISTS bank_deposits (
        id        INTEGER PRIMARY KEY,
        chat_id   INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        amount    INTEGER NOT NULL,
        interest  INTEGER NOT NULL,
        term      TEXT NOT NULL,
        opened_at INTEGER NOT NULL,
        ends_at   INTEGER NOT NULL,
        status    TEXT NOT NULL DEFAULT 'active',
        closed_at INTEGER
    )""",
    # Бомж-тамагочи: у каждого свой. Время — unix-секунды
    """CREATE TABLE IF NOT EXISTS bums (
        chat_id      INTEGER NOT NULL,
        user_id      INTEGER NOT NULL,
        name         TEXT NOT NULL,
        level        INTEGER NOT NULL DEFAULT 0,
        invested     INTEGER NOT NULL DEFAULT 0,
        earned       INTEGER NOT NULL DEFAULT 0,
        fed_until    INTEGER NOT NULL DEFAULT 0,
        collected_at INTEGER NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    ) WITHOUT ROWID""",
    # Рекорды в бесконечных играх: у каждого свой лучший результат
    """CREATE TABLE IF NOT EXISTS arcade_records (
        chat_id INTEGER NOT NULL,
        game    TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        score   INTEGER NOT NULL,
        made_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, game, user_id)
    ) WITHOUT ROWID""",
    # Начатая прогулка: переживает перезапуск бота, чтобы квест можно было продолжить с того же места
    """CREATE TABLE IF NOT EXISTS walks (
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        quest      TEXT NOT NULL,
        node       TEXT NOT NULL,
        steps      INTEGER NOT NULL DEFAULT 0,
        started_at INTEGER NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    ) WITHOUT ROWID""",
    # Счётчики для ачивок: победы по играм, смены, найденные концовки квестов и прочее
    """CREATE TABLE IF NOT EXISTS counters (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        key     TEXT NOT NULL,
        value   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id, key)
    ) WITHOUT ROWID""",
    # Ачивки: level — сколько ступеней (звёзд) взято, награда за каждую выдаётся один раз
    """CREATE TABLE IF NOT EXISTS achievements (
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        key        TEXT NOT NULL,
        level      INTEGER NOT NULL DEFAULT 0,
        paid       INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, user_id, key)
    ) WITHOUT ROWID""",
    # Столы мультиплеера: одна строка — один стол. seats и state — JSON, поэтому правила игр
    # могут меняться, не трогая схему. Стол переживает перезапуск бота и деплой
    """CREATE TABLE IF NOT EXISTS mp_tables (
        id         INTEGER PRIMARY KEY,
        chat_id    INTEGER NOT NULL,
        game       TEXT NOT NULL,
        stake      INTEGER NOT NULL DEFAULT 0,
        status     TEXT NOT NULL DEFAULT 'gathering',
        owner_id   INTEGER NOT NULL,
        seats      TEXT NOT NULL DEFAULT '[]',
        state      TEXT NOT NULL DEFAULT '{}',
        turn_id    INTEGER,
        turn_until INTEGER,
        pot        INTEGER NOT NULL DEFAULT 0,
        message_id INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS mp_tables_live ON mp_tables (chat_id, status)",
    "CREATE INDEX IF NOT EXISTS mp_tables_turn ON mp_tables (turn_until) WHERE status = 'playing'",
    "CREATE INDEX IF NOT EXISTS bank_deposits_user ON bank_deposits (chat_id, user_id, status)",
    "CREATE INDEX IF NOT EXISTS bank_deposits_due ON bank_deposits (ends_at) WHERE status = 'active'",
]

START_BALANCE = 1000  # таджикоинов у каждого на старте


def utcnow_iso() -> str:
    """Время в UTC без таймзоны — в таком формате лежат старые записи."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def parse_utc(iso_dt: str | None) -> Optional[datetime]:
    if not iso_dt:
        return None
    try:
        dt = datetime.fromisoformat(iso_dt)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def summarize(conn: sqlite3.Connection) -> dict[str, int]:
    """Короткая сводка по базе (для подписи бэкапа и проверок)."""
    def scalar(sql: str) -> int:
        try:
            return int(conn.execute(sql).fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0

    return {
        "users": scalar("SELECT COUNT(*) FROM users WHERE COALESCE(is_bot, 0) = 0"),
        "messages": scalar("SELECT SUM(message_count) FROM message_stats"),
        "marriages": scalar("SELECT COUNT(*) FROM marriages"),
        "anketas": scalar("SELECT COUNT(*) FROM anketas"),
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        # Счётчики изменений: по ним фоновая задача понимает, что пора делать бэкап
        self.revision = 0
        self.important_revision = 0

    # ── Подключение ────────────────────────────────────────────────────────────

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        self._migrate()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("База ещё не открыта")
        return self._conn

    def connect_reader(self) -> sqlite3.Connection:
        """Отдельное подключение для тяжёлых запросов в фоновом потоке."""
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self, important: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        self.revision += 1
        if important:
            self.important_revision += 1

    # ── Миграции ───────────────────────────────────────────────────────────────

    def _migrate(self):
        conn = self.conn
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            for statement in _SCHEMA:  # таблицы, добавленные в новых версиях бота, появятся сами
                conn.execute(statement)
            return
        rebuilt_words = False
        with self._tx(important=True):
            for statement in _SCHEMA:
                conn.execute(statement)

            # Колонки, которых не было в старой версии бота
            _add_column(conn, "users", "is_bot", "INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE users SET is_bot = 1 WHERE LOWER(COALESCE(username, '')) LIKE '%bot'")
            if "created_at" not in _columns(conn, "marriages"):
                conn.execute("ALTER TABLE marriages ADD COLUMN created_at TEXT")
                conn.execute("UPDATE marriages SET created_at = ? WHERE created_at IS NULL", (utcnow_iso(),))
            for column in ("stickers", "voices", "media", "questions", "caps"):
                _add_column(conn, "message_stats", column, "INTEGER NOT NULL DEFAULT 0")

            # Таблица слов без rowid занимает в два раза меньше места
            word_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'word_stats'"
            ).fetchone()[0]
            if "WITHOUT ROWID" not in word_sql.upper():
                conn.execute("""
                    CREATE TABLE word_stats_new (
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        word    TEXT NOT NULL,
                        count   INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (chat_id, user_id, word)
                    ) WITHOUT ROWID
                """)
                conn.execute("""
                    INSERT INTO word_stats_new (chat_id, user_id, word, count)
                    SELECT chat_id, user_id, word, SUM(count) FROM word_stats GROUP BY chat_id, user_id, word
                """)
                conn.execute("DROP TABLE word_stats")
                conn.execute("ALTER TABLE word_stats_new RENAME TO word_stats")
                rebuilt_words = True

            # Старый импорт истории записал чат под id из экспорта (без -100) — склеиваем с настоящим чатом
            real_chats = {row[0] for row in conn.execute("SELECT chat_id FROM known_chats WHERE chat_id < 0")}
            real_chats |= {row[0] for row in conn.execute("SELECT DISTINCT chat_id FROM message_stats WHERE chat_id < 0")}
            exported_chats = {row[0] for row in conn.execute("SELECT DISTINCT chat_id FROM message_stats WHERE chat_id >= 0")}
            exported_chats |= {row[0] for row in conn.execute("SELECT DISTINCT chat_id FROM word_stats WHERE chat_id >= 0")}
            for exported_id in exported_chats:
                target = -(SUPERGROUP_ID_SHIFT + exported_id)
                if target not in real_chats and -exported_id in real_chats:
                    target = -exported_id
                self._merge_chat_stats(conn, exported_id, target)
                conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, 'legacy')", (f"history_import:{target}",)
                )
            conn.execute("DELETE FROM known_chats WHERE chat_id >= 0")

            # Все, кто когда-то писал в чат, считаются участниками, пока Telegram не скажет обратное
            conn.execute("""
                INSERT OR IGNORE INTO chat_members (chat_id, user_id, is_member)
                SELECT chat_id, user_id, 1 FROM message_stats WHERE chat_id < 0
            """)

            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if rebuilt_words:
            conn.execute("VACUUM")

    @staticmethod
    def _merge_chat_stats(conn: sqlite3.Connection, source_chat: int, target_chat: int):
        conn.execute("""
            INSERT INTO message_stats (chat_id, user_id, message_count, word_count, created_at, updated_at)
            SELECT ?, user_id, message_count, word_count, created_at, updated_at
            FROM message_stats WHERE chat_id = ?
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                message_count = message_count + excluded.message_count,
                word_count    = word_count + excluded.word_count,
                created_at    = MIN(created_at, excluded.created_at),
                updated_at    = MAX(updated_at, excluded.updated_at)
        """, (target_chat, source_chat))
        conn.execute("DELETE FROM message_stats WHERE chat_id = ?", (source_chat,))
        conn.execute("""
            INSERT INTO word_stats (chat_id, user_id, word, count)
            SELECT ?, user_id, word, count FROM word_stats WHERE chat_id = ?
            ON CONFLICT(chat_id, user_id, word) DO UPDATE SET count = count + excluded.count
        """, (target_chat, source_chat))
        conn.execute("DELETE FROM word_stats WHERE chat_id = ?", (source_chat,))

    # ── Meta ───────────────────────────────────────────────────────────────────

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str, important: bool = False):
        with self._tx(important=important) as c:
            c.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def summary(self) -> dict[str, int]:
        return summarize(self.conn)

    # ── Users ──────────────────────────────────────────────────────────────────

    def upsert_user(self, user_id: int, username: str, full_name: str, is_bot: bool):
        with self._tx() as c:
            c.execute(
                "INSERT INTO users (user_id, username, full_name, is_bot) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, "
                "full_name = excluded.full_name, is_bot = excluded.is_bot",
                (user_id, username, full_name, int(is_bot)),
            )

    def add_user_if_missing(self, user_id: int, full_name: str):
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, '', ?)", (user_id, full_name))

    def get_user(self, user_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username,)
        ).fetchone()
        return dict(row) if row else None

    def get_name_rows(self, user_ids: Iterable[int]) -> dict[int, dict]:
        ids = list(set(user_ids))
        result: dict[int, dict] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT u.user_id, u.username, u.full_name, n.nickname FROM users u "
                f"LEFT JOIN nicknames n ON n.user_id = u.user_id WHERE u.user_id IN ({placeholders})",
                chunk,
            ).fetchall()
            result.update({row["user_id"]: dict(row) for row in rows})
        return result

    # ── Nicknames ──────────────────────────────────────────────────────────────

    def set_nickname(self, user_id: int, nickname: str):
        with self._tx(important=True) as c:
            c.execute(
                "INSERT INTO nicknames (user_id, nickname) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET nickname = excluded.nickname",
                (user_id, nickname),
            )

    def delete_nickname(self, user_id: int):
        with self._tx(important=True) as c:
            c.execute("DELETE FROM nicknames WHERE user_id = ?", (user_id,))

    def get_nickname(self, user_id: int) -> Optional[str]:
        row = self.conn.execute("SELECT nickname FROM nicknames WHERE user_id = ?", (user_id,)).fetchone()
        return row["nickname"] if row else None

    def find_users_by_nickname(self, nickname: str) -> list[int]:
        """Кто носит этот ник (без учёта регистра: SQLite сам не умеет сравнивать кириллицу без регистра)."""
        wanted = nickname.strip().casefold()
        rows = self.conn.execute("SELECT user_id, nickname FROM nicknames").fetchall()
        return [row["user_id"] for row in rows if row["nickname"].strip().casefold() == wanted]

    # ── Anketas ────────────────────────────────────────────────────────────────

    def save_anketa(self, user_id: int, text: str):
        with self._tx(important=True) as c:
            c.execute(
                "INSERT INTO anketas (user_id, text) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET text = excluded.text",
                (user_id, text),
            )

    def get_anketa(self, user_id: int) -> Optional[str]:
        row = self.conn.execute("SELECT text FROM anketas WHERE user_id = ?", (user_id,)).fetchone()
        return row["text"] if row else None

    def set_awaiting_anketa(self, user_id: int, chat_id: int):
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO awaiting_anketa (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id))

    def is_awaiting_anketa(self, user_id: int, chat_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM awaiting_anketa WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ).fetchone()
        return row is not None

    def clear_awaiting_anketa(self, user_id: int, chat_id: int):
        with self._tx() as c:
            c.execute("DELETE FROM awaiting_anketa WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))

    # ── Marriages ──────────────────────────────────────────────────────────────

    @staticmethod
    def _ordered(a: int, b: int) -> tuple[int, int]:
        return (min(a, b), max(a, b))

    def are_married(self, a: int, b: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM marriages WHERE user1_id = ? AND user2_id = ?", self._ordered(a, b)
        ).fetchone()
        return row is not None

    def add_marriage(self, a: int, b: int):
        u1, u2 = self._ordered(a, b)
        with self._tx(important=True) as c:
            c.execute(
                "INSERT OR IGNORE INTO marriages (user1_id, user2_id, created_at) VALUES (?, ?, ?)",
                (u1, u2, utcnow_iso()),
            )

    def remove_marriage(self, a: int, b: int):
        with self._tx(important=True) as c:
            c.execute("DELETE FROM marriages WHERE user1_id = ? AND user2_id = ?", self._ordered(a, b))

    def get_all_marriages(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT user1_id, user2_id, created_at FROM marriages ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_spouses(self, user_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT CASE WHEN user1_id = ? THEN user2_id ELSE user1_id END AS spouse "
            "FROM marriages WHERE user1_id = ? OR user2_id = ? ORDER BY created_at",
            (user_id, user_id, user_id),
        ).fetchall()
        return [r["spouse"] for r in rows]

    def add_marriage_proposal(self, proposer_id: int, target_id: int, chat_id: int):
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO marriage_proposals (proposer_id, target_id, chat_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (proposer_id, target_id, chat_id, utcnow_iso()),
            )

    def get_marriage_proposal(self, proposer_id: int, target_id: int, chat_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM marriage_proposals WHERE proposer_id = ? AND target_id = ? AND chat_id = ?",
            (proposer_id, target_id, chat_id),
        ).fetchone()
        return dict(row) if row else None

    def delete_marriage_proposal(self, proposer_id: int, target_id: int, chat_id: int):
        with self._tx() as c:
            c.execute(
                "DELETE FROM marriage_proposals WHERE proposer_id = ? AND target_id = ? AND chat_id = ?",
                (proposer_id, target_id, chat_id),
            )

    # ── Chats & members ────────────────────────────────────────────────────────

    def remember_chat(self, chat_id: int):
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO known_chats (chat_id) VALUES (?)", (chat_id,))

    def forget_chat(self, chat_id: int):
        with self._tx() as c:
            c.execute("DELETE FROM known_chats WHERE chat_id = ?", (chat_id,))

    def get_known_chats(self) -> list[int]:
        return [r["chat_id"] for r in self.conn.execute("SELECT chat_id FROM known_chats")]

    def get_main_chat(self) -> Optional[int]:
        """Самая болтливая из известных бесед."""
        row = self.conn.execute(
            "SELECT kc.chat_id FROM known_chats kc LEFT JOIN message_stats ms ON ms.chat_id = kc.chat_id "
            "GROUP BY kc.chat_id ORDER BY COALESCE(SUM(ms.message_count), 0) DESC LIMIT 1"
        ).fetchone()
        return row["chat_id"] if row else None

    def set_member(self, chat_id: int, user_id: int, is_member: bool, checked: bool = False):
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO chat_members (chat_id, user_id, is_member, checked_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    is_member  = excluded.is_member,
                    checked_at = COALESCE(excluded.checked_at, checked_at)
                """,
                (chat_id, user_id, int(is_member), utcnow_iso() if checked else None),
            )

    def member_status(self, chat_id: int, user_id: int) -> Optional[bool]:
        """True — в беседе, False — ушёл, None — бот о таком не знает."""
        row = self.conn.execute(
            "SELECT is_member FROM chat_members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
        return None if row is None else bool(row["is_member"])

    def get_member_rows(self, chat_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT user_id, is_member, checked_at FROM chat_members WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_member_candidates(self, chat_id: int) -> list[dict]:
        """Живые участники чата (не боты), о которых бот что-то знает."""
        rows = self.conn.execute(
            """
            SELECT cm.user_id, cm.checked_at, ms.updated_at AS last_active
            FROM chat_members cm
            JOIN users u ON u.user_id = cm.user_id
            LEFT JOIN message_stats ms ON ms.chat_id = cm.chat_id AND ms.user_id = cm.user_id
            WHERE cm.chat_id = ? AND cm.is_member = 1 AND COALESCE(u.is_bot, 0) = 0
            """,
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Message stats ──────────────────────────────────────────────────────────

    def record_message(
        self,
        chat_id: int,
        user_id: int,
        *,
        words: list[str],
        hour: int,
        is_text: bool,
        sticker: bool = False,
        voice: bool = False,
        media: bool = False,
        question: bool = False,
        caps: bool = False,
        phrase: Optional[str] = None,
        sticker_ids: Optional[tuple[str, str]] = None,
    ):
        """Всё, что бот узнаёт из одного сообщения, — одной транзакцией."""
        now = utcnow_iso()
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO message_stats (chat_id, user_id, message_count, word_count, stickers, voices,
                                           media, questions, caps, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    message_count = message_count + excluded.message_count,
                    word_count    = word_count + excluded.word_count,
                    stickers      = stickers + excluded.stickers,
                    voices        = voices + excluded.voices,
                    media         = media + excluded.media,
                    questions     = questions + excluded.questions,
                    caps          = caps + excluded.caps,
                    updated_at    = excluded.updated_at
                """,
                (chat_id, user_id, int(is_text), len(words), int(sticker), int(voice), int(media),
                 int(question), int(caps), now, now),
            )
            if words:
                c.executemany(
                    "INSERT INTO word_stats (chat_id, user_id, word, count) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(chat_id, user_id, word) DO UPDATE SET count = count + excluded.count",
                    [(chat_id, user_id, word, cnt) for word, cnt in Counter(words).items()],
                )
            c.execute(
                "INSERT INTO hourly_stats (chat_id, user_id, hour, count) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(chat_id, user_id, hour) DO UPDATE SET count = count + 1",
                (chat_id, user_id, hour),
            )
            if phrase:
                c.execute("INSERT INTO phrases (chat_id, user_id, text) VALUES (?, ?, ?)", (chat_id, user_id, phrase))
            if sticker_ids:
                file_unique_id, file_id = sticker_ids
                c.execute(
                    "INSERT INTO stickers (chat_id, file_unique_id, file_id) VALUES (?, ?, ?) "
                    "ON CONFLICT(chat_id, file_unique_id) DO UPDATE SET uses = uses + 1, file_id = excluded.file_id",
                    (chat_id, file_unique_id, file_id),
                )

    def bulk_import_stats(self, chat_id: int, user_id: int, message_count: int, word_count: int,
                          words: dict[str, int], last_active: Optional[str] = None):
        """Добавляет статистику из экспорта истории, суммируя с тем, что уже есть."""
        when = last_active or utcnow_iso()
        with self._tx(important=True) as c:
            c.execute(
                """
                INSERT INTO message_stats (chat_id, user_id, message_count, word_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    message_count = message_count + excluded.message_count,
                    word_count    = word_count + excluded.word_count,
                    updated_at    = MAX(updated_at, excluded.updated_at)
                """,
                (chat_id, user_id, message_count, word_count, when, when),
            )
            if words:
                c.executemany(
                    "INSERT INTO word_stats (chat_id, user_id, word, count) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(chat_id, user_id, word) DO UPDATE SET count = count + excluded.count",
                    [(chat_id, user_id, word, cnt) for word, cnt in words.items()],
                )
            c.execute("INSERT OR IGNORE INTO chat_members (chat_id, user_id, is_member) VALUES (?, ?, 1)",
                      (chat_id, user_id))

    def get_user_stats(self, chat_id: int, user_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM message_stats WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    # ── Phrases & stickers (для сглыпы) ────────────────────────────────────────

    def import_phrases(self, chat_id: int, rows: list[tuple[int, str]]):
        """Фразы из истории получают id меньше всех существующих — чтобы считаться самыми старыми."""
        with self._tx(important=True) as c:
            min_id = c.execute("SELECT MIN(id) FROM phrases").fetchone()[0]
            first_id = min(1 if min_id is None else min_id, 1) - len(rows)
            c.executemany(
                "INSERT INTO phrases (id, chat_id, user_id, text) VALUES (?, ?, ?, ?)",
                [(first_id + i, chat_id, user_id, text) for i, (user_id, text) in enumerate(rows)],
            )

    def count_phrases(self, chat_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM phrases WHERE chat_id = ?", (chat_id,)).fetchone()[0]

    def prune_phrases(self, chat_id: int, keep: int):
        with self._tx() as c:
            row = c.execute(
                "SELECT id FROM phrases WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?", (chat_id, keep)
            ).fetchone()
            if row:
                c.execute("DELETE FROM phrases WHERE chat_id = ? AND id <= ?", (chat_id, row["id"]))

    def sample_phrases(self, chat_id: int, recent: int, random_count: int) -> list[str]:
        c = self.conn
        rows = c.execute(
            "SELECT text FROM phrases WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (chat_id, recent)
        ).fetchall()
        rows += c.execute(
            "SELECT text FROM phrases WHERE chat_id = ? ORDER BY RANDOM() LIMIT ?", (chat_id, random_count)
        ).fetchall()
        return [r["text"] for r in rows]

    def random_sticker(self, chat_id: int) -> Optional[str]:
        row = self.conn.execute(
            "SELECT file_id FROM (SELECT file_id FROM stickers WHERE chat_id = ? ORDER BY uses DESC LIMIT 150) "
            "ORDER BY RANDOM() LIMIT 1",
            (chat_id,),
        ).fetchone()
        return row["file_id"] if row else None

    def prune_stickers(self, chat_id: int, keep: int):
        with self._tx() as c:
            c.execute(
                "DELETE FROM stickers WHERE chat_id = ? AND file_unique_id NOT IN "
                "(SELECT file_unique_id FROM stickers WHERE chat_id = ? ORDER BY uses DESC LIMIT ?)",
                (chat_id, chat_id, keep),
            )

    # ── Таджик дня ─────────────────────────────────────────────────────────────

    def get_tajik_of_day(self, chat_id: int, day: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT user_id FROM tajik_of_day WHERE chat_id = ? AND day = ?", (chat_id, day)
        ).fetchone()
        return row["user_id"] if row else None

    def set_tajik_of_day(self, chat_id: int, day: str, user_id: int) -> int:
        """Записывает победителя; если кто-то успел раньше — возвращает того, кто уже записан."""
        with self._tx(important=True) as c:
            c.execute(
                "INSERT OR IGNORE INTO tajik_of_day (chat_id, day, user_id, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, day, user_id, utcnow_iso()),
            )
        return self.get_tajik_of_day(chat_id, day)

    def get_tajik_top(self, chat_id: int, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT user_id, COUNT(*) AS wins, MAX(day) AS last_day FROM tajik_of_day WHERE chat_id = ? "
            "GROUP BY user_id ORDER BY wins DESC, last_day DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_tajik_wins(self, chat_id: int, user_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM tajik_of_day WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()[0]

    def delete_tajik_of_day(self, chat_id: int, day: str):
        with self._tx(important=True) as c:
            c.execute("DELETE FROM tajik_of_day WHERE chat_id = ? AND day = ?", (chat_id, day))

    # ── Таджикоины ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ensure_wallet(c: sqlite3.Connection, chat_id: int, user_id: int):
        c.execute(
            "INSERT OR IGNORE INTO wallets (chat_id, user_id, balance) VALUES (?, ?, ?)",
            (chat_id, user_id, START_BALANCE),
        )

    @staticmethod
    def _balance(c: sqlite3.Connection, chat_id: int, user_id: int) -> int:
        return c.execute(
            "SELECT balance FROM wallets WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()["balance"]

    def get_wallet(self, chat_id: int, user_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM wallets WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)).fetchone()
        if row:
            return dict(row)
        return {"chat_id": chat_id, "user_id": user_id, "balance": START_BALANCE, "last_bonus": None,
                "games": 0, "won": 0, "lost": 0}

    def get_balance(self, chat_id: int, user_id: int) -> int:
        return self.get_wallet(chat_id, user_id)["balance"]

    def add_money(self, chat_id: int, user_id: int, delta: int) -> int:
        """Начисляет или списывает таджикоины (баланс не уходит в минус). Возвращает новый баланс."""
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            c.execute(
                "UPDATE wallets SET balance = MAX(0, balance + ?) WHERE chat_id = ? AND user_id = ?",
                (delta, chat_id, user_id),
            )
            return self._balance(c, chat_id, user_id)

    def take_bet(self, chat_id: int, user_id: int, bet: int) -> bool:
        """Сразу забирает ставку, чтобы одну и ту же сумму нельзя было поставить дважды."""
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            cursor = c.execute(
                "UPDATE wallets SET balance = balance - ? WHERE chat_id = ? AND user_id = ? AND balance >= ?",
                (bet, chat_id, user_id, bet),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _record_game(c: sqlite3.Connection, chat_id: int, user_id: int, game: str,
                     wagered: int, returned: int, special: bool):
        c.execute(
            "UPDATE wallets SET games = games + 1, won = won + ?, lost = lost + ? WHERE chat_id = ? AND user_id = ?",
            (max(returned - wagered, 0), max(wagered - returned, 0), chat_id, user_id),
        )
        c.execute(
            """
            INSERT INTO casino_stats (chat_id, user_id, game, plays, wagered, returned, best_win, special)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id, game) DO UPDATE SET
                plays    = plays + 1,
                wagered  = wagered + excluded.wagered,
                returned = returned + excluded.returned,
                best_win = MAX(best_win, excluded.best_win),
                special  = special + excluded.special
            """,
            (chat_id, user_id, game, wagered, returned, max(returned - wagered, 0), int(special)),
        )
        if returned > wagered:  # счётчики побед нужны ачивкам, поэтому считаем их там же, где и саму игру
            for key in (f"win:{game}", "win:any"):
                c.execute(
                    "INSERT INTO counters (chat_id, user_id, key, value) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(chat_id, user_id, key) DO UPDATE SET value = value + 1",
                    (chat_id, user_id, key),
                )

    def casino_settle(self, chat_id: int, user_id: int, game: str, bet: int, payout: int,
                      special: bool = False) -> int:
        """Выплата после игры, ставка уже списана take_bet (payout — сколько вернуть вместе со ставкой).
        Возвращает новый баланс."""
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                      (payout, chat_id, user_id))
            self._record_game(c, chat_id, user_id, game, bet, payout, special)
            return self._balance(c, chat_id, user_id)

    def casino_adjust(self, chat_id: int, user_id: int, game: str, delta: int, special: bool = False) -> int:
        """Начисление или списание без ставки: русская рулетка, прогулки. Баланс не уходит в минус."""
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            c.execute("UPDATE wallets SET balance = MAX(0, balance + ?) WHERE chat_id = ? AND user_id = ?",
                      (delta, chat_id, user_id))
            self._record_game(c, chat_id, user_id, game, max(-delta, 0), max(delta, 0), special)
            return self._balance(c, chat_id, user_id)

    def casino_russian_roulette(self, chat_id: int, user_id: int, delta: int, died: bool) -> int:
        """Выстрел в русской рулетке: награда за храбрость или штраф на похороны."""
        return self.casino_adjust(chat_id, user_id, "rr", delta, special=died)

    def get_blackjack_hand(self, chat_id: int, user_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT hand FROM blackjack_hands WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
        return json.loads(row["hand"]) if row else None

    def save_blackjack_hand(self, chat_id: int, user_id: int, hand: dict):
        with self._tx() as c:
            c.execute(
                "INSERT INTO blackjack_hands (chat_id, user_id, hand, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id) DO UPDATE SET hand = excluded.hand",
                (chat_id, user_id, json.dumps(hand), utcnow_iso()),
            )

    def finish_blackjack(self, chat_id: int, user_id: int, stake: int, payout: int, blackjack: bool) -> int:
        """Убирает раздачу и выплачивает выигрыш одной транзакцией. Возвращает новый баланс."""
        with self._tx() as c:
            c.execute("DELETE FROM blackjack_hands WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            self._ensure_wallet(c, chat_id, user_id)
            c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                      (payout, chat_id, user_id))
            self._record_game(c, chat_id, user_id, "blackjack", stake, payout, blackjack)
            return self._balance(c, chat_id, user_id)

    def get_casino_stats(self, chat_id: int, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM casino_stats WHERE chat_id = ? AND user_id = ? ORDER BY plays DESC", (chat_id, user_id)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_casino_totals(self, chat_id: int) -> dict[str, dict]:
        """Итоги по каждой игре в беседе."""
        rows = self.conn.execute(
            "SELECT game, SUM(plays) AS plays, SUM(wagered) AS wagered, SUM(returned) AS returned, "
            "SUM(special) AS special FROM casino_stats WHERE chat_id = ? GROUP BY game",
            (chat_id,),
        ).fetchall()
        return {r["game"]: dict(r) for r in rows}

    def claim_bonus(self, chat_id: int, user_id: int, day: str, amount: int) -> Optional[int]:
        """Ежедневный бонус. None — сегодня уже получали."""
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            cursor = c.execute(
                "UPDATE wallets SET balance = balance + ?, last_bonus = ? "
                "WHERE chat_id = ? AND user_id = ? AND COALESCE(last_bonus, '') != ?",
                (amount, day, chat_id, user_id, day),
            )
            return self._balance(c, chat_id, user_id) if cursor.rowcount == 1 else None

    def transfer(self, chat_id: int, from_id: int, to_id: int, amount: int) -> bool:
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, from_id)
            self._ensure_wallet(c, chat_id, to_id)
            cursor = c.execute(
                "UPDATE wallets SET balance = balance - ? WHERE chat_id = ? AND user_id = ? AND balance >= ?",
                (amount, chat_id, from_id, amount),
            )
            if cursor.rowcount != 1:
                return False
            c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, to_id))
            return True

    def grant_once(self, key: str, amount: int) -> int:
        """Разовая раздача всем участникам бесед (кроме ботов и ушедших). Повторно не срабатывает.
        У кого кошелька ещё не было, тому он создаётся ровно с этой суммой. Возвращает, скольким выдали."""
        meta_key = f"grant:{key}"
        if self.get_meta(meta_key) is not None:
            return 0
        with self._tx(important=True) as c:
            rows = c.execute(
                """
                SELECT cm.chat_id, cm.user_id FROM chat_members cm
                JOIN known_chats kc ON kc.chat_id = cm.chat_id
                LEFT JOIN users u ON u.user_id = cm.user_id
                WHERE cm.is_member = 1 AND COALESCE(u.is_bot, 0) = 0
                """
            ).fetchall()
            for row in rows:
                cursor = c.execute(
                    "UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                    (amount, row["chat_id"], row["user_id"]),
                )
                if cursor.rowcount == 0:
                    c.execute(
                        "INSERT INTO wallets (chat_id, user_id, balance) VALUES (?, ?, ?)",
                        (row["chat_id"], row["user_id"], amount),
                    )
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (meta_key, utcnow_iso()))
        return len(rows)

    def grant_user_once(self, key: str, chat_id: int, user_id: int, amount: int) -> Optional[int]:
        """Разовое начисление одному человеку. None — уже начисляли, иначе новый баланс."""
        meta_key = f"grant:{key}"
        with self._tx(important=True) as c:
            if c.execute("SELECT 1 FROM meta WHERE key = ?", (meta_key,)).fetchone():
                return None
            self._ensure_wallet(c, chat_id, user_id)
            c.execute(
                "UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, user_id)
            )
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (meta_key, utcnow_iso()))
            return self._balance(c, chat_id, user_id)

    _RICH_FILTER = (
        "FROM wallets w LEFT JOIN users u ON u.user_id = w.user_id "
        "LEFT JOIN chat_members cm ON cm.chat_id = w.chat_id AND cm.user_id = w.user_id "
        "WHERE w.chat_id = ? AND COALESCE(u.is_bot, 0) = 0 AND COALESCE(cm.is_member, 1) = 1"
    )

    # Капитал для Форбса: кошелёк плюс то, что лежит во вкладах
    _WEALTH = (
        "(w.balance + COALESCE((SELECT SUM(d.amount) FROM bank_deposits d WHERE d.chat_id = w.chat_id "
        "AND d.user_id = w.user_id AND d.status = 'active'), 0))"
    )

    def get_richest(self, chat_id: int, limit: int = 10) -> list[dict]:
        """Самые богатые из тех, кто всё ещё в беседе."""
        rows = self.conn.execute(
            f"SELECT w.user_id, w.balance, {self._WEALTH} AS wealth {self._RICH_FILTER} ORDER BY wealth DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_wealth(self, chat_id: int, user_id: int) -> int:
        return self.get_balance(chat_id, user_id) + self.bank_total(chat_id, user_id)

    def wealth_place(self, chat_id: int, wealth: int) -> int:
        return 1 + self.conn.execute(
            f"SELECT COUNT(*) {self._RICH_FILTER} AND {self._WEALTH} > ?", (chat_id, wealth)
        ).fetchone()[0]

    # ── Общие столы: рулетка и скачки ─────────────────────────────────────────

    def add_table_bet(self, chat_id: int, game: str, round_no: int, user_id: int,
                      kind: str, pick: Optional[int], amount: int, odds: float) -> Optional[int]:
        """Списывает ставку и записывает её на раунд одной транзакцией. None — не хватает денег."""
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            cursor = c.execute(
                "UPDATE wallets SET balance = balance - ? WHERE chat_id = ? AND user_id = ? AND balance >= ?",
                (amount, chat_id, user_id, amount),
            )
            if cursor.rowcount != 1:
                return None
            cursor = c.execute(
                "INSERT INTO table_bets (chat_id, game, round, user_id, kind, pick, amount, odds, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, game, round_no, user_id, kind, pick, amount, odds, utcnow_iso()),
            )
            return cursor.lastrowid

    def round_bets(self, chat_id: int, game: str, round_no: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM table_bets WHERE chat_id = ? AND game = ? AND round = ? ORDER BY id",
            (chat_id, game, round_no),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_round_bets(self, chat_id: int, game: str, round_no: int, user_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM table_bets WHERE chat_id = ? AND game = ? AND round = ? AND user_id = ?",
            (chat_id, game, round_no, user_id),
        ).fetchone()[0]

    def open_bet_rounds(self) -> list[tuple[int, str, int]]:
        """Раунды, в которых есть неразыгранные ставки."""
        rows = self.conn.execute(
            "SELECT DISTINCT chat_id, game, round FROM table_bets WHERE payout IS NULL"
        ).fetchall()
        return [(r["chat_id"], r["game"], r["round"]) for r in rows]

    def round_result(self, chat_id: int, game: str, round_no: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT data FROM casino_history WHERE chat_id = ? AND game = ? AND round = ?", (chat_id, game, round_no)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def settle_table_round(self, chat_id: int, game: str, round_no: int, result: dict,
                           payout_of: Callable[[dict], tuple[int, bool]]) -> Optional[list[dict]]:
        """Записывает исход раунда и выплачивает все его ставки одной транзакцией.
        payout_of(ставка) -> (выплата вместе со ставкой, особый ли выигрыш). None — раунд уже разыгран."""
        with self._tx() as c:
            cursor = c.execute(
                "INSERT OR IGNORE INTO casino_history (chat_id, game, round, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, game, round_no, json.dumps(result, ensure_ascii=False), utcnow_iso()),
            )
            if cursor.rowcount == 0:
                return None
            bets = [dict(r) for r in c.execute(
                "SELECT * FROM table_bets WHERE chat_id = ? AND game = ? AND round = ? AND payout IS NULL ORDER BY id",
                (chat_id, game, round_no),
            ).fetchall()]
            for bet in bets:
                bet["payout"], special = payout_of(bet)
                c.execute("UPDATE table_bets SET payout = ? WHERE id = ?", (bet["payout"], bet["id"]))
                self._ensure_wallet(c, chat_id, bet["user_id"])
                c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                          (bet["payout"], chat_id, bet["user_id"]))
                self._record_game(c, chat_id, bet["user_id"], game, bet["amount"], bet["payout"], special)
            self._prune_history(c, chat_id, game)
            return bets

    def prune_table_bets(self, game: str, before_round: int):
        """Разыгранные ставки старше суток больше не нужны."""
        with self._tx() as c:
            c.execute("DELETE FROM table_bets WHERE game = ? AND round < ? AND payout IS NOT NULL", (game, before_round))

    # ── История игр ────────────────────────────────────────────────────────────

    HISTORY_KEEP = 60  # сколько последних результатов каждой игры хранить

    @classmethod
    def _prune_history(cls, c: sqlite3.Connection, chat_id: int, game: str):
        c.execute(
            "DELETE FROM casino_history WHERE chat_id = ? AND game = ? AND id NOT IN ("
            "SELECT id FROM casino_history WHERE chat_id = ? AND game = ? ORDER BY round DESC, id DESC LIMIT ?)",
            (chat_id, game, chat_id, game, cls.HISTORY_KEEP),
        )

    def add_history(self, chat_id: int, game: str, data: dict):
        with self._tx() as c:
            c.execute(
                "INSERT INTO casino_history (chat_id, game, data, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, game, json.dumps(data, ensure_ascii=False), utcnow_iso()),
            )
            self._prune_history(c, chat_id, game)

    def get_history(self, chat_id: int, game: str, limit: int) -> list[dict]:
        """Последние результаты, свежие первыми. У общих столов в каждом есть номер раунда."""
        rows = self.conn.execute(
            "SELECT id, round, data FROM casino_history WHERE chat_id = ? AND game = ? "
            "ORDER BY round DESC, id DESC LIMIT ?",
            (chat_id, game, limit),
        ).fetchall()
        return [json.loads(r["data"]) | {"id": r["id"]} | ({"round": r["round"]} if r["round"] is not None else {})
                for r in rows]

    # ── Банк ───────────────────────────────────────────────────────────────────

    def open_deposit(self, chat_id: int, user_id: int, amount: int, interest: int, term: str,
                     opened_at: int, ends_at: int) -> Optional[int]:
        """Переносит деньги из кошелька во вклад. None — в кошельке столько нет."""
        with self._tx(important=True) as c:
            self._ensure_wallet(c, chat_id, user_id)
            cursor = c.execute(
                "UPDATE wallets SET balance = balance - ? WHERE chat_id = ? AND user_id = ? AND balance >= ?",
                (amount, chat_id, user_id, amount),
            )
            if cursor.rowcount != 1:
                return None
            cursor = c.execute(
                "INSERT INTO bank_deposits (chat_id, user_id, amount, interest, term, opened_at, ends_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chat_id, user_id, amount, interest, term, opened_at, ends_at),
            )
            return cursor.lastrowid

    def get_deposit(self, deposit_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM bank_deposits WHERE id = ?", (deposit_id,)).fetchone()
        return dict(row) if row else None

    def get_deposits(self, chat_id: int, user_id: int, closed_limit: int = 5) -> list[dict]:
        """Открытые вклады (ближайшие к выплате первыми) и несколько последних закрытых."""
        active = self.conn.execute(
            "SELECT * FROM bank_deposits WHERE chat_id = ? AND user_id = ? AND status = 'active' ORDER BY ends_at",
            (chat_id, user_id),
        ).fetchall()
        closed = self.conn.execute(
            "SELECT * FROM bank_deposits WHERE chat_id = ? AND user_id = ? AND status != 'active' "
            "ORDER BY closed_at DESC, id DESC LIMIT ?",
            (chat_id, user_id, closed_limit),
        ).fetchall()
        return [dict(r) for r in active] + [dict(r) for r in closed]

    def count_active_deposits(self, chat_id: int, user_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM bank_deposits WHERE chat_id = ? AND user_id = ? AND status = 'active'",
            (chat_id, user_id),
        ).fetchone()[0]

    def bank_total(self, chat_id: int, user_id: int) -> int:
        return self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM bank_deposits WHERE chat_id = ? AND user_id = ? AND status = 'active'",
            (chat_id, user_id),
        ).fetchone()[0]

    def due_deposits(self, now: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM bank_deposits WHERE status = 'active' AND ends_at <= ? ORDER BY ends_at", (now,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close_deposit(self, deposit_id: int, status: str, pay: int, now: int) -> bool:
        """Закрывает вклад и возвращает деньги в кошелёк. False — вклад уже закрыт."""
        with self._tx(important=True) as c:
            cursor = c.execute(
                "UPDATE bank_deposits SET status = ?, closed_at = ? WHERE id = ? AND status = 'active'",
                (status, now, deposit_id),
            )
            if cursor.rowcount != 1:
                return False
            row = c.execute("SELECT chat_id, user_id FROM bank_deposits WHERE id = ?", (deposit_id,)).fetchone()
            self._ensure_wallet(c, row["chat_id"], row["user_id"])
            c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                      (pay, row["chat_id"], row["user_id"]))
            return True

    # ── Рекорды бесконечных игр ────────────────────────────────────────────────

    _ARCADE_ALIVE = (
        "FROM arcade_records a LEFT JOIN users u ON u.user_id = a.user_id "
        "LEFT JOIN chat_members cm ON cm.chat_id = a.chat_id AND cm.user_id = a.user_id "
        "WHERE a.chat_id = ? AND COALESCE(u.is_bot, 0) = 0 AND COALESCE(cm.is_member, 1) = 1"
    )

    def save_arcade_score(self, chat_id: int, game: str, user_id: int, score: int) -> int:
        """Оставляет лучший результат игрока. Возвращает его рекорд после попытки."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO arcade_records (chat_id, game, user_id, score, made_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(chat_id, game, user_id) DO UPDATE SET "
                "score = MAX(score, excluded.score), made_at = CASE WHEN excluded.score > score "
                "THEN excluded.made_at ELSE made_at END",
                (chat_id, game, user_id, score, utcnow_iso()),
            )
            return c.execute(
                "SELECT score FROM arcade_records WHERE chat_id = ? AND game = ? AND user_id = ?",
                (chat_id, game, user_id),
            ).fetchone()["score"]

    def arcade_top(self, chat_id: int, game: str, limit: int) -> list[dict]:
        rows = self.conn.execute(
            f"SELECT a.user_id, a.score {self._ARCADE_ALIVE} AND a.game = ? ORDER BY a.score DESC, a.made_at LIMIT ?",
            (chat_id, game, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def arcade_record(self, chat_id: int, game: str) -> Optional[dict]:
        top = self.arcade_top(chat_id, game, 1)
        return top[0] if top else None

    def arcade_best(self, chat_id: int, game: str, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT score FROM arcade_records WHERE chat_id = ? AND game = ? AND user_id = ?",
            (chat_id, game, user_id),
        ).fetchone()
        return row["score"] if row else 0

    def arcade_all(self, chat_id: int) -> list[dict]:
        rows = self.conn.execute(f"SELECT a.user_id, a.game, a.score {self._ARCADE_ALIVE}", (chat_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── Столы мультиплеера ─────────────────────────────────────────────────────

    def mp_create(self, chat_id: int, user_id: int, game: str, stake: int, seats: list, state: dict) -> int:
        now = int(time.time())
        with self._tx() as c:
            cursor = c.execute(
                "INSERT INTO mp_tables (chat_id, game, stake, owner_id, seats, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, game, stake, user_id, json.dumps(seats, ensure_ascii=False),
                 json.dumps(state, ensure_ascii=False), now, now),
            )
            return cursor.lastrowid

    @staticmethod
    def _mp_row(row) -> dict:
        table = dict(row)
        table["seats"] = json.loads(table["seats"])
        table["state"] = json.loads(table["state"])
        return table

    def mp_get(self, table_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM mp_tables WHERE id = ?", (table_id,)).fetchone()
        return self._mp_row(row) if row else None

    def mp_live(self, chat_id: int) -> list[dict]:
        """Столы беседы, которые собираются или играют, — их немного."""
        rows = self.conn.execute(
            "SELECT * FROM mp_tables WHERE chat_id = ? AND status IN ('gathering', 'playing') ORDER BY id",
            (chat_id,),
        ).fetchall()
        return [self._mp_row(row) for row in rows]

    def mp_done(self, chat_id: int, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM mp_tables WHERE chat_id = ? AND status = 'done' ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [self._mp_row(row) for row in rows]

    def mp_save(self, table_id: int, **fields) -> bool:
        """Обновляет стол. seats и state кладём как JSON, остальное как есть."""
        if not fields:
            return False
        for key in ("seats", "state"):
            if key in fields:
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        fields["updated_at"] = int(time.time())
        columns = ", ".join(f"{key} = ?" for key in fields)
        with self._tx() as c:
            cursor = c.execute(f"UPDATE mp_tables SET {columns} WHERE id = ?", (*fields.values(), table_id))
            return cursor.rowcount == 1

    def mp_due(self, now: int) -> list[dict]:
        """Столы, у которых вышло время хода."""
        rows = self.conn.execute(
            "SELECT * FROM mp_tables WHERE status = 'playing' AND turn_until IS NOT NULL AND turn_until <= ?",
            (now,),
        ).fetchall()
        return [self._mp_row(row) for row in rows]

    def mp_gathering(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM mp_tables WHERE status = 'gathering'").fetchall()
        return [self._mp_row(row) for row in rows]

    def mp_prune(self, before: int):
        with self._tx() as c:
            c.execute("DELETE FROM mp_tables WHERE status = 'done' AND updated_at < ?", (before,))

    # ── Прогулки, счётчики и ачивки ────────────────────────────────────────────

    def get_walk(self, chat_id: int, user_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM walks WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    def save_walk(self, chat_id: int, user_id: int, quest: str, node: str, steps: int, started_at: int):
        """Куда игрок дошёл в квесте. Перезапись, чтобы прогулка была одна."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO walks (chat_id, user_id, quest, node, steps, started_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id) DO UPDATE SET quest = excluded.quest, node = excluded.node, "
                "steps = excluded.steps, started_at = excluded.started_at",
                (chat_id, user_id, quest, node, steps, started_at),
            )

    def clear_walk(self, chat_id: int, user_id: int):
        with self._tx() as c:
            c.execute("DELETE FROM walks WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))

    def prune_walks(self, before: int):
        """Брошенные прогулки не копятся в базе."""
        with self._tx() as c:
            c.execute("DELETE FROM walks WHERE started_at < ?", (before,))

    def get_counters(self, chat_id: int, user_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT key, value FROM counters WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchall()
        return {row["key"]: row["value"] for row in rows}

    def get_counter(self, chat_id: int, user_id: int, key: str) -> int:
        row = self.conn.execute(
            "SELECT value FROM counters WHERE chat_id = ? AND user_id = ? AND key = ?", (chat_id, user_id, key)
        ).fetchone()
        return row["value"] if row else 0

    def bump_counter(self, chat_id: int, user_id: int, key: str, delta: int = 1) -> int:
        with self._tx() as c:
            c.execute(
                "INSERT INTO counters (chat_id, user_id, key, value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id, key) DO UPDATE SET value = value + excluded.value",
                (chat_id, user_id, key, delta),
            )
            return c.execute(
                "SELECT value FROM counters WHERE chat_id = ? AND user_id = ? AND key = ?", (chat_id, user_id, key)
            ).fetchone()["value"]

    def set_counter(self, chat_id: int, user_id: int, key: str, value: int):
        with self._tx() as c:
            c.execute(
                "INSERT INTO counters (chat_id, user_id, key, value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id, key) DO UPDATE SET value = excluded.value",
                (chat_id, user_id, key, value),
            )

    def counter_top(self, chat_id: int, key: str, limit: int = 5) -> list[dict]:
        """Кто впереди по счётчику — для рейтингов."""
        rows = self.conn.execute(
            "SELECT user_id, value FROM counters WHERE chat_id = ? AND key = ? AND value > 0 "
            "ORDER BY value DESC LIMIT ?", (chat_id, key, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_achievements(self, chat_id: int, user_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT key, level FROM achievements WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchall()
        return {row["key"]: row["level"] for row in rows}

    def award_achievement(self, chat_id: int, user_id: int, key: str, level: int, reward: int) -> Optional[int]:
        """Поднимает ачивку до уровня и разом выдаёт награду. None — этот уровень уже был взят.

        Уровень и деньги двигаются в одной транзакции, поэтому за одну ступень не заплатят дважды,
        даже если приложение попросит проверить ачивки из двух вкладок сразу.
        """
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            cursor = c.execute(
                "INSERT INTO achievements (chat_id, user_id, key, level, paid, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id, key) DO UPDATE SET level = excluded.level, "
                "paid = paid + excluded.paid, updated_at = excluded.updated_at WHERE level < excluded.level",
                (chat_id, user_id, key, level, reward, utcnow_iso()),
            )
            if cursor.rowcount == 0:
                return None
            if reward:
                c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                          (reward, chat_id, user_id))
            return self._balance(c, chat_id, user_id)

    def achievement_earned(self, chat_id: int, user_id: int) -> int:
        """Сколько всего заработано на ачивках — для экрана достижений."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(paid), 0) AS total FROM achievements WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return row["total"]

    def achievement_leaders(self, chat_id: int, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT user_id, SUM(level) AS stars FROM achievements "
            "WHERE chat_id = ? AND level > 0 AND key NOT LIKE 'end:%' "  # концовки квестов — не звёзды
            "GROUP BY user_id ORDER BY stars DESC, MIN(updated_at) LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── Бомж ───────────────────────────────────────────────────────────────────

    def get_bum(self, chat_id: int, user_id: int, name: str, now: int, fed_until: int) -> dict:
        """Бомж игрока. Если его ещё нет, заводим — сытым и с нулевой выручкой."""
        with self._tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO bums (chat_id, user_id, name, fed_until, collected_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, name, fed_until, now),
            )
            return dict(c.execute("SELECT * FROM bums WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)).fetchone())

    def peek_bum(self, chat_id: int, user_id: int) -> Optional[dict]:
        """Бомж, если он уже есть. В отличие от get_bum ничего не заводит."""
        row = self.conn.execute(
            "SELECT * FROM bums WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    def collect_bum(self, chat_id: int, user_id: int, amount: int, now: int):
        """Выручка бомжа — в кошелёк."""
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                      (amount, chat_id, user_id))
            c.execute("UPDATE bums SET earned = earned + ?, collected_at = ? WHERE chat_id = ? AND user_id = ?",
                      (amount, now, chat_id, user_id))

    def feed_bum(self, chat_id: int, user_id: int, cost: int, fed_until: int) -> bool:
        with self._tx() as c:
            self._ensure_wallet(c, chat_id, user_id)
            cursor = c.execute(
                "UPDATE wallets SET balance = balance - ? WHERE chat_id = ? AND user_id = ? AND balance >= ?",
                (cost, chat_id, user_id, cost),
            )
            if cursor.rowcount != 1:
                return False
            c.execute("UPDATE bums SET fed_until = ? WHERE chat_id = ? AND user_id = ?", (fed_until, chat_id, user_id))
            return True

    def upgrade_bum(self, chat_id: int, user_id: int, cost: int, pending: int, now: int) -> bool:
        """Вложение в бомжа. Накопленная выручка при этом не сгорает, а падает в кошелёк."""
        with self._tx(important=True) as c:
            self._ensure_wallet(c, chat_id, user_id)
            cursor = c.execute(
                "UPDATE wallets SET balance = balance - ? WHERE chat_id = ? AND user_id = ? AND balance >= ?",
                (cost, chat_id, user_id, cost),
            )
            if cursor.rowcount != 1:
                return False
            if pending > 0:
                c.execute("UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
                          (pending, chat_id, user_id))
            c.execute(
                "UPDATE bums SET level = level + 1, invested = invested + ?, earned = earned + ?, collected_at = ? "
                "WHERE chat_id = ? AND user_id = ?",
                (cost, pending, now, chat_id, user_id),
            )
            return True

    def prune_deposits(self, before: int):
        """Закрытые вклады старше месяца уже никому не интересны."""
        with self._tx() as c:
            c.execute("DELETE FROM bank_deposits WHERE status != 'active' AND closed_at < ?", (before,))

    # ── Донаты ─────────────────────────────────────────────────────────────────

    def _reward_donor(self, c: sqlite3.Connection, donation_id: int, coins_per_ruble: int):
        """Таджикоины за подтверждённый донат — в той же транзакции, что и подтверждение."""
        row = c.execute("SELECT chat_id, user_id, amount FROM donations WHERE id = ?", (donation_id,)).fetchone()
        self._ensure_wallet(c, row["chat_id"], row["user_id"])
        c.execute(
            "UPDATE wallets SET balance = balance + ? WHERE chat_id = ? AND user_id = ?",
            (row["amount"] * coins_per_ruble, row["chat_id"], row["user_id"]),
        )
        c.execute(  # ачивка «спонсор казана» считает подтверждённые донаты
            "INSERT INTO counters (chat_id, user_id, key, value) VALUES (?, ?, 'donate', 1) "
            "ON CONFLICT(chat_id, user_id, key) DO UPDATE SET value = value + 1",
            (row["chat_id"], row["user_id"]),
        )

    def add_donation(self, chat_id: int, user_id: int, amount: int, message: str,
                     status: str = "pending", coins_per_ruble: int = 0) -> int:
        with self._tx(important=True) as c:
            cursor = c.execute(
                "INSERT INTO donations (chat_id, user_id, amount, message, status, created_at, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chat_id, user_id, amount, message, status, utcnow_iso(), None if status == "pending" else utcnow_iso()),
            )
            if status == "confirmed" and coins_per_ruble:
                self._reward_donor(c, cursor.lastrowid, coins_per_ruble)
            return cursor.lastrowid

    def get_donation(self, donation_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM donations WHERE id = ?", (donation_id,)).fetchone()
        return dict(row) if row else None

    def decide_donation(self, donation_id: int, status: str, coins_per_ruble: int = 0) -> bool:
        """Подтвердить (и начислить таджикоины) или отклонить. False — донат уже кто-то решил раньше."""
        with self._tx(important=True) as c:
            cursor = c.execute(
                "UPDATE donations SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                (status, utcnow_iso(), donation_id),
            )
            if cursor.rowcount != 1:
                return False
            if status == "confirmed" and coins_per_ruble:
                self._reward_donor(c, donation_id, coins_per_ruble)
            return True

    def count_pending_donations(self, user_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM donations WHERE user_id = ? AND status = 'pending'", (user_id,)
        ).fetchone()[0]
