import sqlite3
import threading
from typing import Optional

DB_PATH = "bot.db"


class Database:
    def __init__(self):
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def init(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT DEFAULT '',
                full_name TEXT DEFAULT ''
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
                user1_id INTEGER,
                user2_id INTEGER,
                PRIMARY KEY (user1_id, user2_id),
                CHECK (user1_id < user2_id)
            );
        """)
        conn.commit()
        conn.close()

    # ── Users ──────────────────────────────────────────────────────────────

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

    # ── Awaiting anketa ────────────────────────────────────────────────────

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
            "INSERT OR IGNORE INTO marriages (user1_id, user2_id) VALUES (?, ?)",
            (u1, u2),
        )
        c.commit()

    def remove_marriage(self, a: int, b: int):
        u1, u2 = self._ordered(a, b)
        c = self._conn()
        c.execute(
            "DELETE FROM marriages WHERE user1_id = ? AND user2_id = ?", (u1, u2)
        )
        c.commit()

    def get_all_marriages(self) -> list[tuple[int, int]]:
        rows = self._conn().execute("SELECT user1_id, user2_id FROM marriages").fetchall()
        return [(r["user1_id"], r["user2_id"]) for r in rows]
