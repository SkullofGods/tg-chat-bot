import sqlite3
import threading
from datetime import datetime
from typing import Optional

DB_PATH = "bot.db"


class Database:
    def __init__(self):
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT DEFAULT '',
                full_name TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS nicknames (
                user_id  INTEGER PRIMARY KEY,
                nickname TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS anketas (
                user_id INTEGER PRIMARY KEY,
                text    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS awaiting_anketa (
                user_id INTEGER,
                chat_id INTEGER,
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS marriages (
                user1_id   INTEGER,
                user2_id   INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user1_id, user2_id),
                CHECK (user1_id < user2_id)
            );
            CREATE TABLE IF NOT EXISTS marriage_proposals (
                proposer_id INTEGER,
                target_id   INTEGER,
                chat_id     INTEGER,
                created_at  TEXT NOT NULL,
                PRIMARY KEY (proposer_id, target_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS known_chats (
                chat_id INTEGER PRIMARY KEY
            );
        """)

        columns = [row[1] for row in conn.execute("PRAGMA table_info(marriages)").fetchall()]
        if "created_at" not in columns:
            conn.execute("ALTER TABLE marriages ADD COLUMN created_at TEXT")
            conn.execute(
                "UPDATE marriages SET created_at = ? WHERE created_at IS NULL",
                (datetime.utcnow().isoformat(),),
            )

        conn.commit()
        conn.close()

    def init(self):
        self._init_db()

    def ensure_user(self, user_id: int, username: str, full_name: str):
        c = self._conn()
        c.execute(
            "INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
            (user_id, username, full_name),
        )
        c.commit()

    def get_user(self, user_id: int) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username,)
        ).fetchone()
        return dict(row) if row else None

    # ── Nicknames ──────────────────────────────────────────────────────────

    def set_nickname(self, user_id: int, nickname: str):
        c = self._conn()
        c.execute(
            "INSERT INTO nicknames (user_id, nickname) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname",
            (user_id, nickname),
        )
        c.commit()

    def delete_nickname(self, user_id: int):
        c = self._conn()
        c.execute("DELETE FROM nicknames WHERE user_id = ?", (user_id,))
        c.commit()

    def get_nickname(self, user_id: int) -> Optional[str]:
        row = self._conn().execute(
            "SELECT nickname FROM nicknames WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["nickname"] if row else None

    # ── Anketas ────────────────────────────────────────────────────────────

    def save_anketa(self, user_id: int, text: str):
        c = self._conn()
        c.execute(
            "INSERT INTO anketas (user_id, text) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET text=excluded.text",
            (user_id, text),
        )
        c.commit()

    def get_anketa(self, user_id: int) -> Optional[str]:
        row = self._conn().execute(
            "SELECT text FROM anketas WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["text"] if row else None

    def set_awaiting_anketa(self, user_id: int, chat_id: int):
        c = self._conn()
        c.execute(
            "INSERT OR REPLACE INTO awaiting_anketa (user_id, chat_id) VALUES (?, ?)",
            (user_id, chat_id),
        )
        c.commit()

    def is_awaiting_anketa(self, user_id: int, chat_id: int) -> bool:
        row = self._conn().execute(
            "SELECT 1 FROM awaiting_anketa WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        return row is not None

    def clear_awaiting_anketa(self, user_id: int, chat_id: int):
        c = self._conn()
        c.execute(
            "DELETE FROM awaiting_anketa WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        c.commit()

    # ── Marriages ──────────────────────────────────────────────────────────

    def _ordered(self, a: int, b: int):
        return (min(a, b), max(a, b))

    def are_married(self, a: int, b: int) -> bool:
        u1, u2 = self._ordered(a, b)
        row = self._conn().execute(
            "SELECT 1 FROM marriages WHERE user1_id = ? AND user2_id = ?", (u1, u2)
        ).fetchone()
        return row is not None

    def add_marriage(self, a: int, b: int):
        u1, u2 = self._ordered(a, b)
        c = self._conn()
        c.execute(
            "INSERT OR IGNORE INTO marriages (user1_id, user2_id, created_at) VALUES (?, ?, ?)",
            (u1, u2, datetime.utcnow().isoformat()),
        )
        c.commit()

    def remove_marriage(self, a: int, b: int):
        u1, u2 = self._ordered(a, b)
        c = self._conn()
        c.execute(
            "DELETE FROM marriages WHERE user1_id = ? AND user2_id = ?", (u1, u2)
        )
        c.commit()

    def get_all_marriages(self) -> list[dict]:
        rows = self._conn().execute(
            "SELECT user1_id, user2_id, created_at FROM marriages ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def add_marriage_proposal(self, proposer_id: int, target_id: int, chat_id: int):
        c = self._conn()
        c.execute(
            "INSERT OR REPLACE INTO marriage_proposals (proposer_id, target_id, chat_id, created_at) VALUES (?, ?, ?, ?)",
            (proposer_id, target_id, chat_id, datetime.utcnow().isoformat()),
        )
        c.commit()

    def get_marriage_proposal(self, proposer_id: int, target_id: int, chat_id: int) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM marriage_proposals WHERE proposer_id = ? AND target_id = ? AND chat_id = ?",
            (proposer_id, target_id, chat_id),
        ).fetchone()
        return dict(row) if row else None

    def delete_marriage_proposal(self, proposer_id: int, target_id: int, chat_id: int):
        c = self._conn()
        c.execute(
            "DELETE FROM marriage_proposals WHERE proposer_id = ? AND target_id = ? AND chat_id = ?",
            (proposer_id, target_id, chat_id),
        )
        c.commit()

    # ── Known chats ────────────────────────────────────────────────────────

    def remember_chat(self, chat_id: int):
        c = self._conn()
        c.execute("INSERT OR IGNORE INTO known_chats (chat_id) VALUES (?)", (chat_id,))
        c.commit()

    def get_known_chats(self) -> list[int]:
        rows = self._conn().execute("SELECT chat_id FROM known_chats").fetchall()
        return [r["chat_id"] for r in rows]
